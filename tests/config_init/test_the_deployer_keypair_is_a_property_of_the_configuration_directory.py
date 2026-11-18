"""The deployer's keypair is a property of a configuration directory, so it is made where one is."""

import os
import re
import shutil
import tempfile
from pathlib import Path

import gnupg
import pytest

from kubementat.config import init as config_init
from kubementat.config.init import CREATED, SKIPPED
from kubementat.config.keys import (
    GPG_PRIVATE_NAME,
    GPG_PUBLIC_NAME,
    KEYPAIR_FILE_NAMES,
    ensure_deployer_keypair,
    require_deployer_keypair,
)
from kubementat.crypto_utils import CryptoUtils
from kubementat.errors import DeployerKeyMissingError

EMAIL = "deployer@base.example.test"


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
    """
    for name in ("BASE_DOMAIN", "KUBEMENTAT_PLATFORM_CONFIG_DIR", "KUBEMENTAT_HOME"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def gpg_generator(monkeypatch):
    """A stand-in for `CryptoUtils.generate_gpg_key` that is faithful where the module needs it."""
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
    """A deployer's own armored secret key, standing where an operator would have put it."""
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
    """Every byte of every file this module is supposed to care about, for a before/after compare.
    """
    return {name: (directory / name).read_bytes() for name in KEYPAIR_FILE_NAMES
            if (directory / name).is_file()}


# --------------------------------------------------------------------------------------------
# The write set.
# --------------------------------------------------------------------------------------------


def test_running_it_again_skips_every_step_and_moves_no_byte(config_dir, gpg_generator):
    """The second run is a report and not a write: same bytes, all `skipped`."""
    ensure_deployer_keypair(config_dir, gpg_email=EMAIL)
    before = read_all(config_dir)

    steps = ensure_deployer_keypair(config_dir, gpg_email=EMAIL)

    assert set(actions(steps)) == {SKIPPED}, \
        f"nothing was missing the second time, so nothing may be claimed to be created: {[str(s) for s in steps]}"
    assert read_all(config_dir) == before, \
        "skipped has to mean the bytes are the ones that were there; a regenerated key is a different deployer"


def test_nothing_here_writes_the_git_crypt_key_and_nothing_writes_beside_the_product(config_dir,
                                                                                     gpg_generator,
                                                                                     decoy_main_dir):
    """The rule in one line, and the fifth name on the reserved list stays git-crypt's."""
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
    """`gpg_key` imports: the secret half is the file that was handed over, byte for byte."""
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
    """The import refuses before writing, so a text file never becomes `gpg_private.key`."""
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
    """No email, no key: refused, with the flag that would have answered."""
    with pytest.raises(Exception) as refused:
        ensure_deployer_keypair(config_dir)

    message = str(refused.value)
    assert "--gpg-email" in message, f"the refusal says which flag supplies the answer: {message}"
    assert "config init" in message, f"and the command that carries it: {message}"
    assert gpg_generator.calls == [], "nothing was asked for, so nothing was generated"
    assert not (config_dir / GPG_PRIVATE_NAME).exists(), \
        "an identity nobody gave cannot be the name on a key that got written anyway"


def test_a_keypair_cannot_be_written_into_a_tree_that_does_not_exist_yet(tmp_path, gpg_generator):
    """A missing directory is refused and not mkdir'd."""
    nowhere = tmp_path / "not_yet"

    with pytest.raises(Exception) as refused:
        ensure_deployer_keypair(nowhere, gpg_email=EMAIL)

    assert str(nowhere) in str(refused.value), f"the refusal names the directory: {refused.value}"
    assert not nowhere.exists(), "and nothing was made in order to refuse"


# --------------------------------------------------------------------------------------------
# The guard, which is what `setup_pipelines` never had.
# --------------------------------------------------------------------------------------------

def test_a_directory_with_no_keypair_refuses_the_run_by_path_by_environment_and_by_command(config_dir):
    """The guard census L found missing, in the words it will be read in."""
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
    """A live run of the guard, not a unit test's reading of it."""
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


def test_a_half_a_keypair_is_missing_in_the_one_way_the_guard_has_to_say(config_dir, gpg_generator):
    """The refusal says which of the four names is absent, so the fix is a command and not a hunt.
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
# Generating a key must not leave it in the operator's own keyring.
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# One line about the module's own habits.
# --------------------------------------------------------------------------------------------
