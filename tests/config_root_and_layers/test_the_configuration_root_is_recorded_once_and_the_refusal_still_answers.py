"""The configuration root is named once and remembered, and the refusal still answers when it can't be.

**What this file is about.** Before this rule existed, a root was named or it was refused: 7 of the 11
commands on the README happy path had to type `--config-dir`, not because they were ambiguous but
because nothing else in the product knew which tree this operator had configured. This file's subject is
the third thing that can name a root — the tree `kmt config init` prepared and *recorded* under the
user's home — and its second subject is the failure the new layer must not create: a command that
silently reads the wrong tree.

So the three cases the rule can meet are all asserted here, and each of them is asserted about the same
function every command falls back to (`resolve_platform_config_dir`, which is what `Config` and
`kmt environments list` and the cluster report in `@kmt_context` all call, so a change here is a change
for all 39 cluster-resolving commands and not for the 1 verb that writes):

* **One tree resolved.** Exactly one recorded tree exists, so the flag is not needed and the command
  reads that tree. This is the whole payoff, so it is asserted through the library *and* through a
  command that prints the file it read.
* **No tree.** Nothing is recorded and nothing is named, and the run refuses with the sentence it has
  always answered with. The sentence is spelled out below as bytes and not paraphrased, because
  "the same message" is only checkable if it is a string.
* **Two plausible trees.** Two recorded trees exist, and that is refused too: choosing between two
  trees by whichever entry was written first is the silent-default bug with a memory instead of a
  wheel, which is the failure mode this item exists not to add.

The record's precedence is asserted as well — it sits **below** the flag and below
`$KUBEMENTAT_PLATFORM_CONFIG_DIR`, so nothing that names a root today behaves differently tomorrow.

Three properties that are easy to get wrong and are the reason this file does not stop at the happy
path:

* **A record is a memory, not a claim about the present.** A recorded tree that is gone is *not* "no
  tree recorded": it says which path is missing, because "run config init again or name the new one"
  is the truth and "I have never heard of a configuration root" is not.
* **A record this process cannot read reads like nothing is recorded.** It must not crash, and it must
  not read some other tree; the worst a corrupt record can do is refuse like a machine that has none.
* **The layer must not be able to read somebody else's machine.** The record lives under `~`, so every
  test here points `KUBEMENTAT_CONFIGURED_ROOTS` — the override that says where the record is — at its
  own file, and `tests/conftest.py` points it at a path that does not exist for the rest of the suite,
  so "nothing recorded" is a fixture fact and not a fact about the laptop running pytest.

The last test is the item's real claim: `kmt config init` runs for real, records what it prepared, and
the *next* command, with no flag anywhere on its command line, reads that tree. A default that only
satisfies a fixture is not a default.
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
    """Where the record is for this test, and nothing else.

    The override is what makes the third layer testable: without it, a suite that runs `kmt config
    init` would write into the home directory of whoever is running pytest, and a suite that asserts a
    refusal would resolve that developer's own tree instead of refusing. Pointing it at `path` makes
    the record's contents a fact the test wrote.
    """
    monkeypatch.setenv(CONFIGURED_ROOTS_ENV_VAR, str(path))
    return path


def write_record(path: Path, *roots) -> Path:
    """The record file, holding `roots` in that order.

    Written through the same JSON the product writes, because a test that wrote a shape only it
    understood would be testing a reader that no run uses.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([str(Path(root).resolve()) for root in roots]), encoding="utf-8")
    return path


def a_tree(tmp_path: Path, name: str, *, environment: str = ENVIRONMENT, server: str) -> Path:
    """One prepared-shaped configuration tree, returned as the root every command resolves to.

    A tree is a checkout here, so the root is its `platform_config` directory: that is the shape
    `redirect_checkout` names, and it is what makes "two plausible trees" two *directories* and not two
    spellings of one.
    """
    checkout_root = tmp_path / name
    checkout_root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(checkout_root / "kubeconfig", {f"context-of-{environment}": server})
    write_environment(checkout_root, environment, context=f"context-of-{environment}",
                      kubeconfig_path=kubeconfig)
    return checkout_root / "platform_config"


def named_nothing(monkeypatch, root: Path) -> None:
    """The state this rule decides: this tree is the checkout, and neither dial names a root.

    `KUBEMENTAT_HOME` is the *output* root and must stay pointed at the tree, or a resolved
    configuration root would be read through the checkout this module happens to live in. The variable
    that names the configuration root is deleted rather than left alone: a stale one on the machine
    running the test would name a root the test never wrote.
    """
    monkeypatch.setenv("KUBEMENTAT_HOME", str(root.parent))
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    for name in PROCESS_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def listing(root: Path):
    """`kmt environments list` with no flag on its command line, through the group the surface builds.

    The listing is the cheapest command that prints which root it read — its SOURCE column is a path
    inside that root — so it answers "did the recorded default resolve the *right* tree" and not merely
    "did a tree resolve".
    """
    return CliRunner(mix_stderr=False).invoke(environments.environments_list, [])


def refusal() -> str:
    """Resolve the way every command falls back, and keep the sentence and not the answer."""
    with pytest.raises(ConfigurationRootNotNamedError) as raised:
        resolve_platform_config_dir(None)
    return str(raised.value)


def test_nothing_recorded_and_nothing_named_refuses_with_the_old_sentence(tmp_path, monkeypatch):
    """The no-tree case, byte for byte.

    `--config-dir` still appears in it, still as the thing to do, and no part of it is reworded into
    something friendlier. A run on a machine that has recorded nothing answers exactly what it answered
    before this layer existed.
    """
    record_at(monkeypatch, tmp_path / "record.json")
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    assert refusal() == NO_ROOT_SENTENCE


def test_a_recorded_tree_that_is_gone_says_which_path_is_missing(tmp_path, monkeypatch):
    """A memory of a tree is not a tree, and the refusal says so instead of pretending.

    The sentence still starts with the one the rule has always used, because that is still what the
    operator has to do; what it adds is the path, so the advice is about this machine and not about
    kubementat in general.
    """
    tree = a_tree(tmp_path, "deleted", server="https://10.0.1.9:6443")
    write_record(record_at(monkeypatch, tmp_path / "record.json"), tree)
    tree.rename(tmp_path / "moved-elsewhere")

    message = refusal()

    assert message.startswith(NO_ROOT_SENTENCE)
    assert str(tree.resolve()) in message, message
    assert "--config-dir" in message


def test_two_plausible_trees_are_refused_and_not_ordered(tmp_path, monkeypatch):
    """The case the new layer exists to get wrong, and the reason the refusal is a feature.

    Two live trees recorded, so a machine that has configured two products must not pick one. The rule
    it would break is `Context.build` reading a tree nobody pointed at, so the assertion is not that a
    refusal happens — it is that *neither* recorded tree answers, that both are named, and that the
    advice is still the flag.
    """
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
    """A default must not become a decision.

    The recorded tree is a decoy here on purpose. The variable answers, and then the flag answers, and
    the decoy never gets a vote — because the runs that name a root today (every CI lane, every
    container, both shipped Tasks) must behave exactly as they do before this item, and a recorded
    default that could outrank them would be the D11 bug coming back with better documentation.
    """
    decoy = a_tree(tmp_path, "decoy", server="https://10.0.9.9:6443")
    named = a_tree(tmp_path, "named", server="https://10.0.1.9:6443")
    write_record(record_at(monkeypatch, tmp_path / "record.json"), decoy)
    named_nothing(monkeypatch, named)

    monkeypatch.setenv(PLATFORM_CONFIG_VARIABLE, str(named.resolve()))
    assert resolve_platform_config_dir(None) == named.resolve()

    assert resolve_platform_config_dir(named.resolve()) == named.resolve()
    assert Context.build(ENVIRONMENT, platform_config_dir=named.resolve()) is not None


def test_a_record_this_process_cannot_read_answers_like_a_machine_with_none(tmp_path, monkeypatch):
    """A broken memory must not crash, and must not read a tree it was never told about.

    Three shapes of broken: a file that is not JSON, a file that is JSON but not a list of paths, and a
    path that is a directory instead of a file. All three answer as nothing is recorded, which is the
    ordinary refusal and not a traceback, because the alternative is a rule that fails differently on
    different machines.
    """
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

    The rule is `KUBEMENTAT_HOME`'s and `KUBEMENTAT_PLATFORM_CONFIG_DIR`'s, and the record gets no
    exemption: falling back to `~/.config/kubementat/configuration_roots.json` here would read a
    stranger's memory of a tree out of a home directory the operator never pointed at.
    """
    monkeypatch.setenv(CONFIGURED_ROOTS_ENV_VAR, "   ")
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    with pytest.raises(RootDirectoryVariableIsEmptyError) as raised:
        resolve_platform_config_dir(None)

    assert CONFIGURED_ROOTS_ENV_VAR in str(raised.value)


def test_config_init_records_the_tree_it_prepared_and_the_next_command_needs_no_flag(
        tmp_path, monkeypatch, tools):
    """The item's claim, run through the two verbs that make it true.

    `kmt config init` is the only verb that lays a tree down, so it is the only verb allowed to say
    afterwards which tree it made — and the proof is not the record file's bytes, it is the *next*
    command: `kmt environments list`, typed with no `--config-dir` and no `$KUBEMENTAT_PLATFORM_CONFIG_DIR`
    in the environment, printing the tree this run prepared. That is what "the flag is typed once" means
    for a newcomer, and nothing in this file is worth much if this test is green by reading a fixture.

    The instruction the verb prints moves with it: the run that remembered the tree drops the flag from
    the `kmt install` line it tells the operator to type, and the sentence that explains why is printed
    because the remembering happened.
    """
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
    """The sentence about remembering, and the run that does not get to print it.

    A home the product cannot write — here, an override whose parent is a file and so is not a
    directory — is not a failure and not a success either. The tree is still prepared, so the verb still
    succeeds, but it must not claim a remembering that did not happen: the instruction it prints keeps
    `--config-dir`, because that is the only way that command still runs. And because the record stayed
    empty, the next command with no flag refuses with the ordinary sentence rather than reading the tree
    this run prepared and calling that a default.
    """
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
    """Where the record lives when nothing overrides it, and the one dial that says otherwise.

    `~/.config/kubementat/configuration_roots.json` is the product's own claim about its location, so it
    is asserted here rather than left to a docstring — with `HOME` pointed at a fixture for the same
    reason the whole file points at the override: a test must not be able to read or write the home of
    whoever is running it.
    """
    home = tmp_path / "somebody-elses-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(CONFIGURED_ROOTS_ENV_VAR, raising=False)
    assert configured_roots_record_path() == home / ".config" / "kubementat" / \
        "configuration_roots.json"

    moved = tmp_path / "somewhere-else.json"
    monkeypatch.setenv(CONFIGURED_ROOTS_ENV_VAR, str(moved))
    assert configured_roots_record_path() == moved
