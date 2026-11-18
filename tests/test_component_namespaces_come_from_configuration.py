"""Where a component's namespace comes from, and what happens when nothing says.

The answer used to be spread over the code: four `namespace = '...'` lines in
`automation.py`, one `# TODO: make this a config` each, and a list of fifteen in
`uninstall_kubementat.sh`. Renaming one namespace meant finding all of them, and the only
live evidence was a lane assert that came back red with an empty list rather than a
"wrong namespace" message. So the rule this file holds: `static.json` answers, `constants`
falls back, and nothing else gets an opinion.

The branch that used to be broken is the missing key: the code had no missing branch,
because the value was never a variable. Reading it means choosing what happens when the
config is silent, and the choice on purpose is *say so, then use the default* — a config
tree initialized before these keys existed must keep working, and a default that arrives
quietly is how a rename stops being visible.
"""

import json
import logging
import re
import types
from pathlib import Path

import pytest

from cluster_fixture import configuration_only_context
from kubementat.infrastructure import helm as helm_utils
from kubementat.core import constants
from kubementat.automations.tunnels import tunnels
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key
from kubementat.infrastructure.helm import HelmUtils
from asset_paths import values_path

ENVIRONMENT = "testenv"
TEAM = "testteam"

REPO_ROOT = Path(__file__).resolve().parent.parent


# The component every tunnel command resolves, and what it expected before the
# namespace was a config value. (`polaris` is off this list since row 25 retired its tunnel
# command; the namespace and selector it used are still configuration, still read by the
# team-account roles that keep them.)
TUNNEL_COMPONENTS = [
    ("open_tunnel_grafana", "grafana", "grafana"),
    ("open_tunnel_tekton_dashboard", "tekton", "tekton-pipelines"),
    ("open_tunnel_concourse", "concourse", "concourse"),
    ("open_tunnel_openbao", "openbao", "openbao"),
]


def write_json(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))


def environment_config(root, env_static):
    """A real `Config` over a seeded `platform_config`, so nothing is read from the checkout."""
    config_root = root / "platform_config"
    write_json(config_root / ENVIRONMENT / "static.json", env_static)
    write_json(config_root / ENVIRONMENT / "static.encrypted.json", {})
    write_json(config_root / ENVIRONMENT / TEAM / "static.json", {})
    write_json(config_root / ENVIRONMENT / TEAM / "static.encrypted.json", {})
    # `platform_config_dir` is named because nothing may be located from `root` alone.
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


class RecordingKubernetesUtils:
    """Whatever a tunnel needs from the cluster, recorded instead of executed."""

    def __init__(self):
        self.selectors = []
        self.tunnels = []

    def get_pod_name_for_deployment(self, namespace, selector):
        self.selectors.append((namespace, selector))
        return "the-pod"

    def open_tunnel(self, namespace, pod_name, local_port, remote_port, host):
        self.tunnels.append((namespace, pod_name, local_port, remote_port, host))
        return "tunnel opened"


def tunnel_automation(root, env_static, monkeypatch):
    """A `Context` whose only cluster collaborator is a recording client.

    The tunnels build the client from the name the slice imported, which is the seam there is no
    longer a `self.kubernetes_utils` to hand over.
    """
    context = types.SimpleNamespace(environment=ENVIRONMENT, team=TEAM,
                                    config=environment_config(root, env_static))
    context.kubernetes_utils = RecordingKubernetesUtils()
    monkeypatch.setattr(tunnels, "KubernetesUtils", lambda ctx: context.kubernetes_utils)
    return context


# --- the key, and what it is derived from ----------------------------------------------


def test_the_configured_namespace_wins_over_the_default(tmp_path, caplog):
    """The whole ticket in one assertion: config answers, code does not."""
    with caplog.at_level(logging.WARNING):
        config = environment_config(tmp_path, {"GRAFANA_DEPLOYMENT_NAMESPACE": "observability"})

        assert config.component_namespace("grafana") == "observability"

    assert caplog.text == "", "an explicit value must not warn about the default"


def test_the_default_is_reported_once_per_config_not_per_read(tmp_path, caplog):
    """Five tunnel commands read the same key; the warning is about the key, not per call."""
    config = environment_config(tmp_path, {})

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            config.component_namespace("grafana")

    assert caplog.text.count("GRAFANA_DEPLOYMENT_NAMESPACE") == 1


def test_an_empty_value_does_not_count_as_an_answer(tmp_path, caplog):
    """`{"GRAFANA_DEPLOYMENT_NAMESPACE": ""}` is a half-typed edit, not a decision.

    Handing `""` to the Kubernetes client would fail as a namespace error somewhere else;
    the file that needs the value has to be named here.
    """
    config = environment_config(tmp_path, {"GRAFANA_DEPLOYMENT_NAMESPACE": "   "})

    with caplog.at_level(logging.WARNING):
        assert config.component_namespace("grafana") == "grafana"

    assert "GRAFANA_DEPLOYMENT_NAMESPACE" in caplog.text


# --- the tunnels, which is where the product reads it -----------------------------------


@pytest.mark.parametrize("function,component,default_namespace", TUNNEL_COMPONENTS)
def test_a_tunnel_uses_the_namespace_the_config_names(tmp_path, monkeypatch, function, component,
                                                      default_namespace):
    """The tunnel commands used to carry their own namespace, so they kept using 'grafana'.

    The lane's rename feature is what caught this, and all it could report was "the tunnel did not
    follow the config", because there was no config to follow.
    """
    configured = "kmt-e2e-renamed"
    automation = tunnel_automation(tmp_path, {
        component_config_key(component, "DEPLOYMENT_NAMESPACE"): configured,
        component_config_key(component, "SERVICE_SELECTOR"): "app=renamed",
    }, monkeypatch)

    getattr(tunnels, function)(automation, local_port=1234, remote_port=5678, host="127.0.0.1")

    assert automation.kubernetes_utils.tunnels == [(configured, "the-pod", 1234, 5678, "127.0.0.1")]
    # ...and the selector came from the same place, so the tunnel cannot land on a pod of a
    # deployment that was renamed out from under it.
    assert automation.kubernetes_utils.selectors == [(configured, "app=renamed")]


# --- the deploy side, where the release actually lands ----------------------------------------

# `Config.component_namespace` answers where a component *is*, and `constants` is the fallback.
# The other half of that question is where a release *goes*, which is decided by a template
# helmfile evaluates rather than by Python, and it is the half that never moved: eleven releases
# had their namespaces written out, so renaming a key renamed everything that reads it and left
# the release alone. Two facts are held here, and each only once: what `helmfile_apply` hands the
# process environment, and what the template does with what it finds there.
HELMFILE_TEMPLATE = values_path("helmfile.yaml.gotmpl")


# `<key>` is what the export writes, `<default>` is `constants`' value for that component.
NAMESPACE_FROM_THE_ENVIRONMENT = re.compile(
    r'^\s*namespace:\s*\{\{\s*env\s+"([A-Z][A-Z_]*)"\s*\|\s*default\s+"([a-z0-9][a-z0-9-]*)"\s*\}\}\s*$')


def exported_namespaces():
    """The `<KEY>: namespace` pairs the export writes -- one per component in the component set."""
    return {component_config_key(component, "DEPLOYMENT_NAMESPACE"): value
            for component, value in sorted(constants.DEFAULT_NAMESPACES.items())}


def component_for_key(key):
    """The component a `<COMPONENT>_DEPLOYMENT_NAMESPACE` key names, the other way round."""
    return key[: -len("_DEPLOYMENT_NAMESPACE")].lower().replace("_", "-")


def template_release_namespaces():
    """`{release name: the namespace line}` for every release the template actually declares.

    Commented releases are not releases, so a commented `namespace:` line is a note about a chart
    nobody installs and must not be read as one.
    """
    releases, current = {}, None
    for line in HELMFILE_TEMPLATE.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        declared = re.match(r"^- name: (\S+)\s*$", line)
        if declared:
            current = declared.group(1)
            continue
        if current is not None and re.match(r"^  namespace:", line):
            releases[current] = line
            current = None
    return releases


class RecordingHelmfile:
    """`shell.run` for helmfile: records argv *and* the environment the child was handed.

    What the child is given is the only thing that reaches a template, so that is what is recorded.
    The recorder used to read `os.environ` inside `run`, which was the same fact while the apply
    wrote its exports into the process -- and is a fact about the test's own interpreter the moment
    the exports travel as an argument. Reading `kwargs["env"]` keeps the ordering claim intact: a
    value that is not in the mapping was not in the child either, no matter when it was decided.

    The cluster reads an apply makes on the way to the apply answer as a clean cluster, and that
    is not a shortcut: until the shipped manifest existed on the fixture's tree, nothing there asked
    the cluster. Now that the manifest is the shipped one, the release index reads and `helm list` is
    one of the calls this recorder stands in for -- and a cluster with no
    releases is the same state the fixture was in, so it is the honest answer rather than a new
    assumption. A test that wants a release on the cluster writes its own recorder.
    """

    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        argv = list(argv)
        if argv and argv[0] != "helmfile":
            return types.SimpleNamespace(returncode=0, ok=True, argv=argv, stdout="[]", stderr="")
        exported = set(exported_namespaces())
        environment = kwargs.get("env") or {}
        self.calls.append({
            "argv": argv,
            "namespaces": {key: environment[key] for key in sorted(exported) if key in environment},
            "inherited_from_the_process": environment.get("PATH") is not None,
        })
        return types.SimpleNamespace(returncode=0, ok=True, argv=argv, stdout="", stderr="")


def helmfile_over(tmp_path, env_static, monkeypatch):
    """`HelmUtils` over a seeded `platform_config`, with helmfile replaced by a recorder.

    The same seeded tree the tunnel asserts use, so the namespace is read from
    `platform_config/{environment}/static.json` here exactly where a tunnel reads it.
    """
    config_root = tmp_path / "platform_config"
    write_json(config_root / ENVIRONMENT / "static.json", env_static)
    # HelmUtils hands the Grafana pair to helmfile the same way it hands the namespaces, and
    # helmfile's template `requiredEnv`s both of them.
    write_json(config_root / ENVIRONMENT / "static.encrypted.json",
               {"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "secret"})
    write_json(config_root / ENVIRONMENT / TEAM / "static.json", {})
    write_json(config_root / ENVIRONMENT / TEAM / "static.encrypted.json", {})

    config = Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(tmp_path),
                    platform_config_dir=str(tmp_path / "platform_config"))
    # The class takes one `Context`, and the `Config` seeded above is inside it. The patch this
    # replaces (`monkeypatch.setattr(helm_utils, "Config", ...)`) existed only so the class would
    # read this tree instead of the checkout's; handing the object in says the same thing without a
    # module-level lie.
    recorder = RecordingHelmfile()
    monkeypatch.setattr(helm_utils, "shell", recorder)
    # The variables this fixture cares about must not be sitting in the process environment for the
    # test, so a child that sees them can only have been handed them.
    for key in exported_namespaces():
        monkeypatch.delenv(key, raising=False)
    return HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, config)), recorder


def test_a_namespace_key_the_config_does_not_declare_costs_nothing(tmp_path, monkeypatch):
    """The rule that decides the mechanism: helmfile gets the key it is handed, absence breaks nothing.

    `requiredEnv` would have failed `helmfile apply` on any tree written before a key existed --
    and on every run that does not go through `HelmUtils`, including `helmfile` by hand. So a
    key that is not there is exported at its default instead of being absent, and the template
    carries the same default a second time for that hand-run case.
    """
    utils, journal = helmfile_over(tmp_path, {"ENVIRONMENT": ENVIRONMENT}, monkeypatch)

    utils.helmfile_apply(environment_name=ENVIRONMENT, helmfile_label_filter="group=standard")

    started = journal.calls[-1]["namespaces"]
    assert started == exported_namespaces()
    assert all(value.strip() for value in started.values()), \
        "an empty namespace is a cluster error somewhere else; it is not what helmfile gets"


def test_the_default_written_into_the_template_is_the_default_the_code_holds():
    """Two copies of a fallback, agreeing: the template's is only there for helmfile run by hand.

    The template is evaluated without `HelmUtils` when `helmfile` is invoked directly, and then
    its own default is the only thing standing between a missing key and nothing. If the two
    defaults disagree, the same config answers two different questions depending on who ran it.
    """
    disagreeing = {}
    for release, line in template_release_namespaces().items():
        declared = NAMESPACE_FROM_THE_ENVIRONMENT.match(line)
        if declared is None:
            continue
        default = constants.DEFAULT_NAMESPACES.get(component_for_key(declared.group(1)))
        if default != declared.group(2):
            disagreeing[release] = (declared.group(2), default)

    assert disagreeing == {}, "the template and constants.py disagree (release: (template, code))"


def test_a_release_namespace_can_never_be_the_reason_an_apply_stops():
    """`requiredEnv` is the one shape that turns a missing key into a failed install.

    The template does use it, for the Grafana pair, because a release without an admin password
    is not a thing kubementat should deploy. No namespace is allowed to work that way: eleven
    releases, eight of them reading one of the fifteen keys the export writes, and a config tree
    that predates any of them must still install.
    """
    asked_required = sorted(
        line for line in HELMFILE_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if "requiredEnv" in line)
    assert asked_required, "the Grafana pair is the only thing this file may insist on"
    assert not [line for line in asked_required if "namespace" in line], \
        "a namespace has to be defaulted, never insisted on"
