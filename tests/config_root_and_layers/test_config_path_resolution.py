"""Where kubementat's configuration lives, and who is allowed to say so."""

import json

import pytest

from kubementat.core.config.config import Config
from kubementat.errors import (
    ConfigurationRootNotNamedError,
    RootDirectoryVariableIsEmptyError,
)

ENVIRONMENT = "testenv"
TEAM = "testteam"
OTHER_TEAM = "otherteam"


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
    """Both sides of one precedence, because the defect was an ordering and not a comparison."""
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
    """Empty-but-set is `_directory_from_environment`'s rule verbatim, not a second rule."""
    monkeypatch.setenv(PLATFORM_CONFIG_VAR, "")

    with pytest.raises(RootDirectoryVariableIsEmptyError) as excinfo:
        Config(ENVIRONMENT, TEAM, preload_configs=False)

    assert PLATFORM_CONFIG_VAR in str(excinfo.value)


def test_no_platform_config_root_is_named_and_the_refusal_names_both_dials(monkeypatch):
    """A configuration root is named, never located."""
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
