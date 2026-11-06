"""Where kubementat's configuration lives, and who is allowed to say so.

Three defects live under this subject:

* **D11** — `kubementat_main_dir` was `Path(__file__).parent.parent.parent`, which is the
  checkout for a working tree and *site-packages* for a real install. `KUBEMENTAT_HOME` is
  the override that makes an installed `kmt` read the checkout it was pointed at, and it is
  the prerequisite for the CI-container work: there the checkout is `/workspace/...` while
  `kmt` itself is installed.
* **D10** — `logging.basicConfig()` ran while `kubementat.core.config.config` was still being imported,
  so every library consumer got a root handler and an INFO level whether it wanted them or
  not. The CLI installs the handler now; importing the library installs nothing.
* The generated trigger config had no accessor at all — `tekton_team_pipeline_dir`,
  `tekton_team_task_dir` and `tekton_team_pipeline_run_dir` existed, triggers were left
  behind — so neither a generator nor its readers had a path to agree on.
* `Config.__init__` consulted `$KUBEMENTAT_HOME` **before** honouring its own
  `kubementat_main_dir` argument, so the environment answered first and the argument only edited
  the answer afterwards. A fully-threaded `Config` refused over an empty-but-set `KUBEMENTAT_HOME`
  it had never been asked about. Two tests under `KUBEMENTAT_HOME` below pin both
  sides of the corrected ordering, and they sit in this file because naming a root is the subject.

The `TODOs.md` ask ("an option for setting the platform_config directory") is the same
mechanism as the first one, so the answer is one environment variable and no CLI option.
"""

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from click.exceptions import ClickException

from kubementat.core.config.config import Config, default_kubementat_main_dir
from kubementat.config_init import init_configuration
from asset_paths import asset_path

ENVIRONMENT = "testenv"
TEAM = "testteam"
OTHER_TEAM = "otherteam"

REPO_ROOT = Path(__file__).resolve().parent.parent

HOME_VAR = "KUBEMENTAT_HOME"
PLATFORM_CONFIG_VAR = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def run_python(code: str) -> subprocess.CompletedProcess:
    """Run `code` in a clean interpreter, from outside the checkout.

    cwd=/tmp so that a path derived from the interpreter's own file cannot pass a test by
    accident, and so that an import-time side effect is the only thing the assertion can be
    proving.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd="/tmp",
    )


def write_json(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))


def seeded_platform_config(root: Path, environment=ENVIRONMENT, team=TEAM):
    """A `platform_config/{environment}/...` tree with one recognisable value per level."""
    write_json(root / f"{environment}/static.json", {"ENV_ONLY": "env-value"})
    write_json(root / f"{environment}/static.encrypted.json", {"ENV_SECRET": "env-secret"})
    write_json(root / f"{environment}/{team}/static.json", {"TEAM_ONLY": "team-value"})
    write_json(root / f"{environment}/{team}/static.encrypted.json", {"TEAM_SECRET": "team-secret"})


def unset_path_variables(monkeypatch):
    """Pin the defaults: this file asserts both what they are and what overrides them."""
    for name in (HOME_VAR, PLATFORM_CONFIG_VAR):
        monkeypatch.delenv(name, raising=False)


# --- KUBEMENTAT_HOME -------------------------------------------------------------------


def test_the_project_root_follows_kubementat_home(tmp_path, monkeypatch):
    """An installed library must still read the checkout it was pointed at (D11)."""
    monkeypatch.setenv(HOME_VAR, str(tmp_path))
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(tmp_path / "named-configuration"))
    config = Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert config.kubementat_main_dir == tmp_path.resolve()


def test_kubementat_home_names_the_checkout_and_no_longer_the_configuration(tmp_path, monkeypatch):
    """One root for the checkout's own trees, another for configuration: the two were split.

    This test used to assert that `platform_config` hung off `KUBEMENTAT_HOME`, which is the
    fallback this ticket deletes, and that `templates/` hung off it too — a deferral that said
    the templates question belonged to a later change. That change is here, and its answer is that
    templates are product data: they resolve out of the package and follow no root, so a
    `KUBEMENTAT_HOME` pointed
    at a checkout being debugged still renders the templates that were shipped. What follows the
    variable is the checkout-relative trees (`tekton_ci/`); what follows the variable that names it
    is configuration.
    """
    monkeypatch.setenv(HOME_VAR, str(tmp_path))
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(tmp_path / "named-configuration"))
    config = Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert config.templates_dir == asset_path("templates"), \
        "templates are package data now, and a root that moves them is the bug this rule closes"
    assert config.templates_dir.is_dir(), "the shipped templates have to be really there"
    assert config.platform_config_dir == (tmp_path / "named-configuration").resolve()
    assert (tmp_path / "platform_config") not in {config.platform_config_dir}, \
        "the checkout-relative configuration root is back"


def test_the_project_root_is_the_checkout_only_when_kubementat_home_is_unset(tmp_path, monkeypatch):
    """With no override the answer is the repository this module was installed from.

    `kubementat_main_dir` is the asset root and its rule is untouched; the configuration
    root has to be named for the object to build at all, which is what the second line is doing.
    """
    unset_path_variables(monkeypatch)
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(tmp_path / "named-configuration"))
    config = Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert config.kubementat_main_dir == REPO_ROOT


def test_an_explicit_path_beats_the_environment_variable(tmp_path, monkeypatch):
    """One instance can be pointed somewhere else without setting a variable machine-wide."""
    monkeypatch.setenv(HOME_VAR, str(tmp_path / "from-the-environment"))
    config = Config(ENVIRONMENT, TEAM,
                    kubementat_main_dir=str(tmp_path / "by-argument"),
                    platform_config_dir=str(tmp_path / "named-configuration"),
                    preload_configs=False)

    assert config.kubementat_main_dir == (tmp_path / "by-argument").resolve()


def test_a_handed_root_answers_and_the_variable_answers_only_when_nothing_is_handed(
        tmp_path, monkeypatch):
    """Both sides of one precedence, because the defect was an ordering and not a comparison.

    `Config.__init__` used to run `default_kubementat_main_dir()` on the line before it honoured
    the argument, so the environment answered first and the argument only edited the answer
    afterwards. The value came out right in that state and wrong beside it, which is the signature
    of an ordering bug: it passes a value test and fails a refusal test. So both directions are
    asserted here, in one place:

    * a `Config` that was **handed** a root is answered by that root, and `$KUBEMENTAT_HOME` —
      pointed at a decoy on purpose — never votes. The derived `tekton_ci/pipeline-runs/` path is
      asserted too, because that is the half a command acts on: the argument had to reach it, and
      not only the attribute the parameter is assigned to.
    * a `Config` that was handed **nothing** is answered by the variable, exactly as before. This
      is the control, and it sits beside the first case rather than being left to
      `test_the_project_root_follows_kubementat_home`: a change that inverted the precedence
      instead of fixing the ordering passes one of these two and fails the other.
    """
    handed = tmp_path / "root-the-command-was-handed"
    decoy = tmp_path / "root-the-environment-happens-to-name"
    monkeypatch.setenv(HOME_VAR, str(decoy))
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(decoy / "platform_config"))

    threaded = Config(ENVIRONMENT, TEAM, kubementat_main_dir=handed,
                      platform_config_dir=decoy / "platform_config", preload_configs=False)

    assert threaded.kubementat_main_dir == handed.resolve(), \
        "the argument never outranked the variable: the precedence was inverted, not fixed"
    assert threaded.tekton_global_pipeline_run_dir == (
        handed.resolve() / "tekton_ci" / "pipeline-runs"), \
        "the argument did not reach the output path a command writes under"

    asked_the_environment = Config(ENVIRONMENT, TEAM, preload_configs=False)
    assert asked_the_environment.kubementat_main_dir == decoy.resolve(), \
        ("the variable stopped answering a run that handed no root, so the ordering fix is an "
         "early return and not a precedence")


def test_an_empty_kubementat_home_cannot_refuse_a_command_that_was_handed_a_root(
        tmp_path, monkeypatch):
    """The state the census measured, and the one case the ordering bug actually broke.

    `KUBEMENTAT_HOME=""` beside
    `Config("dev", "dev1", kubementat_main_dir=<handed down>, preload_configs=False)` raised
    `KUBEMENTAT_HOME is set but empty` — a fully-threaded `Config` refusing over a dial nobody had
    consulted, on a machine where someone had exported the variable to nothing.

    The refusal rule is not weakened: empty-but-set is still a mistake about the name, and the
    second half of this test says so for the construction that really does ask the variable. All
    that moved is *when* the dial is consulted — after the argument, and not before it.
    """
    monkeypatch.setenv(HOME_VAR, "")
    handed = tmp_path / "root-the-command-was-handed"

    config = Config(ENVIRONMENT, TEAM, kubementat_main_dir=handed,
                    platform_config_dir=tmp_path / "named-configuration",
                    preload_configs=False)

    assert config.kubementat_main_dir == handed.resolve(), \
        "an empty variable answered a command that had been handed a root"

    with pytest.raises(ClickException) as raised:
        Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert HOME_VAR in str(raised.value), \
        "the refusal left with the ordering fix: nobody is told which dial was empty"


def test_the_root_the_cli_would_act_on_follows_kubementat_home_too(tmp_path, monkeypatch):
    """The CLI and the library resolve the root the same way, or the two drift apart.

    There used to be a second answer in `cli.kmt.Config`, and it was the one every command was
    handed and none read. Deleting it did not change the invariant: the root a `kmt` process
    acts on is `KUBEMENTAT_HOME`, resolved by the same function every `Config` uses. This asserts
    it against that function, so the day a second resolution appears and disagrees — which is the
    only way the old test could fail — the failure is here.
    """
    monkeypatch.setenv(HOME_VAR, str(tmp_path))

    assert default_kubementat_main_dir() == tmp_path.resolve()


def test_a_root_that_is_set_but_empty_is_an_error_naming_the_variable(monkeypatch):
    """Silently falling back to the default here is D11 again, just quieter.

    `KUBEMENTAT_HOME=` in a unit file or a CI `env:` block would read config out of
    site-packages and say nothing about it, so the mistake has to fail before any path is
    used.
    """
    monkeypatch.setenv(HOME_VAR, "   ")

    with pytest.raises(ClickException) as excinfo:
        Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert HOME_VAR in str(excinfo.value)


# --- KUBEMENTAT_PLATFORM_CONFIG_DIR ----------------------------------------------------


def test_the_platform_config_directory_follows_kubementat_platform_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(tmp_path / "elsewhere"))
    config = Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert config.platform_config_dir == (tmp_path / "elsewhere").resolve()


def test_the_override_moves_every_path_derived_from_it(tmp_path, monkeypatch):
    """Environment and team files are read from the override, not from the checkout."""
    target = tmp_path / "elsewhere"
    seeded_platform_config(target)
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(target))
    config = Config(ENVIRONMENT, TEAM)

    assert config.get("env_static")["ENV_ONLY"] == "env-value"
    assert config.get("team_static")["TEAM_ONLY"] == "team-value"
    assert config.team_static_config_path.parent == target.resolve() / ENVIRONMENT / TEAM


def test_a_platform_config_directory_that_is_set_but_empty_names_the_variable(monkeypatch):
    """Empty-but-set is `_directory_from_environment`'s rule verbatim, not a second rule.

    An empty `KUBEMENTAT_PLATFORM_CONFIG_DIR` in a unit file or a CI `env:` block used to be
    answered by the checkout. It is still answered by nothing: naming the variable is what an
    unset one means, and setting it to nothing is a mistake about the name.
    """
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, "")

    with pytest.raises(ClickException) as excinfo:
        Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert PLATFORM_CONFIG_VAR in str(excinfo.value)


def test_no_platform_config_root_is_named_and_the_refusal_names_both_dials(monkeypatch):
    """A configuration root is named, never located.

    Replaces the test that used to assert `Config(...).platform_config_dir ==
    KUBEMENTAT_HOME / "platform_config"`, which was the deleted behaviour and not a rule worth
    keeping. Nothing is unset-and-defaulted here: with neither `--config-dir` nor the variable,
    there is no root, the sentence names both spellings that could have given one, and it names no
    third place — no checkout, no `platform_config`, no guess. The empty case above is the same
    rule from the other side: a variable set to nothing is not a root either.
    """
    unset_path_variables(monkeypatch)

    with pytest.raises(ClickException) as excinfo:
        Config(ENVIRONMENT, TEAM, preload_configs=False)

    message = str(excinfo.value)
    assert PLATFORM_CONFIG_VAR in message, "the refusal did not name the variable"
    assert "--config-dir" in message, "the refusal did not name the flag"
    assert "platform_config" not in message, \
        f"a refusal that offers a path is a fallback wearing better clothes: {message}"


def test_no_root_named_still_refuses_with_a_preload_turned_off(monkeypatch):
    """`preload_configs=False` is not a way past the rule.

    Every lazy `Config` in the product is built that way — `Context.build` among them — so the
    refusal has to land on the constructor and not only when a file is opened, or the run would
    get as far as a path built out of nothing.
    """
    unset_path_variables(monkeypatch)

    with pytest.raises(ClickException):
        Config(ENVIRONMENT, TEAM, preload_configs=False)


# --- tekton_team_trigger_dir -----------------------------------------------------------


def test_the_trigger_directory_is_per_environment_and_per_team(tmp_path, monkeypatch):
    monkeypatch.delenv(HOME_VAR, raising=False)
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(tmp_path / "named-configuration"))
    same_team = Config(ENVIRONMENT, TEAM, preload_configs=False)
    other_team = Config(ENVIRONMENT, OTHER_TEAM, preload_configs=False)
    other_environment = Config("otherenv", TEAM, preload_configs=False)

    assert same_team.tekton_team_trigger_dir == (
        same_team.platform_config_dir / ENVIRONMENT / TEAM / "tekton" / "triggers"
    )
    assert same_team.tekton_team_trigger_dir != other_team.tekton_team_trigger_dir
    assert same_team.tekton_team_trigger_dir != other_environment.tekton_team_trigger_dir


def test_the_trigger_directory_sits_alongside_the_other_team_tekton_dirs(tmp_path, monkeypatch):
    """The same per-team `tekton/` root as pipelines, tasks and pipeline-runs — a sibling, not
    elsewhere. That is the move: the pre-move path sat one directory higher, at the team
    root, and this assertion is what keeps it from drifting back."""
    monkeypatch.delenv(HOME_VAR, raising=False)
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, str(tmp_path / "named-configuration"))
    config = Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert config.tekton_team_trigger_dir.parent == config.tekton_team_pipeline_dir.parent
    assert config.tekton_team_trigger_dir.parent.name == "tekton"


def test_initialization_creates_the_team_trigger_directory(tmp_path, monkeypatch):
    """`kmt config init` scaffolds it, and scaffolding it twice stays fine.

    `init_configuration` is the write pass that creates the layer directories; the rest of the
    preparation generates keys and runs git-crypt against a repository of its own.
    """
    unset_path_variables(monkeypatch)
    root = tmp_path / "named-configuration"
    config = Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(tmp_path),
                    platform_config_dir=str(root), preload_configs=False)
    trigger_dir = config.tekton_team_trigger_dir

    init_configuration(root, ENVIRONMENT, TEAM)

    assert trigger_dir.is_dir()

    init_configuration(root, ENVIRONMENT, TEAM)
    assert trigger_dir.is_dir()


# --- the logging side effect -----------------------------------------------------------


def test_importing_the_library_configures_no_logging():
    """D10: the import used to leave a root handler and an INFO level behind."""
    result = run_python(
        "import logging, kubementat.core.config.config;"
        "print(len(logging.getLogger().handlers), logging.getLogger().level)"
    )
    assert result.returncode == 0, result.stderr

    handlers, level = result.stdout.split()
    assert handlers == "0", "importing kubementat installed a logging handler"
    assert int(level) == logging.WARN, "importing kubementat changed the log level"


def test_the_cli_entry_point_is_what_configures_logging():
    """Moving the call out of the library must not lose it: the CLI still sets it up."""
    result = run_python(
        "import logging; from cli.kmt import configure_logging;"
        "configure_logging(); print(len(logging.getLogger().handlers), logging.getLogger().level)"
    )
    assert result.returncode == 0, result.stderr

    handlers, level = result.stdout.split()
    assert handlers == "1"
    assert int(level) == logging.INFO


def test_only_the_cli_entry_point_calls_logging_basic_config():
    """A second call anywhere else re-opens D10 for its own importers.

    Parsed rather than grepped, so a comment that mentions the call cannot satisfy it.
    """
    callers = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src").rglob("*.py")
        if any(_is_basic_config_call(node)
               for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))))
    )

    assert callers == ["src/cli/kmt.py"]


def _is_basic_config_call(node) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "basicConfig"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logging")
