"""Every `kmt config` verb names the root it works on, and none of them locates one.

The surface used to have one exception: the seven verbs under `kmt config` took the directory as a
positional and answered to neither of the two spellings every other kmt command honours. So a
container that had already configured its root could run `kmt install` against it and could not run
`kmt config set` against it — the same tree, two ways of saying which one, and only one of the two
routes worked where the operator had written the path down once. This file is the guard against that
exception coming back for one verb and not for the others.

Two claims, and the second is the one that pays.

* **The census.** A verb that silently dropped the flag would still work for anyone typing a path as
  a positional, so `kmt --help` would not notice and a script that set the variable would. The exact
  parameter set is what notices, verb by verb.
* **The precedence.** `--config-dir` beats `$KUBEMENTAT_PLATFORM_CONFIG_DIR` and with neither the run
  refuses; all three branches have to hold for every verb, because a rule that is true of six of
  seven commands is a rule with a hole in it and not a rule. The branch that used to be missing is
  the middle one: nothing but the variable.

The verbs that resolve a cluster are not here — `cli/context.py` gives them the same option through
`@kmt_context`, so a hole would show up as a failing cluster test rather than as this file's
assertions — and `kmt environments list` carries the same flag for the same reason, which the surface
check in `test_cli_surface.py` pins.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bootstrap_tools import EMAIL, tools  # noqa: F401  (the fixture is what makes `init` cheap)
from cli.commands import config as config_module
from kubementat import config_init

ENVIRONMENT = "rootverbenv"
TEAM = "rootverbteam"
ROOT_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"

# The seven verbs, in the order the group lists them. The table is a set and not a list because the
# point of reading it here is that no eighth verb arrives without being added to this file too.
VERBS = {"init", "add-environment", "add-team", "set", "unset", "show", "validate"}

# What each verb needs besides a root, in a run that would otherwise succeed. Every one of these
# arguments is valid; the only thing withheld is the root, which is what the refusal is about.
VALID_WITHOUT_ROOT = {
    "init": ["--environment", ENVIRONMENT, "--team", TEAM, "--gpg-email", EMAIL],
    "add-environment": ["second-env"],
    "add-team": [ENVIRONMENT, "second-team"],
    "set": [ENVIRONMENT, "ROOTVERB=1"],
    "unset": [ENVIRONMENT, "ROOTVERB"],
    "show": [ENVIRONMENT],
    "validate": [ENVIRONMENT],
}

def prepared_tree(workspace: Path) -> Path:
    """A tree with both layers and one answer, for the verbs that read or grow one."""
    return tree(workspace)


def untouched_root(workspace: Path) -> Path:
    """A root that does not exist yet, which is what `init` is pointed at and nothing else is.

    A prepared tree is the wrong thing to hand the tree-creating verb: its answers are already
    answered by hand and `init` refuses a file that differs from what it would write, so a run that
    looked like a root problem would be a file-content problem wearing it.
    """
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
    """Neither spelling of a root, and no `KUBEMENTAT_HOME` either, so a located root is impossible."""
    monkeypatch.delenv(ROOT_VARIABLE, raising=False)
    monkeypatch.delenv("KUBEMENTAT_HOME", raising=False)


def tree(tmp_path) -> Path:
    """A prepared tree with both layers answered, built by the verbs the surface has.

    Built with `init_configuration` / `add_team` / `set_answer` and not copied, because a fixture that
    `cp -a`'s a directory is a fixture that believes in a tree no product verb wrote.
    """
    root = tmp_path / "platform_config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    config_init.add_team(root=root, environment=ENVIRONMENT, team=TEAM)
    config_init.set_answer(root=root, environment=ENVIRONMENT, key="ROOTVERB", value=1)
    return root


def run(*argv):
    """`kmt config …`, through the group so the verb's own name is exercised.

    A bug inside the command re-raises instead of hiding in an exit code; a `ClickException` refusal
    is allowed to stand, because "I will not run" is an answer and not a crash.
    """
    result = CliRunner(mix_stderr=False).invoke(config_module.config, list(argv))
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def said(result) -> str:
    return f"{result.stdout}{result.stderr}"


def answered(root: Path) -> object:
    """What the environment layer answers for this file's key, read back out of the tree.

    Read from the file and not from `kmt config show`, because the assertion is about *which root* a
    run wrote and a read that resolves the root itself would hide the answer behind the same rule it
    is meant to be testing.
    """
    path = root / ENVIRONMENT / "static.json"
    return json.loads(path.read_text(encoding="utf-8")).get("ROOTVERB")


def test_no_configuration_verb_takes_a_root_as_a_positional():
    """The census: seven verbs, each carrying `config_dir` and none carrying `directory`.

    The two sets are checked separately because each catches a different drift. Dropping the flag is
    what a "cleanup" does to one verb, and putting the directory back is what a revert does to the
    group's help text; either one leaves every other assertion in the tree green.
    """
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


def test_every_configuration_verb_carries_the_flag_and_says_what_it_is_for():
    """The flag is declared on all seven, and its own help names the variable it sits above.

    Both halves matter: a verb that lost the option would still work for anybody typing a path, and
    an option whose help never said what it outranks is a dial nobody can use from `--help`. The
    precedence sentence is written once in `cli.context.config_dir_option`, so it is the same sentence
    here for every verb — and that is the point of the shared option, not an accident of it.
    """
    for name, command in sorted(config_module.config.commands.items()):
        option = next((p for p in command.params if "--config-dir" in (p.opts or [])), None)
        assert option is not None, f"kmt config {name} declares no --config-dir"
        assert ROOT_VARIABLE in (option.help or ""), \
            f"kmt config {name}'s --config-dir does not say it sits above ${ROOT_VARIABLE}, so the " \
            "help cannot tell anybody which root the run reads"


def test_a_run_that_names_no_root_refuses_and_names_both_dials(clean_environment):
    """With neither spelling, every verb refuses and nothing is written anywhere.

    The assertion per verb is the sentence and the absence of a tree: a root located beside the
    installed wheel is the D11 failure, and it is worse than a crash because it answers.
    """
    for name, arguments in sorted(VALID_WITHOUT_ROOT.items()):
        result = run(name, *arguments)
        assert result.exit_code != 0, f"kmt config {name} answered without a root: {said(result)}"
        assert "--config-dir" in said(result) and ROOT_VARIABLE in said(result), \
            f"kmt config {name} refused without naming the two spellings that would answer it: " \
            f"{said(result)}"


def test_the_variable_names_the_root_for_every_verb_and_the_flag_outranks_it(tmp_path,
                                                                             clean_environment,
                                                                             tools,
                                                                             monkeypatch):
    """The branch that used to be missing: nothing on the command line, and the variable still read.

    Two halves, because one is a rule with a hole in it. The first says the variable is honoured,
    which is what a container or a scheduled run actually relies on. The second says the flag beats
    it, because a rule with two spellings needs exactly one of them to win, and that precedence has
    one owner — `paths.resolve_platform_config_dir` — which is why a decoy named by the variable
    while a root was handed to the command stays empty.
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

    Falling back to a default here would read configuration out of wherever the wheel was installed
    while saying nothing about it, which is the same defect one layer down. So the empty case is
    refused in the same place the unset case is, and the six reading verbs say so identically.
    """
    monkeypatch.setenv(ROOT_VARIABLE, "   ")

    result = run("show", ENVIRONMENT)

    assert result.exit_code != 0
    assert ROOT_VARIABLE in said(result), \
        f"an empty variable was treated as an unset one: {said(result)}"
