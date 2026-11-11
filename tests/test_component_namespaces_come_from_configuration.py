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
from kubementat import constants, helm_utils
from kubementat.automation import Automation
from kubementat.config import Config, component_config_key
from kubementat.helm_utils import HelmUtils
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


def tunnel_automation(root, env_static):
    automation = Automation.__new__(Automation)
    automation.environment = ENVIRONMENT
    automation.team = TEAM
    automation.config = environment_config(root, env_static)
    automation.kubernetes_utils = RecordingKubernetesUtils()
    return automation


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
def test_a_tunnel_uses_the_namespace_the_config_names(tmp_path, function, component, default_namespace):
    """The tunnel commands used to carry their own namespace, so they kept using 'grafana'.

    The lane's rename feature is what caught this, and all it could report was "the tunnel did not
    follow the config", because there was no config to follow.
    """
    configured = "kmt-e2e-renamed"
    automation = tunnel_automation(tmp_path, {
        component_config_key(component, "DEPLOYMENT_NAMESPACE"): configured,
        component_config_key(component, "SERVICE_SELECTOR"): "app=renamed",
    })

    getattr(automation, function)(local_port=1234, remote_port=5678, host="127.0.0.1")

    assert automation.kubernetes_utils.tunnels == [(configured, "the-pod", 1234, 5678, "127.0.0.1")]
    # ...and the selector came from the same place, so the tunnel cannot land on a pod of a
    # deployment that was renamed out from under it.
    assert automation.kubernetes_utils.selectors == [(configured, "app=renamed")]


@pytest.mark.parametrize("function,component,default_namespace", TUNNEL_COMPONENTS)
def test_a_tunnel_falls_back_to_the_same_default_as_everything_else(
        tmp_path, function, component, default_namespace):
    """No tunnel keeps a private namespace for the config that predates these keys."""
    automation = tunnel_automation(tmp_path, {})

    getattr(automation, function)()

    assert automation.kubernetes_utils.tunnels[0][0] == default_namespace


def test_the_service_selector_defaults_match_the_selectors_the_tunnels_used(tmp_path):
    """Same trick, second value: the selector was string-interpolated from the component.

    It happens to be right for grafana and wrong to keep deriving in code: a helm release
    name is not the namespace's business either.
    """
    automation = tunnel_automation(tmp_path, {})

    automation.open_tunnel_grafana()

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
    """`shell.run` for helmfile: records argv *and* what the environment held when it started.

    The order is the fact being tested. helmfile reads its variables from the environment of the
    process it starts, so anything the export does after the command is launched is invisible to
    it -- which is also the only way a unit test can tell "exported before apply" apart from
    "exported at some point during it".

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
        self.calls.append({
            "argv": argv,
            "namespaces": {key: os.environ[key] for key in sorted(exported) if key in os.environ},
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
    # The export writes into the process environment; `monkeypatch` puts it back, so a test that
    # runs before does not decide what a test that runs after thinks the cluster looks like.
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


# --- nothing else gets to answer the same question ---------------------------------------


def test_no_module_under_src_binds_a_namespace_to_a_literal():
    """`src/` may not hold a namespace value, and the lane assert that used to say so is this.

    Checked over the whole tree rather than four known lines: the grep in the plan's exit
    criterion names the four values it happened to have seen.

    The check reads the value *and* the name it is written under, which is the difference
    between catching the old literals and flagging two correct Traefik constants. The exception
    table below says which names are exempt and why; every case that table would happily excuse
    but must not is listed in `NAMESPACE_BINDINGS_STILL_CAUGHT`.
    """
    offenders = [f"{path.relative_to(REPO_ROOT)}: {line.strip()}"
                 for path, line, (name, value) in kubementat_namespace_bindings()
                 if declared_not_a_namespace(name, value) is None]

    assert offenders == []


def kubementat_namespace_bindings():
    """Every line in `src/` that assigns a string literal whose value is one of ours.

    Yields `(path, line, (name, value))` for lines where the *value* is a namespace or a selector
    kubementat answers with. Whether such a line is an offender depends on the name, so this is
    the evidence and not the verdict.
    """
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            bound = _namespace_literal_binding(line)
            if bound is not None and namespace_or_selector_value(bound[1]):
                yield path, line, bound


def namespace_or_selector_value(value):
    """True for a string that is one of kubementat's namespace/selector answers.

    `constants` is the one place those are written down, so a value equal to one of them is
    either binding a namespace or naming something that happens to share the string -- and which
    of the two is a question about the name, not about the value.
    """
    return value in set(constants.DEFAULT_NAMESPACES.values()) or value in set(
        constants.DEFAULT_SERVICE_SELECTORS.values())


def _namespace_literal_binding(line):
    """`(name, value)` for a line assigning a plain string literal, else `None`.

    The shape is deliberately this narrow: a simple `NAME = "..."` assignment on its own line.
    Everything the old code did with namespaces was this shape, and widening it (a trailing
    comment, a typed assignment, a dict entry) is a change to what the guard catches, which is
    not this file's business.
    """
    assignment = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([\"'])([^\"']+)\2\s*$", line)
    if assignment is None:
        return None
    return assignment.group(1), assignment.group(3)


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


def chart_names_constants_names():
    """The chart names kubementat names, read back out of the `*_CHART` constants.

    `constants.TRAEFIK_CHART` is `traefik/traefik`, so the chart is named `traefik` -- the same
    string as the namespace it deploys into, which is the collision in its second form. A chart
    name no constant declares is not exempt: the suffix alone cannot earn the exception, so a
    chart that is also a namespace has to be written down in `constants` before `src/` may hold a
    constant named after it.
    """
    return {
        value.rsplit("/", 1)[1] if "/" in value else value
        for name, value in vars(constants).items()
        if name.endswith("_CHART") and isinstance(value, str)
    }


def declared_not_a_namespace(name, value):
    """The `DECLARED_BINDINGS` row that excuses this name/value pair, or `None`.

    Case is not a declaration: `component` and `COMPONENT` say the same thing, and what the guard
    reads is the word at the end of the identifier plus what the value actually is.
    """
    identifier = name.upper()
    for row in DECLARED_BINDINGS:
        if (identifier == row[0] or identifier.endswith("_" + row[0])) and row[2](value):
            return row
    return None


def _namespace_literal_assignment(line):
    """True for `NAME = 'grafana'`, where the value is one of kubementat's namespaces.

    Both halves have to hold: the value is a namespace or selector kubementat answers with, *and*
    the name does not declare itself to be one of the two things Traefik's name is shared with.
    Dropping either half alone turns this into a guard that either misses the old literals or
    flags correct code.
    """
    bound = _namespace_literal_binding(line)
    if bound is None:
        return False
    name, value = bound
    return namespace_or_selector_value(value) and declared_not_a_namespace(name, value) is None


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


@pytest.mark.parametrize("line,why", NAMESPACE_BINDINGS_STILL_CAUGHT,
                         ids=[line.split("=")[0].strip().lower() for line, _ in
                              NAMESPACE_BINDINGS_STILL_CAUGHT])
def test_the_exception_table_exempts_nothing_it_should(line, why):
    """The narrowing is a rule about two names, not a relaxation of the check.

    If the exception were a substring heuristic, or an exception keyed on the value `traefik`, or
    an exception earned by the suffix alone, `TRAEFIK_COMPONENT_NAMESPACE`, `COMPONENT =
    \"tekton-pipelines\"` and `GRAFANA_CHART_NAME = \"grafana\"` would all be reported as clean
    code. They are not clean code; they are the reason the exception has a predicate.
    """
    assert _namespace_literal_assignment(line), f"not caught: {line} -- {why}"


def test_a_component_name_and_a_chart_name_are_not_namespace_bindings():
    """The two constants that broke the guard while it had no exceptions.

    Both hold the string `traefik`, both hold it for a reason that has nothing to do with a
    namespace, and neither is anywhere near the code that decides where a release lands. The
    component name is an input to `component_config_key`; the chart name is compared against the
    `chart` field of a cluster read. Neither is a namespace binding, and renaming them to satisfy
    a text check would move a fact that is load-bearing in its own module for a reason that lives
    in a test.
    """
    for line in ('COMPONENT = "traefik"', 'TRAEFIK_PROXY_CHART_NAME = "traefik"'):
        assert not _namespace_literal_assignment(line), line


def test_the_exempted_lines_are_exempt_for_their_names_and_not_their_values():
    """What the exception is actually worth: it travels with the name, not with the string.

    Read over `src/` rather than over the two literals, so the claim is about the tree. For each
    line the table excuses, the same value under a name that claims nothing must still be an
    offender -- which is the difference between an exception that two constants earned and an
    exception keyed on `"traefik"`, which is the same thing as not checking the value at all and
    would mean the next `namespace = "traefik"` in `src/` reports clean.
    """
    exempted = [(path, line, name, value)
                for path, line, (name, value) in kubementat_namespace_bindings()
                if declared_not_a_namespace(name, value) is not None]
    assert exempted, \
        "nothing in src/ is exempt any more: the collision was renamed, and the table should " \
        "lose the row it was written for"

    for path, line, name, value in exempted:
        same_value_other_name = f'namespace = "{value}"'
        assert _namespace_literal_assignment(same_value_other_name), \
            f"{path.relative_to(REPO_ROOT)}: {line.strip()} is exempt, but the same string under " \
            "a name that claims nothing reads clean too, so what is being excused is the value " \
            "and not the name"


def test_an_exception_that_no_line_claims_is_dropped():
    """An exception list grows by one row per collision and shrinks by nothing, until it is tested.

    A row that excuses no line in `src/` is not protection, it is an assumption that has outlived
    the constant it was written for. Delete the row and the collision is handled by the code that
    caused it -- a rename, or a name that says what it is.
    """
    claimed = {row[0] for _, line, (name, value) in kubementat_namespace_bindings()
               for row in [declared_not_a_namespace(name, value)] if row is not None}

    stale = [row[0] for row in DECLARED_BINDINGS if row[0] not in claimed]
    assert stale == [], f"exempting nothing in src/: {stale}"


def test_every_entry_in_the_exception_table_says_what_it_excuses():
    """A row names the thing it exempts, not just the letters at the end of its name.

    Two rows and two collisions is the whole table; a third would be a third collision, and the
    plan for this guard is that it be readable rather than exhaustive.
    """
    assert len(DECLARED_BINDINGS) <= 3, \
        "the table is short on purpose: a fourth row needs the collision written down, not " \
        "another suffix that sounds harmless"
    for suffix, what, _predicate in DECLARED_BINDINGS:
        assert what.strip() and what.strip().lower() != suffix.lower(), \
            f"{suffix}: the entry describes itself and not what it excuses"
        assert suffix.isupper() and suffix.isidentifier(), \
            f"{suffix}: the table matches the end of an identifier, so a row is one upper-case " \
            "word-run and not a phrase that might accidentally fit"


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


def test_the_shell_side_reads_namespaces_from_static_json_rather_than_holding_their_own():
    """The cleanup-cronjob script carried its own copy of the namespace, and is now gone.

    It was the last place `src/` could have inherited a namespace constant from: bash said
    `CRONJOB_NAMESPACE="cluster-cronjobs"` while install and uninstall read
    `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE`, so a renamed namespace left the job behind. What
    replaced it asks the config, so the guard is that the script stayed deleted and that the
    module which does the work now has no answer of its own — the same rule
    `test_no_module_under_src_binds_a_namespace_to_a_literal` enforces over all of `src/`, stated
    for the one path that had a private copy.
    """
    relative = "scripts/automation/tekton/setup_tekton_pipelinerun_cleanup_job.sh"
    assert not (REPO_ROOT / relative).exists(), \
        "both implementations would mean two answers, and only one of them is executed"

    source = (REPO_ROOT / "src/kubementat/tekton_utils.py").read_text(encoding="utf-8")

    offenders = [line.strip() for line in source.splitlines() if _namespace_literal_assignment(line)]
    assert offenders == [], "the replacement may not have inherited the script's constant"
    assert "component_namespace" in source, \
        "the namespace has to come from the same accessor install and uninstall read"


def test_the_python_uninstall_has_no_namespace_list_of_its_own():
    """The bash half is gone, so the Python half must not have inherited its list.

    The script read fourteen keys out of `static.json` and missed `cert-manager`, whose release then
    outlived a real uninstall, and never read `nginx-ingress` at all -- which is not a component
    kubementat installs any more, and is swept by a step that reads the cluster instead of this
    table. Sweeping namespaces is now
    one expression over `constants.DEFAULT_NAMESPACES`, read through the same accessor the
    tunnels use, so there is exactly one component set in the tree and the configuration is the
    layer above it. What that buys on a cluster is asserted one namespace at a time in
    `test_uninstall_sweeps_everything_kubementat_owns.py`; this only says the answer is not
    written down twice.
    """
    source = (REPO_ROOT / "src/kubementat/automation.py").read_text(encoding="utf-8")
    # `namespace_sweep` is the walk; `namespaces_kubementat_owns` is its list view. The guard has
    # to read the function that decides, which is why this name is spelled out and not guessed at.
    sweep = _function_source(source, "namespace_sweep")

    assert "constants.DEFAULT_NAMESPACES" in sweep, "the component set is constants', not this function's"
    assert "component_namespace" in sweep, "a namespace comes from the config, with constants as the fallback"
    assert not (REPO_ROOT / "uninstall_kubementat.sh").exists(), \
        "both implementations would mean two answers, and only one of them is executed"

    offenders = [f"src/kubementat/automation.py: {line.strip()}"
                 for line in source.splitlines()
                 if _namespace_literal_assignment(line)]
    assert offenders == []


def test_the_e2e_harness_has_no_opinion_about_the_cleanup_job_namespace():
    """The suite looked in `<team>-pipelines` while the job lived in `cluster-cronjobs`.

    Both sides reported an empty list and it looked like a product bug. The harness reads the
    same key now, so a namespace that moves moves the assert along with the product.
    """
    stages = (REPO_ROOT / "tests/e2e/harness/stages.py").read_text(encoding="utf-8")
    cleanup_job = _function_source(stages, "feature_cleanup_job")

    assert "CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE" in cleanup_job
    assert '"%s-pipelines" % config.TEAM' not in cleanup_job


def test_the_e2e_harness_renames_the_file_the_product_reads():
    """A rename test that patches a file nobody reads proves that the harness can edit files.

    It used to patch the team-level `static.json` and report the tunnel as broken, which was
    the harness being right about nothing.
    """
    rename = _function_source(
        (REPO_ROOT / "tests/e2e/harness/stages.py").read_text(encoding="utf-8"),
        "feature_namespace_rename",
    )

    assert "config.TEAM" not in rename, "the rename has to target the environment-level config"
    assert "config.ENVIRONMENT" in rename


def _function_source(source, name):
    """The body of one function, so an assert can say which one it looked at."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"no function named {name} in the harness")
