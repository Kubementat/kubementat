"""The class the routing release installs is the class the configuration answers -- measured, not restated.

Two files used to answer one question, and the one that mattered answered second. `constants.py` (as
L0) and the layers of `platform_config` answered `TRAEFIK_INGRESS_CLASS` for every
Python reader, and `helmfile.yaml.gotmpl` answered it again for the traefik release with
`env "TRAEFIK_INGRESS_CLASS" | default "traefik"`. Measured at the HEAD before this ticket, nothing in
`src/` put that variable into the environment helmfile evaluates its templates in --
`_export_component_namespaces` exports the namespaces and `export_component_release_knobs` exports the
two release knobs, and neither touches this key -- so `env` found nothing, the `default` fired on
every run, and a tree that declared `public` got a release configured with `traefik` while the same
cluster's generated Ingress and the cert-manager solver named `public`. That is not a stale value: it
is an answer that never reached the thing it is about.

So this file measures three things, in the order a reviewer will read them:

* **the manifest names no class of its own.** A template that can answer for itself is a second owner
  that fires exactly when the export breaks, which is the failure that looks like success. Asserted by
  reading the file, because "the file says no default" is a fact about the file.
* **what the operator wrote reaches the built release** -- and that is asserted by running helmfile
  over a tree that declares `public`, not by asserting that the export ran. If the release helmfile
  builds does not say `public`, the export is decoration, which is the falsification the ticket asks
  for and the reason this test is the ticket's whole value.
* **a tree that declares nothing still gets `traefik`,** through L0, because the export does not skip
  absent keys. That is the opposite of `export_component_release_knobs`, which is right to export
  nothing for an undeclared knob: here the answer exists even when no layer wrote it, and an export
  that copied that skip would fail for the common tree.

The last pair of tests is the failure with no fallback in it. With no answer anywhere, the honest
sentence is "helmfile's own failure" -- measured here, it is a build that renders an *empty* class
name and not a silent `traefik`, which is what a class nothing routes to looks like from the outside.
Nothing in `src/` codes a fallback for that case: `Config.component_ingress_class` refuses a component
the table stopped answering for, and the apply never reaches helmfile.
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
    """A real `Config` over a seeded tree -- the four-file shape the layering tests use.

    No literal for the answer appears here: `environment_level` is what the operator wrote, and a tree
    built by this helper with nothing declared is a `config init` tree, which is the state that has to
    keep working.
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


def built_class(**exported) -> list:
    """Every `ingressClass.name` the **built** traefik release carries, with `exported` in the environment.

    This is helmfile's own answer and not a re-reading of the gotmpl: `helmfile build` evaluates the
    file in the environment it is handed and prints the state it will apply, which is the only place
    "what the release says" and "what the template says" are the same sentence.

    The three configuration coordinates are popped, as `test_the_manifest_tells_helmfile_which_releases_are_installed.py`
    does, so a run from a shell that is carrying somebody's tree builds the shipped manifest.
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
    """Every `{{ ... }}` in the manifest that reads this key, with the surrounding line dropped.

    Line-scoped on purpose: the release block carries a prose comment about the removed `default`, and
    a guard that scanned the whole file for the word would be a guard against documentation.
    """
    found = []
    for line in text.splitlines():
        for expression in re.findall(r"\{\{([^}]*)\}\}", line):
            if KEY in expression:
                found.append(expression)
    return found


# --- what the manifest says --------------------------------------------------------------------


def test_the_manifest_names_no_ingress_class_of_its_own():
    """No `default` for this key may survive in the file, and the reason is the failure it causes.

    One `default` in a template is a second owner that is silent while it agrees and loud when it does
    not: the value it hands out comes from the file and not from any layer, so an export that stops
    working -- a renamed component, a `KeyError`, a `Config` that is not the one the command was given
    -- is a run that installs `traefik` and says nothing about having made that decision. If the
    variable is missing the honest answer is that it is missing, which is why no `default`, no
    `requiredEnv`, no second table and no fallback in this file replaces it.
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
    """The other half, and the half the copy-everything refactoring would have broken.

    A `config init` tree declares nothing, so L0 answers (`config show` agrees: the key is tagged
    `default` and names no file), and the export still exports. `export_component_release_knobs` skips a
    key that answers `None` and is right to, because an undeclared knob belongs to the manifest's own
    `default`; copying that skip here is how this ticket's fix would be undone, because for this key
    "the layer said nothing" is not the same fact as "nothing answers it".
    """
    config = seed(tmp_path)
    monkeypatch.delenv(KEY, raising=False)
    exported = export_ingress_class(config)
    try:
        answer = _answered(config, KEY)
        assert answer.value == constants.DEFAULT_STATIC_ANSWERS[KEY]
        assert answer.origin == "default", f"{KEY} was answered by {answer.origin}, not L0"
        assert answer.source is None, f"it claims a file answered it: {answer.source}"
        assert exported == {KEY: answer.value}, \
            "the export and the report gave two different answers for one key"
        assert built_class(**exported) == [answer.value]
    finally:
        monkeypatch.delenv(KEY, raising=False)


# --- the apply path, which is the only place an export can be forgotten -------------------------


def test_helmfile_apply_hands_the_class_over_before_it_runs_helmfile(tmp_path, monkeypatch):
    """An export that exists but is never called by `helmfile_apply` is an export that never happens.

    The variable is read out of the environment handed to the recorded `shell.run`, which is the
    mapping the child actually evaluates its templates in. So this says two things at once -- the
    answer is in the handover, and it is there before argv is assembled.
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
        """`kubementat.infrastructure.shell.run`, recording what the child was handed and what it could read."""

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
