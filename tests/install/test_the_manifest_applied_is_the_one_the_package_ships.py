"""The manifest kubementat applies is the one the package ships."""

import json
from pathlib import Path

from asset_paths import asset_path, values_path
from cluster_fixture import configuration_only_context

from kubementat.core.config.config import Config
from kubementat.infrastructure import helm as helm_utils
from kubementat.infrastructure.helm import HelmUtils

ENVIRONMENT = "helmroot"
TEAM = "packaged"

# A release the product does not ship, in the shape `kmt component install` reads. `goldilocks` is
# deliberately a `constants.DEFAULT_NAMESPACES` key with no packaged release: it is the component the
# stale-copy bug used to be able to make installable, so the index is asserted against a name the
# command is allowed to be asked about.
STALE_COPY = """\
releases:
- name: goldilocks
  chart: fairwinds-incubator/goldilocks
  namespace: goldilocks
  labels:
    component_name: goldilocks
    group: standard
"""


class RecordingShell:
    """`kubementat.infrastructure.shell` reduced to what a helmfile run needs: argv, and the
    environment it saw.
    """

    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append({
            "argv": argv,
            "argv0": argv[0] if argv else "",
            "cwd": kwargs.get("cwd"),
            "environment": kwargs.get("env"),
            "helmfile": argv[0] == "helmfile",
        })
        return type("Result", (), {"returncode": 0, "ok": True, "argv": argv,
                                   "stdout": "[]", "stderr": ""})()


def seeded_config(tmp_path, monkeypatch, team=TEAM):
    """A `Config` over a scratch root, with the two environment files helmfile reads."""
    root = tmp_path / "platform_config"
    environment = root / ENVIRONMENT
    environment.mkdir(parents=True, exist_ok=True)
    (environment / "static.json").write_text(json.dumps({
        "ENVIRONMENT": ENVIRONMENT, "CLUSTER_KUBE_CONTEXT": None}), encoding="utf-8")
    (environment / "static.encrypted.json").write_text(json.dumps(
        {"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "not-a-password"}), encoding="utf-8")
    if team is not None:
        team_dir = environment / TEAM
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "static.json").write_text("{}", encoding="utf-8")
        (team_dir / "static.encrypted.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(root))
    return Config(ENVIRONMENT, team, platform_config_dir=str(root)), root


def apply_helmfile(utils, monkeypatch, recorder=None):
    """One `helmfile apply`, with the recorder installed as `kubementat.infrastructure.shell`."""
    recorder = recorder or RecordingShell()
    monkeypatch.setattr(helm_utils, "shell", recorder)
    utils.helmfile_apply(environment_name=ENVIRONMENT,
                         helmfile_label_filter="group=standard")
    return recorder


def helmfile_call(recorder):
    """The one helmfile argv the run produced."""
    return [call for call in recorder.calls if call["helmfile"]][-1]


# --- (a) where the answer comes from ------------------------------------------------------------


def test_the_manifest_and_the_working_directory_are_the_packaged_ones(tmp_path, monkeypatch):
    """`helmfile_paths()` answers with the packaged manifest and its own directory."""
    config, root = seeded_config(tmp_path, monkeypatch)
    working_directory, manifest = HelmUtils(
        configuration_only_context(ENVIRONMENT, TEAM, config)).helmfile_paths()

    assert manifest == values_path("helmfile.yaml.gotmpl"), manifest
    assert working_directory == values_path(), working_directory
    assert manifest.is_file(), "the packaged manifest is not in the package: " + str(manifest)
    assert str(manifest).startswith(str(asset_path("values"))), manifest
    assert str(root) not in str(manifest), \
        "the configuration root is in the answer, which is the old resolution: " + str(manifest)


def test_the_answer_never_changes_when_the_configuration_root_changes(tmp_path, monkeypatch):
    """Two different configuration roots, one manifest."""
    first, _ = seeded_config(tmp_path / "one", monkeypatch)
    second, _ = seeded_config(tmp_path / "two", monkeypatch)

    first_paths = HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, first)).helmfile_paths()
    second_paths = HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, second)).helmfile_paths()

    assert first_paths == second_paths
    assert first_paths[1] == values_path("helmfile.yaml.gotmpl")


# --- (b) the bug, made unconstructible ----------------------------------------------------------


def test_a_stale_copy_left_by_hand_changes_nothing(tmp_path, monkeypatch):
    """A manifest path written into the configuration is not read; the packaged manifest is applied."""
    config, root = seeded_config(tmp_path, monkeypatch)
    utils = HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, config))

    stale = root / ENVIRONMENT / "kubementat_components" / "helmfile.yaml.gotmpl"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(STALE_COPY, encoding="utf-8")

    without_copy = utils.component_release_index()
    call = helmfile_call(apply_helmfile(utils, monkeypatch))

    assert without_copy.get("goldilocks") in (None, []), \
        "the release index read the copy, so a component nobody ships became installable: " \
        f"{without_copy.get('goldilocks')}"
    assert call["argv"][call["argv"].index("-f") + 1] == str(
        values_path("helmfile.yaml.gotmpl").absolute()), call["argv"]
    assert call["cwd"] == str(values_path().absolute()), \
        "helmfile is still run from the copy's directory, which is the other half of the same bug"
    assert str(stale) not in " ".join(call["argv"]), \
        "the copy reached the command line: " + " ".join(call["argv"])


# --- (c) the values the manifest names -----------------------------------------------------------


# --- (d) the exports ------------------------------------------------------------------------------


def test_the_exports_come_before_helmfile_runs(tmp_path, monkeypatch):
    """Order is the fact: a variable exported after the spawn is a variable helmfile never read."""
    config, root = seeded_config(tmp_path, monkeypatch)
    utils = HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, config))
    seen = {}

    class WatchingShell(RecordingShell):
        def run(self, argv, **kwargs):
            if list(argv)[:1] == ["helmfile"]:
                seen.update({name: (kwargs.get("env") or {}).get(name) for name in
                             ("KUBEMENTAT_CONFIG_ROOT", "KUBEMENTAT_ENVIRONMENT", "KUBEMENTAT_TEAM")})
            return super().run(argv, **kwargs)

    apply_helmfile(utils, monkeypatch, WatchingShell())

    assert seen["KUBEMENTAT_ENVIRONMENT"] == ENVIRONMENT, seen
    assert seen["KUBEMENTAT_CONFIG_ROOT"] == str(Path(str(root)).absolute()), seen
    assert seen["KUBEMENTAT_TEAM"] == TEAM, seen
