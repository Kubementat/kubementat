"""The fixtures a *prepared* configuration tree is measured with, shared by the library and the verb.

Two files ask the same question about the same machinery: what does `kubementat` do, in what order,
to make a configuration directory ready to install from? One asks it of
`config_bootstrap.prepare_configuration_directory` directly; the other asks it of
`kmt config init`, which is that call plus a print and a closing instruction. The answer needs the
same four things, and the reason they live here rather than being typed twice is that a fixture kept
in two files is a fixture that can disagree: the library version of the fake `git-crypt` would keep
writing the keystore file while the verb's version had drifted to writing only one of the three
effects, and the idempotence test would then be measuring the fake.

So there is one way in, and it is `tests/asset_paths.py`'s way — a helper module that hands the
thing over rather than each file building its own. What it hands over:

* `tools` — the recorded binaries. `git` is real and `git-crypt` is not, and each is a shim that
  writes its argv to a log before running (or, for git-crypt, performs the three filesystem effects
  the idempotence checks read). Log-reading goes through `argv` / `since_last_run` / `crypt_calls*`.
* `root` — the directory being prepared, deliberately *not created*, because a verb that is claimed
  to prepare a directory is also claiming that it makes the directory.
* the `CryptoUtils` generators and the one keyring question step 7 asks, both patched here so no
  test in the suite puts a key into the keyring of whoever is running it.

Two things are deliberately not shared. The verb's own `run_init` / `reported` / `snapshot` helpers
are about Click's two streams and not about the machinery, and the library file's ordering spy patches
`init_configuration` for a claim the verb does not make.

The fingerprint in `FINGERPRINT` is a fixture fact and not a real key. What `config_bootstrap` reads
that fingerprint from is a question about its own key-file read, patched here for every test that uses
this module; the generation and the import that feed it are `config_keys`' own claims against a real
gpg. What is measured is the argv kubementat passes and what lands where.
"""

import os
import shutil
import subprocess
from pathlib import Path

import gnupg
import pytest

from kubementat import config_bootstrap
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
    """The two recorded binaries, with their own absolute paths baked into the scripts.

    `git` is real, because a repository that is not a repository would make every claim about the
    first commit vacuous. `git-crypt` is not real, because nothing asserted here is git-crypt's
    behaviour and the lane is the place that runs it for real.

    `CryptoUtils`' two generators are replaced so a per-test run does not put 4096 bits of RSA into
    whoever's keyring owns this checkout — a side effect `config_keys`' own docstring already owns
    and tests.
    """
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
    """The directory being prepared. Absent, the way `config init` names it.

    Neither fixture creates it, which is half of the point: preparing a directory is claimed to
    include making the directory, and a fixture that made it first would keep that claim from being
    tested. Nothing points at this checkout either — a configuration root has no
    default, and a test that inherited one would be measuring the machine it runs on.
    """
    return tmp_path / "configuration"


@pytest.fixture(scope="module")
def deployer_secret_key(tmp_path_factory):
    """One real armored secret key, made once per module, standing where an operator would have it.

    It has to be real armor, not the header-sniffing bytes the other fixtures write: what `--gpg-key`
    asks of gpg is an import, and a file that does not import is refused. Making one is a 2048-bit
    RSA keygen in a keyring that survives only for the length of the call, so the fixture is scoped
    to the module — the key is only ever read here, never signed with, and nothing in the suite needs
    two of them.

    This is the shape `test_the_deployer_keypair_is_a_property_of_the_configuration_directory.py`
    builds for the same file, and for the same reason; it is here because both a `config_keys`-level
    test and a `kmt config init`-level test need to hand one over.
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
    """The calls made since this was last called, and an empty log behind them.

    Two runs write one log and the idempotence test needs the calls belonging to the second one.
    Truncating is the whole separation and the only kind this suite needs, because nothing here is
    concurrent.
    """
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
    """Every byte outside `.git`, for the before/after compare idempotence needs.

    The repository's own directory is out and not because it does not count: a second run that makes
    no commit still lets `git diff --quiet` refresh the index, so comparing `.git` would report a
    write where the claim is about the tree.
    """
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
    """The same reduction over an explicit list of calls, which is what the second-run claims need."""
    return [call for call in calls if call and call[0] == "git-crypt" and verb in call]
