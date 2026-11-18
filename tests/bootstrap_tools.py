"""The fixtures a *prepared* configuration tree is measured with, shared by the library and the verb.
"""

import os
import shutil
import subprocess
from pathlib import Path

import gnupg
import pytest

from kubementat.config import bootstrap as config_bootstrap
from kubementat.crypto_utils import CryptoUtils

__all__ = ["EMAIL", "FINGERPRINT", "GPG_PRIVATE", "GPG_PUBLIC", "actions", "argv", "commit_count",
           "crypt_calls", "crypt_calls_on", "deployer_secret_key", "root", "since_last_run", "tools",
           "tree_bytes"]

EMAIL = "deployer2912@example.invalid"

# The fingerprint the fake git-crypt registers. A fixed string rather than a generated key, because
# every claim here is about a call, a mode, or an order; the module reads the keystore for this name
# and a generated key would answer a question no test is asking.
FINGERPRINT = "1A2B3C4D5E6F7A8B"

# Armored-looking content. `_is_gpg_private_key`/`_is_gpg_public_key` sniff these headers, and the
# real key material is what `config_keys`' tests are about.
GPG_PRIVATE = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nkubementat t29b fixture\n-----END PGP PRIVATE KEY BLOCK-----\n"
GPG_PUBLIC = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nkubementat t29b fixture\n-----END PGP PUBLIC KEY BLOCK-----\n"

LOG_LINE = ("{ printf 'CALL\\t@PROGRAM@'; for a in \"$@\"; do printf '\\t%s' \"$a\"; done;"
            " printf '\\n'; } >> \"@LOG@\"")

FAKE_GIT = """#!/usr/bin/env bash
# Records argv, then runs the real git. Both paths are baked into this text when the fixture writes
# it, so the shim depends on nothing inherited from the process that spawned it.
export GIT_AUTHOR_NAME=kubementat GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=kubementat GIT_COMMITTER_EMAIL=test@example.invalid
@LOG_GIT@
exec "@GIT@" "$@"
"""

FAKE_GIT_CRYPT = """#!/usr/bin/env bash
# Records argv and performs the three effects the idempotence checks read — init writes the keystore's
# master key, add-gpg-user writes the collaborator's key file, export-key writes the symmetric key.
# None of that is git-crypt's behaviour, and none of it needs a real git-crypt.
@LOG_CRYPT@
case "$1" in
  init) mkdir -p .git/git-crypt/keys && : > .git/git-crypt/keys/default ;;
  add-gpg-user) mkdir -p .git-crypt/keys/default/0 && : > .git-crypt/keys/default/0/@FPR@.gpg ;;
  export-key) printf 'plaintext that a real git-crypt would have encrypted\\n' > "$2" ;;
esac
exit 0
"""


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """The two recorded binaries, with their own absolute paths baked into the scripts."""
    directory = tmp_path / "fake-tools"
    directory.mkdir()
    log = tmp_path / "tool.log"
    log.write_text("")
    real_git = shutil.which("git")
    assert real_git, "these tests run real `git init`, so git has to be on PATH"

    (directory / "git").write_text(
        FAKE_GIT.replace("@LOG_GIT@", LOG_LINE.replace("@PROGRAM@", "git").replace("@LOG@", str(log)))
        .replace("@GIT@", real_git), encoding="utf-8")
    (directory / "git-crypt").write_text(
        FAKE_GIT_CRYPT.replace(
            "@LOG_CRYPT@", LOG_LINE.replace("@PROGRAM@", "git-crypt").replace("@LOG@", str(log)))
        .replace("@FPR@", FINGERPRINT), encoding="utf-8")
    for script in directory.iterdir():
        script.chmod(0o755)

    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    for name in ("KUBEMENTAT_HOME", "KUBEMENTAT_PLATFORM_CONFIG_DIR", "BASE_DOMAIN", "GNUPGHOME"):
        monkeypatch.delenv(name, raising=False)

    # The committer identity a run can see is a machine-level fact, and a machine-level fact must not
    # be inherited by a fixture that asserts an argv list. `config init` reads `user.name` and
    # `user.email` before its first commit and writes kubementat's own identity only into the gap it
    # finds, so a box that has a global identity records two reads and no writes, while a VM that was
    # just created -- where the read of either half answers nothing -- records two `git config` writes
    # as well. Both are correct behaviour and both belong to the commit step, so the same expected
    # ordering is true on one machine and false on the other. This fixture therefore states the
    # identity in an isolated HOME: the reads answer, nothing is written, and the ordering claim is
    # about the steps. That both halves of the fallback work is asserted against real git by
    # test_config_init_can_commit_on_a_machine_that_names_no_committer.py, which repoints HOME the same
    # way and states the opposite case on purpose.
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Fixture Operator\n"
                                      "\temail = fixture@example.invalid\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    # A stale $XDG_CONFIG_HOME on the machine running the suite would otherwise outrank the file above
    # and leak somebody's real identity into a fixture that just declared it has none.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    # The two gpg questions step 7 asks about a key file, answered by the fixture. See the module
    # docstring: the fingerprint is the fact the keystore file is named after, and the import is what
    # makes git-crypt able to resolve it. Neither is real here, because the armor in `GPG_PUBLIC` is a
    # marker string and real armor is `config_keys`' own claim against a real gpg.
    monkeypatch.setattr(config_bootstrap, "_directory_fingerprint", lambda root: FINGERPRINT)
    monkeypatch.setattr(config_bootstrap, "_import_the_public_half",
                        lambda root, fingerprint: None)

    def generate_ssh(key_size=2048, key_file_base_name=None, target_directory=None):
        directory = Path(target_directory)
        (directory / f"{key_file_base_name}.pub").write_text(f"ssh-rsa AAAAfixture {EMAIL}\n")
        (directory / key_file_base_name).write_text("-----BEGIN RSA PRIVATE KEY-----\nfixture\n"
                                                    "-----END RSA PRIVATE KEY-----\n")
        return "fixture", "fixture"

    def generate_gpg(name_email, passphrase="", key_type="RSA", key_length=4096,
                     private_key_filepath=None, public_key_filepath=None):
        Path(private_key_filepath).write_text(GPG_PRIVATE, encoding="ascii")
        Path(public_key_filepath).write_text(GPG_PUBLIC, encoding="ascii")
        return {"public_keys": GPG_PUBLIC, "private_keys": GPG_PRIVATE}

    monkeypatch.setattr(CryptoUtils, "generate_ssh_key_pair", staticmethod(generate_ssh))
    monkeypatch.setattr(CryptoUtils, "generate_gpg_key", staticmethod(generate_gpg))
    return {"log": log, "git": real_git}


@pytest.fixture
def root(tmp_path):
    """The directory being prepared. Absent, the way `config init` names it."""
    return tmp_path / "configuration"


@pytest.fixture(scope="module")
def deployer_secret_key(tmp_path_factory):
    """One real armored secret key, made once per module, standing where an operator would have it.
    """
    home = tmp_path_factory.mktemp("deployer-keyring")
    os.chmod(home, 0o700)
    gpg = gnupg.GPG(gnupghome=str(home), verbose=False)
    key = gpg.gen_key(gpg.gen_key_input(name_real=EMAIL, name_email=EMAIL, no_protection=True,
                                        key_type="RSA", key_length=2048))
    assert key.fingerprint, f"the fixture's keygen produced no key: {key}"
    path = home.parent / "deployer-secret-key.asc"
    path.write_text(gpg.export_keys(keyids=key.fingerprint, secret=True, expect_passphrase=False),
                    encoding="ascii")
    return path


def argv(tools):
    """Every recorded call, in order, as argv lists — the ordered list the ordering test asserts."""
    return _parse(tools["log"].read_text())


def since_last_run(tools):
    """The calls made since this was last called, and an empty log behind them."""
    recorded = argv(tools)
    tools["log"].write_text("")
    return recorded


def _parse(text):
    recorded = []
    for line in text.splitlines():
        parts = line.split("\t")
        if parts and parts[0] == "CALL":
            recorded.append(parts[1:])
    return recorded


def actions(steps):
    return [step.action for step in steps]


def tree_bytes(root):
    """Every byte outside `.git`, for the before/after compare idempotence needs."""
    root = Path(root)
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def commit_count(root, tools):
    """How many commits HEAD reaches — the number that says a second run made none."""
    return subprocess.run([tools["git"], "rev-list", "--count", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()


def crypt_calls(tools, verb):
    return [call for call in argv(tools) if call and call[0] == "git-crypt" and verb in call]


def crypt_calls_on(calls, verb):
    """The same reduction over an explicit list of calls, which is what the second-run claims need.
    """
    return [call for call in calls if call and call[0] == "git-crypt" and verb in call]
