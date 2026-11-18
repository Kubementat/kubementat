"""`config init` prepares a directory a lane can install from, and the order is what it proves."""

import subprocess

import pytest
from bootstrap_tools import (
    EMAIL,
    FINGERPRINT,
    GPG_PRIVATE,
    actions,
    argv,
    commit_count,
    crypt_calls_on,
    since_last_run,
    tree_bytes,
)

from kubementat.config import bootstrap as config_bootstrap
from kubementat.config import keys as config_keys
from kubementat.config.bootstrap import (
    GIT_CRYPT_SYMMETRIC_KEY_NAME,
    prepare_configuration_directory,
)
from kubementat.config.init import CREATED, SKIPPED
from kubementat.errors import DeployerIdentityNotNamedError

# ---------------------------------------------------------------------------------------------
# The first run.
# ---------------------------------------------------------------------------------------------

def test_a_fresh_directory_runs_the_nine_steps_in_the_order_the_order_is_the_product(root, tools):
    """One assertion over the ordered argv list, because the swap is the bug and three asserts hide it.
    """
    steps = prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    assert argv(tools) == [
        ["git", "init"],
        ["git-crypt", "init"],
        ["git-crypt", "add-gpg-user", "--no-commit", "--trusted", FINGERPRINT],
        ["git-crypt", "export-key", str(root / GIT_CRYPT_SYMMETRIC_KEY_NAME)],
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        ["git", "add", "-A"],
        # The two committer-identity reads sit here and not before `add`: they are a precondition of the
        # commit and nothing else, so asking earlier would read a repository that may not have a
        # HEAD to read against. They answer without writing, so the list holds no identity writes, and
        # that is a property of the fixture and not of the machine running the suite: `tools` states an
        # identity in an isolated HOME, so the read answers on a developer's box and on a freshly
        # created VM alike. What kubementat writes when the read answers nothing is asserted against
        # real git by the committer-identity file.
        ["git", "config", "user.name"],
        ["git", "config", "user.email"],
        ["git", "commit", "-m", "chore: initialize kubementat configuration"],
    ], f"the steps ran in this order: {argv(tools)}"
    assert set(actions(steps)) == {CREATED}, \
        f"a fresh directory had nothing in it, so nothing may report skipped: {[str(s) for s in steps]}"
    assert (root / ".gitattributes").read_bytes() == b"*.encrypted.* filter=git-crypt diff=git-crypt\n"
    assert commit_count(root, tools) == "1"


# ---------------------------------------------------------------------------------------------
# The second run.
# ---------------------------------------------------------------------------------------------


def test_the_second_call_on_a_tree_nothing_touched_reports_skipped_and_moves_no_byte(root, tools):
    """Idempotence, measured as bytes and as a commit count and not as a step count."""
    prepare_configuration_directory(root, "dev", gpg_email=EMAIL)
    before, first = tree_bytes(root), commit_count(root, tools)
    since_last_run(tools)

    steps = prepare_configuration_directory(root, "dev", gpg_email=EMAIL)
    second_run = argv(tools)

    steps = prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    assert set(actions(steps)) == {SKIPPED}, \
        f"nothing was missing the second time, so nothing may claim to be created: {[str(s) for s in steps]}"
    assert tree_bytes(root) == before, "skipped has to mean the bytes are the ones that were there"
    assert commit_count(root, tools) == first, "the second run committed, and the history is the lane's evidence"
    assert crypt_calls_on(second_run, "add-gpg-user") == [], \
        "the collaborator was registered again: a re-registration is a write that reports itself as none"
    assert crypt_calls_on(second_run, "export-key") == [], \
        "the symmetric key was re-exported over a file that was already correct, which is a write no step owns"
    assert crypt_calls_on(second_run, "init") == [], \
        "the keystore was initialized again on top of one that was already there"
    assert (root / ".git/git-crypt/keys/default").is_file(), \
        "the master key is what step 6 checks, so the skip has to be true about it"


# ---------------------------------------------------------------------------------------------
# The refusals, and what is never exec'd.
# ---------------------------------------------------------------------------------------------

def test_an_attributes_file_somebody_else_wrote_is_refused_and_force_does_not_lift_it(root, tools):
    """The operator's own filters are refused by name, because kubementat cannot tell deliberate from
    stale.
    """
    root.mkdir(parents=True)
    (root / ".gitattributes").write_text("*.txt filter=identity diff=identity\n", encoding="utf-8")
    before = tree_bytes(root)

    with pytest.raises(Exception) as refused:
        prepare_configuration_directory(root, "dev", gpg_email=EMAIL, force=True)

    message = str(refused.value)
    assert ".gitattributes" in message, f"the refusal names the file it refused to touch: {message}"
    assert tree_bytes(root) == before, "force rewrote a filter rule somebody configured"
    assert commit_count(root, tools) == "", "a run that refused still made the first commit"
    assert not (root / ".git/git-crypt/keys/default").exists(), \
        "git-crypt was initialized in a run that stopped before the tree was accounted for"


def test_the_first_commit_can_be_left_to_the_operator_without_losing_anything_else(root, tools):
    """`commit=False` stages the tree and makes no history."""
    steps = prepare_configuration_directory(root, "dev", gpg_email=EMAIL, commit=False)

    assert commit_count(root, tools) == "", "HEAD exists, so a commit was made anyway"
    staged = subprocess.run([tools["git"], "diff", "--cached", "--name-only"], cwd=root,
                            capture_output=True, text=True).stdout.split()
    assert ".gitattributes" in staged and "dev/static.encrypted.json" in staged, \
        f"the whole tree should be staged for the operator to commit: {staged}"
    assert not [call for call in argv(tools) if call[:2] == ["git", "commit"]], \
        "commit=False still committed"
    assert any(step.action == SKIPPED and config_bootstrap.GIT_COMMIT_STEP in str(step)
               for step in steps), "the commit step said nothing about not committing"


def test_nothing_in_this_run_unlocks_or_inspects_the_keystore(root, tools):
    """`unlock`, `status`, `check` — none of them appear, and their absence is a rule and not a habit.
    """
    prepare_configuration_directory(root, "dev", gpg_email=EMAIL)
    prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    for call in argv(tools):
        assert not (call and call[0] == "git-crypt"
                    and any(word in call for word in ("unlock", "status", "check"))), (
        f"kubementat ran {call}: unlock belongs to the lane's clone task, and status is a listing "
        f"this module has no business parsing")


# ---------------------------------------------------------------------------------------------
# The refusal made before the first byte.
# ---------------------------------------------------------------------------------------------

def test_a_run_with_no_identity_refuses_before_it_creates_anything(root, tools):
    """The answer decided ahead of step 1: no identity is not a step that can be skipped."""
    with pytest.raises(DeployerIdentityNotNamedError) as refused:
        prepare_configuration_directory(root, "dev")

    assert not root.exists(), (f"the run refused and still made the directory: {argv(tools)}")
    assert argv(tools) == [], f"the refusal ran something first: {argv(tools)}"
    message = str(refused.value)
    assert config_keys.GPG_EMAIL_FLAG in message and config_keys.GPG_KEY_FLAG in message, (
        f"the refusal has to name the two flags that answer it, because one of them is what the "
        f"operator has to type next: {message}")


def test_a_directory_that_holds_one_half_of_the_keypair_is_refused_by_the_pair_rule(root, tools):
    """The new guard is about material that is *absent*, and it does not step on the half-pair rule.
    """
    root.mkdir(parents=True)
    (root / config_keys.GPG_PRIVATE_NAME).write_text(GPG_PRIVATE, encoding="ascii")

    with pytest.raises(Exception) as refused:
        prepare_configuration_directory(root, "dev")

    message = str(refused.value)
    assert "half a gpg keypair" in message, (
        f"the refusal is the pair rule's and not a request for an email: {message}")
    assert config_keys.GPG_EMAIL_FLAG not in message, (
        f"a directory that already has half a keypair is not fixed by an email: {message}")
