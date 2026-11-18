"""The manifest kubementat applies is the one the package ships.

Four claims, and they are the ones the whole ticket stands on:

**(a) the paths resolve inside the package.** `helmfile_paths()` answers with the shipped
`helmfile.yaml.gotmpl` and its own directory. Nothing in the answer comes from
`platform_config/`, because the operator's checkout is not part of the question "which manifest is
this applying".

**(b) a stale copy changes nothing.** Plan row 13's bug was a cluster whose hand-edited copy of the
manifest declared releases the installed product did not, with both files read for different
questions. The fix is not a check that catches the divergence and explains it; it is a surface that
cannot construct it. So the test that matters here writes the copy by hand and asserts the run is
indistinguishable from a run without it -- the apply is addressed at the packaged file, and the
release index that decides what is installable reads that same file.

**(c) the values files resolve from the packaged working directory.** helmfile takes a release's
`values:` path relative to the directory of the manifest it was handed, so the directory this
function returns and the files the manifest names have to be the same tree.

**(d) the configuration is named by three exports.** A packaged manifest cannot reach the operator's
tree by path, so `helmfile_apply` exports the root, the environment and the team when one is named.
These are the only new mechanism on the surface, and the claim is about what the *child* sees.

The rest of the file is the guard for (b) at the level of a single release, because that is the shape
the bug had: a label on a release nobody ships.
"""

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
    """`kubementat.infrastructure.shell` reduced to what a helmfile run needs: argv, and the environment it saw.

    The environment is read at call time, because that is the only way to tell "exported before the
    child started" apart from "exported at some point during it" -- helmfile's templating looks at
    the environment of the process, and a test that reads `os.environ` afterwards is reading the
    test's own interpreter.
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
    """A `Config` over a scratch root, with the two environment files helmfile reads.

    `KUBEMENTAT_PLATFORM_CONFIG_DIR` is what names the root, so the root under test is the
    root the command resolves and not this checkout's.
    """
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
    """One `helmfile apply`, with the recorder installed as `kubementat.infrastructure.shell`.

    Every cluster read the apply makes on its way to the apply goes through the same recorder and is
    answered as a cluster holding no releases, which is what this scratch root is: nothing on it has
    been deployed, and the check that refuses a namespace rename asks exactly that question.
    """
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
    """`helmfile_paths()` answers with the packaged manifest and its own directory.

    Both halves have to be inside the asset root, and neither is allowed to be under the
    configuration root -- that is the sentence the ticket exists to make true.
    """
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
    """Two different configuration roots, one manifest.

    `helmfile_paths()` takes nothing from the root, so a second environment, a second root, or the
    same root twice all answer with the same file. This is the read-only half of (b): the root is
    what decides which *values* apply, and nothing else.
    """
    first, _ = seeded_config(tmp_path / "one", monkeypatch)
    second, _ = seeded_config(tmp_path / "two", monkeypatch)

    first_paths = HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, first)).helmfile_paths()
    second_paths = HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, second)).helmfile_paths()

    assert first_paths == second_paths
    assert first_paths[1] == values_path("helmfile.yaml.gotmpl")


# --- (b) the bug, made unconstructible ----------------------------------------------------------


def test_a_stale_copy_left_by_hand_changes_nothing(tmp_path, monkeypatch):
    """The plan-row-13 bug, written by hand into the configuration and read by nothing.

    Both readers of the manifest are asserted: the argv `helmfile_apply` builds, and the release
    index `kmt component install` decides with. A copy that fools one and not the other would still
    be the divergence this ticket was written to end, so a single reader's assertion is not enough.
    """
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
    """Order is the fact: a variable exported after the spawn is a variable helmfile never read.

    The same reason the namespaces are exported the way they are -- helmfile evaluates its templates
    with the environment of the process it starts -- so this is asserted at the moment of the call
    and not afterwards.
    """
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
