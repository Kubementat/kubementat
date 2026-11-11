"""A release's two knobs are answered by the highest layer that declares them.

Two facts belong to a release and to nothing else: whether it is installed, and which chart version
of it. Until now an operator could state the namespace they land in from `static.json` and had to
edit the shipped manifest for either of these two -- which is the asymmetry an owner decision closed:
`<COMPONENT>_ENABLED` and `<COMPONENT>_VERSION` are `static.json` answers, read
through the same `L2 > L1` walk that answers a namespace.

Why this file exists rather than a one-line reuse: the accessor that owns namespaces and selectors,
`Config._component_value`, tests `isinstance(value, str)` on its way to the code default, and a JSON
`false` is not a string. Routed through it, `GRAFANA_ENABLED: false` declared by a team would fall
through to the manifest's `default "true"`, log a sentence about defaulting, and install Grafana
anyway. The accessor the bug was caught by is the bug, so the knobs get their own reader, and the
test that proves the choice is the boolean one below: `false` from the team beats `"true"` from the
environment, is exported as the string helmfile resolves, and prints nothing about a default.

The manifest is the entry point, and so it owns the version literals that an L0 default would
otherwise duplicate.
"""

import json
import logging

import click
import pytest

from kubementat.config import Config
from kubementat.helm_utils import export_component_release_knobs

ENVIRONMENT = "layerenv"
TEAM = "layerteam"

# Grafana's two knobs, because that is the release the namespace tests already address and the read
# has to be the same one a command makes. `component_config_key` builds these out of the component
# name, so spelling them here is a second statement of the rule and not a second source.
ENABLED_KEY = "GRAFANA_ENABLED"
VERSION_KEY = "GRAFANA_VERSION"


def seed(root, environment_level=None, team_level=None, environment_encrypted=None,
         team_encrypted=None, team_files_exist=True):
    """A `Config` over a seeded tree. Every layer is optional; an omitted pair writes `{}`.

    Copied from `test_a_key_is_answered_by_the_highest_layer_that_declares_it.py` rather than
    reinvented: the tree shape is what is under test here too, and a second way to build one is a
    second way for the precedence to be wrong.
    """
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
    """L2 over L1, for the knob that removes a release.

    The environment says install, the team says do not. The team's answer is the run's answer, the
    same way a team's namespace outranks the environment's, and the encrypted half of the
    environment's layer is not a way for it to be overridden from below.
    """
    config = seed(tmp_path,
                  environment_level={ENABLED_KEY: True},
                  environment_encrypted={ENABLED_KEY: True},
                  team_level={ENABLED_KEY: False})

    assert config.component_release_enabled("grafana") == "false"


def test_the_teams_layer_answers_version_before_the_environments_pair(tmp_path):
    """The same precedence for the same release's other knob, spelled through both files of a layer."""
    config = seed(tmp_path,
                  environment_level={VERSION_KEY: "14.0.0"},
                  environment_encrypted={VERSION_KEY: "14.1.0"},
                  team_level={VERSION_KEY: "13.2.0"})

    assert config.component_release_version("grafana") == "13.2.0"


def test_the_environment_answers_when_the_team_declares_neither_knob(tmp_path):
    """The half that has to fall through: a team that says nothing is a team that permits.

    Both keys are read from the same instance here so the pair is checked to agree about which layer
    answered -- two knobs and two different layers would be the same bug the namespace had.
    """
    config = seed(tmp_path,
                  environment_level={ENABLED_KEY: False, VERSION_KEY: "12.1.0"},
                  team_level={})

    assert config.component_release_enabled("grafana") == "false"
    assert config.component_release_version("grafana") == "12.1.0"


def test_the_encrypted_half_of_a_layer_answers_a_knob_before_the_plain_half(tmp_path):
    """A layer is a pair of files, and the knob obeys that like every other key does.

    If this were to read the plain file first, `static.encrypted.json` would stop being a layer and
    become a hint.
    """
    config = seed(tmp_path,
                  environment_level={ENABLED_KEY: True},
                  environment_encrypted={ENABLED_KEY: False})

    assert config.component_release_enabled("grafana") == "false"


def test_a_knob_that_was_never_declared_answers_as_nothing_and_says_nothing(tmp_path, caplog):
    """The absence case, stated twice on purpose.

    `None` is the answer because an undeclared knob is a decision not to override, and the manifest
    is the next reader. It is also silent: there is no built-in default in `constants.py` to have
    defaulted to, and therefore no "you forgot" sentence to tell -- a warning here would be the noise
    that trains an operator to read none of them.
    """
    config = seed(tmp_path, environment_level={}, team_level={})

    with caplog.at_level(logging.WARNING):
        assert config.component_release_enabled("grafana") is None
        assert config.component_release_version("grafana") is None

    assert caplog.text == ""


def test_a_teamless_configuration_still_answers_its_environment_knobs(tmp_path):
    """A missing team is not part of the knobs.

    A `Config` with no team has no team layer to consult, and a read that tried to name its paths
    would raise over a question about a release. The environment half answers, which is what a
    command that was given no team needs.
    """
    environment_dir = tmp_path / "platform_config" / ENVIRONMENT
    environment_dir.mkdir(parents=True)
    (environment_dir / "static.json").write_text(json.dumps({ENABLED_KEY: False}))
    (environment_dir / "static.encrypted.json").write_text("{}")
    config = Config(ENVIRONMENT, None, kubementat_main_dir=str(tmp_path),
                    platform_config_dir=str(tmp_path / "platform_config"))

    assert config.component_release_enabled("grafana") == "false"
    assert config.component_release_version("grafana") is None


# --- the case the accessor exists for ------------------------------------------------------


def test_a_teams_false_beats_an_environments_true_and_is_exported_as_the_string_helmfile_resolves(
        tmp_path, caplog, monkeypatch):
    """The regression this whole read path was written to close.

    A team that writes `false` as a JSON boolean and an environment that writes `"true"` as a
    string. The accessor must answer `false`, export the lower-cased string helmfile's
    `installed:` resolves, and log nothing -- because the one reader that could have answered this
    (`_component_value`, which tests for a string on the way to its default) would have installed
    Grafana and reported that it had defaulted.
    """
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


def test_a_boolean_answers_lower_cased_because_helmfile_resolves_only_those_two_words(tmp_path):
    """`True` is Python's spelling, `"true"` is helmfile's, and only the second one is an answer.

    A capitalised `True` inside `installed: {{ env ... }}` is a string helmfile does not resolve to
    its boolean field, which is the failure mode the whole export has to avoid.
    """
    config = seed(tmp_path, environment_level={ENABLED_KEY: True, "TRAEFIK_ENABLED": False})

    assert config.component_release_enabled("grafana") == "true"
    assert config.component_release_enabled("traefik") == "false"
    # The answer is the string helmfile resolves and not Python's spelling, so what the export would
    # write is exactly these two words.
    assert {config.component_release_enabled("grafana"),
            config.component_release_enabled("traefik")} <= {"true", "false"}


# --- shapes that are not answers -----------------------------------------------------------


def test_a_knob_declared_as_null_is_the_same_as_not_declaring_it(tmp_path, caplog):
    """`null` is nobody's decision, so it costs nothing and prints nothing.

    It is not the same as `false`: `false` removes a release and `null` installs it, so treating a
    `null` as a refusal would turn a placeholder into a deletion.
    """
    config = seed(tmp_path, environment_level={ENABLED_KEY: None, VERSION_KEY: None})

    with caplog.at_level(logging.WARNING):
        assert config.component_release_enabled("grafana") is None
        assert config.component_release_version("grafana") is None
    assert caplog.text == ""


def test_a_knob_declared_with_blank_is_the_same_as_not_declaring_it(tmp_path):
    """Whitespace is the half-typed edit `component_namespace` warns about; here there is no default
    to warn about, so it is silence and the manifest's answer."""
    config = seed(tmp_path, environment_level={ENABLED_KEY: "   ", VERSION_KEY: " 13.2.0 \t"})

    assert config.component_release_enabled("grafana") is None
    # ...and a version that is not blank comes back stripped, because helm resolves a version string
    # including whatever whitespace it was stored with.
    assert config.component_release_version("grafana") == "13.2.0"


@pytest.mark.parametrize("value", [29, 1.0, ["29.40.1"], {"chart": "29.40.1"}])
def test_a_version_declared_as_anything_but_a_string_is_refused_where_it_was_typed(
        tmp_path, value, caplog):
    """There is no valid version that is a number, and a default that guessed would install one.

    `1.0` is the shape that actually happens: JSON reads it as a number, helm resolves it as a
    version string, and the two answers are not the same chart. The refusal names the key, the file
    that declared it, and the two files of that layer the key could have lived in, because the only
    question left is which file they edited.
    """
    config = seed(tmp_path, environment_level={VERSION_KEY: value})

    with pytest.raises(click.ClickException) as error:
        config.component_release_version("grafana")

    assert VERSION_KEY in str(error.value)
    assert str(config.env_static_config_path) in str(error.value)
    assert "static.json" in str(error.value)
    assert "static.encrypted.json" in str(error.value)
    assert caplog.text == ""


def test_an_enabled_knob_declared_as_a_number_is_refused_and_not_fallen_through_from(tmp_path):
    """The other direction of the same mistake, asserted separately.

    A number `0` would read as a decision not to install by every reading of "falsy" in the tree and
    as `installed: 0` to helmfile, which is a template that fails to parse. Better to refuse.
    """
    config = seed(tmp_path, environment_level={ENABLED_KEY: 0})

    with pytest.raises(click.ClickException) as error:
        config.component_release_enabled("grafana")

    assert ENABLED_KEY in str(error.value)


def test_a_knob_only_the_team_layer_declared_answers_and_names_no_missing_file(tmp_path, caplog):
    """A team that declares a knob the environment never heard of is a team that has decided.

    Nothing in this read path should be able to say "not set in ..." about a key it found: the
    message belongs to the layer that declares a default and not to a pair that declared a decision.
    """
    config = seed(tmp_path, environment_level={}, team_encrypted={ENABLED_KEY: False})

    with caplog.at_level(logging.WARNING):
        assert config.component_release_enabled("grafana") == "false"
    assert caplog.text == ""
