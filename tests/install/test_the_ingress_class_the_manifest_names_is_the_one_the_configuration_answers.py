"""The class the routing release installs is the class the configuration answers -- measured, not
restated.
"""

import json
import os
import re
import shutil
import subprocess

import pytest
import yaml
from asset_paths import values_path
from cluster_fixture import DEV_SERVER, write_environment, write_kubeconfig

from kubementat.config import answers as config_answers
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key
from kubementat.infrastructure import helm as helm_utils
from kubementat.infrastructure.helm import HelmUtils, export_ingress_class

KEY = component_config_key(constants.COMPONENT, "INGRESS_CLASS")
RELEASE = "traefik"
MANIFEST = values_path("helmfile.yaml.gotmpl")

ENVIRONMENT = "classtest"
TEAM = "routing-team"

HELMFILE = shutil.which("helmfile")
# `helmfile build` asks the installed helm for its version before it renders anything, so a host with
# helmfile and no helm does not skip this: it fails inside helmfile with `error determining helm
# version`. Both binaries are preconditions, and the guard says so.
helmfile_only = pytest.mark.skipif(HELMFILE is None or shutil.which("helm") is None,
                                   reason="helmfile builds its state through the installed helm, and "
                                           "this host is missing one of the two")

# The value an operator would write, spelled so that nothing in this file can confuse it with the
# default: a build that fell back would come back `traefik` and every assertion below would say so.
DECLARED = "public"


def seed(root, environment_level=None, team_level=None) -> Config:
    """A real `Config` over a seeded tree -- the four-file shape the layering tests use."""
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


def built_class(**exported) -> list:
    """Every `ingressClass.name` the **built** traefik release carries, with `exported` in the
    environment.
    """
    environment = dict(os.environ)
    for name in ("KUBEMENTAT_CONFIG_ROOT", "KUBEMENTAT_ENVIRONMENT", "KUBEMENTAT_TEAM", KEY):
        environment.pop(name, None)
    environment["GRAFANA_ADMIN_USER"] = "admin"
    environment["GRAFANA_ADMIN_PASSWORD"] = "not-a-password"
    for name, value in exported.items():
        environment[name] = str(value)

    result = subprocess.run([HELMFILE, "-f", str(MANIFEST), "build"],
                            cwd=str(values_path()), env=environment,
                            capture_output=True, text=True)
    assert result.returncode == 0, f"`helmfile build` said: {result.stderr.strip()}"
    state = yaml.safe_load(result.stdout)
    releases = {release["name"]: release for release in (state or {}).get("releases", [])}
    assert RELEASE in releases, f"the manifest built no {RELEASE} release: {result.stdout[:400]}"
    found = []
    for entry in releases[RELEASE].get("values") or []:
        if isinstance(entry, dict) and "ingressClass" in entry:
            class_block = entry["ingressClass"]
            assert isinstance(class_block, dict), f"ingressClass is not a mapping: {class_block!r}"
            found.append(class_block.get("name"))
    return found


def template_expressions(text: str) -> list:
    """Every `{{ ... }}` in the manifest that reads this key, with the surrounding line dropped."""
    found = []
    for line in text.splitlines():
        for expression in re.findall(r"\{\{([^}]*)\}\}", line):
            if KEY in expression:
                found.append(expression)
    return found


# --- what the manifest says --------------------------------------------------------------------


def test_the_manifest_names_no_ingress_class_of_its_own():
    """No `default` for this key may survive in the file, and the reason is the failure it causes.
    """
    expressions = template_expressions(MANIFEST.read_text(encoding="utf-8"))

    assert expressions, (f"{MANIFEST} never reads {KEY}, so the release names a class from somewhere "
                         "else and this file's premise is gone")
    second_owners = [expression for expression in expressions if "default" in expression]
    assert not second_owners, (
        f"{KEY} is answered by the manifest itself again: {second_owners}. The configuration answers "
        f"this key and `helm_utils.export_ingress_class` exports it; a `default` here is a second "
        f"owner that fires only when that export breaks.")


# --- what reaches helmfile, measured at helmfile ------------------------------------------------


def _answered(config, key: str):
    """One key's answer as the report gives it, from the same walk every verb uses."""
    return {answer.key: answer
            for answer in config_answers.effective_answers(config.platform_config_dir,
                                                           ENVIRONMENT, TEAM)}[key]
@helmfile_only
def test_a_tree_that_declares_nothing_builds_the_release_l0_answers(tmp_path, monkeypatch):
    """The other half, and the half the copy-everything refactoring would have broken."""
    config = seed(tmp_path)
    monkeypatch.delenv(KEY, raising=False)
    exported = export_ingress_class(config)
    try:
        answer = _answered(config, KEY)
        assert answer.value == constants.DEFAULT_STATIC_ANSWERS[KEY]
        assert answer.origin == "default", f"{KEY} was answered by {answer.origin}, not by kubementat's default"
        assert answer.source is None, f"it claims a file answered it: {answer.source}"
        assert exported == {KEY: answer.value}, \
            "the export and the report gave two different answers for one key"
        assert built_class(**exported) == [answer.value]
    finally:
        monkeypatch.delenv(KEY, raising=False)


# --- the apply path, which is the only place an export can be forgotten -------------------------


def test_helmfile_apply_hands_the_class_over_before_it_runs_helmfile(tmp_path, monkeypatch):
    """An export that exists but is never called by `helmfile_apply` is an export that never happens.
    """
    main_dir = tmp_path / "checkout"
    main_dir.mkdir(parents=True)
    write_environment(main_dir, ENVIRONMENT, context="apply-context",
                      kubeconfig_path=write_kubeconfig(main_dir / "kubeconfig",
                                                       {"apply-context": DEV_SERVER}))
    static = main_dir / "platform_config" / ENVIRONMENT / "static.json"
    declaration = json.loads(static.read_text(encoding="utf-8"))
    declaration[KEY] = DECLARED
    static.write_text(json.dumps(declaration), encoding="utf-8")
    # Grafana's admin pair is read before anything is applied and a missing user refuses the run, so
    # the tree has to carry one; nothing in this file is about grafana.
    (main_dir / "platform_config" / ENVIRONMENT / "static.encrypted.json").write_text(
        json.dumps({"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "not-a-password"}),
        encoding="utf-8")
    monkeypatch.setenv("KUBEMENTAT_HOME", str(main_dir))
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(main_dir / "platform_config"))
    monkeypatch.delenv(KEY, raising=False)
    for name in ("KUBEMENTAT_CONFIG_ROOT", "KUBEMENTAT_ENVIRONMENT", "KUBEMENTAT_TEAM"):
        monkeypatch.delenv(name, raising=False)

    seen = {}

    class RecordingShell:
        """`kubementat.infrastructure.shell.run`, recording what the child was handed and what it could
        read.
        """

        def __init__(self):
            self.calls = []

        def run(self, argv, **kwargs):
            self.calls.append(list(argv))
            if argv and argv[0] == "helmfile":
                seen[KEY] = (kwargs.get("env") or {}).get(KEY)
                return _Completed(argv, "")
            return _Completed(argv, "[]")

    class _Completed:
        def __init__(self, argv, stdout):
            self.argv = list(argv)
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0
            self.ok = True

    recorder = RecordingShell()
    monkeypatch.setattr(helm_utils, "shell", recorder)
    try:
        from kubementat.core.identity.context import Context
        HelmUtils(Context.build(ENVIRONMENT, TEAM)).helmfile_apply(
            ENVIRONMENT, "group=routing", helmfile_working_directory=str(tmp_path),
            helmfile_path=str(MANIFEST))
    finally:
        monkeypatch.delenv(KEY, raising=False)

    assert recorder.calls, "nothing was run, so nothing was exported to"
    assert seen.get(KEY) == DECLARED, (
        f"helmfile was started with {KEY}={seen.get(KEY)!r}; the configuration answers "
        f"{DECLARED!r}, so the release it rendered had no answer to read")


# --- the case nobody coded a fallback for ------------------------------------------------------
