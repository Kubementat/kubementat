"""`kmt config init` — the verb that *prepares* a directory, and the account it gives of itself."""

import stat
import subprocess
from pathlib import Path

import pytest
from bootstrap_tools import (
    EMAIL,
    FINGERPRINT,
    argv,
    commit_count,
    crypt_calls_on,
    since_last_run,
    tree_bytes,
)
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import bootstrap as config_bootstrap
from kubementat.config import keys as config_keys
from kubementat.crypto_utils import CryptoUtils

ENVIRONMENT = "dev"
MANIFEST = Path("kubementat.yaml")
ENV_STATIC = Path(ENVIRONMENT) / "static.json"
ENV_ENCRYPTED = Path(ENVIRONMENT) / "static.encrypted.json"

# The two layer directories a run without `--team` creates, in the order the steps land. Printed and
# asserted as paths here because the print is a path list; which `Config` property each one *is* is
# asserted against the property in test_config_init_creates_the_directories_a_reader_looks_in.py.
ENV_TEKTON_DIRS = [Path(ENVIRONMENT) / "tekton" / "tasks",
                   Path(ENVIRONMENT) / "tekton" / "pipelines"]


@pytest.fixture(autouse=True)
def no_configuration_root_in_the_environment(monkeypatch):
    """Neither dial is set for any test here, so a tree that lands on a decoy did it on its own."""
    monkeypatch.delenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", raising=False)
    monkeypatch.delenv("KUBEMENTAT_HOME", raising=False)


def run_init(root, *arguments):
    """`kmt config init --config-dir <root> …`, through the group so the verb's name is exercised.
    """
    return in_root_group(["init", *arguments, "--config-dir", str(root)])


def in_root_group(argv):
    """`kmt config …`, dispatched the way `main()` dispatches it: through the root group class."""
    from cli.kmt import KmtGroup

    root_group = KmtGroup(name="kmt")
    root_group.add_command(config_module.config, "config")
    result = CliRunner(mix_stderr=False).invoke(root_group, ["config", *argv])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def reported(result) -> str:
    """Both streams, for the assertions that ask what the operator would have read."""
    return f"{result.stdout}{result.stderr}"


def snapshot(directory: Path) -> dict:
    """Every file under `directory`, keyed by path and held as bytes."""
    return {path.relative_to(directory): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def lines(text: str) -> list:
    return [line for line in text.splitlines() if line.strip()]


def prepared_paths(root, *, again=False) -> list:
    """Every path a prepared run reports, in the order the steps were applied."""
    return [
        root / ".git",
        root / ".gitattributes",
        root / ".gitignore",
        root / MANIFEST,
        root / ENV_STATIC,
        root / ENV_ENCRYPTED,
        *[root / directory for directory in ENV_TEKTON_DIRS],
        root / config_keys.SSH_PRIVATE_NAME,
        root / config_keys.SSH_PUBLIC_NAME,
        root / config_keys.GPG_PRIVATE_NAME,
        root / config_keys.GPG_PUBLIC_NAME,
        root / ".git" / "git-crypt" / "keys" / "default",
        (root / ".git-crypt" / "keys" / "default" / "0" / f"{FINGERPRINT}.gpg" if again
         else root / ".git-crypt" / "keys" / "default" / "0"),
        root / config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_NAME,
        root,
        root,
    ]


def identity_arguments() -> list:
    """`--gpg-email <email>`, because every run in this file that expects success needs an identity.
    """
    return ["--gpg-email", EMAIL]


def staged(root, tools) -> list:
    """What `git add` has taken, which is the only way `--no-commit` can be checked."""
    return subprocess.run([tools["git"], "diff", "--cached", "--name-only"], cwd=root,
                          capture_output=True, text=True).stdout.split()


def tracked(root, tools) -> list:
    """What the first commit actually holds — the list that says what got into the object store."""
    return subprocess.run([tools["git"], "ls-tree", "-r", "--name-only", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.split()


def test_a_second_run_writes_no_byte_makes_no_commit_and_names_the_steps_it_skipped(root, tools):
    """Test 2: idempotence as a claim about bytes, commits and calls."""
    run_init(root, "--environment", ENVIRONMENT, *identity_arguments())
    before, first = tree_bytes(root), commit_count(root, tools)
    since_last_run(tools)

    result = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert result.exit_code == 0, reported(result)
    assert tree_bytes(root) == before, "skipped has to mean the bytes are the ones that were there"
    assert commit_count(root, tools) == first, "the second run committed"
    printed = lines(result.stdout)
    assert printed == [f"skipped {path}" for path in prepared_paths(root, again=True)], reported(result)
    assert "created" not in result.stdout

    second = argv(tools)
    assert crypt_calls_on(second, "export-key") == [], \
        "the symmetric key was exported again over a file that was already right, which is a write"
    assert crypt_calls_on(second, "add-gpg-user") == [], \
        "the deployer was registered a second time"
    assert crypt_calls_on(second, "init") == [], "the keystore was initialized a second time"


def test_a_set_argument_with_no_equals_sign_is_refused_naming_the_flag(root, tools):
    """Test 5: `--set GRAFANA_ENABLED` is a typo and not a half-answer."""
    result = run_init(root, "--environment", ENVIRONMENT, "--set", "GRAFANA_ENABLED")

    assert result.exit_code != 0
    assert "--set" in reported(result)
    assert not root.exists() or tree_bytes(root) == {}


def test_the_same_key_given_twice_is_refused_and_not_last_write_wins(root, tools):
    """Test 6: a JSON object cannot carry a key twice, so a repeated answer is a question."""
    result = run_init(root, "--environment", ENVIRONMENT,
                      "--set", "BASE_DOMAIN=a.test", "--set", "BASE_DOMAIN=b.test",
                      *identity_arguments())

    assert result.exit_code != 0
    assert "BASE_DOMAIN" in reported(result)
    assert not (root / MANIFEST).exists(), "the tree was written by a run that refused"
    assert not (root / ENV_STATIC).exists(), "the answer was picked anyway"


def test_foreign_material_is_named_and_a_force_leaves_it_alone(root, tools):
    """Test 7: `--force` means "this tree holds something of someone else's", never "overwrite"."""
    root.mkdir(parents=True)
    foreign = root / "NOTES.md"
    foreign.write_text("hand-written by a human\n", encoding="utf-8")
    untouched = foreign.read_bytes()

    refused = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())
    assert refused.exit_code != 0
    assert "NOTES.md" in reported(refused)
    assert foreign.read_bytes() == untouched

    accepted = run_init(root, "--environment", ENVIRONMENT, "--force", *identity_arguments())

    assert accepted.exit_code == 0, reported(accepted)
    assert foreign.read_bytes() == untouched
    kept = [line for line in lines(accepted.stdout) if "NOTES.md" in line]
    assert kept == [f"kept {foreign} (not kubementat's)"], (
        "force accepted a file and said so in the only wording that says nobody touched it")
    assert (root / ENV_STATIC).is_file()


def test_the_flag_outranks_the_platform_config_variable_and_the_decoy_stays_empty(tmp_path,
                                                                                 root,
                                                                                 tools,
                                                                                 monkeypatch):
    """Test 8: the flag is the layer above the variable, so a decoy the variable names stays empty.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(decoy))

    result = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert result.exit_code == 0, reported(result)
    assert snapshot(decoy) == {}
    assert (root / MANIFEST).is_file()
    assert (root / ENV_STATIC).is_file()


# ---------------------------------------------------------------------------------------------
# What the verb prepares, as claims of their own.
# ---------------------------------------------------------------------------------------------


def test_the_exported_symmetric_key_is_the_run_that_can_read_every_credential(root, tools):
    """The exported file's mode is the claim, because git-crypt writes it at the run's umask."""
    result = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert result.exit_code == 0, reported(result)
    exported = root / config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_NAME
    assert exported.is_file(), reported(result)
    mode = stat.S_IMODE(exported.stat().st_mode)
    assert mode == 0o600, f"{exported.name} grants {oct(mode)}; that file unlocks the whole tree"
    assert exported.name not in tracked(root, tools), \
        "the symmetric key reached the commit, which is how a gitignored file stops being ignored"
    assert exported.name in (root / ".gitignore").read_text(encoding="utf-8")


def test_the_symmetric_key_instruction_is_printed_once_and_only_when_this_run_exported_it(root,
                                                                                          tools):
    """The sentence about the export, and the half that says *once*."""
    first = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())
    assert first.exit_code == 0, reported(first)

    sentence = "ability to read every credential"
    assert first.stderr.count(sentence) == 1, (
        f"the export happened, so the sentence about it belongs exactly once on stderr: "
        f"{first.stderr}")
    assert sentence not in first.stdout, "the file list is stdout and the file list only"

    since_last_run(tools)
    again = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert again.exit_code == 0, reported(again)
    assert sentence not in again.stderr, (
        "the second run exported nothing and still repeated the advice about exporting")
    assert crypt_calls_on(argv(tools), "export-key") == [], (
        "the sentence was right and the export ran again anyway, which is the other shape of the bug")


def test_the_closing_instruction_names_the_remote_it_was_handed_and_not_one_it_made_up(root, tools):
    """`--git-url` is a flag about the summary and not about the repository."""
    named = run_init(root, "--environment", ENVIRONMENT, "--git-url", "git.example.invalid:k/t.git",
                     *identity_arguments())
    assert named.exit_code == 0, reported(named)
    assert "git.example.invalid:k/t.git" in named.stderr

    local = run_init(root / "second", "--environment", ENVIRONMENT, *identity_arguments())
    assert local.exit_code == 0, reported(local)
    assert "no remote" in local.stderr.lower(), (
        f"a run with no URL still has to say the commit is local: {local.stderr}")


def test_gpg_key_is_imported_and_nothing_is_generated(root, tools, monkeypatch, deployer_secret_key):
    """The done-when for the import, as the verb: the file the operator handed over *is* the private
    key.
    """
    generated: list = []

    def record_generation(*args, **kwargs):
        generated.append(kwargs)
        return ("the generator was asked for despite a key that was handed over, which is the failure "
                "this assertion exists to notice")

    monkeypatch.setattr(CryptoUtils, "generate_gpg_key", staticmethod(record_generation))

    result = run_init(root, "--environment", ENVIRONMENT, "--gpg-key", str(deployer_secret_key))

    assert result.exit_code == 0, reported(result)
    assert (root / config_keys.GPG_PRIVATE_NAME).read_bytes() == deployer_secret_key.read_bytes(), \
        "the operator's key was not imported byte for byte"
    assert (root / config_keys.GPG_PUBLIC_NAME).is_file(), \
        "the public half the import exports is missing"
    assert generated == [], "a gpg keypair was generated beside the key that was handed over"
