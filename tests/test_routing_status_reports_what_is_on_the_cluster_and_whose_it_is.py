"""What `kmt routing status` says, and why it is worth running before `--update`.

The report is the product, and the reason is a decision the operator has to make:
`kmt routing install` either installs or skips, and `--update` is what makes it overwrite
something. The only honest way to answer those two questions is to read the cluster, so the report
has to carry the facts `install` decides on -- namespace, chart+version, class, whose it is, and
the action the command would take -- and say which of them it could not read.

Two layers are checked here, and they check different things:

* **The report** (`presence.report()`), answered by a fixture cluster through the reader seam
  `discover` exposes. This is where the wording is pinned: a fact the guard acts on has to appear
  in the text, not only on the object.
* **The command** (`kmt routing status <environment>`), run against `tests/shims/` as the fake
  cluster tools and a seeded `platform_config`. This is the layer that proves the CLI prints the
  report rather than paraphrasing it, that it costs exactly the three reads, and that it writes
  nothing -- the claim that makes it safe to run before `--update`, while a cluster is live.

Both layers read a cluster that is not there: nothing in this file needs a machine, and the reads
are answered either in-process (`Cluster`) or by the recorder shims (`fake_binaries`).
"""

import json
from types import SimpleNamespace

from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from cli.commands import routing
from cluster_fixture import TARGET_DEV, stamp_tree, write_kubeconfig
from conftest import as_k8s_object, cluster_state
from kubementat.infrastructure import shell
from kubementat.automations.routing import traefik_presence
from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget
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
    """An `IngressClass` on the controller string the chart's `_helpers.tpl` writes.

    `spec.service` is the half that lets a cluster-scoped object be placed in a namespace, so it
    belongs in the fixture: without it, the report would have to say the class is unattributable.
    """
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
    """A cluster that answers the three reads, over the two channels they arrive on.

    The release read is a command, so this answers it with a real `CommandResult`, `capture=True`
    and `check=False`, the way discovery asks. The class and pod reads are API calls, so this
    answers them with objects and raises what a cluster raises when it has no answer -- a refusal
    that arrives as a return code on one channel and an `ApiException` on the other is the same
    finding on both, and that is the claim this fixture exists to let a test make.
    """

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
    """The report for one fixture cluster, with the namespace the configuration would name.

    The namespace is handed in rather than resolved here on purpose: the file that proves it comes
    from `Config.component_namespace` is
    `test_the_namespace_that_decides_whose_a_release_is_comes_from_the_configuration`, and this
    file's command layer proves it through a real `platform_config` and the shims. What is not
    patched is the reading -- `reader` and `cluster` are the two seams `discover` advertises for
    exactly this, one per channel.
    """
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
    """The `none` branch, and the two facts that make it a decision instead of a shrug.

    `install` would create this, so the report says what it would create and where: the chart from
    `constants`, and the namespace from the configuration. Everything else would be a guess.
    """
    report = read(Cluster()).report()

    assert "no traefik found by any of the three reads" in report
    assert "would install it" in report
    assert "traefik/traefik" in report, "the chart, spelled the way constants spells it"
    assert f"into namespace {CONFIGURED}" in report
    assert "group=routing" in report, "and the group it lands through -- not kmt install"


def test_the_report_of_a_cluster_that_would_not_answer_is_none_said_as_a_guess():
    """The same word, two meanings, and the difference is the whole point.

    A cluster that refuses every read produces the same three classifications as a clean one.
    Nothing may let the report present that as an answer, because `install` would act on it and
    put a second controller beside one it could not see.
    """
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
    """A `platform_config` in a throwaway checkout, so the report's namespace is configuration.

    The report names the key it read; a test that patched the namespace instead would be asserting
    a string that no configuration ever contained. `KUBEMENTAT_HOME` is what makes that read the
    throwaway and not this checkout.

    It also declares a cluster, because `kmt routing status` resolves one before it reads and
    an environment that declares nothing is refused. The address is written into the payload rather
    than left to `$KUBECONFIG`, so the test names every file its answer came out of.
    """
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
    """The three reads, answered by the two fakes the command layer has.

    The release read is a command, so the shims answer it; the other two are client calls, so the
    recorder does. Passing both is not ceremony: a test that answered only the shims would read a
    command and two refusals, and the report it compared against would be describing a cluster that
    is not the one declared here.

    A refused read is written the way each channel refuses: the command exits non-zero and prints to
    stderr, the API raises. Both have to survive into the report, which is the claim this fixture
    pair exists to let a test make.
    """
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
    """`kmt routing status`, invoked the way the operator or the harness invokes it.

    The streams are kept apart because the resolution report goes on stderr before the body
    runs, and what these tests compare is the command's own report — which is the thing discovery
    produced, not the sentence saying which cluster it was read against.

    No `--team`, because the command takes none: it reads the cluster and one namespace out of the
    environment's own `static.json`, which gives it no team to name. A run of this command
    used to pass a team it never used, and that is what made `TEAM: None` possible on the tunnels.
    """
    return CliRunner(mix_stderr=False).invoke(routing.status, [environment])


def test_the_command_prints_the_report_once_and_asks_the_cluster_three_questions(
        tmp_path, monkeypatch, fake_binaries, recorded_api):
    """The command layer of the same answer: the report, the reads, and no side effect.

    Three things in one assertion, because they are the three ways this command could be wrong: it
    could paraphrase discovery (two products giving two answers about one cluster), it could run
    more than the three reads, or it could run one that writes. The report is computed first
    through the same configuration and the same two fakes, and the call log is truncated between the
    two so what follows belongs to the command alone. If the CLI ever re-summarises discovery the
    equality fails; if it reaches for `helm upgrade` or `kubectl apply` the comparison fails, and
    both failures say which.

    The three questions land on two channels, so they are asserted on two channels: the release read
    in the shim log, the class and pod reads in the recorder's. A test that read only the first
    would be told a read-only command ran one command, which is true and beside the point.
    """
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
