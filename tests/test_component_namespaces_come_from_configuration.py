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

import ast
import json
import logging
import os
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
from asset_paths import template_path, values_path

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


@pytest.mark.parametrize("component,key", [
    ("grafana", "GRAFANA_DEPLOYMENT_NAMESPACE"),
    ("cert-manager", "CERT_MANAGER_DEPLOYMENT_NAMESPACE"),
    ("tekton-dashboard", "TEKTON_DASHBOARD_DEPLOYMENT_NAMESPACE"),
    ("cluster-cronjobs", "CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE"),
])
def test_the_config_key_is_derived_from_the_component_name(component, key):
    """Nothing may spell a key out per call site, or the two drift apart.

    'tekton' -> TEKTON_DEPLOYMENT_NAMESPACE while its default namespace is
    'tekton-pipelines' is the point: the component is named, the namespace is where it
    happens to live.
    """
    assert component_config_key(component, "DEPLOYMENT_NAMESPACE") == key


def test_the_configured_namespace_wins_over_the_default(tmp_path, caplog):
    """The whole ticket in one assertion: config answers, code does not."""
    with caplog.at_level(logging.WARNING):
        config = environment_config(tmp_path, {"GRAFANA_DEPLOYMENT_NAMESPACE": "observability"})

        assert config.component_namespace("grafana") == "observability"

    assert caplog.text == "", "an explicit value must not warn about the default"


def test_the_built_in_default_is_used_when_the_key_is_absent(tmp_path):
    """A config tree written before these keys existed still resolves.

    This is the branch that did not exist before: there was nothing to fall back
    from, because the value *was* the code.
    """
    config = environment_config(tmp_path, {"ENVIRONMENT": ENVIRONMENT})

    assert config.component_namespace("grafana") == constants.DEFAULT_NAMESPACES["grafana"]
    assert config.component_namespace("tekton") == "tekton-pipelines"


def test_falling_back_to_the_default_says_which_key_and_which_file(tmp_path, caplog):
    """Silence is the failure mode being prevented: a default has to be visible, and acted on.

    'The namespace defaulted' is not actionable. 'GRAFANA_DEPLOYMENT_NAMESPACE is not set in
    .../static.json' is, and it names the one file that has to change.
    """
    config = environment_config(tmp_path, {})

    with caplog.at_level(logging.WARNING):
        namespace = config.component_namespace("grafana")

    assert namespace == "grafana"
    assert "GRAFANA_DEPLOYMENT_NAMESPACE" in caplog.text
    assert str(tmp_path / "platform_config" / ENVIRONMENT / "static.json") in caplog.text


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


def test_an_unknown_component_is_refused_rather_than_defaulted(tmp_path):
    """A component name typo has to fail where it was made.

    Defaulting it would mean deploying into a namespace named after the typo, which is the
    same class of bug, just in the other direction.
    """
    config = environment_config(tmp_path, {})

    with pytest.raises(KeyError) as excinfo:
        config.component_namespace("grafanana")

    message = str(excinfo.value)
    assert "constants" in message
    assert "static.json" in message
    assert "grafanana" in message
    # The template is package data, so the message names it as what this kmt ships and not
    # as a path under a checkout. That distinction is what this assert is for: the sentence used to
    # read `templates/environment/static.json.template`, which is a direction a wheel-installed kmt
    # cannot follow, and it is the same sentence class as the trigger generator's.
    assert "templates/environment/" not in message, \
        "the message sends a human to a directory the checkout no longer carries"


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


@pytest.mark.parametrize("function,component,default_namespace", TUNNEL_COMPONENTS)
def test_a_tunnel_falls_back_to_the_same_default_as_everything_else(
        tmp_path, monkeypatch, function, component, default_namespace):
    """No tunnel keeps a private namespace for the config that predates these keys."""
    automation = tunnel_automation(tmp_path, {}, monkeypatch)

    getattr(tunnels, function)(automation)

    assert automation.kubernetes_utils.tunnels[0][0] == default_namespace


def test_the_service_selector_defaults_match_the_selectors_the_tunnels_used(tmp_path, monkeypatch):
    """Same trick, second value: the selector was string-interpolated from the component.

    It happens to be right for grafana and wrong to keep deriving in code: a helm release
    name is not the namespace's business either.
    """
    automation = tunnel_automation(tmp_path, {}, monkeypatch)

    tunnels.open_tunnel_grafana(automation)

    selectors = [selector for _, selector in automation.kubernetes_utils.selectors]
    assert selectors == [
        "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana",
    ]


# --- the deploy side, where the release actually lands ----------------------------------------

# `Config.component_namespace` answers where a component *is*, and `constants` is the fallback.
# The other half of that question is where a release *goes*, which is decided by a template
# helmfile evaluates rather than by Python, and it is the half that never moved: eleven releases
# had their namespaces written out, so renaming a key renamed everything that reads it and left
# the release alone. Two facts are held here, and each only once: what `helmfile_apply` hands the
# process environment, and what the template does with what it finds there.
HELMFILE_TEMPLATE = values_path("helmfile.yaml.gotmpl")

# The releases whose namespace the template still writes out as a literal, which is none of them.
# An empty table is the finding, not an oversight: the three git-provider releases that needed a
# literal were retired as kubementat components on 2026-10-29 and took their namespaces with them,
# so every release the template declares reads its namespace from the variable `helmfile_apply`
# exports. The reasoning that made the table honest outlives those releases, so it stays stated:
# a literal is the right shape for a release whose component `constants` does not name, because
# then `Config.component_namespace` refuses rather than guesses, there is no
# `<COMPONENT>_DEPLOYMENT_NAMESPACE` for it in `static.json.template`, and `kmt uninstall` does not
# sweep it. Inventing the keys to close that gap is how `CERT_MANAGER_DEPLOYMENT_NAMESPACE` came
# to be read by nobody and survive a real uninstall, and a row for a release that is no longer in
# the file is the same lie pointing the other way. So: empty is honest today, and an entry belongs
# here only with the release that justifies it next to it.
RELEASE_NAMESPACES_STILL_WRITTEN_OUT = {}

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


@pytest.mark.parametrize("key", sorted(exported_namespaces()))
def test_helmfile_starts_with_every_component_namespace_already_exported(tmp_path, monkeypatch, key):
    """What helmfile is told is what `Config` says, for every component, at the moment it runs.

    One variable per component, derived from the component set rather than listed here: a
    component that is added to `constants` is exported without a second edit, and one nobody
    named cannot be exported under a name somebody guessed.
    """
    utils, journal = helmfile_over(tmp_path, {"ENVIRONMENT": ENVIRONMENT}, monkeypatch)

    utils.helmfile_apply(environment_name=ENVIRONMENT, helmfile_label_filter="group=standard")

    started = journal.calls[-1]["namespaces"]
    assert started[key] == constants.DEFAULT_NAMESPACES[component_for_key(key)]


def test_a_renamed_namespace_key_is_the_namespace_helmfile_is_asked_to_use(tmp_path, monkeypatch):
    """One line of the mechanism: the key is what helmfile is handed, in this process, now.

    What that does to a release is a second question and it has two answers, which is why this is
    not written as "the rename moves the release". A component with no release anywhere lands in the
    renamed namespace; a component that already has one is refused, because a deployed release's
    namespace is convention and not something a config edit moves
    (`tests/test_a_deployed_release_is_not_moved_by_its_configuration.py` is that half). What is
    exported here is the *ask*, and both answers read it.
    """
    utils, journal = helmfile_over(tmp_path, {"GRAFANA_DEPLOYMENT_NAMESPACE": "kmt-e2e-renamed"},
                                   monkeypatch)

    utils.helmfile_apply(environment_name=ENVIRONMENT, helmfile_label_filter="group=standard")

    started = journal.calls[-1]["namespaces"]
    assert started["GRAFANA_DEPLOYMENT_NAMESPACE"] == "kmt-e2e-renamed"
    assert started["PROMETHEUS_DEPLOYMENT_NAMESPACE"] == "prometheus", \
        "one key renamed renames that namespace, not the ones next to it"


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


def test_the_template_reads_every_release_namespace_from_the_variable_the_export_writes():
    """A template that ignores the export is a red assert waiting for the next rename.

    Reading the file is not ceremony: the failure this guards is a namespace that is configurable
    in `src/` and still a string in the template, which is the defect the lane has been red on.
    """
    config_driven = {}
    for release, line in template_release_namespaces().items():
        # No release is written out today, so this branch only fires if a literal comes back --
        # which is allowed, but only with a row here saying which release and why.
        if release in RELEASE_NAMESPACES_STILL_WRITTEN_OUT:
            assert line == f"  namespace: {RELEASE_NAMESPACES_STILL_WRITTEN_OUT[release]}", \
                f"{release} is written out for a stated reason; the reason is a change, not a drift"
            continue
        declared = NAMESPACE_FROM_THE_ENVIRONMENT.match(line)
        assert declared, (f"{release}: a release namespace has to be read from the exported "
                          f"variable, got {line!r}")
        config_driven[release] = declared.group(1)

    assert config_driven, "no release is configurable is not the finding anyone expects here"
    for release, key in sorted(config_driven.items()):
        assert key == component_config_key(component_for_key(key), "DEPLOYMENT_NAMESPACE"), \
            f"{release} reads {key}, which no component's name produces"
    assert set(config_driven.values()) <= set(exported_namespaces()), \
        "a variable the template reads and nobody exports is a release that always deploys to its default"


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
# What a name is allowed to declare that its value is, when the string it holds is one of ours.
#
# One reason for the whole table, and it is Traefik's: it is the only component `constants` names
# whose component name, whose chart name and whose default namespace are all the same string.
# `tekton` is a component and `tekton-pipelines` is where it happens to land, so for every other
# component "this value is one of our namespaces" was evidence enough of a namespace binding. For
# `traefik` that same evidence says nothing, and two constants that are correct where they sit were
# being reported as the defect the guard exists to find.
#
# Two halves decide it, and only the second one is new. The match is on the *end* of the name, at
# a word boundary, so an identifier has to declare itself rather than merely contain a word:
# `TRAEFIK_PROXY_CHART_NAME` ends with `CHART_NAME`, while `TRAEFIK_COMPONENT_NAMESPACE` contains
# `COMPONENT` and ends with `NAMESPACE` and is a namespace binding. And the value has to be the
# thing claimed -- `COMPONENT = "traefik"` is a component, `COMPONENT = "tekton-pipelines"` is a
# namespace wearing a component's name. An entry that only listed its suffix would be the same
# blank cheque a substring heuristic is; the predicate is what makes it a claim that can be wrong.
#
# A row is added by writing down the collision that forced it, and
# `test_an_exception_that_no_line_claims_is_dropped` is what takes a stale one back out.
DECLARED_BINDINGS = (
    ("COMPONENT",
     "the component name a config key is derived from, so it is the name that is looked up and "
     "not the namespace it resolves to (traefik_presence.py, read through `component_config_key`)",
     lambda value: value in constants.DEFAULT_NAMESPACES),
    ("CHART_NAME",
     "the chart half of the `<name>-<version>` string a `helm list` row reports in its `chart` "
     "field, compared against what the cluster is running (automation.py, "
     "`_release_runs_traefik_proxy`)",
     lambda value: value in chart_names_constants_names()),
)
# Every line below is a namespace binding, and every one of them has to come out of the guard as
# an offender. This is the narrowing's counter-proof: the exception table excuses two names, and
# nothing on this list is close enough to either name to be excused by accident. The first four are
# the literals `automation.py` carried with a `# TODO: make this a config` above them -- the values
# named by the plan's exit criterion, which is the reason this guard exists at all.
NAMESPACE_BINDINGS_STILL_CAUGHT = [
    ("namespace = 'grafana'",
     "one of the four literals automation.py had, verbatim"),
    ("namespace = 'tekton-pipelines'",
     "one of the four, and the one where the component name and the namespace differ"),
    ("namespace = 'polaris'",
     "one of the four"),
    ("namespace = 'openbao'",
     "the secret-management namespace, and the counter-proof that the guard still reads values "
     "out of `constants` rather than out of a remembered list: `vault` was one of the four "
     "literals `automation.py` carried, and it left with the component, so it is no longer an "
     "offender and no longer belongs on this list"),
    ('namespace = "traefik"',
     "the newest namespace in the set, written under the name that binds a namespace"),
    ('TRAEFIK_NAMESPACE = "traefik"',
     "the collision case itself: the name says NAMESPACE, so the Traefik in front of it is not a "
     "declaration of anything"),
    ('NAMESPACE = "cert-manager"',
     "a bare NAMESPACE has no claim in it, which is the whole test"),
    ('TRAEFIK_COMPONENT_NAMESPACE = "traefik"',
     "the name contains COMPONENT and ends with NAMESPACE; the table matches endings and never "
     "substrings, so this is a namespace binding with a component's word in the middle of it"),
    ('COMPONENT = "tekton-pipelines"',
     "the name claims a component and the value says otherwise: no component is named "
     "tekton-pipelines, so it is a namespace wearing a component's name"),
    ('GRAFANA_CHART_NAME = "grafana"',
     "the name claims a chart and no chart kubementat installs is named grafana, so it is a "
     "namespace wearing a chart's name"),
    ('selector = "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana"',
     "a selector is bound by the same rule, and nothing in this name declares it is not ours"),
]


def test_every_default_namespace_is_a_key_the_template_ships():
    """The two places that say the same thing have to agree, in both directions.

    `constants.py` is what a config tree gets when it is silent; if the template ships a
    different value for the same key, a re-initialized environment moves a namespace with no
    warning.

    The other direction is the one that hurt before: `uninstall_kubementat.sh` read fourteen
    keys and missed `CERT_MANAGER_DEPLOYMENT_NAMESPACE` and `NGINX_INGRESS_DEPLOYMENT_NAMESPACE`
    -- keys the shipped config held and no code read, so both releases outlived a real uninstall.
    A namespace key has to be a component kubementat names, because that component set is what
    `HelmUtils` exports and what the uninstall sweeps. A key outside it is config nobody reads.
    The second of those two keys is gone from the template, because the controller it named is not
    a component any more; the rule that took it out is the same rule that kept `cert-manager` in.
    """
    template = json.loads((template_path("environment", "static.json.template")).read_text())
    template_namespaces = {
        key: value for key, value in template.items() if key.endswith("_DEPLOYMENT_NAMESPACE")
    }

    disagreeing = {
        component_config_key(component, "DEPLOYMENT_NAMESPACE"):
            (value, template_namespaces.get(component_config_key(component, "DEPLOYMENT_NAMESPACE")))
        for component, value in constants.DEFAULT_NAMESPACES.items()
        if template_namespaces.get(component_config_key(component, "DEPLOYMENT_NAMESPACE")) != value
    }

    assert disagreeing == {}, "constants.py and the template disagree (key: (default, template))"

    named_by_nothing = sorted(
        key for key in template_namespaces
        if component_for_key(key) not in constants.DEFAULT_NAMESPACES)
    assert named_by_nothing == [], \
        "a namespace key no component owns is one nothing exports, deploys or sweeps"


def test_the_template_declares_a_selector_for_every_component_the_code_can_ask_for():
    """A key the code reads has to exist, and a key that exists but is never read is dead config."""
    template = json.loads((template_path("environment", "static.json.template")).read_text())

    missing = [
        component_config_key(component, "SERVICE_SELECTOR")
        for component in constants.DEFAULT_SERVICE_SELECTORS
        if component_config_key(component, "SERVICE_SELECTOR") not in template
    ]

    assert missing == []