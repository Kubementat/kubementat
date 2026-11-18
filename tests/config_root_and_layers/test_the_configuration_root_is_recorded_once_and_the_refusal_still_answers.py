"""The configuration root is named once and remembered, and the refusal still answers when it can't be.
"""

import json
from pathlib import Path

import pytest
from bootstrap_tools import EMAIL
from click.testing import CliRunner
from cluster_fixture import PROCESS_VARIABLES, write_environment, write_kubeconfig

from cli.commands import config as config_module
from cli.commands import environments
from kubementat.core.config.paths import (
    CONFIGURED_ROOTS_ENV_VAR,
    configured_roots_record_path,
    resolve_platform_config_dir,
)
from kubementat.core.identity.context import Context
from kubementat.errors import (
    ConfigurationRootNotNamedError,
    RootDirectoryVariableIsEmptyError,
)

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"
ENVIRONMENT = "dev"

# The sentence the rule has always refused with, spelled in bytes and not paraphrased. It is copied
# from the parent of this change rather than from the current file so that a "the message got nicer"
# regression shows up as a failure here: the record layer is allowed to *add* a sentence about what it
# found, and is not allowed to edit the one it was refusing with.
NO_ROOT_SENTENCE = (
    "no configuration root is named. Give this command --config-dir PATH, or set "
    f"{PLATFORM_CONFIG_VARIABLE} to the directory that holds the environments' static.json. "
    "kubementat does not read configuration out of the directory it was installed from, and it "
    "has no default root to fall back to."
)


def record_at(monkeypatch, path: Path) -> Path:
    """Where the record is for this test, and nothing else."""
    monkeypatch.setenv(CONFIGURED_ROOTS_ENV_VAR, str(path))
    return path


def write_record(path: Path, *roots) -> Path:
    """The record file, holding `roots` in that order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([str(Path(root).resolve()) for root in roots]), encoding="utf-8")
    return path


def a_tree(tmp_path: Path, name: str, *, environment: str = ENVIRONMENT, server: str) -> Path:
    """One prepared-shaped configuration tree, returned as the root every command resolves to."""
    checkout_root = tmp_path / name
    checkout_root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(checkout_root / "kubeconfig", {f"context-of-{environment}": server})
    write_environment(checkout_root, environment, context=f"context-of-{environment}",
                      kubeconfig_path=kubeconfig)
    return checkout_root / "platform_config"


def named_nothing(monkeypatch, root: Path) -> None:
    """The state this rule decides: this tree is the checkout, and neither dial names a root."""
    monkeypatch.setenv("KUBEMENTAT_HOME", str(root.parent))
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    for name in PROCESS_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def listing(root: Path):
    """`kmt environments list` with no flag on its command line, through the group the surface builds.
    """
    return CliRunner(mix_stderr=False).invoke(environments.environments_list, [])


def refusal() -> str:
    """Resolve the way every command falls back, and keep the sentence and not the answer."""
    with pytest.raises(ConfigurationRootNotNamedError) as raised:
        resolve_platform_config_dir(None)
    return str(raised.value)


def test_nothing_recorded_and_nothing_named_refuses_with_the_old_sentence(tmp_path, monkeypatch):
    """The no-tree case, byte for byte."""
    record_at(monkeypatch, tmp_path / "record.json")
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    assert refusal() == NO_ROOT_SENTENCE


def test_a_recorded_tree_that_is_gone_says_which_path_is_missing(tmp_path, monkeypatch):
    """A memory of a tree is not a tree, and the refusal says so instead of pretending."""
    tree = a_tree(tmp_path, "deleted", server="https://10.0.1.9:6443")
    write_record(record_at(monkeypatch, tmp_path / "record.json"), tree)
    tree.rename(tmp_path / "moved-elsewhere")

    message = refusal()

    assert message.startswith(NO_ROOT_SENTENCE)
    assert str(tree.resolve()) in message, message
    assert "--config-dir" in message


def test_two_plausible_trees_are_refused_and_not_ordered(tmp_path, monkeypatch):
    """The case the new layer exists to get wrong, and the reason the refusal is a feature."""
    first = a_tree(tmp_path, "first", server="https://10.0.1.9:6443")
    second = a_tree(tmp_path, "second", environment="prod", server="https://10.0.5.1:6443")
    write_record(record_at(monkeypatch, tmp_path / "record.json"), first, second)
    named_nothing(monkeypatch, first)

    message = refusal()

    assert message.startswith(NO_ROOT_SENTENCE)
    assert str(first.resolve()) in message and str(second.resolve()) in message, message
    assert "--config-dir" in message
    with pytest.raises(ConfigurationRootNotNamedError):
        Context.build(ENVIRONMENT)


def test_the_record_sits_below_both_spellings_that_already_outrank_it(tmp_path, monkeypatch):
    """A default must not become a decision."""
    decoy = a_tree(tmp_path, "decoy", server="https://10.0.9.9:6443")
    named = a_tree(tmp_path, "named", server="https://10.0.1.9:6443")
    write_record(record_at(monkeypatch, tmp_path / "record.json"), decoy)
    named_nothing(monkeypatch, named)

    monkeypatch.setenv(PLATFORM_CONFIG_VARIABLE, str(named.resolve()))
    assert resolve_platform_config_dir(None) == named.resolve()

    assert resolve_platform_config_dir(named.resolve()) == named.resolve()
    assert Context.build(ENVIRONMENT, platform_config_dir=named.resolve()) is not None


def test_a_record_this_process_cannot_read_answers_like_a_machine_with_none(tmp_path, monkeypatch):
    """A broken memory must not crash, and must not read a tree it was never told about."""
    for payload in ("not json at all", '{"roots": []}', "[7, 8]", '["", "   "]'):
        path = record_at(monkeypatch, tmp_path / f"record-{abs(hash(payload))}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
        assert refusal() == NO_ROOT_SENTENCE, payload

    directory = tmp_path / "record-is-a-directory"
    directory.mkdir()
    record_at(monkeypatch, directory)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    assert refusal() == NO_ROOT_SENTENCE


def test_an_empty_override_refuses_and_does_not_fall_back_to_somebody_elses_memory(tmp_path,
                                                                                   monkeypatch):
    """A variable set to nothing is not a variable left unset, and the record's own dial obeys that.
    """
    monkeypatch.setenv(CONFIGURED_ROOTS_ENV_VAR, "   ")
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    with pytest.raises(RootDirectoryVariableIsEmptyError) as raised:
        resolve_platform_config_dir(None)

    assert CONFIGURED_ROOTS_ENV_VAR in str(raised.value)


def test_config_init_records_the_tree_it_prepared_and_the_next_command_needs_no_flag(
        tmp_path, monkeypatch, tools):
    """The item's claim, run through the two verbs that make it true."""
    from cli.kmt import KmtGroup

    record = record_at(monkeypatch, tmp_path / "record.json")
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    root = tmp_path / "prepared-tree"

    def init(*extra):
        group = KmtGroup(name="kmt")
        group.add_command(config_module.config, "config")
        return CliRunner(mix_stderr=False).invoke(
            group, ["config", "init", "--environment", ENVIRONMENT, "--team", "dev1",
                    "--gpg-email", EMAIL, *extra])

    first = init("--config-dir", str(root))
    assert first.exit_code == 0, f"{first.output}{first.stderr}"
    assert record.is_file(), "the verb that prepares a tree is the only thing allowed to record it"
    assert json.loads(record.read_text(encoding="utf-8")) == [str(root.resolve())]
    assert "no --config-dir" in first.stderr, \
        f"the run that remembered the tree says so and drops the flag from what it tells you to type: " \
        f"{first.stderr}"
    assert f"kmt install {ENVIRONMENT} dev1" in first.stderr and \
        f"--config-dir {root}" not in first.stderr, first.stderr

    # The next command, with neither dial spelled anywhere.
    named_nothing(monkeypatch, root / "platform_config")
    listed = listing(root / "platform_config")

    assert listed.exit_code == 0, f"{listed.output}{listed.stderr}"
    assert str(root.resolve()) in listed.output, \
        f"the tree init prepared is not the tree the next command read: {listed.output}"

    # And the same verb again, on the same tree: idempotent, so the record holds one entry still.
    again = init("--config-dir", str(root))
    assert again.exit_code == 0, f"{again.output}{again.stderr}"
    assert json.loads(record.read_text(encoding="utf-8")) == [str(root.resolve())], \
        "a second init of the same tree added a second entry, which would make the next run ambiguous"


def test_a_run_that_cannot_write_the_record_prints_the_command_with_the_flag(tmp_path, monkeypatch,
                                                                            tools):
    """The sentence about remembering, and the run that does not get to print it."""
    from cli.kmt import KmtGroup

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file standing where a directory has to be", encoding="utf-8")
    record_path = record_at(monkeypatch, blocked / "configuration_roots.json")
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    root = tmp_path / "prepared-but-not-remembered"

    group = KmtGroup(name="kmt")
    group.add_command(config_module.config, "config")
    result = CliRunner(mix_stderr=False).invoke(
        group, ["config", "init", "--environment", ENVIRONMENT, "--team", "dev1",
                "--gpg-email", EMAIL, "--config-dir", str(root)])

    assert result.exit_code == 0, f"{result.output}{result.stderr}"
    assert not record_path.is_file(), "a record that could not be written was written anyway"
    assert f"--config-dir {root}" in result.stderr, \
        f"a run that could not remember the tree still has to print a runnable command: {result.stderr}"
    assert "is recorded as kubementat's configuration root" not in result.stderr, \
        f"nothing was recorded, so nothing may be claimed about it: {result.stderr}"

    named_nothing(monkeypatch, root / "platform_config")
    assert refusal() == NO_ROOT_SENTENCE


def test_the_record_path_is_the_users_own_and_the_override_moves_it(tmp_path, monkeypatch):
    """Where the record lives when nothing overrides it, and the one dial that says otherwise."""
    home = tmp_path / "somebody-elses-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(CONFIGURED_ROOTS_ENV_VAR, raising=False)
    assert configured_roots_record_path() == home / ".config" / "kubementat" / \
        "configuration_roots.json"

    moved = tmp_path / "somewhere-else.json"
    monkeypatch.setenv(CONFIGURED_ROOTS_ENV_VAR, str(moved))
    assert configured_roots_record_path() == moved
