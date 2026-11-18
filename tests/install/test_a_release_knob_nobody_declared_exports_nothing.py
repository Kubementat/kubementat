"""The export writes what the operator declared, and only that -- for the releases the manifest names.

`Config` answers a release's two knobs (`<COMPONENT>_ENABLED`, `<COMPONENT>_VERSION`); this is the
half that hands them to helmfile, which reads them out of the process environment because that is
where a template looks. Two decisions live here and both are the kind that fail quietly:

* **Absence exports nothing.** A tree that declares no knob is exported as `{}` and leaves
  `os.environ` exactly as it found it, so the manifest's own `default` answers. Writing a value here
  -- an invented `"true"`, an empty string, "the accessor's answer either way" -- would take that
  answer away from the manifest and hand it to a function that had nothing to say.
* **The set is the manifest's labels, not `constants.DEFAULT_NAMESPACES`.** The two sets do not
  line up, and both directions of the mismatch are bugs: alloy and the two prometheus exporters have
  releases and no namespace key of their own, so exporting off the namespace set leaves them with no
  knob; `vpa`, `goldilocks`, `cluster-cronjobs` and the three tekton keys are namespaces kubementat
  creates and applies no release, so exporting off it writes keys no template line will ever read --
  the silently dead key `_export_component_namespaces` names for the same reason. `tests/` reads as
  an alloy case rather than as a set difference because that is where a reviewer will look.

The exports are global state, so every test here is `monkeypatch`-scoped: a knob left in the process
environment is a knob the next test thinks the cluster was told about.
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
    """A `Config` over a seeded tree, seeded the way the layering tests seed one.

    No third way to build a configuration tree: this is the same four-file shape
    `test_a_key_is_answered_by_the_highest_layer_that_declares_it.py` uses.
    """
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
    """`{component_name label: releases}` for the manifest this suite ships.

    Read and not listed: the whole point of the set is that it comes out of the file helmfile
    applies, so a test that spelled it here would keep passing when the file gained a release.
    """
    return HelmUtils(configuration_only_context(
        ENVIRONMENT, TEAM, Config(ENVIRONMENT, TEAM, platform_config_dir=UNREAD_ROOT,
                                  preload_configs=False))).component_release_index(LIVE_HELMFILE)


def knob_keys(names):
    """Every variable those names' knobs could be spelled as, sorted."""
    return sorted(component_config_key(name, suffix)
                  for name in sorted(names) for suffix in SUFFIXES)


def clean(monkeypatch, keys):
    """Put the process environment where a tree that declares nothing leaves it, for this test only.

    `monkeypatch` restores each variable at teardown, which is what stops an export from outliving
    the test that caused it.
    """
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_a_release_knob_nobody_declared_exports_nothing(tmp_path, monkeypatch):
    """An undeclared knob is not a value to export, and `os.environ` is not a place to guess at.

    `{}` back, nothing written. That is the assertion, and it is the same one `helmfile_apply` runs
    on for every tree that has never edited a knob: the answer the template gives is its own
    `default`, and this function is not the one that gets to improve on it.
    """
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
    """One key at a time. Half a pair still exports, and the half nobody typed stays absent.

    This is what makes `installed` fall back to the manifest while `version` does not: the export is
    per key and not per component, so a tree that renames one knob is not told to keep the other.
    """
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
    """Per release, the pair is exported. A component that lost one of its two lines fails here.

    Parametrised over the index rather than listed, for the same reason the export iterates it.
    """
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
