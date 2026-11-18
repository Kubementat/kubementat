"""The export writes what the operator declared, and only that -- for the releases the manifest names.
"""

import json
import os

import pytest
from asset_paths import values_path
from cluster_fixture import UNREAD_ROOT, configuration_only_context

from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key
from kubementat.infrastructure.helm import HelmUtils, export_component_release_knobs

ENVIRONMENT = "knobenv"
TEAM = "knobteam"

# The shipped manifest, read the way `helmfile_apply` reads it: the same file decides which releases
# exist and which variables the export is allowed to write.
LIVE_HELMFILE = values_path("helmfile.yaml.gotmpl")

SUFFIXES = ("ENABLED", "VERSION")


def seed(root, environment_level=None, team_level=None):
    """A `Config` over a seeded tree, seeded the way the layering tests seed one."""
    environment_dir = root / "platform_config" / ENVIRONMENT
    team_dir = environment_dir / TEAM
    environment_dir.mkdir(parents=True, exist_ok=True)
    team_dir.mkdir(parents=True, exist_ok=True)
    (environment_dir / "static.json").write_text(json.dumps(environment_level or {}))
    (environment_dir / "static.encrypted.json").write_text("{}")
    (team_dir / "static.json").write_text(json.dumps(team_level or {}))
    (team_dir / "static.encrypted.json").write_text("{}")
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def live_index():
    """`{component_name label: releases}` for the manifest this suite ships."""
    return HelmUtils(configuration_only_context(
        ENVIRONMENT, TEAM, Config(ENVIRONMENT, TEAM, platform_config_dir=UNREAD_ROOT,
                                  preload_configs=False))).component_release_index(LIVE_HELMFILE)


def knob_keys(names):
    """Every variable those names' knobs could be spelled as, sorted."""
    return sorted(component_config_key(name, suffix)
                  for name in sorted(names) for suffix in SUFFIXES)


def clean(monkeypatch, keys):
    """Put the process environment where a tree that declares nothing leaves it, for this test only.
    """
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_a_release_knob_nobody_declared_exports_nothing(tmp_path, monkeypatch):
    """An undeclared knob is not a value to export, and `os.environ` is not a place to guess at."""
    index = live_index()
    keys = knob_keys(index)
    clean(monkeypatch, keys)
    before = {key: os.environ.get(key) for key in keys}
    config = seed(tmp_path, environment_level={}, team_level={})

    exported = export_component_release_knobs(config, index)

    assert exported == {}
    assert {key: os.environ.get(key) for key in keys} == before, \
        "an undeclared knob was written into the environment anyway"


def test_only_the_knob_that_was_declared_is_written_and_the_other_is_left_alone(tmp_path,
                                                                                monkeypatch):
    """One key at a time. Half a pair still exports, and the half nobody typed stays absent."""
    index = live_index()
    keys = knob_keys(index)
    clean(monkeypatch, keys)
    config = seed(tmp_path, environment_level={component_config_key("grafana", "ENABLED"): False})

    exported = export_component_release_knobs(config, index)

    assert exported == {component_config_key("grafana", "ENABLED"): "false"}
    assert os.environ.get(component_config_key("grafana", "VERSION")) is None, \
        "the half nobody typed has to stay absent from the environment it is handed out of"


@pytest.mark.parametrize("component", sorted(live_index()))
def test_every_release_the_manifest_names_gets_both_variables_written(tmp_path, monkeypatch,
                                                                      component):
    """Per release, the pair is exported. A component that lost one of its two lines fails here."""
    index = live_index()
    keys = knob_keys(index)
    clean(monkeypatch, keys)
    config = seed(tmp_path, environment_level={
        component_config_key(component, "ENABLED"): True,
        component_config_key(component, "VERSION"): "1.2.3",
    })

    exported = export_component_release_knobs(config, index)

    assert exported[component_config_key(component, "ENABLED")] == "true"
    assert exported[component_config_key(component, "VERSION")] == "1.2.3"
