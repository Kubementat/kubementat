"""`config init` prepares a directory a lane can install from, and the order is what it proves.

`config_bootstrap.prepare_configuration_directory` is the library half of `kmt config init`: `config_init`'s tree,
`config_keys`' keypair, and the git-crypt dance that makes the encrypted files actually encrypted.
Nothing here is a command and nothing here prints — the flag, the step lines and the closing prose are
`kmt config init`'s own, in `test_config_init_creates_a_tree_and_reports_what_it_touched.py`.

What is asserted, and why each one is the thing that breaks:

* **The argv sequence, in order.** The nine steps are not nine independent facts; the *order* is the
  product, because git-crypt encrypts on `git add` through the filter `.gitattributes` declares. One
  assertion over the ordered list, so a reordering cannot pass three small tests.
* **`.gitattributes` is on disk while the encrypted layer is being written.** The swap this guards is
  the plausible one — git first, attributes later — and it is invisible in the working tree forever
  after: the checkout decrypts the file and it looks fine, while `git show HEAD:<path>` holds the
  plaintext that went into the object store on the first `git add`. The lane's first-commit assert is
  the version of this test that runs against a real repository.
* **`--no-commit` on `add-gpg-user`.** The flag's job is to stop git-crypt committing behind our back.
  `initialize` let it, and the commits it wrote were read by the next run as evidence that a keystore
  step had run. And **the thing registered is a fingerprint and not an email**, because a keyring that
  holds two keys under one email picks one of them and succeeds with the wrong one.
* **`0600` on the exported key**, which is the ability to read every credential in the cluster and
  which git-crypt writes at whatever umask the run had.
* **A second run writes nothing**, asserted on bytes and commit count and not on step counts, because
  `git-crypt export-key` over an identical file is still a write.
* **A `.gitattributes` somebody else configured is refused, and `force` does not lift it.**
* **Nothing execs `git-crypt unlock`, `status`, or `check`.** kubementat reads, it never unlocks.
* **A run with no identity writes nothing at all.** The keypair question is decided ahead of `git
  init`, so a refusal leaves no `.git` — the promise `config_keys` makes and the promise a check made
  four steps in could not keep.

Both files run on `tests/bootstrap_tools.py` — the recorded `git`, the recorded `git-crypt` that writes
the three filesystem effects idempotence is decided on, the patched generators, the patched keyring,
and the absent-but-nameable directory. Nothing here builds a fixture of its own, because a fixture
kept in two files is a fixture that can drift, and the drift would show up as the library's fake
still writing a keystore file while the verb's had stopped.
"""

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

    The two git files land before `init_configuration` writes the encrypted layer, the keystore is
    initialized before the collaborator is registered, the collaborator is registered before the key
    is exported — an export that runs first leaves the tree with a key nobody can read — and the
    commit is last, because that commit is what a first-commit check reads.
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
    """Idempotence, measured as bytes and as a commit count and not as a step count.

    Asserted that way because `git-crypt export-key` over an identical file is still a write, so a run
    that says `skipped` while re-exporting is the exact agreement that hides the bug. Same for the
    commit: a second `chore: initialize kubementat configuration` would be a lie about what changed.
    The git-crypt assertions read the *second* run's calls and not the whole log, because the first
    run legitimately made every one of them.
    """
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
    """The operator's own filters are refused by name, because kubementat cannot tell deliberate from stale.

    `force` is about material kubementat did not write, and this file is a rule somebody chose. The
    byte-identity rule that protects it is the one `init_configuration` applies to a layer file, so it
    has to survive `force=True` here as well — if it did not, this module would have given the flag a
    second and softer meaning, which is how a flag stops meaning anything.
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
    """`commit=False` stages the tree and makes no history.

    A deliberate shape and not a half-run: the directory is ready, the keystore is configured, the key
    is exported, and the operator decides what the first commit says. Asserted on the absence of HEAD
    and the presence of a staged tree, because a commit named for initializing that the operator did
    not ask for is the one thing this flag exists to withhold.
    """
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

    kubementat reads and never unlocks, and a sweep of `src/` found nothing
    running them. An unlock would make the next command work, which is precisely why it is the most
    plausible-looking wrong thing this function could do: it takes the checkout's guarantee away from
    the directory the operator is standing in, and it moves the one place the lane's clone task
    belongs. `status` and `check` answer about the keystore a question this module answers by looking
    at the filesystem, so running them would give one fact two owners.
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
    """The answer decided ahead of step 1: no identity is not a step that can be skipped.

    What is asserted is the absence, and there is nothing gentle about it: a run that decides its
    question four steps late has already made a repository, written two files, and left the operator
    with a directory that reads as prepared and is not. `config_keys` promises every refusal is
    decided before a file is written, and this is the case where that promise was being broken from
    outside the module that made it.
    """
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

    A directory with a private key and no public key is a directory somebody put something in, and
    `config_keys` owns that refusal: finishing the pair would mean writing over the half that is
    there. So the pre-flight stands down the moment it sees a GPG file, and what comes back is the
    sentence that tells the operator which file is missing — not the one that tells them to pass a
    flag they could not have used.
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
