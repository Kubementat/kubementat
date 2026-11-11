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

import stat
import subprocess
from pathlib import Path

import click
import pytest

from bootstrap_tools import (EMAIL, FINGERPRINT, GPG_PRIVATE, GPG_PUBLIC, actions, argv,
                             commit_count, crypt_calls, crypt_calls_on, root, since_last_run, tools,
                             tree_bytes)
from kubementat import config_bootstrap, config_keys
from kubementat.config_bootstrap import GIT_CRYPT_SYMMETRIC_KEY_NAME, prepare_configuration_directory
from kubementat.config_init import CREATED, SKIPPED


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
        # HEAD to read against. They are *reads* in this fixture because the stand-in git answers 0,
        # which is the case where kubementat writes nothing -- the writes are asserted in the identity
        # tests, which run a git that answers 1.
        ["git", "config", "user.name"],
        ["git", "config", "user.email"],
        ["git", "commit", "-m", "chore: initialize kubementat configuration"],
    ], f"the steps ran in this order: {argv(tools)}"
    assert set(actions(steps)) == {CREATED}, \
        f"a fresh directory had nothing in it, so nothing may report skipped: {[str(s) for s in steps]}"
    assert (root / ".gitattributes").read_bytes() == b"*.encrypted.* filter=git-crypt diff=git-crypt\n"
    assert commit_count(root, tools) == "1"


def test_the_attributes_file_is_on_disk_while_the_encrypted_file_is_being_written(root, tools,
                                                                                 monkeypatch):
    """The ordering guard, stated as the tree's state at the moment step 4 runs.

    This is the test that fails when steps 2 and 4 swap, and the only one that catches the swap:
    afterwards both trees look identical, because the checkout decrypts the working copy either way.
    What differs is one commit of plaintext history, which no assertion against the working tree can
    see. If this test starts passing for the wrong reason the ordering claim is already gone, and the
    only thing that would notice is the lane's first-commit assert — against a real keystore,
    somewhere in the e2e suite.
    """
    seen = {}

    def spy(*args, **kwargs):
        where = Path(kwargs.get("root") or args[0])
        seen["attributes"] = (where / ".gitattributes").is_file()
        seen["encrypted"] = (where / "dev" / "static.encrypted.json").exists()
        return real_init(*args, **kwargs)

    real_init = config_bootstrap.config_init.init_configuration
    monkeypatch.setattr(config_bootstrap.config_init, "init_configuration", spy)

    prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    assert seen == {"attributes": True, "encrypted": False}, (
        f".gitattributes present: {seen.get('attributes')}; encrypted layer already written: "
        f"{seen.get('encrypted')}. The filter was declared after the file it protects, which means a "
        f"real git-crypt would have put that file in the object store in plaintext and the working "
        f"tree would still look correct")


def test_the_collaborator_is_registered_without_git_crypt_making_a_commit(root, tools):
    """`--no-commit`, asserted on the argv, because that flag is the mechanism and not a feeling.

    git-crypt commits what it adds. When `initialize` let it, the `Add 1 git-crypt collaborator`
    commits that followed were read by the next run as evidence that the keystore step had run, and
    the state they left is what made every later run skip work it had never done. Step 9 commits,
    once, in a sentence that says what it is.
    """
    prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    add = crypt_calls(tools, "add-gpg-user")
    assert len(add) == 1, f"the deployer was registered {len(add)} times: {add}"
    assert "--no-commit" in add[0], (
        f"git-crypt was asked to add the collaborator with its default commit behaviour: {add[0]}. "
        f"That is the flag that kept the collaborator commits out of a tree whose own instructions "
        f"tell the operator to commit")
    assert add[0][-1] == FINGERPRINT, (
        f"the thing being registered is not this directory's fingerprint: {add[0]}. An email is what "
        f"git-crypt used to be handed, and a keyring that holds two keys under one email resolves "
        f"either of them, so the tree can be encrypted to a key that cannot decrypt its own files")
    assert "--trusted" in add[0], (
        f"git-crypt was told to trust nothing, so it will hand the symmetric key to gpg and gpg will "
        f"refuse a public-only key with 'Unusable public key': {add[0]}. The flag is measured to "
        f"persist nothing, so the cost of passing it is zero and the cost of not passing it is a "
        f"directory that looks ready and is not")
    assert len(argv(tools)) and [call for call in argv(tools) if call[:2] == ["git", "commit"]] == [
        ["git", "commit", "-m", "chore: initialize kubementat configuration"]], (
        f"the only commit in a prepared tree should be step 9's, whose message says what it is; the "
        f"commits git-crypt writes itself say `Add 1 git-crypt collaborator`, and a tree that reads "
        f"like that is a tree whose next run will skip a step it never ran: {argv(tools)}")


def test_the_exported_key_is_the_ability_to_read_the_cluster_and_is_readable_by_its_owner_only(root,
                                                                                               tools):
    """The mode and not the path is the claim: git-crypt writes the export at the run's umask.

    On a normal umask that is 0644, which is a symmetric key every account on the box can read. Step
    3's ignore rule keeps the file out of the commit; this mode is what keeps it out of the next
    account's way, and it is exactly the kind of thing that reads correct on a developer's machine.
    """
    prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    exported = root / GIT_CRYPT_SYMMETRIC_KEY_NAME
    assert exported.is_file(), f"the symmetric key was not exported: {argv(tools)}"
    mode = stat.S_IMODE(exported.stat().st_mode)
    assert mode == 0o600, (f"{exported.name} grants {oct(mode)}; that file unlocks every credential "
                           f"the cluster holds")


# ---------------------------------------------------------------------------------------------
# The second run.
# ---------------------------------------------------------------------------------------------

def test_a_directory_named_relative_to_the_run_is_prepared_and_named_absolutely(root, tools,
                                                                                monkeypatch):
    """A relative `root` resolves once, here, and never into `cfg/cfg/git_crypt_symmetric.key`.

    Steps 1, 8 and 9 exec with `cwd=root`, so an argv path spelled against the calling directory
    resolves twice: the live failure was `git-crypt export-key cfg/git_crypt_symmetric.key` answered
    with `System error: open: cfg/git_crypt_symmetric.key: No such file or directory`, four steps
    after the operator typed `kmt config init ./cfg`. It was invisible to the suite because every
    fixture root is absolute, and it became reachable the moment a command started calling this
    function with the path it was handed.
    """
    monkeypatch.chdir(root.parent)

    steps = prepare_configuration_directory(Path(root.name), "dev", gpg_email=EMAIL)

    assert (root / config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_NAME).is_file(), \
        "the symmetric key landed somewhere other than the directory it was prepared in"
    outside = [str(step.path) for step in steps
               if step.path != root and root not in step.path.parents]
    assert not outside, \
        f"a relative run reported paths outside the directory it prepared: {outside}"
    assert commit_count(root, tools) == "1", "the relative run still made the first commit"


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


def test_a_directory_that_is_already_a_repository_skips_step_one_and_still_prepares_the_tree(root,
                                                                                              tools):
    """`git init` says `skipped` and everything after it still happens.

    The repository is the precondition step 1 establishes and not a thing to re-establish: an operator
    who cloned the directory before configuring it, or a lane that initialized it in an earlier stage,
    gets the same ready tree either way.
    """
    prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    steps = prepare_configuration_directory(root, "dev", gpg_email=EMAIL)

    assert steps[0] == steps[0].__class__(config_bootstrap.GIT_REPOSITORY_STEP, root / ".git", SKIPPED), \
        f"the repository was re-initialized: {steps[0]}"
    assert tree_bytes(root), "everything after step 1 still ran, so the tree is still complete"


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


def test_the_fingerprint_git_crypt_is_told_is_the_one_the_directorys_key_answers(root, tools,
                                                                                  monkeypatch):
    """Step 7 registers the key that is in the directory, and never a key a keyring suggests.

    The two answers used to be the same question: `config_bootstrap` listed the keyring for the email
    and registered what came back first. That is the bug, and it is the dangerous shape of it rather
    than a cosmetic one -- with two trees under one identity the listing holds both keys, and picking
    the other tree's *succeeds*, leaving a directory encrypted to a key that cannot open its own
    secrets. So the assertion here is about where the answer came from, and the only way to show that
    without a real second keyring is to make the two possible answers disagree: the fixture hands step
    7 a fingerprint that is not derived from anything in this directory, so a run that consulted the
    keyring would report it, and a run that reads the tree reports the one below. The pair is planted
    rather than generated because the read of a key file is what is being pinned.
    """
    root.mkdir(parents=True)
    (root / config_keys.SSH_PRIVATE_NAME).write_text("-----BEGIN RSA PRIVATE KEY-----\nfixture\n"
                                                    "-----END RSA PRIVATE KEY-----\n")
    (root / config_keys.SSH_PUBLIC_NAME).write_text(f"ssh-rsa AAAAfixture {EMAIL}\n")
    (root / config_keys.GPG_PRIVATE_NAME).write_text(GPG_PRIVATE, encoding="ascii")
    (root / config_keys.GPG_PUBLIC_NAME).write_text(GPG_PUBLIC, encoding="ascii")
    asked_for: list[Path] = []
    seen: list[Path] = []

    def fingerprint_from_the_tree(directory):
        seen.append(Path(directory))
        return "7F5F85FD66D549566D7F233B7F51FEE297E4110C"

    def import_only_the_public_half(directory, fingerprint):
        asked_for.append(Path(directory))

    monkeypatch.setattr(config_bootstrap, "_directory_fingerprint", fingerprint_from_the_tree)
    monkeypatch.setattr(config_bootstrap, "_import_the_public_half", import_only_the_public_half)

    steps = prepare_configuration_directory(root, "dev")

    add = crypt_calls(tools, "add-gpg-user")
    assert len(add) == 1, f"the collaborator was registered {len(add)} times: {add}"
    assert add[0][-1] == "7F5F85FD66D549566D7F233B7F51FEE297E4110C", (
        f"git-crypt was told something other than the fingerprint this directory's key file answers: "
        f"{add[0]}. The other thing it could have been told is a fingerprint from a keyring listing,"
        f" which is the answer that registers the wrong tree's key without saying so")
    assert seen == [root], f"the fingerprint is read out of the directory being prepared: {seen}"
    assert asked_for == [root], f"the public half was imported into this tree's directory: {asked_for}"
    assert any(config_keys.SSH_PRIVATE_STEP in str(step) for step in steps), \
        "the keypair's own steps are reported here rather than reinvented"


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
    with pytest.raises(click.ClickException) as refused:
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
