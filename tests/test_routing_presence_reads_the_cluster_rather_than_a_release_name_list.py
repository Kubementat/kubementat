"""Discovery reads the cluster, so it survives the cases a name list gets wrong.

This is the exit criterion `traefik_presence.py` had to meet, and the shape of it is a claim about
where the answer lives: a list of release names is a copy of a decision somebody else made on a
cluster, and the three cluster reads are the decision.

What that buys, case by case:

* **A controller installed from plain YAML.** No helm release exists at all -- only an IngressClass
  and pods, which is what `helm list` is blind to. k3s's bundled controller is the other extreme (a
  real release pair in `kube-system`, which the same read does report), and a controller applied by
  a git-ops repo is a third shape. All three answers are found, and none of them by name.
* **A release somebody renamed.** The chart field is what the match is made on, so the release
  called `edge-router` running `traefik-27.0.0` is Traefik, and the release called `traefik`
  running `contour-1.30.1` is not. A name list gets both halves of that sentence wrong.
* **A cluster that says nothing.** No kubeconfig, an API server that refuses, or a cluster old
  enough to have no `IngressClass`: the read fails, the failure is a finding in the report rather
  than a traceback, and the answer is still one of the three classifications.

No cluster is needed to read this file, and none is used: the three reads are answered the way a
cluster answers them, with the same argv, the same exit codes and the same JSON.
"""

import json
import logging

import pytest
from kubernetes.client.exceptions import ApiException

from cluster_fixture import TARGET_DEV
from conftest import as_k8s_object
from kubementat import helm_utils, shell
from kubementat.automations.routing import traefik_presence
from kubementat.cluster import ClusterTarget, Context
from kubementat.config import Config
from kubementat.helm_utils import helm_list_releases_argv

CONFIGURED = "traefik"
TRAEFIK_CHART_VERSION = "traefik-27.0.0"
TRAEFIK_POD_IMAGE = "rancher/mirrored-library-traefik:3.3.2"

# The one release read a probe is allowed to make: the product's own, spelled in
# `helm_utils.helm_list_releases_argv`. Spelled there and compared here is the point -- a probe that
# wrote its own argv would answer "what releases live here" differently from the uninstall sweep
# that reads the same constant, and the tree-wide guard fails this file on exactly that split. It is
# compared *addressed*, because the read is not the whole question: the same argv asked of
# another cluster is a different answer, and an unaddressed one is a bug.
HELM_RELEASE_READ = helm_list_releases_argv(TARGET_DEV)


# ---------------------------------------------------------------------------------------
# the cluster under the reads
# ---------------------------------------------------------------------------------------


def helm_release(name, namespace="kube-system", chart=TRAEFIK_CHART_VERSION, status="deployed",
                 revision="4"):
    """One row of `helm list --all-namespaces --output json`, as helm prints it.

    `chart` is the field the match is made on. Note that it is one string carrying both the chart
    name and its version -- that is what helm prints, and why "chart+version" is one value here.
    """
    return {"name": name, "namespace": namespace, "chart": chart, "status": status,
            "revision": revision, "app_version": "2.11.2"}


def ingress_class(name, namespace="kube-system", controller=traefik_presence.TRAEFIK_CONTROLLER_NAME,
                  with_service=True):
    """One `IngressClass`, on the controller string the chart's own `_helpers.tpl` writes.

    `spec.service` is what lets a cluster-scoped object be traced back to a namespace, which is how
    a class answers "whose controller is this". Without it the class says nothing about where it
    came from, and has to be reported as the cluster-scoped object it is.
    """
    spec = {"controller": controller}
    if with_service:
        spec["service"] = {"name": "traefik", "namespace": namespace}
    return {"apiVersion": "networking.k8s.io/v1", "kind": "IngressClass",
            "metadata": {"name": name}, "spec": spec}


def pod(name, namespace="kube-system", image=TRAEFIK_POD_IMAGE):
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": namespace},
            "spec": {"containers": [{"name": "proxy", "image": image}]}}


class Cluster:
    """The cluster under the three reads, answered over the two channels a read can travel.

    The release read is a command, so this answers it with `run`, the real
    :class:`kubementat.shell.CommandResult`, the same exit codes and the same JSON. The class and
    pod reads are API calls, so this answers them with methods, and the objects they hand back are
    the shape the deserialised models have. That asymmetry is not the fixture being untidy: it is
    what discovery now is, and a fake that answered all three as commands would keep testing a
    module that no longer exists.

    Each answer is an `(answer, payload)` pair. For a command the first is the exit code and for an
    API call it is the status the server refused with, which is the closest thing the two channels
    have in common -- neither is allowed to be a traceback.

    `target` is here because the release read is addressed to something, and a fake with no address
    would let discovery read a machine it was not pointed at.
    """

    target = TARGET_DEV

    def __init__(self, helm=None, classes=None, pods=None):
        self._answers = {
            "helm": helm if helm is not None else (0, []),
            "classes": classes if classes is not None else (0, []),
            "pods": pods if pods is not None else (0, []),
        }
        self.started = []
        self.asked = []

    def run(self, argv, **kwargs):
        """`shell.run`'s contract, checked rather than assumed: argv list, captured, unchecked."""
        assert isinstance(argv, list) and all(isinstance(item, str) for item in argv), \
            f"a cluster read has to be started with an argv list, got {argv!r}"
        assert kwargs.get("capture") is True, "these reads are parsed, so they are captured"
        assert kwargs.get("check") is False, \
            "a cluster that answers nothing is a finding, not an exception"
        self.started.append(list(argv))
        if "helm" not in " ".join(argv):
            raise AssertionError(f"discovery started a command read nobody configured: {argv!r}")
        code, payload = self._answers["helm"]
        return shell.CommandResult(argv, code,
                                   stdout=self._printed(code, payload),
                                   stderr="" if code == 0 else "helm: the cluster said no")

    def list_ingress_classes(self):
        self.asked.append(traefik_presence.PROBE_INGRESS_CLASS)
        return self._objects("classes")

    def list_pods_for_all_namespaces(self):
        self.asked.append(traefik_presence.PROBE_PODS)
        return self._objects("pods")

    def _objects(self, probe):
        """One list of cluster objects, or the refusal that stands in for it.

        `helm` prints an array and the API answers with `items`, which is why the two channels are
        answered differently: a fixture that printed a bare array for a client read would be a
        shape no real cluster produces.
        """
        code, payload = self._answers[probe]
        if code:
            raise _refused(code, probe)
        if isinstance(payload, str):
            raise AssertionError(f"an API read cannot answer with {payload!r}; a refusal is what a"
                                 " cluster does instead")
        return [as_k8s_object(item) for item in payload]

    @staticmethod
    def _printed(code, payload):
        """What the release read prints, in the shape `helm list -o json` prints.

        A payload that is already a string is what a cluster prints when it prints a warning where
        JSON was asked for, and it goes through as the text it is.
        """
        if code != 0 or payload == "":
            return ""
        if isinstance(payload, str):
            return json.dumps(payload)
        return json.dumps(payload)


def _refused(status, probe):
    """The refusal half of an API answer, as the client raises it.

    The fixtures say "this read does not answer" with the number they have always used, and what
    comes back is what a cluster that has no `IngressClass`, or will not say, actually raises.
    """
    return ApiException(status=status, reason=f"{probe}: the cluster said no")


def cluster(helm=(), classes=(), pods=()):
    """A cluster that answers all three reads, from the objects each one is asked about."""
    return Cluster(helm=(0, list(helm)), classes=(0, list(classes)), pods=(0, list(pods)))


def a_context(environment="dev", team="dev1", config=None):
    """The `Context` the CLI would have resolved before discovery ran.

    Built and not resolved: `discover` is told which cluster it is reading and never locates one,
    and the target here is the fixture one whose address cannot be dialed. `config` is only read
    when a caller asks for a namespace it has not resolved.
    """
    return Context(environment=environment, team=team, config=config,
                   target=ClusterTarget(environment=environment, context=environment,
                                        kubeconfig_path=None,
                                        address="https://presence-fixture.invalid:6443",
                                        source="fixture"))


def discover(reads, namespace=CONFIGURED, environment="dev"):
    """The cluster half of discovery, whose namespace is the configuration's answer.

    One fake is handed to `discover` on both channels: `reader` answers the command read and
    `cluster` answers the two API reads, so a test that says "the cluster has these classes" says
    it once. What is not touched is attribution: the namespace stays the configuration's answer,
    and the file that proves that is
    `test_the_namespace_that_decides_whose_a_release_is_comes_from_the_configuration`, which builds
    a real `Config` over a seeded `platform_config` and calls `discover` through it.
    """
    return traefik_presence.discover(a_context(environment=environment), namespace=namespace,
                                     reader=reads.run, cluster=reads)


# ---------------------------------------------------------------------------------------
# the cases a name list gets wrong
# ---------------------------------------------------------------------------------------


def test_a_controller_installed_from_a_manifest_is_found_without_any_helm_release():
    """No release, no name list, still Traefik: the class and the pods are the answer.

    This is the row of the decision table a `helm list` cannot see, which is the reason discovery is
    three reads. What must survive into the report is that it was *not* found by the helm probe --
    that is what makes it somebody else's controller rather than a release kubementat lost track
    of.
    """
    presence = discover(cluster(
        classes=[ingress_class("traefik", namespace="ingress-objects")],
        pods=[pod("traefik-7c9d8-2xkz", namespace="ingress-objects")]))

    assert presence.classification == traefik_presence.CLASSIFICATION_FOREIGN
    assert len(presence.findings) == 1
    finding = presence.findings[0]
    assert finding.namespace == "ingress-objects"
    assert finding.releases == ()
    assert [c.name for c in finding.classes] == ["traefik"]
    assert [p.name for p in finding.pods] == ["traefik-7c9d8-2xkz"]
    assert finding.probes == (traefik_presence.PROBE_INGRESS_CLASS, traefik_presence.PROBE_PODS)
    report = presence.report()
    assert "found by: ingressclass, pods" in report
    assert "ingress-objects" in report


def test_only_the_pods_answer_still_makes_a_controller_a_finding():
    """The narrowest read is still a read: a chart told not to write a class still runs pods.

    No IngressClass and no release is the union discovery has to be, and one pod is enough for
    "there is a controller here" to be a fact rather than a guess.
    """
    presence = discover(cluster(pods=[pod("router-5f6", namespace="edge")]))

    assert presence.classification == traefik_presence.CLASSIFICATION_FOREIGN
    assert presence.findings[0].probes == (traefik_presence.PROBE_PODS,)
    assert "found by: pods" in presence.report()
    assert "router-5f6" in presence.report()


def test_the_chart_is_what_says_a_release_is_traefik_and_not_the_release_name():
    """Both halves of the rename case, in one test, because they are one decision.

    A release called `traefik` that is not Traefik must not be reported as one -- that is the false
    positive that lets a skip hide a controller. And `edge-router`, which is Traefik under another
    name, must be -- that is the false positive that installs a second controller next to one that
    is already running.
    """
    presence = discover(cluster(helm=[
        helm_release("edge-router", namespace="kube-system"),
        helm_release("traefik", namespace="gateway", chart="contour-1.30.1"),
        helm_release("cert-manager", namespace="cert-manager", chart="cert-manager-1.14.4")]))

    found = {release.name: release
             for finding in presence.findings for release in finding.releases}
    assert list(found) == ["edge-router"], "only the chart is Traefik's, whatever the release is called"
    assert found["edge-router"].chart == TRAEFIK_CHART_VERSION
    assert found["edge-router"].namespace == "kube-system"
    assert presence.classification == traefik_presence.CLASSIFICATION_FOREIGN


def test_the_companion_crds_chart_is_not_reported_as_a_controller():
    """`traefik-crds` is deprecated and ships no proxy, so a cluster carrying it runs no controller.

    Matching a prefix would read `traefik-crds-1.18.0` as a match and report a Traefik that cannot
    route anything, which is the wrong answer to give about a cluster.
    """
    presence = discover(cluster(helm=[helm_release("traefik-crds", chart="traefik-crds-1.18.0")]))

    assert presence.findings == ()
    assert presence.classification == traefik_presence.CLASSIFICATION_NONE


def test_the_three_reads_are_the_reads_the_plan_names():
    """What each read *is*, asserted and not assumed.

    Two of them became API calls and one stayed a command, so this asserts both halves: which
    method answered the cluster, and the argv the release read was started with. A read that quietly
    became `kubectl get svc` would still pass every other test in this file, and a class read that
    quietly went back to a `kubectl` with no cluster on it would pass this one -- which is why the
    same three reads are pinned by the guard in
    `test_no_kubectl_is_addressed_by_the_process_any_more.py`.

    The helm read is not the string an earlier note for this verb asked for, and the difference is
    stated rather than left out: that note asks for
    `helm list --all-namespaces --all --output json`, and helm 4 deleted that flag,
    so asking for it exits 1 with `unknown flag: --all` on every cluster and reads as "the cluster
    answered nothing". The deviation is deliberate -- it is the product's one release read,
    `helm_utils.helm_list_releases_argv`, which `automation.py`'s uninstall sweep and its Traefik
    sweep read directly so that a release set is not decided twice -- and it is addressed here
    rather than left to whatever `~/.kube/config` says.
    """
    reads = cluster()

    discover(reads)

    assert reads.started == [HELM_RELEASE_READ]
    assert reads.asked == [traefik_presence.PROBE_INGRESS_CLASS, traefik_presence.PROBE_PODS]
    assert "--all" not in reads.started[0], \
        "helm 4 rejects --all, and a read that exits 1 is the same answer as a silent cluster"
    assert "--kube-context" in reads.started[0], \
        "a release read that is not told which cluster is a read that answers for another one"


# ---------------------------------------------------------------------------------------
# a cluster that will not answer is still answered
# ---------------------------------------------------------------------------------------


def test_a_cluster_that_answers_nothing_is_still_answered(caplog):
    """No kubeconfig, no API server, nothing on the other end: `none`, said honestly.

    Two things have to be true at once, and they are usually the second and third thing anyone
    remembers: the command may not raise, and the report may not dress silence up as an answer.
    `none` is still the classification, because it is what `install` would act on -- and the line
    after it says which of the two kinds of `none` this is.
    """
    silent = Cluster(helm=(1, ""), classes=(1, ""), pods=(1, ""))

    with caplog.at_level(logging.WARNING):
        presence = discover(silent)

    assert presence.classification == traefik_presence.CLASSIFICATION_NONE
    assert presence.answered is False
    assert presence.findings == ()
    report = presence.report()
    assert "no traefik found" in report
    assert "nothing could be read" in report
    for read in (traefik_presence.PROBE_HELM, traefik_presence.PROBE_INGRESS_CLASS,
                 traefik_presence.PROBE_PODS):
        assert read in report
        assert "no answer" in report
    assert "nothing could be read" in caplog.text
    assert len(silent.started) + len(silent.asked) == 3, "three reads, even when all three fail"


def test_a_cluster_old_enough_to_have_no_ingressclass_reports_the_class_and_keeps_reading(caplog):
    """`IngressClass` post-dates some clusters, so that read failing is a finding, not an error.

    The other two reads still answer, and the class probe's failure is stated as a reason rather
    than as an exit code, because "exit 1" is not something a reader can act on.
    """
    old_cluster = Cluster(helm=(0, [helm_release("traefik", namespace=CONFIGURED)]),
                          classes=(1, ""),
                          pods=(0, [pod("traefik-1", namespace=CONFIGURED)]))

    with caplog.at_level(logging.WARNING):
        presence = discover(old_cluster)

    assert presence.classification == traefik_presence.CLASSIFICATION_OURS
    assert presence.answered is True
    report = presence.report()
    assert "ingressclass" in report
    assert "no IngressClass" in report, "the class read reports what a missing IngressClass means"
    assert "IngressClass" in caplog.text
    helm_line = [line for line in report.splitlines() if "helm release list" in line][0]
    assert "3 release" not in helm_line and "1 object" in helm_line, \
        "the read that answered is reported as having answered"


def test_a_read_that_answers_with_something_unreadable_is_a_finding_too(caplog):
    """A cluster is allowed to print a warning where JSON was asked for.

    Same shape as a silent cluster: the classification is still decided from what could be read,
    the report says which read could not be parsed, and no traceback is shown for something the
    operator can fix by looking at their kubeconfig. This one is the *command* read, because that
    is the only read that prints: the two API reads either answer or refuse, and a refusal is
    already a finding in `test_a_cluster_that_answers_nothing_is_still_answered`.
    """
    reads = cluster(classes=[ingress_class("traefik", namespace="kube-system")])
    reads._answers["helm"] = (0, "not-json")

    presence = discover(reads)

    assert presence.classification == traefik_presence.CLASSIFICATION_FOREIGN
    helm = next(probe for probe in presence.probes if probe.probe == traefik_presence.PROBE_HELM)
    assert helm.ok is False
    assert "not the JSON this read expects" in presence.report()
    assert "not-json" in presence.report(), "the report quotes what was actually printed"
    assert "not-json" in caplog.text


# ---------------------------------------------------------------------------------------
# the namespace, which is the whole ours/foreign decision
# ---------------------------------------------------------------------------------------


def seeded_config(root, environment="dev", team="dev1", **environment_values):
    """A real `Config` over a seeded `platform_config`, so nothing reads this checkout's."""
    for path, payload in {
        f"{environment}/static.json": environment_values,
        f"{environment}/static.encrypted.json": {},
        f"{environment}/{team}/static.json": {},
        f"{environment}/{team}/static.encrypted.json": {},
    }.items():
        target = root / "platform_config" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload))
    return Config(environment, team, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def test_the_namespace_that_decides_whose_a_release_is_comes_from_the_configuration(tmp_path):
    """`ours` is an address the configuration names, so a renamed namespace renames the answer.

    The release that used to be kubementat's now looks foreign, and that is the honest result: the
    platform_config says where a component lives, and a module that hardcoded `traefik` would call
    a correct install somebody else's. The read goes through `Config.component_namespace`, the same
    accessor the tunnels and helmfile read it through -- reached here through the `Context`, which
    is the only object that answers "what is the configuration" once.
    """
    reads = cluster(helm=[helm_release("traefik", namespace="routing-ingress"),
                          helm_release("traefik", namespace="traefik")])
    presence = traefik_presence.discover(
        a_context(config=seeded_config(tmp_path, TRAEFIK_DEPLOYMENT_NAMESPACE="routing-ingress")),
        reader=reads.run, cluster=reads)

    assert presence.namespace == "routing-ingress"
    # What was read, and in what order: the namespace came from the configuration, and the
    # findings came from these three reads -- nothing else, and nothing skipped.
    assert reads.started == [HELM_RELEASE_READ], \
        "a configuration-resolved namespace must not cost a read, and must not add one"
    assert reads.asked == [traefik_presence.PROBE_INGRESS_CLASS, traefik_presence.PROBE_PODS]
    assert len(presence.findings) == 2
    assert [finding.namespace for finding in presence.owned_findings()] == ["routing-ingress"]
    assert [finding.namespace for finding in presence.foreign_findings()] == ["traefik"]
    # Ours and foreign together resolve to the more consequential answer, not the friendlier one.
    assert presence.classification == traefik_presence.CLASSIFICATION_FOREIGN
    report = presence.report()
    assert "routing-ingress" in report and "traefik" in report
    assert "TRAEFIK_DEPLOYMENT_NAMESPACE" in report, "the report names the key that answered"


def test_a_release_in_the_configured_namespace_is_kubementats_own_and_nothing_more_is_needed():
    """The friendly case: one release, one namespace, and `ours` says so without hedging.

    Nothing else on the cluster corroborates it -- the class and the pods sit in that same
    namespace beside the release -- so they are the same controller's evidence and not two
    findings, which is exactly the mistake a per-probe report would make.
    """
    presence = discover(cluster(
        helm=[helm_release("traefik", namespace=CONFIGURED)],
        classes=[ingress_class("traefik", namespace=CONFIGURED)],
        pods=[pod("traefik-abc", namespace=CONFIGURED)]))

    assert len(presence.findings) == 1
    assert presence.findings[0].owned is True
    assert presence.classification == traefik_presence.CLASSIFICATION_OURS
    assert set(presence.findings[0].probes) == {traefik_presence.PROBE_HELM,
                                               traefik_presence.PROBE_INGRESS_CLASS,
                                               traefik_presence.PROBE_PODS}


def test_a_cluster_scoped_class_no_service_can_place_is_reported_as_such():
    """A class that names no Service cannot be attributed by namespace, so it says so.

    Attributing it to whatever else is running would be an inference dressed up as a fact, which is
    the kind of report that gets pasted into a bug report and argued about for a week.
    """
    presence = discover(cluster(classes=[ingress_class("traefik", with_service=False)]))

    assert presence.classification == traefik_presence.CLASSIFICATION_FOREIGN
    assert presence.findings[0].namespace is None
    report = presence.report()
    assert "cluster-scoped" in report
    assert "no Service" in report


def test_both_controllers_are_reported_together_and_not_hidden_by_the_winner():
    """The k3s case with kubementat's release alongside it: two controllers, one report.

    Resolving to `foreign` is not the whole requirement; the finding kubementat owns must not be
    dropped from the text because it lost. That is what keeps a second-controller warning from
    being written about a cluster the reader cannot inspect.
    """
    presence = discover(cluster(
        helm=[helm_release("traefik", namespace="kube-system"),
              helm_release("traefik", namespace=CONFIGURED)],
        classes=[ingress_class("traefik", namespace="kube-system"),
                 ingress_class("kubementat", namespace=CONFIGURED)],
        pods=[pod("traefik-abc", namespace="kube-system"),
              pod("traefik-xyz", namespace=CONFIGURED)]))

    assert presence.classification == traefik_presence.CLASSIFICATION_FOREIGN
    assert len(presence.owned_findings()) == 1
    assert len(presence.foreign_findings()) == 1
    report = presence.report()
    assert "finding 1:" in report and "finding 2:" in report
    assert "kubementat's own" in report
    assert "not kubementat's" in report


def test_the_reads_never_write_to_the_cluster():
    """A discovery module that installs is a guard that installs, whatever its name says.

    Checked against the process list rather than against the source: three reads, all `helm list`
    or `kubectl get`, and no `apply`, `install`, `delete`, `create`, `patch` or `upgrade`. Anything
    else on this list is a side effect this ticket does not have.
    """
    reads = cluster(helm=[helm_release("traefik", namespace="kube-system")])

    discover(reads)

    assert all(" get " in " ".join(argv) or " list " in " ".join(argv) for argv in reads.started)
    assert not [word for argv in reads.started for word in argv
                if word in ("apply", "install", "delete", "create", "patch", "upgrade", "diff")]
