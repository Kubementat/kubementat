"""Every `kmt config` verb names the root it works on, and none of them locates one."""

import json
from pathlib import Path

import pytest
from bootstrap_tools import EMAIL
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import init as config_init

ENVIRONMENT = "rootverbenv"
TEAM = "rootverbteam"
ROOT_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"

# The seven verbs, in the order the group lists them. The table is a set and not a list because the
# point of reading it here is that no eighth verb arrives without being added to this file too.
VERBS = {"init", "add-environment", "add-team", "set", "unset", "show", "validate"}


def prepared_tree(workspace: Path) -> Path:
    """A tree with both layers and one answer, for the verbs that read or grow one."""
    return tree(workspace)


def untouched_root(workspace: Path) -> Path:
    """A root that does not exist yet, which is what `init` is pointed at and nothing else is."""
    return workspace / "platform_config"


# What each verb is pointed at, the arguments that name its layer or its answer, and how a run proves
# it read the root it was pointed at rather than refused for an unrelated reason. The check gets the
# root and the run's own output, because `show` answers a root with a line and not with a file.
READS_THE_ROOT = {
    "init": (untouched_root,
             ["--environment", ENVIRONMENT, "--team", TEAM, "--gpg-email", EMAIL],
             lambda root, result: (root / "kubementat.yaml").is_file()),
    "add-environment": (prepared_tree,
                        ["second-env"],
                        lambda root, result: (root / "second-env" / "static.json").is_file()),
    "add-team": (prepared_tree,
                 [ENVIRONMENT, "second-team"],
                 lambda root, result: (root / ENVIRONMENT / "second-team" / "static.json").is_file()),
    "set": (prepared_tree,
            [ENVIRONMENT, "ROOTVERB=flagged"],
            lambda root, result: answered(root) == "flagged"),
    "unset": (prepared_tree, [ENVIRONMENT, "ROOTVERB"], lambda root, result: answered(root) is None),
    "show": (prepared_tree, [ENVIRONMENT], lambda root, result: "ROOTVERB = 1" in result.stdout),
    "validate": (prepared_tree, [ENVIRONMENT], lambda root, result: "no errors" in result.stdout),
}


@pytest.fixture
def clean_environment(monkeypatch):
    """Neither spelling of a root, and no `KUBEMENTAT_HOME` either, so a located root is impossible.
    """
    monkeypatch.delenv(ROOT_VARIABLE, raising=False)
    monkeypatch.delenv("KUBEMENTAT_HOME", raising=False)


def tree(tmp_path) -> Path:
    """A prepared tree with both layers answered, built by the verbs the surface has."""
    root = tmp_path / "platform_config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    config_init.add_team(root=root, environment=ENVIRONMENT, team=TEAM)
    config_init.set_answer(root=root, environment=ENVIRONMENT, key="ROOTVERB", value=1)
    return root


def run(*argv):
    """`kmt config …`, through the group so the verb's own name is exercised."""
    from cli.kmt import KmtGroup

    root_group = KmtGroup(name="kmt")
    root_group.add_command(config_module.config, "config")
    result = CliRunner(mix_stderr=False).invoke(root_group, ["config", *argv])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def said(result) -> str:
    return f"{result.stdout}{result.stderr}"


def answered(root: Path) -> object:
    """What the environment layer answers for this file's key, read back out of the tree."""
    path = root / ENVIRONMENT / "static.json"
    return json.loads(path.read_text(encoding="utf-8")).get("ROOTVERB")


def test_no_configuration_verb_takes_a_root_as_a_positional():
    """The census: seven verbs, each carrying `config_dir` and none carrying `directory`."""
    assert set(config_module.config.commands) == VERBS, \
        "the config group changed shape; this file's tables need the same change"

    for name, command in sorted(config_module.config.commands.items()):
        params = {param.name for param in command.params}
        assert "config_dir" in params, \
            f"kmt config {name} no longer takes --config-dir, so a run that configured the variable " \
            "still has to spell the path on the command line"
        assert "directory" not in params, \
            f"kmt config {name} took its root back as a positional, which is the second way to name " \
            "one and the reason a container's configuration went unread"


def test_the_variable_names_the_root_for_every_verb_and_the_flag_outranks_it(tmp_path,
                                                                             clean_environment,
                                                                             tools,
                                                                             monkeypatch):
    """The branch that used to be missing: nothing on the command line, and the variable still read.
    """
    for name, (seed, arguments, proved) in sorted(READS_THE_ROOT.items()):
        workspace = tmp_path / name
        workspace.mkdir()
        root, decoy = seed(workspace), workspace / "decoy"
        decoy.mkdir()

        monkeypatch.setenv(ROOT_VARIABLE, str(root))
        result = run(*[name, *arguments])
        assert result.exit_code == 0, f"kmt config {name} refused a root named by the variable: " \
                                      f"{said(result)}"
        assert proved(root, result), \
            f"kmt config {name} ran against {root}, said it ran, and did nothing there: " \
            f"{said(result)}"
        monkeypatch.delenv(ROOT_VARIABLE)

    # ...and the same run with both spellings present, on the verb whose answer is a key and not a
    # directory: the flag's root takes the answer and the decoy keeps nothing.
    named = tree(tmp_path / "flagged")
    decoy = tmp_path / "flagged" / "decoy"
    decoy.mkdir()
    monkeypatch.setenv(ROOT_VARIABLE, str(decoy))

    result = run("set", ENVIRONMENT, "ROOTVERB=flagged", "--config-dir", str(named))
    assert result.exit_code == 0, said(result)
    assert answered(named) == "flagged", "the flag named the root and the variable answered it"
    assert not (decoy / ENVIRONMENT).exists(), \
        "the decoy the variable named took an answer that was handed to the flag"


def test_the_variable_set_to_nothing_is_refused_and_not_located(tmp_path, monkeypatch):
    """An empty variable is not an unset one, so the refusal names the variable and not a default.
    """
    monkeypatch.setenv(ROOT_VARIABLE, "   ")

    result = run("show", ENVIRONMENT)

    assert result.exit_code != 0
    assert ROOT_VARIABLE in said(result), \
        f"an empty variable was treated as an unset one: {said(result)}"
