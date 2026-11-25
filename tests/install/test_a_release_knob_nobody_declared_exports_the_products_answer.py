"""What the export hands helmfile: what a layer declared, and where nothing declared, what the product ships.
"""

import json
import os

import pytest
from asset_paths import values_path
from cluster_fixture import UNREAD_ROOT, configuration_only_context

from kubementat.core import versions
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key
from kubementat.infrastructure.helm import HelmUtils, export_component_release_knobs

ENVIRONMENT = "knobenv"
TEAM = "knobteam"

# The shipped manifest, read the way `helmfile_apply` reads it: the same file decides which releases
# exist and which variables the export is allowed to write.
LIVE_HELMFILE = values_path("helmfile.yaml.gotmpl")

SUFFIXES = ("ENABLED", "VERSION")


def shipped_knobs(components):
    """`{variable: value}` for every version knob and image tag the shipped file answers."""
    exported = {component_config_key(component, "VERSION"): versions.chart_version(component)
                for component in sorted(components)}
    for component in sorted(components):
        image_tag = versions.chart_image_tag(component)
        if image_tag is not None:
            exported[component_config_key(component, "IMAGE_TAG")] = image_tag
    return exported


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


def test_a_release_knob_nobody_declared_exports_the_versions_the_product_ships(tmp_path, monkeypatch):
    """Versions are no longer the manifest's to answer, so the apply hands them over whether or not a layer spoke.

    ENABLED keeps its manifest `default` and is still exported only when declared, and `os.environ`
    is still not a place to guess at."""
    index = live_index()
    keys = knob_keys(index)
    clean(monkeypatch, keys)
    before = {key: os.environ.get(key) for key in keys}
    config = seed(tmp_path, environment_level={}, team_level={})

    exported = export_component_release_knobs(config, index)

    assert exported == shipped_knobs(index)
    assert not [key for key in exported if key.endswith("_ENABLED")], \
        "the opt-in whose default still sits in the manifest was exported as if it were not"
    assert {key: os.environ.get(key) for key in keys} == before, \
        "the export was written into the process environment instead of handed to the child"


def test_a_declared_enabled_answers_for_itself_and_the_untouched_version_still_answers_from_the_product(
        tmp_path, monkeypatch):
    """One key at a time. Half a pair still exports, and the half nobody typed arrives from the file.
    """
    index = live_index()
    keys = knob_keys(index)
    clean(monkeypatch, keys)
    config = seed(tmp_path, environment_level={component_config_key("grafana", "ENABLED"): False})

    exported = export_component_release_knobs(config, index)

    assert exported[component_config_key("grafana", "ENABLED")] == "false"
    assert exported[component_config_key("grafana", "VERSION")] == versions.chart_version("grafana"), \
        "the half nobody typed lost the answer that used to sit in the manifest and now sits in the file"
    assert os.environ.get(component_config_key("grafana", "VERSION")) is None, \
        "the answer that shipped was written into the environment it is handed out of"


@pytest.mark.parametrize("component", sorted(live_index()))
def test_both_variables_the_declared_knob_and_the_shipped_answer_arrive_for_every_release(tmp_path,
                                                                                          monkeypatch,
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


def test_the_image_tag_the_file_pairs_with_a_chart_version_is_exported_beside_it(tmp_path, monkeypatch):
    """Concourse owns a chart version and an appVersion tag as one fact, and both leave in one dict.

    The tag is not a layer knob -- there is no `CONCOURSE_IMAGE_TAG` key `Config` reads -- so what
    this row proves is the export rule for it: one owner (`versions.yaml`), one variable per apply.
    """
    index = live_index()
    clean(monkeypatch, knob_keys(index))
    config = seed(tmp_path, environment_level={}, team_level={})

    exported = export_component_release_knobs(config, index)

    assert exported[component_config_key("concourse", "IMAGE_TAG")] == versions.chart_image_tag("concourse")
    assert exported[component_config_key("concourse", "VERSION")] == versions.chart_version("concourse")
    image_tags = [key for key in exported if key.endswith("_IMAGE_TAG")]
    assert image_tags == [component_config_key("concourse", "IMAGE_TAG")], \
        "releases that own no image tag of their own had one exported anyway: %s" % sorted(image_tags)
