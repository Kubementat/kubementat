"""A release's two knobs are answered by the highest layer that declares them."""

import json
import logging

import pytest

from kubementat.core.config.config import Config
from kubementat.errors import ReleaseKnobNotABooleanOrStringError
from kubementat.infrastructure.helm import export_component_release_knobs

ENVIRONMENT = "layerenv"
TEAM = "layerteam"

# Grafana's two knobs, because that is the release the namespace tests already address and the read
# has to be the same one a command makes. `component_config_key` builds these out of the component
# name, so spelling them here is a second statement of the rule and not a second source.
ENABLED_KEY = "GRAFANA_ENABLED"
VERSION_KEY = "GRAFANA_VERSION"


def seed(root, environment_level=None, team_level=None, environment_encrypted=None,
         team_encrypted=None, team_files_exist=True):
    """A `Config` over a seeded tree. Every layer is optional; an omitted pair writes `{}`."""
    environment_dir = root / "platform_config" / ENVIRONMENT
    team_dir = environment_dir / TEAM
    environment_dir.mkdir(parents=True, exist_ok=True)
    if team_files_exist:
        team_dir.mkdir(parents=True, exist_ok=True)
    (environment_dir / "static.json").write_text(json.dumps(environment_level or {}))
    (environment_dir / "static.encrypted.json").write_text(json.dumps(environment_encrypted or {}))
    if team_files_exist:
        (team_dir / "static.json").write_text(json.dumps(team_level or {}))
        (team_dir / "static.encrypted.json").write_text(json.dumps(team_encrypted or {}))
    # The root is named and never located, so the fixture names it.
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


# --- the layer that declares it is the one that answers ------------------------------------


def test_the_teams_layer_answers_enabled_before_the_environments_pair(tmp_path):
    """The team's answer beats the environment's, for the knob that removes a release."""
    config = seed(tmp_path,
                  environment_level={ENABLED_KEY: True},
                  environment_encrypted={ENABLED_KEY: True},
                  team_level={ENABLED_KEY: False})

    assert config.component_release_enabled("grafana") == "false"


def test_the_teams_layer_answers_version_before_the_environments_pair(tmp_path):
    """The same precedence for the same release's other knob, spelled through both files of a layer.
    """
    config = seed(tmp_path,
                  environment_level={VERSION_KEY: "14.0.0"},
                  environment_encrypted={VERSION_KEY: "14.1.0"},
                  team_level={VERSION_KEY: "13.2.0"})

    assert config.component_release_version("grafana") == "13.2.0"


def test_the_encrypted_half_of_a_layer_answers_a_knob_before_the_plain_half(tmp_path):
    """A layer is a pair of files, and the knob obeys that like every other key does."""
    config = seed(tmp_path,
                  environment_level={ENABLED_KEY: True},
                  environment_encrypted={ENABLED_KEY: False})

    assert config.component_release_enabled("grafana") == "false"


def test_a_knob_that_was_never_declared_answers_as_nothing_and_says_nothing(tmp_path, caplog):
    """The absence case, stated twice on purpose."""
    config = seed(tmp_path, environment_level={}, team_level={})

    with caplog.at_level(logging.WARNING):
        assert config.component_release_enabled("grafana") is None
        assert config.component_release_version("grafana") is None

    assert caplog.text == ""


# --- the case the accessor exists for ------------------------------------------------------


def test_a_teams_false_beats_an_environments_true_and_is_exported_as_the_string_helmfile_resolves(
        tmp_path, caplog, monkeypatch):
    """The regression this whole read path was written to close."""
    config = seed(tmp_path, environment_level={ENABLED_KEY: "true"}, team_level={ENABLED_KEY: False})
    monkeypatch.delenv(ENABLED_KEY, raising=False)

    with caplog.at_level(logging.WARNING):
        exported = export_component_release_knobs(config, {"grafana": ["grafana"]})

    assert exported == {ENABLED_KEY: "false"}
    assert exported[ENABLED_KEY] == "false"
    assert "true" not in exported[ENABLED_KEY]
    with caplog.at_level(logging.WARNING):
        assert config.component_release_enabled("grafana") == "false"
    assert ENABLED_KEY not in caplog.text, caplog.text
    assert "default" not in caplog.text.lower(), caplog.text


# --- shapes that are not answers -----------------------------------------------------------


@pytest.mark.parametrize("value", [29, 1.0, ["29.40.1"], {"chart": "29.40.1"}])
def test_a_version_declared_as_anything_but_a_string_is_refused_where_it_was_typed(
        tmp_path, value, caplog):
    """There is no valid version that is a number, and a default that guessed would install one."""
    config = seed(tmp_path, environment_level={VERSION_KEY: value})

    with pytest.raises(ReleaseKnobNotABooleanOrStringError) as error:
        config.component_release_version("grafana")

    assert VERSION_KEY in str(error.value)
    assert str(config.env_static_config_path) in str(error.value)
    assert "static.json" in str(error.value)
    assert "static.encrypted.json" in str(error.value)
    assert caplog.text == ""
