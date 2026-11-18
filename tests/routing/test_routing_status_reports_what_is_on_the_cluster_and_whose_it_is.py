"""What `kmt routing status` says, and why it is worth running before `--update`."""

import json
from types import SimpleNamespace

from click.testing import CliRunner
from cluster_fixture import TARGET_DEV, stamp_tree, write_kubeconfig
from conftest import as_k8s_object, cluster_state
from kubernetes.client.exceptions import ApiException

from cli.commands import routing
from kubementat.automations.routing import traefik_presence
from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget
from kubementat.infrastructure import shell
from kubementat.infrastructure.helm import helm_list_releases_argv

ENVIRONMENT = "dev"
TEAM = "dev1"

# The cluster the seeded environment addresses. Unreachable by construction, like every fixture
# address in this suite: resolving is a file read, so a green test proves nothing was contacted.
CLUSTER_SERVER = "https://routing-fixture.invalid:6443"

# The namespace kubementat's configuration names for traefik. `constants.DEFAULT_NAMESPACES` holds
# it as the fallback, so a test may say so; what it may not do is hardcode it into the product.
CONFIGURED = "traefik"
K3S_NAMESPACE = "kube-system"
# What `helm list` prints in the chart column: one string carrying both, which is why the report
# quotes "chart traefik-33.2.1" rather than a name and a version it never read separately.
K3S_CHART = "traefik-33.2.1"
TRAEFIK_IMAGE = "rancher/mirrored-library-traefik:3.7.15"


# ---------------------------------------------------------------------------------------
# the fixture cluster, answered in-process
# ---------------------------------------------------------------------------------------


def helm_release(name, namespace=K3S_NAMESPACE, chart=K3S_CHART, status="deployed", revision="7"):
    """One `helm list` row. `chart` carries the version, because that is what helm prints."""
    return {"name": name, "namespace": namespace, "chart": chart, "status": status,
            "revision": revision, "app_version": "3.7.15"}


def ingress_class(name, namespace=K3S_NAMESPACE, with_service=True):
    """An `IngressClass` on the controller string the chart's `_helpers.tpl` writes."""
    spec = {"controller": traefik_presence.TRAEFIK_CONTROLLER_NAME}
    if with_service:
        spec["service"] = {"name": "traefik", "namespace": namespace}
    return {"apiVersion": "networking.k8s.io/v1", "kind": "IngressClass",
            "metadata": {"name": name}, "spec": spec}


def pod(namespace=K3S_NAMESPACE, name="traefik-6f59c4d8c-j2xq8", image=TRAEFIK_IMAGE):
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": namespace},
            "spec": {"containers": [{"name": "proxy", "image": image}]}}


class Cluster:
    """A cluster that answers the three reads, over the two channels they arrive on."""

    target = TARGET_DEV

    def __init__(self, helm=(), classes=(), pods=(), refusing=()):
        self._answers = {
            "helm": (1, "") if "helm" in refusing else (0, list(helm)),
            "classes": (1, "") if "classes" in refusing else (0, list(classes)),
            "pods": (1, "") if "pods" in refusing else (0, list(pods)),
        }
        self.asked = []
        self.started = []

    def run(self, argv, **kwargs):
        assert kwargs.get("capture") is True and kwargs.get("check") is False, \
            "discovery parses these reads, so it captures them and never raises on a refusal"
        self.started.append(list(argv))
        if "helm" not in " ".join(argv):
            raise AssertionError(f"discovery started a command read nobody configured: {argv!r}")
        code, payload = self._answers["helm"]
        if code:
            return shell.CommandResult(argv, code, stdout="",
                                       stderr="helm: the cluster said no")
        return shell.CommandResult(argv, 0, stdout=json.dumps(payload), stderr="")

    def list_ingress_classes(self):
        self.asked.append(traefik_presence.PROBE_INGRESS_CLASS)
        return self._objects("classes")

    def list_pods_for_all_namespaces(self):
        self.asked.append(traefik_presence.PROBE_PODS)
        return self._objects("pods")

    def _objects(self, probe):
        code, payload = self._answers[probe]
        if code:
            raise ApiException(status=503, reason=f"{probe}: the cluster said no")
        return [as_k8s_object(item) for item in payload]


def read(cluster, namespace=CONFIGURED, environment=ENVIRONMENT):
    """The report for one fixture cluster, with the namespace the configuration would name."""
    return traefik_presence.discover(
        Context(environment=environment, team=TEAM, config=None,
                target=ClusterTarget(environment=environment, context=environment,
                                    kubeconfig_path=None,
                                    address="https://routing-fixture.invalid:6443",
                                    source="fixture")),
        namespace=namespace, reader=cluster.run, cluster=cluster)


# ---------------------------------------------------------------------------------------
# what the report says
# ---------------------------------------------------------------------------------------


def test_the_report_says_a_cluster_that_answered_and_found_nothing_would_install_it():
    """The `none` branch, and the two facts that make it a decision instead of a shrug."""
    report = read(Cluster()).report()

    assert "no traefik found by any of the three reads" in report
    assert "would install it" in report
    assert "traefik/traefik" in report, "the chart, spelled the way constants spells it"
    assert f"into namespace {CONFIGURED}" in report
    assert "group=routing" in report, "and the group it lands through -- not kmt install"


def test_the_report_of_a_cluster_that_would_not_answer_is_none_said_as_a_guess():
    """The same word, two meanings, and the difference is the whole point."""
    presence = read(Cluster(refusing=("helm", "classes", "pods")))

    report = presence.report()
    assert presence.classification == traefik_presence.CLASSIFICATION_NONE
    assert presence.answered is False
    assert "none of the three reads answered" in report
    assert "'none' here means nothing could be read" in report
    assert "that is a guess" in report
    assert "no answer" in report


# ---------------------------------------------------------------------------------------
# the action, which is what makes this worth running before --update
# ---------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------
# the command, against the shims and a seeded configuration
# ---------------------------------------------------------------------------------------


def seeded_configuration(tmp_path, monkeypatch, namespace=CONFIGURED):
    """A `platform_config` in a throwaway checkout, so the report's namespace is configuration."""
    root = tmp_path / "checkout"
    root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {"routing": CLUSTER_SERVER})
    for path, payload in {
        f"{ENVIRONMENT}/static.json": {"TRAEFIK_DEPLOYMENT_NAMESPACE": namespace,
                                       "CLUSTER_KUBE_CONTEXT": "routing",
                                       "CLUSTER_KUBECONFIG_PATH": str(kubeconfig)},
        f"{ENVIRONMENT}/static.encrypted.json": {},
        f"{ENVIRONMENT}/{TEAM}/static.json": {},
        f"{ENVIRONMENT}/{TEAM}/static.encrypted.json": {},
    }.items():
        target = root / "platform_config" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload))
    stamp_tree(root)
    monkeypatch.setenv("KUBEMENTAT_HOME", str(root))
    # The root is named, so `kmt routing status` reads the throwaway's `platform_config`
    # rather than one located under `KUBEMENTAT_HOME`.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(root / "platform_config"))
    for name in ("CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH"):
        monkeypatch.delenv(name, raising=False)
    return root


def answering(fake_binaries, recorded_api, helm=(), classes=(), pods=(), refusing=()):
    """The three reads, answered by the two fakes the command layer has."""
    fake_binaries.respond("helm", json.dumps(list(helm)), subcommand="list",
                          exit_code=1 if "helm" in refusing else 0)
    cluster_state(recorded_api, "ingressclass", items=list(classes),
                  error="classes: the cluster said no" if "classes" in refusing else None)
    if "pods" in refusing:
        def refuse(**kwargs):
            raise ApiException(status=503, reason="pods: the cluster said no")

        recorded_api.answer("list_pod_for_all_namespaces", refuse)
    else:
        recorded_api.answer("list_pod_for_all_namespaces",
                            lambda **kwargs: SimpleNamespace(
                                items=[as_k8s_object(item) for item in pods]))
    return fake_binaries


def status(environment=ENVIRONMENT):
    """`kmt routing status`, invoked the way the operator or the harness invokes it."""
    return CliRunner(mix_stderr=False).invoke(routing.status, [environment])


def test_the_command_prints_the_report_once_and_asks_the_cluster_three_questions(
        tmp_path, monkeypatch, fake_binaries, recorded_api):
    """The command layer of the same answer: the report, the reads, and no side effect."""
    seeded_configuration(tmp_path, monkeypatch)
    answering(fake_binaries, recorded_api, helm=[helm_release("traefik", chart=K3S_CHART)],
              classes=[ingress_class("traefik")], pods=[pod()])
    context = Context.build(ENVIRONMENT, TEAM)
    report = traefik_presence.discover(context).report()
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    result = status()

    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout == report + "\n"
    assert [call.argv for call in fake_binaries.take_calls()] == [
        helm_list_releases_argv(context.target)], \
        "a read-only command may not ask the cluster for anything else"
    assert recorded_api.methods() == ["list_ingress_class", "list_pod_for_all_namespaces"], \
        "the two client reads are two of the three, and neither is a command"
    assert "not kubementat's" in result.output and "traefik-33.2.1" in result.output
