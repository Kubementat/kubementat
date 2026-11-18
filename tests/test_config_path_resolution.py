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

import json
from pathlib import Path

import pytest
from kubementat.errors import ConfigurationRootNotNamedError, RootDirectoryVariableIsEmptyError

from kubementat.core.config.config import Config

ENVIRONMENT = "testenv"
TEAM = "testteam"
OTHER_TEAM = "otherteam"

REPO_ROOT = Path(__file__).resolve().parent.parent

HOME_VAR = "KUBEMENTAT_HOME"
PLATFORM_CONFIG_VAR = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def write_json(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))


def unset_path_variables(monkeypatch):
    """Pin the defaults: this file asserts both what they are and what overrides them."""
    for name in (HOME_VAR, PLATFORM_CONFIG_VAR):
        monkeypatch.delenv(name, raising=False)


# --- KUBEMENTAT_HOME -------------------------------------------------------------------


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


# --- KUBEMENTAT_PLATFORM_CONFIG_DIR ----------------------------------------------------


def test_a_platform_config_directory_that_is_set_but_empty_names_the_variable(monkeypatch):
    """Empty-but-set is `_directory_from_environment`'s rule verbatim, not a second rule.

    An empty `KUBEMENTAT_PLATFORM_CONFIG_DIR` in a unit file or a CI `env:` block used to be
    answered by the checkout. It is still answered by nothing: naming the variable is what an
    unset one means, and setting it to nothing is a mistake about the name.
    """
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, "")

    with pytest.raises(RootDirectoryVariableIsEmptyError) as excinfo:
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

    with pytest.raises(ConfigurationRootNotNamedError) as excinfo:
        Config(ENVIRONMENT, TEAM, preload_configs=False)

    message = str(excinfo.value)
    assert PLATFORM_CONFIG_VAR in message, "the refusal did not name the variable"
    assert "--config-dir" in message, "the refusal did not name the flag"
    assert "platform_config" not in message, \
        f"a refusal that offers a path is a fallback wearing better clothes: {message}"


# --- tekton_team_trigger_dir -----------------------------------------------------------


# --- the logging side effect -----------------------------------------------------------
