"""The deployer's keypair is a property of a configuration directory, so it is made where one is.

`config_keys.py` is the successor to the key half of `initialize` (census L, Q2 items 6 and 7): the
four files must land *inside* the directory `config init` prepares, because after phase 5 that is the
only place git-crypt can protect them and the only place a reader can find them. What is asserted
here is the module's own account of itself, not the wiring — the verb belongs to whoever calls it.

The claims:

* **Four files, and only four.** `deployer_ssh_key`, `deployer_ssh_key.pub`, `gpg_private.key`,
  `gpg_public_key.gpg`. `git_crypt_symmetric.key` is git-crypt's name and is never written here, and
  nothing is written beside the product: `kubementat_main_dir` is not this module's to know about.
* **Idempotence is about bytes and not about step counts.** A rerun says `SKIPPED` and moves nothing;
  foreign material says `EXISTED` and moves nothing. Both are asserted by reading the files back,
  because a step count that agrees with a write that happened anyway is exactly the kind of agreement
  that hides a bug.
* **The flag that supplies a key supplies it.** `gpg_key` imports, generates nothing, and names the key
  it was handed. A `gpg_key` that points at nothing refuses with the path rather than quietly
  generating a key nobody asked for — the "silently generated instead" version of this bug reports
  itself as success.
* **Nothing is invented.** No environment variables, no `deployer@localhost`: a generation that is not
  given an identity refuses with the flag that would have supplied it, and the identity a directory
  already has is read back out of the key rather than remembered.
* **The guard is the thing `setup_pipelines` never had** (census L, Q2 item 8): a directory with no
  keypair says so by name, by path, and by command instead of dying inside a client serializer.
"""

import ast
import os
import re
import shutil
import tempfile
from pathlib import Path

import gnupg
import pytest
from cryptography.hazmat.primitives.serialization import load_ssh_public_key

from kubementat import config_init, config_keys
from kubementat.config_init import CREATED, EXISTED, SKIPPED
from kubementat.config_keys import (GPG_PRIVATE_NAME, GPG_PUBLIC_NAME, KEYPAIR_FILE_NAMES,
                                    SSH_PRIVATE_NAME, SSH_PUBLIC_NAME,
                                    DeployerKeypair, ensure_deployer_keypair,
                                    require_deployer_keypair)
from kubementat.errors import DeployerKeyMissingError
from kubementat.crypto_utils import CryptoUtils

REPO_ROOT = Path(__file__).resolve().parents[1]
EMAIL = "deployer@base.example.test"

PRIVATE_NAMES = (SSH_PRIVATE_NAME, GPG_PRIVATE_NAME)
PUBLIC_NAMES = (SSH_PUBLIC_NAME, GPG_PUBLIC_NAME)


@pytest.fixture
def config_dir(tmp_path):
    """A directory that is being prepared. Nothing here makes one: that is `config init`'s job."""
    directory = tmp_path / "configuration"
    directory.mkdir()
    return directory


@pytest.fixture
def decoy_main_dir(tmp_path, monkeypatch):
    """A `kubementat_main_dir` that is not the checkout, so the rule is measured and not assumed."""
    directory = tmp_path / "somewhere_else"
    directory.mkdir()
    monkeypatch.setenv("KUBEMENTAT_HOME", str(directory))
    return directory


@pytest.fixture(autouse=True)
def no_answers_in_the_environment(monkeypatch):
    """This module reads no dial, so every test runs with the dials a real machine would have set.

    `BASE_DOMAIN` is cleared because `initialize` derived the deployer's identity from it
    (`automation.py:338-340`) and this module must not; the two root variables go for the same reason
    a decoy is set elsewhere. A module that read any of them would still pass the tests above, which
    is why the source is read as well — see the last test.
    """
    for name in ("BASE_DOMAIN", "KUBEMENTAT_PLATFORM_CONFIG_DIR", "KUBEMENTAT_HOME"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def gpg_generator(monkeypatch):
    """A stand-in for `CryptoUtils.generate_gpg_key` that is faithful where the module needs it.

    The real one asks the operator's own keyring for 4096 bits of entropy, which is slow and leaves a
    key in `~/.gnupg` that no test has a right to add. This one generates into a keyring it throws
    away, writes the same two files from the same two keyword arguments, returns the same shape, and
    records the call — so the armor is real GPG armor (which is what `require_deployer_keypair` reads
    an identity out of) and the module's own request is still on the record.

    What it is also evidence for: the module never passes a passphrase, so the key it asks for is
    unprotected, which is the consequence the module docstring claims rather than a bug found later.
    """
    calls: list[dict] = []

    def generate(name_email, passphrase="", key_type="RSA", key_length=4096,
                 private_key_filepath=None, public_key_filepath=None):
        record = {"name_email": name_email, "passphrase": passphrase, "key_type": key_type,
                  "key_length": key_length, "private_key_filepath": private_key_filepath,
                  "public_key_filepath": public_key_filepath}
        calls.append(record)
        armor = _generate_keyarmor(name_email)
        _put(private_key_filepath, armor["secret"])
        _put(public_key_filepath, armor["public"])
        return {"public_keys": armor["public"], "private_keys": armor["secret"]}

    monkeypatch.setattr(CryptoUtils, "generate_gpg_key", staticmethod(generate))
    generate.calls = calls
    return generate


@pytest.fixture
def secret_key_file(tmp_path):
    """A deployer's own armored secret key, standing where an operator would have put it.

    Real armor, made once per test, because the whole point of `--gpg-key` is that it takes a file and
    not a description of one.
    """
    path = tmp_path / "someone-elses-key.asc"
    _put(path, _generate_keyarmor(EMAIL)["secret"])
    return path


def _generate_keyarmor(email: str) -> dict:
    """One 2048-bit key in a keyring that survives only for the length of the call."""
    home = Path(tempfile.mkdtemp(prefix="kubementat-test-keyring-"))
    os.chmod(home, 0o700)
    try:
        gpg = gnupg.GPG(gnupghome=str(home), verbose=False)
        key = gpg.gen_key(gpg.gen_key_input(name_real=email, name_email=email, no_protection=True,
                                            key_type="RSA", key_length=2048))
        return {"public": gpg.export_keys(key.fingerprint),
                "secret": gpg.export_keys(keyids=key.fingerprint, secret=True,
                                          expect_passphrase=False)}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _put(path, text) -> None:
    if path is None:
        return
    Path(path).write_text(text, encoding="ascii")


def actions(steps) -> list[str]:
    return [step.action for step in steps]


def created_paths(steps) -> list[Path]:
    return [step.path for step in steps if step.action == CREATED]


def read_all(directory) -> dict:
    """Every byte of every file this module is supposed to care about, for a before/after compare."""
    return {name: (directory / name).read_bytes() for name in KEYPAIR_FILE_NAMES
            if (directory / name).is_file()}


def is_private_key(path: Path) -> bool:
    return b"PRIVATE KEY" in path.read_bytes()[:64]


# --------------------------------------------------------------------------------------------
# The write set.
# --------------------------------------------------------------------------------------------

def test_generating_a_keypair_creates_exactly_four_files_and_reads_only_to_their_owner(config_dir,
                                                                                        gpg_generator):
    """Four files, in the four reserved names, with the private halves readable by their owner alone.

    `CryptoUtils` writes through `open(path, "w")`, so without the module's own `chmod` the private
    halves land at whatever umask the run had — 0644 on a normal machine, which is a deployer key
    every account on the box can read.
    """
    steps = ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    assert sorted(name for name in os.listdir(config_dir)) == sorted(KEYPAIR_FILE_NAMES), \
        "the write set is the four keypair files and nothing else, not a set that grew a marker file"
    assert actions(steps) == [CREATED, CREATED, CREATED, CREATED], \
        f"every path was missing, so every step did create the file: {[str(s) for s in steps]}"

    for name in PRIVATE_NAMES:
        mode = (config_dir / name).stat().st_mode & 0o777
        assert mode == 0o600, f"{name} is the deployer's own half and must not be readable by anyone else, got {oct(mode)}"
    for name in PUBLIC_NAMES:
        assert (config_dir / name).read_bytes().strip(), f"{name} is the half that gets handed to git, so empty is not it"
    assert is_private_key(config_dir / SSH_PRIVATE_NAME), \
        "the ssh private half is a private key and not an empty file with the right name"
    assert load_ssh_public_key((config_dir / SSH_PUBLIC_NAME).read_bytes().split(b"\n")[0].strip()), \
        "the two ssh halves are supposed to be one key; the public one has to parse as one"


def test_the_generated_gpg_key_is_asked_for_by_the_email_the_caller_gave_and_not_another_one(config_dir,
                                                                                             gpg_generator):
    """The email goes to the generator and nothing else answers for it.

    A module that invented an identity — `deployer@localhost` is the obvious candidate, and the shape
    `initialize` settled for — would pass every file test above and be wrong about who the deployer
    is. So the test that matters is the one on the argument the module was handed.
    """
    ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    assert len(gpg_generator.calls) == 1, \
        f"one key was asked for, not two: {gpg_generator.calls}"
    call = gpg_generator.calls[0]
    assert call["name_email"] == EMAIL, \
        f"the identity is the caller's answer and not one the library found: {call['name_email']!r}"
    assert call["passphrase"] == "" and call["public_key_filepath"], \
        f"the generator is reached with no passphrase, which is what makes this non-interactive: {call}"


def test_running_it_again_skips_every_step_and_moves_no_byte(config_dir, gpg_generator):
    """The second run is a report and not a write: same bytes, all `skipped`.

    Asserted on bytes and not on the step count, because a count that says "skipped" over a key that
    was actually regenerated is a lie about the thing that matters — the deployer the directory
    describes is the one that was made the first time, and the git-crypt collaborator added against it
    would silently stop being that deployer.
    """
    ensure_deployer_keypair(config_dir, gpg_email=EMAIL)
    before = read_all(config_dir)

    steps = ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    assert set(actions(steps)) == {SKIPPED}, \
        f"nothing was missing the second time, so nothing may be claimed to be created: {[str(s) for s in steps]}"
    assert read_all(config_dir) == before, \
        "skipped has to mean the bytes are the ones that were there; a regenerated key is a different deployer"


def test_a_foreign_keypair_is_somebody_elses_and_becomes_the_one_step_that_touches_it_not_at_all(config_dir):
    """Material this module was not told to make is `EXISTED`, untouched.

    `SKIPPED` would be almost as true and is the step the next ticket would trust as "this is mine".
    `EXISTED` is what `config_init` uses for the same situation for the same reason, and the bytes are
    what proves the word.
    """
    for name in KEYPAIR_FILE_NAMES:
        (config_dir / name).write_bytes(b"somebody else's file entirely\n")
    before = read_all(config_dir)

    steps = ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    assert actions(steps) == [EXISTED, EXISTED, EXISTED, EXISTED], \
        f"foreign material is reported as foreign, not as work done: {[str(s) for s in steps]}"
    assert read_all(config_dir) == before, "existing material is not touched, whatever the run was asked to do"


def test_nothing_here_writes_the_git_crypt_key_and_nothing_writes_beside_the_product(config_dir,
                                                                                     gpg_generator,
                                                                                     decoy_main_dir):
    """The rule in one line, and the fifth name on the reserved list stays git-crypt's.

    `git_crypt_symmetric.key` is in `config_init.RESERVED_ROOT_NAMES` because phase 5 wants it in the
    directory, and it is also the name git-crypt exports itself. Writing it here would be this module
    deciding what git-crypt's key is. `kubementat_main_dir` is pointed somewhere else on purpose: a
    module that located its own root instead of being handed one would land a file there, and that
    decoy is what stays empty.
    """
    steps = ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    assert "git_crypt_symmetric.key" not in {path.name for path in created_paths(steps)}, \
        "git-crypt's own key is not this module's to write"
    inside_main = [path for path in created_paths(steps) if decoy_main_dir in path.resolve().parents]
    assert inside_main == [], \
        f"the write set is under the directory the caller handed and nowhere else: {inside_main}"
    assert list(decoy_main_dir.iterdir()) == [], "the decoy is proof that no root was located by proximity"


# --------------------------------------------------------------------------------------------
# The flag half of the ruling.
# --------------------------------------------------------------------------------------------

def test_an_imported_key_becomes_the_directories_own_key_and_nothing_is_generated(config_dir,
                                                                                   gpg_generator,
                                                                                   secret_key_file):
    """`gpg_key` imports: the secret half is the file that was handed over, byte for byte.

    A re-encoded key would be a key that no longer matches what the operator holds. The public half is
    derived from it, which is why a pair made this way is one pair and not two claims — and the
    identity that pair carries is read back out of it, so `--gpg-key` needs no `--gpg-email` to go
    with it.
    """
    steps = ensure_deployer_keypair(config_dir, gpg_key=secret_key_file)

    assert actions(steps) == [CREATED, CREATED, CREATED, CREATED], \
        f"the ssh half is generated and the gpg half is imported, and both are creations: {[str(s) for s in steps]}"
    assert (config_dir / GPG_PRIVATE_NAME).read_bytes() == secret_key_file.read_bytes(), \
        "an imported key is the same key: the bytes that came in are the bytes that land"
    assert (config_dir / GPG_PUBLIC_NAME).read_text().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"), \
        "the public half is derived from the imported key and not written from a generation"
    assert gpg_generator.calls == [], \
        f"a supplied key is imported and nothing is generated: {gpg_generator.calls}"
    assert require_deployer_keypair(config_dir).gpg_email == EMAIL, \
        "the imported pair is asked its name, and it answers with the operator's own"


def test_a_missing_key_file_refuses_by_name_and_generates_nothing_as_a_substitute(config_dir,
                                                                                  gpg_generator,
                                                                                  tmp_path):
    """A `gpg_key` that points at nothing is refused with the path, and not quietly generated around.

    This is the bug the ticket would otherwise be reported for: a run that says "created" three lines
    later has made a deployer the operator never asked for, into a directory they believed they were
    configuring with their own key.
    """
    absent = tmp_path / "there-is-no-such-key.asc"

    with pytest.raises(Exception) as refused:
        ensure_deployer_keypair(config_dir, gpg_key=absent)

    message = str(refused.value)
    assert str(absent) in message, \
        f"the refusal has to name the file the operator mis-typed: {message}"
    assert "config init" in message, f"and the command that fixes it: {message}"
    assert gpg_generator.calls == [], \
        "nothing may be generated on the strength of a path that is not there"
    for name in (GPG_PRIVATE_NAME, GPG_PUBLIC_NAME):
        assert not (config_dir / name).exists(), \
            f"{name} would be a generated stand-in for the key that was asked for"


def test_a_supplied_key_that_is_not_a_key_refuses_rather_than_writing_a_file_it_cannot_read(config_dir,
                                                                                             gpg_generator,
                                                                                             tmp_path):
    """The import refuses before writing, so a text file never becomes `gpg_private.key`.

    Writing the file and failing afterwards would leave the directory holding half a pair under a name
    the guard then has to explain.
    """
    junk = tmp_path / "notes.txt"
    junk.write_text("the deployer is alice, apparently\n")

    with pytest.raises(Exception) as refused:
        ensure_deployer_keypair(config_dir, gpg_key=junk)

    assert str(junk) in str(refused.value), f"the refusal names the file it tried to import: {refused.value}"
    assert not (config_dir / GPG_PRIVATE_NAME).exists(), \
        "a file that is not a key does not become one"
    assert gpg_generator.calls == [], "and a junk file is no reason to generate"


def test_a_generation_that_is_not_told_whose_key_it_is_refuses_and_names_the_flag(config_dir,
                                                                                  gpg_generator):
    """No email, no key: refused, with the flag that would have answered.

    `initialize` answered this by building `deployer<n>@$BASE_DOMAIN` out of an environment variable
    (`automation.py:338-340`), which is the answer this rule takes away. Inventing one here would pass
    a test that checks only that a file exists.
    """
    with pytest.raises(Exception) as refused:
        ensure_deployer_keypair(config_dir)

    message = str(refused.value)
    assert "--gpg-email" in message, f"the refusal says which flag supplies the answer: {message}"
    assert "config init" in message, f"and the command that carries it: {message}"
    assert gpg_generator.calls == [], "nothing was asked for, so nothing was generated"
    assert not (config_dir / GPG_PRIVATE_NAME).exists(), \
        "an identity nobody gave cannot be the name on a key that got written anyway"


def test_half_a_pair_stops_the_run_instead_of_becoming_a_pair_of_two_different_keys(config_dir,
                                                                                    gpg_generator):
    """One file of a pair and not the other is a refusal.

    The only ways past it are both false: `CryptoUtils.generate_ssh_key_pair` writes *both* halves, so
    finishing the pair would overwrite the half that is there, and generating only the missing half
    would leave a deployer whose halves came from different keys — which fails later, and says so in a
    sentence about signatures rather than this one.
    """
    (config_dir / SSH_PRIVATE_NAME).write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\n")

    with pytest.raises(Exception) as refused:
        ensure_deployer_keypair(config_dir)

    message = str(refused.value)
    assert SSH_PUBLIC_NAME in message and SSH_PRIVATE_NAME in message, \
        f"the refusal names both halves of the pair it refused to finish: {message}"
    assert gpg_generator.calls == [], "and it refuses before it writes anything else"
    assert not (config_dir / GPG_PRIVATE_NAME).exists(), \
        "the gpg half is not collateral damage in a run that stopped on the ssh half"


def test_a_keypair_cannot_be_written_into_a_tree_that_does_not_exist_yet(tmp_path, gpg_generator):
    """A missing directory is refused and not mkdir'd.

    Preparing the tree is `config init`'s verb and this module's caller; a keypair that writes its own
    home is a second rule about where the configuration lives.
    """
    nowhere = tmp_path / "not_yet"

    with pytest.raises(Exception) as refused:
        ensure_deployer_keypair(nowhere, gpg_email=EMAIL)

    assert str(nowhere) in str(refused.value), f"the refusal names the directory: {refused.value}"
    assert not nowhere.exists(), "and nothing was made in order to refuse"


# --------------------------------------------------------------------------------------------
# The guard, which is what `setup_pipelines` never had.
# --------------------------------------------------------------------------------------------

def test_a_directory_with_no_keypair_refuses_the_run_by_path_by_environment_and_by_command(config_dir):
    """The guard census L found missing, in the words it will be read in.

    `setup_pipelines` hands both deployer keys to a Secret without asking (census L, Q2 item 8), so a
    tree prepared without one dies inside the Kubernetes client. `DeployerKeyMissingError` is what that
    call becomes, and a sentence that names none of path / environment / command would leave the
    operator to find the fix themselves.
    """
    with pytest.raises(DeployerKeyMissingError) as refused:
        require_deployer_keypair(config_dir)

    message = str(refused.value)
    assert str(config_dir) in message, f"the path that was looked in: {message}"
    assert "config init" in message, f"the command that creates the pair, and not the one being deleted: {message}"
    assert "initialize" not in message, \
        f"pointing at a command that is on its way out is the mistake this exception exists to fix: {message}"
    for name in KEYPAIR_FILE_NAMES:
        assert name in message, f"the four names are the answer to 'what is missing': {message}"


def test_a_directory_holding_none_of_the_four_says_so_once_and_not_twice(config_dir):
    """A live run of the guard, not a unit test's reading of it.

    With an empty directory the missing set *is* the whole set, so a message built by naming the missing
    files and then naming them again as "missing out of" recites every filename twice. That is a sentence
    that looks informative and reads as a bug report about the tool itself — the operator is handed a
    list of four names, sees it twice, and reasonably concludes the tool is confused about which files it
    was looking for. So each name may appear once.
    """
    with pytest.raises(DeployerKeyMissingError) as refused:
        require_deployer_keypair(config_dir)

    message = str(refused.value)
    # Counted with a boundary on purpose: `deployer_ssh_key` is a prefix of `deployer_ssh_key.pub`, so a
    # plain `str.count` reports the private key's name as repeated whenever its public half is present.
    # That is the tool's bug in the test, not the message's, and it would be reported as one.
    twice = [name for name in KEYPAIR_FILE_NAMES
             if len(re.findall(re.escape(name) + r"(?![\w.])", message)) > 1]
    assert twice == [], f"the same file named twice in one sentence is not information, it is noise: {message}"
    assert "none of" in message, \
        f"an empty directory deserves 'none of the four', not a list of the four it is missing: {message}"


def test_a_keypair_made_here_is_the_pair_a_caller_is_handed_paths_and_an_identity_with(config_dir,
                                                                                        gpg_generator):
    """The record is complete, and its identity is the key's and not a second opinion about it.

    `gpg_email` is read out of `gpg_public_key.gpg` because the directory has no fifth name to keep the
    answer in — a pair that cannot say who it belongs to is not a pair a git-crypt collaborator can be
    added against. Two readers of one directory get one answer.
    """
    ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    pair = require_deployer_keypair(config_dir)

    assert isinstance(pair, DeployerKeypair)
    assert (pair.ssh_private, pair.ssh_public, pair.gpg_private, pair.gpg_public) == tuple(
        config_dir / name for name in KEYPAIR_FILE_NAMES), \
        "the paths are the four names under the directory the caller handed, and nothing else"
    assert pair.gpg_email == EMAIL, \
        f"the identity comes from the key: {pair.gpg_email!r}"
    assert pair.ssh_private.is_file() and pair.gpg_public.is_file(), \
        "the record describes files that exist, which is the whole promise of the guard"


def test_a_half_a_keypair_is_missing_in_the_one_way_the_guard_has_to_say(config_dir, gpg_generator):
    """The refusal says which of the four names is absent, so the fix is a command and not a hunt.

    A message that recites all four names is what a reader of a directory that has three of them needs
    not to have to diff themselves.
    """
    ensure_deployer_keypair(config_dir, gpg_email=EMAIL)
    (config_dir / GPG_PUBLIC_NAME).unlink()

    with pytest.raises(DeployerKeyMissingError) as refused:
        require_deployer_keypair(config_dir)

    message = str(refused.value)
    assert f"{GPG_PUBLIC_NAME} is missing" in message, \
        f"the one name that is absent is named: {message}"
    assert str(config_dir) in message, f"and so is the place it was looked for in: {message}"


def test_an_environment_layer_named_after_a_keypair_file_is_refused_because_the_files_are_reserved(config_dir):
    """The reserved-name guard `config_init` already runs is what makes these four names safe here.

    This module spells the names again rather than finding a `Config` property for them, and there is a
    reason for both halves of that: nothing in `src/` opens these files today (census L, Q2 item 6), so
    a property would be a name with no reader — and the guard that refuses an environment called
    `deployer_ssh_key` is exactly why the keypair can live at the root without ever being read as a
    layer. Writing the keypair first must not make `config init` reject a legal environment name, and
    the name check is what the first half of this test measures.

    The other half is asserted on the same directory: `init_configuration` reads a root file that is
    not its own as foreign material, so the keypair is either laid down after the tree or a tree init
    can account for is passed. That is reported and not decided here.
    """
    assert set(KEYPAIR_FILE_NAMES) <= set(config_init.RESERVED_ROOT_NAMES), \
        "the four names are already reserved at the root of a configuration directory, which is why" \
        " this module may write them there"

    ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    with pytest.raises(Exception) as taken:
        config_init.init_configuration(root=config_dir, environment="secrets")
    assert "already taken" in str(taken.value), \
        f"an environment wearing one of these names is refused for the reason the guard states:" \
        f" {taken.value}"

    # The other half of the seam, asserted here so that it is not met in front of a failing
    # stage: `init_configuration` reads a root file that is not its own as foreign material, so the
    # keypair lands after the tree and not before it. Both refusals come before init's first byte, so
    # neither of them writes beside the keypair — which is the claim in the last line.
    with pytest.raises(Exception) as foreign:
        config_init.init_configuration(root=config_dir, environment="dev")
    assert "did not write" in str(foreign.value), \
        f"a keypair that is already in the directory is foreign material to init: {foreign.value}"
    assert sorted(path.name for path in config_dir.iterdir()) == sorted(KEYPAIR_FILE_NAMES), \
        "both refusals happen before the first byte, so neither of them wrote beside the keypair"


# --------------------------------------------------------------------------------------------
# --------------------------------------------------------------------------------------------
# Generation's side effect, which is what the ticket calls the leak.
# --------------------------------------------------------------------------------------------


def test_generating_a_deployer_key_leaves_the_operators_keyring_untouched(tmp_path, monkeypatch):
    """The leak this ticket is about, asserted with a real `gpg` rather than described.

    `CryptoUtils.generate_gpg_key` used to build `gnupg.GPG()` without a home, which is `gpg` being
    left to choose: it chooses the home of whoever ran the command, so every `config init` that
    generated deposited an unencrypted private key in `~/.gnupg` under the deployer's identity. With
    two trees under one identity that is two keys for one email, which is what made step 7 fail.

    Nothing here needs the ring that is being protected to be real, so the ring is a fresh directory
    named by `GNUPGHOME` and the assertion is that it stayed empty. A 2048-bit key keeps the test
    under a second; the mode `0600` is `config_keys`' promise and is asserted where it is made.
    """
    operator_ring = tmp_path / "operator-keyring"
    operator_ring.mkdir()
    os.chmod(operator_ring, 0o700)
    monkeypatch.setenv("GNUPGHOME", str(operator_ring))

    private = tmp_path / GPG_PRIVATE_NAME
    public = tmp_path / GPG_PUBLIC_NAME
    CryptoUtils.generate_gpg_key(name_email=EMAIL, key_length=2048,
                                 private_key_filepath=str(private),
                                 public_key_filepath=str(public))

    assert private.read_text(encoding="ascii").startswith("-----BEGIN PGP PRIVATE KEY BLOCK-----"), \
        "the private key is still expected to be in the file, which is the only place it belongs"

    after = gnupg.GPG(gnupghome=str(operator_ring), verbose=False)
    assert after.list_keys() == [], (
        f"the deployer's key reached the keyring of whoever ran the command: "
        f"{[k['fingerprint'] for k in after.list_keys()]}. The private key that lands there is the "
        f"leak, and a second tree under the same identity is the same leak twice")
    assert after.list_keys(secret=True) == [], "the secret half landed there in particular"


def test_the_keyring_generation_used_is_gone_by_the_time_the_call_returns(tmp_path, monkeypatch):
    """The ring is created for the call, so no directory outlives it.

    A generated key that lives in a keyring nobody can find is a worse answer than one that lives in
    the operator's: the promise is not 'somewhere else' but 'nowhere after this function returns'.
    """
    homes: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        home = real_mkdtemp(*args, **kwargs)
        homes.append(home)
        return home

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.delenv("GNUPGHOME", raising=False)

    CryptoUtils.generate_gpg_key(name_email=EMAIL, key_length=2048,
                                 private_key_filepath=str(tmp_path / "p.key"),
                                 public_key_filepath=str(tmp_path / "p.pub"))

    assert homes, "generation is supposed to create a home for the call, not find one"
    left = [home for home in homes if Path(home).exists()]
    assert left == [], f"a keyring was still on the machine after generation returned: {left}"


# --------------------------------------------------------------------------------------------
# One line about the module's own habits.
# --------------------------------------------------------------------------------------------


def test_the_module_reads_no_answer_out_of_the_environment():
    """The half of this module that reads nothing from the environment, checked in the source rather
    than asserted about the behaviour.

    `initialize` took the deployer's identity from `BASE_DOMAIN` and the rest of its answers from the
    environment it printed a table of. A library that does that here would be correct on the operator's
    machine and wrong on every other, and no test with a monkeypatched environment could tell the two
    apart — so the claim is measured on the syntax tree, where an `os.environ`, a `getenv`, or an
    `os.getenv` cannot hide.
    """
    tree = ast.parse(Path(config_keys.__file__).read_text(encoding="utf-8"))
    offenders = [
        f"{node.lineno}: {ast.dump(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "os", "putenv", "getenv")
        or isinstance(node, ast.Name) and node.id in ("environ", "getenv")
        or isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "getenv"
    ]
    assert offenders == [], \
        f"the caller owns these answers, so they are arguments here and not environment reads: {offenders}"

