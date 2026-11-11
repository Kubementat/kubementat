"""What `kmt uninstall` deletes, and how it knows.

The bash that did this until now kept its own answer in two places: fourteen
`*_NAMESPACE` keys read out of `static.json`, and two team names typed into the script. The
configuration declares twelve component namespaces and any number of teams, so the script
was wrong in both directions at once. A full lane measured it (`result.json` of
`20261027T184758Z-full-3e8b3f6`):

    helm releases that survived uninstall: ['cert-manager', 'nginx-ingress']
    snapshots/ns-after-uninstall.json:     still lists both namespaces

Two keys with no reader, and the routing half of what an uninstall owes stayed up. This file is the
argv-level evidence that the Python path asks for them — and for the things the script never asked for
at all: every team's namespaces, the claims it only announced, the Linkerd and the ingress
controller a cluster may still be carrying even though this product stopped installing either of
them, and the CRDs as the cluster names them rather than as nineteen strings.

Traefik joins the set from the other direction: kubementat installs it, so its namespace is
already in the sweep and its release is already found by `helm list`, and what needs owning is
only what Helm never takes back -- the CustomResourceDefinitions its chart applies with the
`crds/` directory, which outlive the release, the namespace and the log line that said the
uninstall was finished.

The cluster-scoped RBAC joins it from the gap the script left: it deleted `helm-deployer-cluster-role`
by name and everything else in that class stayed on the cluster -- the Tekton, Triggers and
Dashboard releases apply theirs with `kubectl apply`, which writes what a manifest declares and not
what kubementat would have liked, and no namespace delete reaches any of it. What identifies those
objects is not a label (upstream declares none kubementat owns) and not a name list (rejected, and
rightly), so it is the object's own subjects and `roleRef` -- both of which only mean something
while the namespaces they point at still exist, which is why that sweep goes first.

What only a real lane can still confirm is written down at the bottom of this file.
"""

import json
import logging
import shutil
from types import SimpleNamespace

import pytest
from click.exceptions import ClickException
from kubernetes.client.exceptions import ApiException

from conftest import as_k8s_object

import kubementat.automation

from kubementat import constants, shell
from kubementat.automation import Automation, UninstallIncompleteError
from cluster_fixture import configuration_only_context
from conftest import cluster_deletes, cluster_state


def shell_result(argv, stdout, code=0, stderr=""):
    """One answer from a tool that is not a shim, so a test can say what a real refusal said.

    The shims hold one static answer per call shape, which is enough while "it worked or it did not"
    is the whole question. Once the difference between an absence and a refusal decides the residue
    list, a test needs a result carrying the server's sentence -- and this is that object, not a
    fixture pretending to be a cluster.
    """
    return shell.CommandResult(argv, code, stdout=stdout, stderr=stderr)

# What `helm list --all-namespaces` says is deployed: the release that survived the last real
# uninstall, a couple of the usual suspects, and one that belongs to the cluster's own add-ons and
# must not be touched. Plus kubementat's router, whose namespace comes from the one table that
# answers for it rather than from a string written here: a checkout that lost the entry says so at
# import time with a `KeyError` naming the component, which is what `kmt uninstall` would do too.
TRAEFIK_NAMESPACE = constants.DEFAULT_NAMESPACES["traefik"]

HELM_RELEASES = [
    {"name": "cert-manager", "namespace": "cert-manager", "status": "deployed",
     "chart": "cert-manager-v1.10.1"},
    # The secret-management release, under the name and namespace kubementat installs today. It is
    # the row that used to be `vault`/`vault-0.27.0`: the sweep finds releases by the namespace the
    # component set declares, so this row has to name the component that is in that set.
    {"name": "openbao", "namespace": "openbao", "status": "deployed", "chart": "openbao-0.30.6"},
    {"name": "coredns", "namespace": "kube-system", "status": "deployed",
     "chart": "rke2-coredns-2.11.0"},
    # Ours, in its own namespace, under the name k3s also calls its bundled controller. Two
    # namespaces, one name: which release goes is decided by the pair, which is what the two
    # router tests are about.
    {"name": "traefik", "namespace": TRAEFIK_NAMESPACE, "status": "deployed",
     "chart": "traefik-42.1.3"},
]

# What `helm list --all-namespaces` prints for every one of those releases, and not only a name,
# because a `chart` is the field the router sweep reads (`Automation._release_runs_traefik_proxy`):
# helm writes it as `<name>-<version>`, so it says which controller the release ran and not what
# anybody chose to call it. The Traefik rows are sourced rather than written -- `42.1.3` is the pin
# this checkout's helmfile carries for kubementat's own release, and k3s's bundled controller is
# `traefik-41.6.x`, of which the row below is one patch -- and the other four are what some chart
# would answer with: none of them is Traefik-shaped, which is the whole of what is asked of them.

# k3s's bundled controller is a pair of real helm releases in `kube-system` -- the cluster's own,
# installed by the node's HelmChart CRs, not by kubementat, and not to be touched by anything here.
# The pair is also the shape of the answer the definition sweep reads before it deletes: the proxy is
# the first row, and the deprecated companion is the second, whose chart is named `traefik-crds` and
# which ships no proxy. They are named apart so a test can hold one of them up on its own, and they
# are not in the fixture's own answer, for the reason the fixture gives.
K3S_TRAEFIK_PROXY_RELEASE = {"name": "traefik", "namespace": "kube-system", "status": "deployed",
                             "chart": "traefik-41.6.1"}
K3S_TRAEFIK_CRDS_RELEASE = {"name": "traefik-crd", "namespace": "kube-system", "status": "deployed",
                            "chart": "traefik-crds-1.3.0"}
K3S_TRAEFIK_RELEASES = [K3S_TRAEFIK_PROXY_RELEASE, K3S_TRAEFIK_CRDS_RELEASE]

# The release a cluster that got the old controller carries, and the only place its real name is
# written down. It is not in `HELM_RELEASES` because a release in a namespace this cluster does not
# have is not reported by a real `helm list`, and because the sweep that finds it is the controller
# step's and not the component sweep's - which is the point of the two controller tests.
INGRESS_CONTROLLER_RELEASE = {"name": "ingress-nginx", "namespace": "nginx-ingress",
                              "status": "deployed", "chart": "ingress-nginx-4.11.3"}

# `kubectl get ingressclass -o json`. The first is what the chart installed, the second is somebody
# else's controller and must survive, the third has no controller field at all. The last is
# kubementat's own router class, on the controller Traefik really writes: it is here so a sweep can
# be shown *not* to have deleted it, because the release created it and `helm uninstall` removes it.
INGRESS_CLASSES = [
    {"metadata": {"name": "nginx"},
     "spec": {"controller": "k8s.io/ingress-nginx", "service": {"name": "nginx-ingress-controller"}}},
    {"metadata": {"name": "other"}, "spec": {"controller": "example.com/other-controller"}},
    {"metadata": {"name": "legacy", "annotations": {"ingressclass.kubernetes.io/is-default-class": "true"}}},
    {"metadata": {"name": "traefik"}, "spec": {"controller": constants.TRAEFIK_CONTROLLER_NAME}},
]

# Read back from `kubectl get crd`. `tektonconfigs.tekton.dev` is the one no name in the old
# script's list covered; `prometheusrules.monitoring.coreos.com` is not kubementat's and is
# expected to outlive the uninstall untouched.
# ... plus what the `traefik/traefik` chart's `crds/` directory installed, one kind from each of
# the two groups `helm show crds traefik/traefik --version 42.1.3` prints (25 definitions on
# 2026-10-28: ten under `traefik.io`, fifteen under `hub.traefik.io`). Two of them are enough to
# say "both groups"; the survivor is what says the sweep is a group match and not a name match.
# `tektonconfigs.tekton.dev` is the one no name in the old script's list covered;
# `prometheusrules.monitoring.coreos.com` is not kubementat's and is expected to outlive the
# uninstall untouched; and cert-manager's six definitions -- of which three are here, one from
# each of its two API groups and one (`orders.acme.cert-manager.io`) that only the wider of the two
# groups would notice -- arrived with a component and leave with one, so they belong to the same
# sweep `tekton.dev` does and not to the "helm never took this back" one Traefik is in.
CRD_ITEMS = [
    {"metadata": {"name": "tasks.tekton.dev"}},
    {"metadata": {"name": "clustertasks.tekton.dev"}},
    {"metadata": {"name": "tektonconfigs.tekton.dev"}},
    {"metadata": {"name": "servers.linkerd.io"}},
    {"metadata": {"name": "middlewares.traefik.io"}},
    {"metadata": {"name": "ingressroutes.traefik.io"}},
    {"metadata": {"name": "accesscontrolpolicies.hub.traefik.io"}},
    {"metadata": {"name": "certificates.cert-manager.io"}},
    {"metadata": {"name": "orders.acme.cert-manager.io"}},
    {"metadata": {"name": "prometheusrules.monitoring.coreos.com"}},
    # What Alloy's dependency chart installs and `helm uninstall` removes. Verified against
    # `grafana/alloy` 1.15.0 on 2026-11-01: the chart depends on a subchart named `crds` whose entire
    # content is `charts/crds/crds/monitoring.grafana.com_podlogs.yaml`, so there is one definition and
    # one group. It is what the full lane found after the loki migration: every sweep reported clean and
    # `assert:no-extra-cluster-scoped:customresourcedefinitions` still read
    # `unexpected-1 (podlogs.monitoring.grafana.com)`.
    {"metadata": {"name": "podlogs.monitoring.grafana.com"}},
]

# An Alloy kubementat did not install, in a namespace kubementat does not own. Its `PodLogs` objects are
# how somebody else collects their logs, and deleting the definition deletes those objects -- which is
# the whole reason this sweep is gated on a cluster read and not run with the ungated ones.
FOREIGN_ALLOY_RELEASE = {"name": "grafana-agent", "namespace": "observability",
                         "status": "deployed", "chart": "alloy-1.14.4"}

# --------------------------------------------------------------- cluster-scoped RBAC
# Everything an install leaves outside every namespace, in the shape the cluster answers for it.
# Two halves, and they are identified two different ways -- which is the whole of what the sweep
# under test has to get right.
#
# The release half is sourced, not imagined: `TektonUtils.install()` `kubectl apply`s the three
# pinned manifests, and the rows below are what those files declared when they were read on
# 2026-10-30 (`pipeline/v1.17.1/release.yaml`, `triggers/v0.37.2/release.yaml`,
# `dashboard/v0.73.0/release-full.yaml`). What is true of all sixteen of the bindings those
# manifests ship is that none of them carries `managed-by`: `kubectl apply` writes what the
# manifest declares, and upstream declares `app.kubernetes.io/part-of`. What *is* true of all
# sixteen is that they bind a ServiceAccount inside `tekton-pipelines`,
# `tekton-pipelines-resolvers` or `tekton-dashboard` -- three namespaces
# `constants.DEFAULT_NAMESPACES` says kubementat owns, and three of the namespaces this command
# is about to delete. That is the fact the sweep reads, so a release that renames its roles is
# still swept and one that renames its namespace is settled by the configuration and not here.
#
# The `ClusterRole` half is deliberately not a copy of the manifest: `tekton-aggregate-view` has no
# binding of its own anywhere upstream and is reached here only through the dashboard binding that
# grants it, which is the `roleRef` rule earning its place and not a name somebody remembered.
RELEASE_RBAC = {
    "bindings": [
        {"metadata": {"name": "tekton-pipelines-controller-cluster-access"},
         "subjects": [{"kind": "ServiceAccount", "name": "tekton-pipelines-controller",
                       "namespace": "tekton-pipelines"}],
         "roleRef": {"kind": "ClusterRole", "name": "tekton-pipelines-controller-cluster-access",
                     "apiGroup": "rbac.authorization.k8s.io"}},
        {"metadata": {"name": "tekton-triggers-controller-admin"},
         "subjects": [{"kind": "ServiceAccount", "name": "tekton-triggers-controller",
                       "namespace": "tekton-pipelines"}],
         "roleRef": {"kind": "ClusterRole", "name": "tekton-triggers-admin",
                     "apiGroup": "rbac.authorization.k8s.io"}},
        {"metadata": {"name": "tekton-dashboard-pipelines-view"},
         "subjects": [{"kind": "ServiceAccount", "name": "tekton-dashboard",
                       "namespace": "tekton-pipelines"}],
         "roleRef": {"kind": "ClusterRole", "name": "tekton-aggregate-view",
                     "apiGroup": "rbac.authorization.k8s.io"}},
        {"metadata": {"name": "tekton-events-controller-cluster-access"},
         "subjects": [{"kind": "ServiceAccount", "name": "tekton-events-controller",
                       "namespace": "tekton-pipelines"}],
         "roleRef": {"kind": "ClusterRole", "name": "tekton-events-controller-cluster-access",
                     "apiGroup": "rbac.authorization.k8s.io"}},
    ],
    "roles": [
        {"metadata": {"name": "tekton-pipelines-controller-cluster-access"}},
        {"metadata": {"name": "tekton-triggers-admin"}},
        {"metadata": {"name": "tekton-aggregate-view"}},
        {"metadata": {"name": "tekton-events-controller-cluster-access"}},
        # Bound to nothing at all: the aggregation layer reads its own labels, and a role no
        # kubementat namespace binds is not what this sweep is for.
        {"metadata": {"name": "tekton-aggregate-edit",
                      "labels": {"rbac.authorization.k8s.io/aggregate-to-edit": "true"}}},
    ],
}

# kubementat's own writes: stamped, as `tekton_utils._trigger_labels` and
# `automation._setup_pipelines_setup_role_bindings` stamp them, and bound wherever the team's
# `static.json` said. The label is what takes them, so a binding that lands in a namespace this
# command owns but is not labelled still goes -- and one that is labelled goes whatever its
# subjects say. The namespace is the fixture's own and not a string, because a sweep that matched a
# hard-coded `dev1` would be the name list this ticket forbids.
def owned_rbac_bindings(fixture_repo):
    trigger_binding_name = ("tekton-triggers-sa-to-tekton-triggers-bindings-and-interceptors-role-binding-"
                            + fixture_repo.team)
    return [{"metadata": {"name": trigger_binding_name,
                          "labels": {"managed-by": "kubementat", "team": fixture_repo.team}},
              "subjects": [{"kind": "ServiceAccount", "name": "tekton-triggers-sa",
                            "namespace": fixture_repo.pipeline_namespace}],
              "roleRef": {"kind": "ClusterRole",
                          "name": "tekton-triggers-bindings-and-interceptors-role",
                          "apiGroup": "rbac.authorization.k8s.io"}},
             {"metadata": {"name": "tekton-cleanup-cluster-role-binding",
                           "labels": {"managed-by": "kubementat"}},
              "subjects": [{"kind": "ServiceAccount", "name": "tekton-cleanup-service-account",
                            "namespace": fixture_repo.pipeline_namespace}],
              "roleRef": {"kind": "ClusterRole", "name": "tekton-cleanup-cluster-role",
                          "apiGroup": "rbac.authorization.k8s.io"}}]


def owned_rbac_roles(fixture_repo):
    """The `ClusterRole`s that same install path wrote, all of them labelled.

    `helm-deployer-cluster-role` is in here on purpose: its binding is a *namespaced* RoleBinding,
    which is invisible to a sweep of cluster-scoped objects, so the label is the only thing that
    reaches it from out here -- and `helm-deployer-cluster-role` is the one name the script already
    knew, so this is where "the one object that gets forgotten" stops being one.
    """
    return [{"metadata": {"name": "tekton-triggers-bindings-and-interceptors-role",
                          "labels": {"managed-by": "kubementat"}}},
            {"metadata": {"name": "tekton-cleanup-cluster-role", "labels": {"managed-by": "kubementat"}}},
            {"metadata": {"name": "helm-deployer-cluster-role", "labels": {"managed-by": "kubementat"}}}]

# What must survive. Three shapes, because the sweep has three ways to be wrong about it: a role
# with no claim and no binding of any kind, a role reached only by a *foreign* namespace's binding,
# and a binding whose subjects are not accounts at all (`cluster-admin`'s are groups, which carry no
# namespace and therefore answer "kubementat's?" with a no that is not a guess).
FOREIGN_RBAC = {
    "roles": [
        {"metadata": {"name": "cluster-admin"}},
        {"metadata": {"name": "prometheus-k8s"}},
        {"metadata": {"name": "gitlab-runner-cluster-admin"}},
    ],
    "bindings": [
        {"metadata": {"name": "gitlab-runner-cluster-access"},
         "subjects": [{"kind": "ServiceAccount", "name": "runner", "namespace": "gitlab-managed"}],
         "roleRef": {"kind": "ClusterRole", "name": "gitlab-runner-cluster-admin",
                     "apiGroup": "rbac.authorization.k8s.io"}},
        {"metadata": {"name": "kubeadm:cluster-admin"},
         "subjects": [{"kind": "Group", "name": "system:masters"}],
         "roleRef": {"kind": "ClusterRole", "name": "cluster-admin",
                     "apiGroup": "rbac.authorization.k8s.io"}},
        {"metadata": {"name": "prometheus-k8s-read"},
         "subjects": [{"kind": "ServiceAccount", "name": "prometheus-k8s",
                       "namespace": "monitoring"}],
         "roleRef": {"kind": "ClusterRole", "name": "prometheus-k8s",
                     "apiGroup": "rbac.authorization.k8s.io"}},
    ],
}


def answering_the_cluster_scoped_rbac(recorded_api, bindings, roles):
    """What the cluster answers about its ClusterRoles and ClusterRoleBindings.

    Written as a helper rather than left to each test body because two of the asserts below read
    the same two answers twice -- once for what went, once for what stayed -- and a fixture that
    only writes one of them would have the sweep read an empty cluster through one hole.
    """
    cluster_state(recorded_api, "clusterrole", list(roles))
    cluster_state(recorded_api, "clusterrolebinding", list(bindings))
    return recorded_api


def swept_rbac_deletes(recorded_api):
    """The cluster-scoped RBAC the *sweep* deleted, as `(kind, name)` pairs.

    Only the objects it decided it owned — which excludes `helm-deployer-cluster-role`, deleted by
    name unconditionally, and would otherwise make every "the sweep claimed nothing" assert read as
    a failure the code never committed. This used to be read out of `kubectl delete ...`
    argv, where the by-name line landed in the same log and the distinction was invisible.
    """
    named = kubementat.automation.Automation.HELM_DEPLOYER_CLUSTER_ROLE
    roles = {("clusterrole", kwargs["name"])
             for method, kwargs in recorded_api.calls
             if method == "delete_cluster_role" and kwargs["name"] != named}
    bindings = {("clusterrolebinding", kwargs["name"])
                for method, kwargs in recorded_api.calls if method == "delete_cluster_role_binding"}
    return roles | bindings


def deleted_rbac(recorded_api):
    """Everything cluster-scoped the run deleted, the sweep and the by-name line together."""
    return swept_rbac_deletes(recorded_api) | {
        ("clusterrole", kubementat.automation.Automation.HELM_DEPLOYER_CLUSTER_ROLE)}


CLUSTER_ADDONS = {"default", "kube-system", "kube-public", "kube-node-lease"}


class NodeList:
    """Whatever `list_node().items` hands back, without importing the generated models twice."""

    def __init__(self, names):
        self.items = [type("Node", (), {"metadata": type("Meta", (), {"name": name})()})
                      for name in names]


class NamespaceList:
    """What `list_namespace().items` hands back, optionally carrying a deletion phase.

    The phase is not decoration: a namespace that was told to delete and has not finished is
    `Terminating`, and reporting it as a bare name is what let a four-second-old observation be
    read as "still there". An unspecified namespace is `Active`, which is what the fake was
    already asserting without saying so.
    """

    def __init__(self, names, phases=None):
        phases = phases or {}
        self.items = [type("Namespace",
                           (), {"metadata": type("Meta", (), {"name": name})(),
                                "status": type("Status", (), {"phase": phases.get(name, "Active")})()})
                      for name in names]





def on_a_cluster(recorded_api, present_namespaces):
    """Point the recording client at a cluster that has exactly these namespaces.

    Deletion is *modelled*, not assumed: the set the cluster reports shrinks when something deletes
    a namespace, because that is what a cluster does -- asynchronously, several seconds later, which
    is exactly why `uninstall` has to poll instead of taking the client's word for it. A fake that
    keeps reporting a namespace it was told to delete is a fake that contradicts the API.
    """
    present = set(present_namespaces)
    recorded_api.answer("list_namespace", lambda **kwargs: NamespaceList(sorted(present)))
    recorded_api.answer("delete_namespace", lambda name=None, **kwargs: present.discard(name))
    recorded_api.answer("list_node", lambda **kwargs: NodeList(["k3s-server-1"]))


def what_helm_list_answers(fake_binaries):
    """The rows `helm list --all-namespaces` is answering with, at this moment of the run.

    The fake's answer is a file the shim cats, so that file is the state, and `respond` writes the
    same path a test body would. Reading it rather than keeping a list here is what lets a test
    overwrite the answer for its own purposes and still have the model keep working underneath.
    """
    listing = fake_binaries.responses / "helm.list"
    return json.loads(listing.read_text()) if listing.exists() else []


def a_helm_that_forgets_what_it_uninstalled(fake_binaries):
    """A `helm list` that stops reporting the releases this run has already taken.

    One uninstall reads the release list twice: once to find the releases to take, and once again
    afterwards to ask whether a Traefik controller is still serving from the definitions the sweep is
    about to delete. An answer that never changes is false at the second reading, because it is still
    carrying the release the run deleted a few calls earlier -- and it is that reading, and nothing
    else, that decides the delete. So a successful `helm uninstall` drops its row out of the answer,
    which is all a cluster does, and a failing one leaves it there to be found again.
    """
    real_run = kubementat.automation.shell.run

    def answering(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if result.ok and argv[:2] == ["helm", "uninstall"]:
            remaining = [row for row in what_helm_list_answers(fake_binaries)
                         if (row.get("name"), row.get("namespace")) != uninstalled_target(argv)]
            fake_binaries.respond("helm", json.dumps(remaining), subcommand="list")
        return result

    return answering


def uninstalled_target(argv):
    """`(release, namespace)` a `helm uninstall` addressed, read out of a line that carries its cluster.

    The line used to be `helm uninstall NAME --namespace NS`, and this harness read the name at index
    two and the namespace at the end. The address sits in between now, and index two is a
    kubeconfig path -- so the fake would keep reporting a release this run had already deleted, the
    Traefik sweep would read a controller still serving from the definitions, and the residue assert
    would say "clean" about a cluster that still had them. Reading it by position is what broke;
    reading it by what the flags mean is the fix, and `namespace_of`/`named_after` are how this file
    already reads every other helm line.
    """
    flags = {"--kubeconfig", "--kube-context", "--context"}
    words = []
    skipping = False
    for word in argv:
        if skipping:
            skipping = False
            continue
        if word in flags:
            skipping = True
            continue
        words.append(word)
    if "uninstall" not in words:
        return None, None
    position = words.index("uninstall") + 1
    namespace = words[words.index("--namespace") + 1] if "--namespace" in words else None
    return (words[position] if position < len(words) else None), namespace


def helm_uninstall_of(commands, name, namespace):
    """The `helm uninstall` lines that addressed *name* in *namespace*.

    Written as a check against the whole command line until the cluster moved into the middle of
    it, which made `["helm", "uninstall", "openbao", "--namespace", "openbao"] in calls` a claim
    about where the flags sit and not about what was uninstalled. The same two facts are read out of
    the line instead, so the assertion still says what it was written to say.
    """
    return [argv for argv in commands
            if argv[:2] == ["helm", "uninstall"]
            and uninstalled_target(argv) == (name, namespace)]


@pytest.fixture
def uninstaller(fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """`kmt uninstall`'s implementation against a fixture cluster that is carrying everything.

    Everything, including the two routing namespaces whose names no reader could find: that is
    the state the lane found the residue in, so it is the state every test here starts from.

    The node's own Traefik is deliberately absent from the release list. Whether it is there is the
    fact the definition sweep reads before it deletes, so the tests that are about it say so where
    they assert it and the rest get a cluster whose only Traefik release is kubementat's own - which
    the release sweep takes, and which therefore is not on the list when the sweep asks.
    """
    present = set(constants.DEFAULT_NAMESPACES.values()) | {
        fixture_repo.pipeline_namespace, fixture_repo.app_namespace} | CLUSTER_ADDONS
    on_a_cluster(recorded_api, present)
    monkeypatch.setattr("kubementat.automation.shell.run", a_helm_that_forgets_what_it_uninstalled(fake_binaries))
    fake_binaries.respond("helm", json.dumps(HELM_RELEASES), subcommand="list")
    cluster_state(recorded_api, "crd", CRD_ITEMS)
    cluster_state(recorded_api, "persistentvolumeclaims", {})
    # IngressClasses answer empty, because that is what the shim's silence used to mean and what
    # the cluster this fixture stands for reports: kubementat does not install a controller, so a
    # default cluster has no class of its own. The tests that want one say so.
    cluster_state(recorded_api, "ingressclass", [])
    return Automation(configuration_only_context(fixture_repo.environment, None))


def namespaces_deleted(recorded_api):
    return [kwargs["name"] for method, kwargs in recorded_api.calls if method == "delete_namespace"]


def recorded(fake_binaries):
    """Everything the shims saw, as calls - and the log is left empty behind them.

    The two helpers below read the same list: `take_calls` is the only way to know which calls
    belonged to this test rather than to the fixture that set the responses up.
    """
    return fake_binaries.take_calls()


def argv_of(calls):
    """The same calls as command lines, because `["helm", "uninstall", ...] in calls` compares
    against a `RecordedCall` and can never be true."""
    return [call.argv for call in calls]


def written(fake_binaries):
    """The calls that were asked to change something, ignoring reads."""
    return [call.argv for call in recorded(fake_binaries)
            if "delete" in call.argv or "uninstall" in call.argv]


def client_writes(recorded_api):
    return [method for method, _kwargs in recorded_api.calls if method.startswith("delete_")]


def deleted_names(recorded_api, method):
    """The names one delete method was asked for, in the order it was asked.

    The sweeps moved off `kubectl delete <kind> <name> --namespace <ns>` and onto one call
    per object, so the name is a parameter and not the last word of an argv. The order survives,
    which is the part the ordering asserts below depend on.
    """
    return [kwargs["name"] for name, kwargs in recorded_api.calls if name == method]


def deleted_claim_names(recorded_api):
    """`[(namespace, name)]` for the claims the run deleted."""
    return [(kwargs["namespace"], kwargs["name"]) for method, kwargs in recorded_api.calls
            if method == "delete_namespaced_persistent_volume_claim"]


def test_every_component_namespace_the_configuration_declares_is_swept(uninstaller, recorded_api,
                                                                       caplog):
    """The measured defect, asserted one namespace at a time.

    `cert-manager` was read by nobody, so it was deleted by nobody. Here every namespace comes from
    `constants.DEFAULT_NAMESPACES` like every other component, read through
    `Config.component_namespace` so a renamed namespace is still honoured. `nginx-ingress` is not on
    this list any more - it is not a component kubementat installs - and the two tests that say so
    are further down.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = set(namespaces_deleted(recorded_api))
    missing = sorted(set(constants.DEFAULT_NAMESPACES.values()) - deleted)
    assert missing == [], f"the configuration declares these and uninstall left them: {missing}"
    for namespace in ("cert-manager", "openbao"):
        assert namespace in deleted, f"{namespace} survived the last uninstall and still must not"


def test_a_namespace_the_configuration_declares_that_the_cluster_does_not_have_is_not_deleted(
        uninstaller, recorded_api, fake_binaries, monkeypatch, caplog):
    """`goldilocks` is configured and never installed; that is a no-op, not a failure.

    The sweep is the intersection of what the configuration declares and what the cluster has.
    Naming objects that are not there is what `kubectl delete ... || true` did to hide whether
    anything had been deleted at all.
    """
    monkeypatch.setitem(constants.DEFAULT_NAMESPACES, "goldilocks", "goldilocks-not-here")
    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert "goldilocks-not-here" not in namespaces_deleted(recorded_api)
    assert any("Never present" in record.message for record in caplog.records)


def test_the_namespaces_of_every_team_are_deleted_not_only_the_ones_someone_wrote_down(
        uninstaller, fixture_repo, recorded_api):
    """`dev1` and `smoke` were two names; `ops` is a team the old script never knew about.

    Its own TODO said this. A team is a directory with a `static.json`, and both of its
    namespaces go whatever their names are.
    """
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "ops"
                            / "static.json",
                            {"PIPELINE_NAMESPACE": "ops-pipelines",
                             "APP_DEPLOYMENT_NAMESPACE": "ops-apps"})
    on_a_cluster(recorded_api, sorted(set(constants.DEFAULT_NAMESPACES.values())
                                      | {fixture_repo.pipeline_namespace,
                                         fixture_repo.app_namespace,
                                         "ops-pipelines", "ops-apps"}
                                      | CLUSTER_ADDONS))
    recorded_api.calls.clear()

    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = namespaces_deleted(recorded_api)
    assert "ops-pipelines" in deleted and "ops-apps" in deleted
    assert fixture_repo.pipeline_namespace in deleted and fixture_repo.app_namespace in deleted


def test_a_team_directory_that_is_not_a_team_does_not_stop_the_teardown(uninstaller, fixture_repo,
                                                                       recorded_api, fake_binaries):
    """A directory under the environment that is not a team used to abort the whole uninstall.

    That is the regression the full lane measured: `dev2` exists because
    `tests/e2e/harness/stages.py` creates it as a config-isolation probe, it declares half a
    namespace list, and one `ClickException` from it stopped `kmt uninstall` from deleting
    anything at all. Both passes exited 1 and six residue asserts went red off the same line.

    A foreign directory is now swept for what it declares and skipped for what it does not. It
    still cannot hold the teardown hostage, which is the only promise a cluster-wide command can
    make about a filesystem it does not own.
    """
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "pandas"
                            / "static.json", {"ENVIRONMENT": "dev"})

    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = set(namespaces_deleted(recorded_api))
    for namespace in ("cert-manager", fixture_repo.pipeline_namespace,
                      fixture_repo.app_namespace):
        assert namespace in deleted, f"{namespace} survived because a directory that is not a team said nothing"
    assert written(fake_binaries), "the sweep stopped before it had deleted anything with a command"


def test_the_namespaces_a_team_directory_does_declare_are_still_swept(uninstaller, fixture_repo,
                                                                     recorded_api):
    """Not a team, but not nothing either: the namespaces it names are kubementat's to delete.

    The harness's probe declares `APP_DEPLOYMENT_NAMESPACE` and no pipeline namespace. Guessing
    the missing half is still forbidden, but the declared half is on the cluster and it belongs to
    this installation, so the sweep takes it and leaves the rest alone.
    """
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "dev2"
                            / "static.json",
                            {"SENTINEL": "kmt-e2e-sentinel",
                             "APP_DEPLOYMENT_NAMESPACE": "dev2-workloads"})
    on_a_cluster(recorded_api, sorted(set(constants.DEFAULT_NAMESPACES.values())
                                      | {fixture_repo.pipeline_namespace, fixture_repo.app_namespace,
                                         "dev2-workloads"} | CLUSTER_ADDONS))
    recorded_api.calls.clear()

    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = namespaces_deleted(recorded_api)
    assert "dev2-workloads" in deleted, "the namespace it did declare was not swept, so the assert cannot tell a skip from a stall"
    assert fixture_repo.app_namespace in deleted and fixture_repo.pipeline_namespace in deleted


def test_a_team_directory_that_names_no_namespace_is_not_swept_and_says_why(
        uninstaller, fixture_repo, recorded_api, caplog):
    """A directory that says nothing deletes nothing, even though the rule over a team's name can
    answer for it.

    This is the line the owner drew on 2026-11-03 after the derivation landed: a team's name decides
    where its pipelines and applications live, and that is right, because it is a rule and not a guess.
    It is not permission to delete. `pandas-pipelines` is an address that rule produces, and this
    cluster may already hold a namespace of that name that kubementat never made — so a namespace that
    no file named is left alone, and the log says that is the reason, not "the directory was empty".

    A directory that *does* declare both namespaces is swept by the test above it. This one differs in
    exactly one thing: what is in the file.
    """
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "pandas"
                            / "static.json", {"ENVIRONMENT": "dev"})

    with caplog.at_level(logging.WARNING):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert not [n for n in namespaces_deleted(recorded_api) if "pandas" in n], \
        "a namespace was deleted on the strength of a directory name, which is a rule and not a deed"
    assert "pandas-pipelines" in caplog.text and "answered by the team's name" in caplog.text, \
        f"the log said the directory was skipped without saying what it declined to delete: {caplog.text}"
    assert str(fixture_repo.platform_config / fixture_repo.environment / "pandas"
               / "static.json") in caplog.text, "the sentence did not name the file it is about"


def test_a_directory_whose_configuration_will_not_parse_does_not_stop_the_teardown(
        uninstaller, fixture_repo, recorded_api, caplog):
    """A `static.json` that is not JSON is read the same way as one that says nothing.

    It is the second way a single foreign directory can hold the teardown hostage, and it is the
    more common one in the wild: a directory is copied, a template is left in place, a `sed`
    edits it into something `json.load` refuses. The answer is the same shape as the missing key
    -- nothing is swept for it, nothing is guessed, and the file is named -- and the reason it has
    to be the same answer is that the previous behaviour was to abort with a `JSONDecodeError`
    nobody had to explain.
    """
    foreign = fixture_repo.platform_config / fixture_repo.environment / "template" / "static.json"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text('{"PIPELINE_NAMESPACE": "unparsed",\n')

    with caplog.at_level(logging.WARNING):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert str(foreign) in caplog.text, "the warning did not name the file it could not read"
    said = [record.getMessage() for record in caplog.records if "is not readable as JSON" in record.getMessage()]
    assert len(said) == 1, \
        f"a directory that is not a team gets one warning, not one per place that noticed: {said}"
    assert not [namespace for namespace in namespaces_deleted(recorded_api)
                if "unparsed" in namespace], "a namespace was deleted out of a file that was never read"
    assert fixture_repo.app_namespace in namespaces_deleted(recorded_api), \
        "the rest of the sweep stopped for a file that is not part of it"


def test_the_run_summary_says_which_team_directories_it_swept_and_which_it_skipped(
        uninstaller, fixture_repo, recorded_api, caplog):
    """The residue asserts read the cluster, so they cannot tell "skipped a directory" from "swept everything".

    The run's own log can, and it has to: this is the line a green `no-extra-namespaces` is
    explained by, and the line that stops a `DONE` from meaning more than it does.
    """
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "ops"
                            / "static.json",
                            {"PIPELINE_NAMESPACE": "ops-pipelines",
                             "APP_DEPLOYMENT_NAMESPACE": "ops-apps"})
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "pandas"
                            / "static.json", {"ENVIRONMENT": "dev"})
    on_a_cluster(recorded_api, sorted(set(constants.DEFAULT_NAMESPACES.values())
                                      | {fixture_repo.pipeline_namespace, fixture_repo.app_namespace,
                                         "ops-pipelines", "ops-apps"} | CLUSTER_ADDONS))
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert "Team 'ops' was swept for ops-pipelines, ops-apps." in caplog.text
    # `pandas` declares nothing, so the rule answers both namespaces and the sweep deletes neither.
    # The summary has to say that out loud, because "swept" and "answered" are different words and a
    # reader is deciding whether the cluster is clean from this line alone.
    assert "Team directory 'pandas'" in caplog.text and "answered by the team's name" in caplog.text, \
        f"a namespace that was answered and not deleted must appear in the summary: {caplog.text}"
    assert "Team 'pandas' was swept" not in caplog.text, \
        f"nothing may be called swept when no file named it: {caplog.text}"
    assert "DONE" in caplog.text


def test_a_root_that_configures_no_teams_says_so_and_does_not_claim_it_swept_one(
        uninstaller, fixture_repo, recorded_api, caplog):
    """Census D's worst case: a valid environment layer under the given root, and no teams.

    This tree is legal -- `kmt initialize` writes no team directory, and deleting nothing there is
    the correct answer -- so the run must not refuse. What it must not do is print `DONE` without
    saying which root it read and that no team was swept from it: a sweep of every component
    namespace and zero teams otherwise reads exactly like a sweep of its teams, and the reader has
    no way to tell "there were none" from "they were handled".

    So both halves are asserted: the sentence naming the root appears, and the claim it replaces --
    that there was something team-shaped to sweep -- is not silently made.
    """
    team_directory = fixture_repo.platform_config / fixture_repo.environment / fixture_repo.team
    shutil.rmtree(team_directory)
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    root = fixture_repo.platform_config / fixture_repo.environment
    said = [index for index, record in enumerate(caplog.records)
            if "No team directory is configured under" in record.getMessage()]
    assert len(said) == 1, \
        f"the empty team half gets one line naming its root, not silence: {said}"
    assert str(root.resolve()) in caplog.records[said[0]].getMessage(), \
        "the line did not name the root it looked in, so the reader cannot tell the trees apart"

    # The run is still allowed to finish, and it still must not be read as a completed team sweep.
    done = [index for index, record in enumerate(caplog.records)
            if record.getMessage() == "DONE"]
    assert done, "a zero-team run is legal, so it prints DONE -- and this test is about what it says around it"
    assert said[0] < done[0], \
        "the sentence came after DONE, so the log reads as a sweep first and a denial second"
    assert "was swept for" not in caplog.text, \
        "the log claimed a team sweep this root never configured"
    assert not any("Team directory" in record.getMessage() for record in caplog.records), \
        "a root with no team directories is not a set of skipped ones"

    deleted = set(namespaces_deleted(recorded_api))
    assert deleted <= set(constants.DEFAULT_NAMESPACES.values()), \
        f"zero teams deleted namespaces no team configured: {sorted(deleted - set(constants.DEFAULT_NAMESPACES.values()))}"


def test_an_environment_that_was_never_initialized_still_refuses_to_delete_anything(
        uninstaller, fixture_repo, recorded_api, fake_binaries):
    """Tolerating a foreign directory is not the same as tolerating no environment.

    The asymmetry has to stay: `platform_config/dev/` absent is not "a team that named nothing",
    it is an environment nobody configured, and deleting namespaces on the strength of a typo is
    not something a command that takes `--yes` gets to do. `Config.teams()`' `ClickException` is
    the answer and it comes before the first write.
    """
    for name in ("static.json", "static.encrypted.json"):
        (fixture_repo.platform_config / fixture_repo.environment / name).unlink()
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    with pytest.raises(ClickException) as raised:
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert "static.json" in str(raised.value)
    assert client_writes(recorded_api) == []
    assert written(fake_binaries) == []


def test_the_cluster_is_printed_before_a_single_object_is_deleted(uninstaller, caplog):
    """A human with a terminal has to be able to object, so the log says the cluster first.

    Ordering is the whole contract here, so it is asserted on the log the operator reads rather
    than on the calls, which arrive in two logs and cannot be interleaved.
    """
    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    printed = [index for index, record in enumerate(caplog.records)
               if "You are going to uninstall kubementat from the following cluster" in record.message]
    first_deletion = [index for index, record in enumerate(caplog.records)
                      if "Deleting namespace" in record.message or "Deleting cluster role" in record.message]
    assert printed, "the cluster was never printed"
    assert first_deletion, "nothing was deleted, so this test is proving nothing"
    assert printed[0] < min(first_deletion)
    assert "k3s-server-1" in caplog.text, "the nodes are what say this is the right cluster"


def test_releases_living_in_kubementat_namespaces_are_uninstalled_and_no_others(
        uninstaller, fake_binaries):
    """`no-kubementat-releases` is a fact about releases, not about namespaces.

    Deleting the namespace takes the release secret as a side effect of a collection nobody
    scheduled, so the uninstaller asks helm for each release by name. The cluster's own release
    in `kube-system` is not kubementat's to touch.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    calls = argv_of(recorded(fake_binaries))
    assert helm_uninstall_of(calls, "cert-manager", "cert-manager"), calls
    assert helm_uninstall_of(calls, "openbao", "openbao"), calls
    assert not helm_uninstall_of(calls, "coredns", "kube-system"), calls


def test_claims_are_deleted_instead_of_announced(uninstaller, recorded_api, fixture_repo):
    """The bash printed advice about PVCs and deleted nothing, so the assert was reading prose.

    Claims are read per namespace and deleted by name before the namespace goes, so the verdict
    does not depend on how fast the namespace's controller finishes.
    """
    cluster_state(recorded_api, "persistentvolumeclaims",
                  {"openbao": [{"metadata": {"name": "data-openbao-0"}}]})

    uninstaller.uninstall_kubementat(assume_yes=True)

    assert ("openbao", "data-openbao-0") in deleted_claim_names(recorded_api), \
        "the claim was announced and not deleted"


def test_each_claim_is_deleted_by_its_own_command_and_not_in_one_bulk_call(uninstaller,
                                                                          recorded_api,
                                                                          fixture_repo):
    """One name per `kubectl delete`, because one bulk call cannot tell the two answers apart.

    A real lane lost this: it listed `prometheus-server` and `storage-prometheus-alertmanager-0`,
    handed both to one `kubectl delete`, and the API had already released the first while the
    namespaces were going away. kubectl exits 1 when *any* named object cannot be deleted, so the
    whole sweep was reported as residue that had never existed, and the only number the artifact
    carried was the return code. Asking per claim is what makes the failure per claim, which is the
    only granularity a teardown can act on.
    """
    cluster_state(recorded_api, "persistentvolumeclaims", {"prometheus": [
        {"metadata": {"name": "prometheus-server"}},
        {"metadata": {"name": "storage-prometheus-alertmanager-0"}}]})

    uninstaller.uninstall_kubementat(assume_yes=True)

    # One call per object is what the client makes, so the granularity this test exists for is now
    # a property of the code and not of an argv a reader had to count names in.
    names = deleted_names(recorded_api, "delete_namespaced_persistent_volume_claim")
    assert "prometheus-server" in names and "storage-prometheus-alertmanager-0" in names, \
        "both claims must be asked for by name"
    assert len(names) == len([call for call in recorded_api.calls
                              if call[0] == "delete_namespaced_persistent_volume_claim"]), \
        "one name per call, and one call per name"


def test_a_claim_that_is_already_gone_is_not_residue(uninstaller, recorded_api, fixture_repo,
                                                     caplog):
    """The API saying "that is not there" is the answer a teardown wanted, not a failure.

    The reason this is a claim and not a detail: `kubectl` has no machine-readable way to say which
    of its own refusals were an absence, so the distinction lives only in the server's reason on
    stderr -- which is why the test asserts the sentence was *read*, not merely that nothing was
    raised. Namespace deletion releases volumes while the sweep is running, so a name present a
    moment ago may be gone when the delete reaches it, and calling that residue makes `kmt
    uninstall` fail on a cluster that is cleaner than the one it started from.
    """
    cluster_state(recorded_api, "persistentvolumeclaims",
                  {"prometheus": [{"metadata": {"name": "prometheus-server"}}]})
    # The absence is an HTTP status now and not a sentence on stderr: the API server states it as 404
    # and says nothing else.
    cluster_deletes(recorded_api, "persistentvolumeclaims",
                    refused=lambda name: ApiException(status=404, reason="Not Found"))

    # INFO is under caplog's default floor, and the sentence that says "already gone" is INFO.
    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    # `shell.run` logs every check=False non-zero exit generically, so the claim's name alone is
    # not evidence of anything. What must not appear is this function's own refusal sentence, which
    # is the one thing that says "the teardown gave up on this object".
    warnings = " ".join(record.getMessage() for record in caplog.records
                        if record.levelno >= logging.WARNING)
    assert "could not delete claim" not in warnings, \
        "an absent claim was reported as a refused delete, which is how a clean cluster reads as " \
        "a dirty one"
    assert any("already gone" in record.getMessage() for record in caplog.records), \
        "the absence was decided silently, which is indistinguishable from never having tried"


def test_a_claim_refused_for_a_real_reason_is_residue_and_its_words_reach_the_error(
        uninstaller, recorded_api, fixture_repo, caplog):
    """The other half: a refusal that is not an absence is residue, with its sentence attached.

    Both halves matter. Leaving it out would be a lie about the cluster; leaving the server's own
    words out would be the artifact this lane already had, where the only evidence was `exit 1` and
    the namespace had to be guessed out of a traceback.
    """
    cluster_state(recorded_api, "persistentvolumeclaims",
                  {"prometheus": [{"metadata": {"name": "prometheus-server"}}]})
    cluster_deletes(recorded_api, "persistentvolumeclaims",
                    refused=lambda name: ApiException(
                        status=422,
                        reason='PersistentVolumeClaim "prometheus-server" is invalid: '
                               "spec.volumeName: field is immutable"))

    with pytest.raises(UninstallIncompleteError) as raised:
        uninstaller.uninstall_kubementat(assume_yes=True)

    message = str(raised.value)
    assert "prometheus-server" in message, "the residue did not name the claim that survived"
    assert "field is immutable" in message, \
        "the server's own sentence was dropped and only the return code was left"
    assert not any("already gone" in record.getMessage() for record in caplog.records), \
        "a refusal was written up as an absence"


def test_a_refusal_that_left_nothing_behind_is_said_but_not_counted(uninstaller, fake_binaries,
                                                                   fixture_repo, caplog,
                                                                   monkeypatch, recorded_api):
    """The verdict is the namespace's, not the command's.

    After a refusal the sweep asks the namespace once more, and a claim that is not there now is not
    something this command can be blamed for. It is still logged: a delete that exits 1 while the
    object is on its way out is precisely the thing a later reader would otherwise file as a bug in
    the sweep. This is the one shape the shims cannot answer -- they hold one static answer per call
    shape -- so the namespace is answered directly.
    """
    # Present when the sweep looks, gone by the time it asks again -- which the recorder answers
    # by counting the reads it has already been asked for. This is expressible because a client
    # call is one stateful answer, while a shim holds one static response per call shape.
    seen = []

    def claims(namespace=None, **kwargs):
        payload = [] if seen else [{"metadata": {"name": "prometheus-server"}}]
        return SimpleNamespace(items=[as_k8s_object(item) for item in payload])

    def refusing(name=None, namespace=None, **kwargs):
        seen.append(name)
        raise ApiException(status=500, reason="request timed out")

    recorded_api.answer("list_namespaced_persistent_volume_claim", claims)
    recorded_api.answer("delete_namespaced_persistent_volume_claim", refusing)

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert seen, "the claim was never asked for, so nothing here proves anything"
    assert any("not there anymore" in message or "already gone" in message
               for message in (record.getMessage() for record in caplog.records)), \
        "the refusal was resolved in silence"
    assert not any("residue" in record.getMessage() for record in caplog.records)


def test_linkerd_is_swept_off_a_cluster_that_still_carries_the_mesh(uninstaller, fixture_repo,
                                                                    fake_binaries, recorded_api,
                                                                    caplog):
    """A cluster that got Linkerd while kubementat installed it still has it, and `uninstall` takes it off.

    The mesh is not a component any more, so no entry in `constants.DEFAULT_NAMESPACES` and no
    `static.json` key would find its two namespaces: they are swept by their own step, which is
    what a cluster that never had a mesh also needs to be told about. Its
    CustomResourceDefinitions are the other half — cluster-scoped, invisible to a namespace
    delete, and named by nothing else in this command.

    The thing being locked here is that this happens without the `linkerd` binary. Handing
    deletion to the mesh's own uninstallers made the removal depend on a tool kubementat stopped
    shipping, so a lane on a machine that only has kubectl would report a green run over a cluster
    that still had a control plane. Nothing named `linkerd` is started, in any branch.
    """
    on_a_cluster(recorded_api, sorted(set(constants.DEFAULT_NAMESPACES.values())
                                      | {fixture_repo.pipeline_namespace,
                                         fixture_repo.app_namespace,
                                         "linkerd", "linkerd-viz"}
                                      | CLUSTER_ADDONS))
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    calls = recorded(fake_binaries)
    deleted = namespaces_deleted(recorded_api)
    assert "linkerd" in deleted and "linkerd-viz" in deleted, \
        "both mesh namespaces belong to the install, whoever put them there"
    assert "servers.linkerd.io" in deleted_names(recorded_api, "delete_custom_resource_definition"), \
        "the mesh's definitions outlive its namespaces unless somebody names the group"
    assert not [call for call in calls if call.program == "linkerd"], \
        "removing the mesh may not depend on the tool that would have removed it"
    assert "Removing Linkerd" in caplog.text


def test_linkerds_definitions_go_before_their_namespaces(uninstaller, fixture_repo,
                                                         fake_binaries, recorded_api, monkeypatch):
    """The order is the product here, not cosmetics.

    A mesh resource inside a namespace that is being torn down is how a namespace gets stuck in
    `Terminating`, and deleting the definition is what takes its instances with it. Both recorders
    in this file are separate logs with no shared clock, so the ordering claim is checked with a
    spy on each side of the one call the step makes.
    """
    order = []
    real_delete = uninstaller.kubernetes_utils.delete_namespace

    def deleting(namespace, **kwargs):
        order.append(("namespace", namespace))
        return real_delete(namespace, **kwargs)

    real_crd = uninstaller.kubernetes_utils.delete_cluster_scoped

    def deleting_crd(kind, name, **kwargs):
        if kind == "crd":
            order.append(("crd", name))
        return real_crd(kind, name, **kwargs)

    monkeypatch.setattr(uninstaller.kubernetes_utils, "delete_cluster_scoped", deleting_crd)
    monkeypatch.setattr(uninstaller.kubernetes_utils, "delete_namespace", deleting)
    on_a_cluster(recorded_api, sorted(set(constants.DEFAULT_NAMESPACES.values())
                                      | {fixture_repo.pipeline_namespace,
                                         fixture_repo.app_namespace,
                                         "linkerd", "linkerd-viz"}
                                      | CLUSTER_ADDONS))

    uninstaller.uninstall_kubementat(assume_yes=True)

    mesh_deletes = [index for index, (kind, name) in enumerate(order)
                    if kind == "namespace" and name in ("linkerd", "linkerd-viz")]
    mesh_crds = [index for index, (kind, name) in enumerate(order)
                 if kind == "crd" and name.endswith(".linkerd.io")]
    assert mesh_crds and mesh_deletes, "the mesh sweep deleted neither its CRDs nor its namespaces"
    assert max(mesh_crds) < min(mesh_deletes), \
        "a mesh resource left behind inside a terminating namespace is how the namespace sticks"


def test_linkerd_is_not_touched_on_a_cluster_that_never_got_a_mesh(uninstaller, fake_binaries,
                                                                   recorded_api, caplog):
    """Absence is a normal answer, and this is the state every lane this suite runs is in.

    The old script ran `linkerd viz uninstall | kubectl delete -f -` unconditionally and, under
    `set -e`, took the whole uninstall down on a cluster that had never seen a mesh. An install
    that never had the service mesh now costs one line in the log: no program is started, no
    namespace is named, and nothing is reported as residue.
    """
    # The fixture's cluster is mesh-free, and so is its `linkerd.io` group: `uninstall` has to
    # tell the two halves of the sweep apart and find neither of them.
    cluster_state(recorded_api, "crd", [{"metadata": {"name": "tasks.tekton.dev"}}])
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert not [call for call in recorded(fake_binaries) if call.program == "linkerd"], \
        "there is no mesh here, so there is no uninstaller to run"
    assert not [name for name in namespaces_deleted(recorded_api) if name.startswith("linkerd")], \
        "a namespace that was never there must not be named as if it had been deleted"
    assert not [name for name in deleted_names(recorded_api, "delete_custom_resource_definition")
                if name.endswith(".linkerd.io")], "no Linkerd CRDs, so nothing to delete"
    assert "Linkerd is not installed" in caplog.text


def test_the_ingress_controller_is_swept_off_a_cluster_that_still_carries_it(
        uninstaller, fixture_repo, fake_binaries, recorded_api, caplog):
    """A cluster that got the controller while kubementat installed it still has it, and
    `uninstall` takes it off. Same shape as the mesh, three times over.

    nginx-ingress is not a component any more, so no entry in `constants.DEFAULT_NAMESPACES` and no
    `static.json` key would find its namespace: it is swept by its own step, which is what a cluster
    that never had a controller also needs to be told about. The release is found by asking the
    cluster where it lives rather than by naming it, so a release somebody named after the chart
    instead of the namespace is still taken off. And the `IngressClass` it registered is
    cluster-scoped: it is the third thing a namespace delete leaves behind, and it keeps advertising
    a class nothing serves.

    A class bound to somebody else's controller is asserted to survive, because naming objects by
    what they are bound to is how an uninstall stops being able to delete the right things.
    """
    on_a_cluster(recorded_api, sorted(set(constants.DEFAULT_NAMESPACES.values())
                                      | {fixture_repo.pipeline_namespace,
                                         fixture_repo.app_namespace,
                                         "nginx-ingress"}
                                      | CLUSTER_ADDONS))
    fake_binaries.respond("helm", json.dumps(HELM_RELEASES + [INGRESS_CONTROLLER_RELEASE]),
                          subcommand="list")
    cluster_state(recorded_api, "ingressclass", INGRESS_CLASSES)
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    calls = recorded(fake_binaries)
    deleted = namespaces_deleted(recorded_api)
    assert "nginx-ingress" in deleted, \
        "the controller's namespace belongs to the install, whoever put it there"
    assert helm_uninstall_of(argv_of(calls), "ingress-nginx", "nginx-ingress"), \
        "the release is addressed by the name the cluster reports, not by a name written here"
    deleted_classes = deleted_names(recorded_api, "delete_ingress_class")
    assert "nginx" in deleted_classes, \
        "an IngressClass is cluster-scoped, so nothing else in this command would take it"
    assert "other" not in deleted_classes, \
        "a class bound to a controller kubementat never shipped is not kubementat's to delete"
    assert not [call for call in calls
                if call.program in ("nginx-ingress-controller", "ingress-nginx", "nginx")], \
        "removing the controller may not depend on the tool that would have removed it"
    assert "Removing the ingress controller" in caplog.text


def test_the_ingress_controller_is_not_touched_on_a_cluster_that_never_got_one(
        uninstaller, fake_binaries, recorded_api, caplog):
    """A cluster that never had a controller costs one line in the log.

    This is the state every lane this suite runs is in, and it is the branch a removal has to get
    right: the install that stopped shipping the controller is the normal case, so a cluster that
    carries nothing may not be told it left residue, and it may not be asked to name a class it
    does not have. The fake answers with somebody else's class only, which is the closest thing a
    cluster that never had nginx-ingress can say about IngressClasses.
    """
    cluster_state(recorded_api, "ingressclass", [INGRESS_CLASSES[1]])
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert not [name for name in namespaces_deleted(recorded_api) if "nginx" in name], \
        "a namespace that was never there must not be named as if it had been deleted"
    assert not [call for call in recorded(fake_binaries)
                if call.argv[:2] == ["helm", "uninstall"] and "nginx-ingress" in call.argv], \
        "there is no controller here, so there is no release to uninstall"
    assert not deleted_names(recorded_api, "delete_ingress_class"), \
        "no class here is bound to what kubementat removed, so nothing is deleted"
    assert "nginx-ingress is not installed" in caplog.text


def test_the_router_goes_from_the_namespace_it_was_installed_into(uninstaller, fake_binaries,
                                                                  recorded_api, caplog):
    """Traefik is a component, so its uninstall is the ordinary one and not a new sweep.

    Nothing here names Traefik's namespace. The entry in `constants.DEFAULT_NAMESPACES` is the
    same fact `kmt install` deploys through and `kmt uninstall` deletes, so the router arrives in
    this set for free and the release that lives in it is found by `helm list` like every other
    one. That is the whole of the namespace half of the ticket, and the point of asserting it on
    the shared table rather than on a string written down here: an entry that went missing would
    take the namespace and the release with it, and the command would still say DONE.

    The IngressClass goes too, and in this run by a path no code takes: the release kubementat
    created owns the class, so taking the release takes it. The next test is the half that says
    nothing else should have deleted it.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    calls = argv_of(recorded(fake_binaries))
    assert helm_uninstall_of(calls, "traefik", TRAEFIK_NAMESPACE), calls
    assert TRAEFIK_NAMESPACE in namespaces_deleted(recorded_api), \
        "the router's namespace is a component namespace and this sweep is what components get"


def test_k3ss_own_router_stays_on_the_cluster_with_its_namespace(uninstaller, fake_binaries,
                                                                 recorded_api):
    """Same release name, different owner: ours goes, the node's stays.

    k3s ships its controller as `traefik` and `traefik-crd` in `kube-system`, which is why the
    sweep addresses a release by namespace *and* name rather than by name alone. A sweep keyed on
    `traefik` would have taken the cluster's own add-on down while reporting a clean teardown of
    kubementat's, and on the everyday lane that is the one delete here nothing else puts back.
    """
    fake_binaries.respond("helm", json.dumps(HELM_RELEASES + K3S_TRAEFIK_RELEASES),
                          subcommand="list")
    fake_binaries.take_calls()

    uninstaller.uninstall_kubementat(assume_yes=True)

    calls = argv_of(recorded(fake_binaries))
    assert not helm_uninstall_of(calls, "traefik", "kube-system"), calls
    assert not helm_uninstall_of(calls, "traefik-crd", "kube-system"), calls
    assert "kube-system" not in namespaces_deleted(recorded_api), \
        "the node's namespace is not kubementat's to delete, whatever lives inside it"


def test_both_groups_of_definitions_helm_never_removes_are_swept(uninstaller, fake_binaries, recorded_api):
    """`helm uninstall` takes the release and no part of the chart's `crds/`.

    So a router that is gone leaves `*.traefik.io` and `*.hub.traefik.io` behind, cluster-scoped,
    owned by no release secret, named by nothing else in kubementat -- which is the state a residue
    assert reads as clean. Both groups, because the evidence is the chart's own directory rather
    than a guess: `helm show crds traefik/traefik --version 42.1.3` (run 2026-10-28) prints 25
    definitions, ten under `traefik.io` and fifteen under `hub.traefik.io`. A sweep of the
    first group alone would delete ten, log a clean sweep, and leave fifteen serving objects
    nobody owns -- the failure mode the plan names for this ticket.

    `prometheusrules.monitoring.coreos.com` is the control: the sweep is a group match, not a
    "everything the cluster has" match, and deleting somebody else's definition is not an
    uninstall.

    The cluster this runs against is the shape that makes the sweep entitled: the router is the only
    Traefik release on it and the release sweep above has taken it by the time the definition sweep
    asks, so nothing is left serving from them. What a controller that *did* stay does about that is
    the next two tests.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_names(recorded_api, "delete_custom_resource_definition")
    ours = sorted(name for name in deleted
                  if name.endswith(".traefik.io") or name.endswith(".hub.traefik.io"))
    assert {"middlewares.traefik.io", "ingressroutes.traefik.io",
            "accesscontrolpolicies.hub.traefik.io"} <= set(ours), \
        f"both groups have to be swept, not one: {sorted(ours)}"
    assert set(ours) == {"middlewares.traefik.io", "ingressroutes.traefik.io",
                         "accesscontrolpolicies.hub.traefik.io"}, \
        "the cluster named three and exactly three should have gone: %s" % sorted(ours)
    assert "prometheusrules.monitoring.coreos.com" not in deleted


def test_a_traefik_the_node_still_runs_keeps_the_definitions_it_is_serving(uninstaller,
                                                                          fake_binaries,
                                                                          recorded_api, caplog):
    """On a k3s node the controller stayed in `kube-system`, so its definitions stay too.

    Deleting a CustomResourceDefinition garbage-collects every object written against it, and on the
    everyday cluster those objects belong to the proxy the *node* installed and not to the release
    kubementat is standing down. So the sweep asks what survived the releases before it deletes
    anything, and a Traefik chart still deployed anywhere is the answer that stops it. What is named
    in the log is the release and the namespace that stopped it, because "found a Traefik, deleted
    nothing" is not something the operator can act on. Nothing here contributes residue: what is not
    kubementat's to delete cannot be left behind by kubementat.

    The two things that must still hold are asserted on the same run, because they are the run's
    whole risk: the node's releases and its namespace are untouched, and the controller sweep still
    reaches only the class bound to `k8s.io/ingress-nginx`, which is the string this ticket had to
    leave pointed where kubementat left it.
    """
    fake_binaries.respond("helm", json.dumps(HELM_RELEASES + K3S_TRAEFIK_RELEASES),
                          subcommand="list")
    cluster_state(recorded_api, "ingressclass", INGRESS_CLASSES)
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    calls = recorded(fake_binaries)
    deleted = deleted_names(recorded_api, "delete_custom_resource_definition")
    assert not [name for name in deleted if name.endswith((".traefik.io", ".hub.traefik.io"))], \
        "the node's own controller is still serving from them, so none of them was kubementat's to " \
        "delete: %s" % sorted(deleted)
    assert {"tasks.tekton.dev", "tektonconfigs.tekton.dev"} <= set(deleted), \
        "the gate is about Traefik and stops nothing that is kubementat's from going: %s" % sorted(deleted)
    assert "release 'traefik' in namespace 'kube-system'" in caplog.text, \
        "the no-op has to say which release made it one: %s" % caplog.text
    assert deleted_names(recorded_api, "delete_ingress_class") == ["nginx"], \
        "the controller sweep is not what the definition gate widened, and it still finds only the " \
        "controller kubementat no longer ships"
    assert not helm_uninstall_of(argv_of(calls), "traefik", "kube-system"), \
        argv_of(calls)
    assert "kube-system" not in namespaces_deleted(recorded_api), \
        "the node's namespace is not kubementat's to delete, whatever lives inside it"


def test_the_companion_chart_alone_is_not_a_controller_that_stayed(uninstaller, fake_binaries,
                                                                   caplog, recorded_api):
    """The gate reads the chart *name*, and the chart with no proxy in it is not called `traefik`.

    `traefik-crds` is deprecated since chart v38.0.2, ships no proxy, and is not what kubementat
    installs (`constants.TRAEFIK_CHART`) -- but its chart string, like the proxy's, begins
    `traefik-`. A gate keyed on that prefix alone reads a cluster carrying only the companion as one
    that still has a controller, skips the sweep for ever, and lets the residue assert call fifteen
    orphaned definitions a clean teardown. The field is read as `<name>-<version>` for exactly this
    reason, and the name of the companion is `traefik-crds`.
    """
    fake_binaries.respond("helm", json.dumps(HELM_RELEASES + [K3S_TRAEFIK_CRDS_RELEASE]),
                          subcommand="list")
    fake_binaries.take_calls()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_names(recorded_api, "delete_custom_resource_definition")
    assert {"middlewares.traefik.io", "accesscontrolpolicies.hub.traefik.io"} <= set(deleted), \
        "a companion chart is not a controller, so the definitions are still kubementat's: %s" % sorted(deleted)
    assert "would not answer" not in caplog.text, \
        "nothing here was left alone, so the sweep that ran has to be the sweep that deleted: %s" % caplog.text


def test_the_alloy_dependency_crds_go_when_no_alloy_is_collecting(uninstaller, fake_binaries, recorded_api):
    """A dependency chart's `crds/` is invisible to both sweeps that report clean.

    The release sweep deletes the release, the namespace sweep deletes the namespace, and neither of
    them can reach a cluster-scoped definition that belongs to no namespace and no release name. This is
    the lane's failure turned into a unit test: `kmt uninstall` may not claim a clean teardown while
    `podlogs.monitoring.grafana.com` is sitting on the cluster.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_names(recorded_api, "delete_custom_resource_definition")
    assert "podlogs.monitoring.grafana.com" in deleted, \
        "a definition a dependency chart installed is residue like any other: %s" % sorted(deleted)
    assert "prometheusrules.monitoring.coreos.com" not in deleted, \
        "sweeping `monitoring.grafana.com` must not read as a licence for `monitoring.coreos.com`"


def test_a_foreign_alloy_still_collecting_keeps_its_customresourcedefinition(uninstaller, fake_binaries,
                                                                            caplog, recorded_api):
    """The gate, asked of the cluster and not of a component list.

    kubementat installs one Alloy, so the easy implementation deletes the definition unconditionally and
    is wrong the day somebody else's Alloy is running in a namespace this tool would never have swept.
    Deleting a CustomResourceDefinition deletes every object of that kind, so the answer to "is this
    still somebody's?" has to come from `helm list` over every namespace rather than from a name.
    """
    fake_binaries.respond("helm", json.dumps(HELM_RELEASES + [FOREIGN_ALLOY_RELEASE]), subcommand="list")
    fake_binaries.take_calls()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_names(recorded_api, "delete_custom_resource_definition")
    assert "podlogs.monitoring.grafana.com" not in deleted, \
        "a release kubementat never installed is still using these definitions: %s" % sorted(deleted)
    assert "still collects logs" in caplog.text, \
        "the decision has to be readable: what was left, and why: %s" % caplog.text


def test_the_definitions_go_before_the_namespace_they_could_stick(uninstaller, recorded_api,
                                                                  monkeypatch):
    """Order again, for the same physical reason as Linkerd's.

    An `IngressRoute` still pointing at a definition is how a namespace being torn down stays in
    `Terminating`, and a delete that is still running when the sweep reports is what let a lane
    read a clean cluster out of a half-finished one. The two recorders have no shared clock, so
    the claim is checked with spies on the one pair of calls that has an order.
    """
    order = []
    real_delete = uninstaller.kubernetes_utils.delete_namespace

    def deleting(namespace, **kwargs):
        order.append(("namespace", namespace))
        return real_delete(namespace, **kwargs)

    real_crd = uninstaller.kubernetes_utils.delete_cluster_scoped

    def deleting_crd(kind, name, **kwargs):
        if kind == "crd":
            order.append(("crd", name))
        return real_crd(kind, name, **kwargs)

    monkeypatch.setattr(uninstaller.kubernetes_utils, "delete_cluster_scoped", deleting_crd)
    monkeypatch.setattr(uninstaller.kubernetes_utils, "delete_namespace", deleting)

    uninstaller.uninstall_kubementat(assume_yes=True)

    router_crds = [index for index, (kind, name) in enumerate(order)
                   if kind == "crd" and (name.endswith(".traefik.io")
                                         or name.endswith(".hub.traefik.io"))]
    router_namespaces = [index for index, (kind, name) in enumerate(order)
                         if kind == "namespace" and name == TRAEFIK_NAMESPACE]
    assert router_crds and router_namespaces, \
        "neither the definitions nor the namespace was deleted, so the order proves nothing"
    assert max(router_crds) < min(router_namespaces), \
        "a definition deleted after its namespace is how the namespace gets stuck"


def test_a_class_bound_to_another_controller_is_not_deleted_by_the_router_sweep(
        uninstaller, fake_binaries, recorded_api):
    """Traefik's class survives every delete this command makes, and nothing else's goes with it.

    The nginx sweep matches an IngressClass by controller, and the one string it matches is
    `k8s.io/ingress-nginx`. Traefik's class carries `constants.TRAEFIK_CONTROLLER_NAME`, which is
    not that string, so the sweep that takes the old controller's class must come away having
    taken nothing else: widening the prefix to reach it would delete the class kubementat's own
    release created and is serving traffic with. The evidence is the negative -- only `nginx` was
    addressed, so nothing reached for the class next to it.
    """
    cluster_state(recorded_api, "ingressclass", INGRESS_CLASSES)
    fake_binaries.take_calls()

    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted_classes = deleted_names(recorded_api, "delete_ingress_class")
    assert deleted_classes == ["nginx"], \
        "only the controller kubementat no longer ships is swept by class: %s" % sorted(
            deleted_classes)


def test_a_cluster_that_will_not_answer_for_its_definitions_does_not_stop_the_uninstall(
        uninstaller, fake_binaries, recorded_api, caplog):
    """The read is asked of the cluster, and a cluster that answers with an error is not fatal.

    A cluster old enough to predate `CustomResourceDefinition`, or a kubeconfig that points at
    nothing, replies with an exit code and no items. The sweep may not raise through that: this is
    the highest-risk command in the product, and half-deleting a platform while chasing a
    definition nobody asked for is worse than finishing the sweep. What it may not do is pretend -
    "could not ask" is logged as itself, not as `No kubementat CRDs on the cluster.`, because those
    two sentences mean different things to the lane that reads the log afterwards.
    """
    cluster_state(recorded_api, "crd", error="the cluster would not answer")
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    calls = recorded(fake_binaries)
    assert not [call for call in calls if call.argv[:3] == ["kubectl", "delete", "crd"]], \
        "nothing was read, so nothing should have been claimed as deleted"
    assert helm_uninstall_of(argv_of(calls), "openbao", "openbao"), \
        "the rest of the uninstall finished despite the definition that could not be read"
    assert namespaces_deleted(recorded_api), "the namespace sweep ran to the end"
    assert "would not answer" in caplog.text, \
        "a failed read has to be said as a failed read: %s" % caplog.text


def test_a_cluster_that_will_not_answer_for_its_releases_claims_no_definition_as_deleted(
        uninstaller, fake_binaries, recorded_api, monkeypatch, caplog):
    """The gate's own read, and the tolerance it is written with.

    The release sweep asks `helm list` with `check=True` and aborts when it fails, which is right at
    that point of the run: nothing has been deleted yet and there is everything to go back to. By the
    time the definition sweep asks the same question, namespaces are gone and there is nothing to
    abort into, so a cluster that has gone quiet gets the only answer that cannot do damage: delete
    nothing, and say that nothing is claimed as deleted. The warning is not logged as `No Traefik CRDs
    on the cluster.`, for the reason the definition read two methods later gives -- a sweep that found
    nothing and a sweep that could not ask read the same to a log, and are not the same fact.

    The fake models the gap and not a broken binary. A `helm` that fails from its first call onward
    raises inside the release sweep, which is a different question and one the tests above answer.
    """
    real_run = kubementat.automation.shell.run
    listings = []

    def a_cluster_that_goes_quiet(argv, **kwargs):
        if argv[:2] == ["helm", "list"]:
            listings.append(argv)
            if len(listings) > 1:
                fake_binaries.respond("helm", "connection refused\n", exit_code=1,
                                      subcommand="list")
        return real_run(argv, **kwargs)

    monkeypatch.setattr("kubementat.automation.shell.run", a_cluster_that_goes_quiet)
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    calls = recorded(fake_binaries)
    assert len(listings) > 1, "the gate never asked, so nothing here exercised its answer"
    untouched = [call.argv[-1] for call in calls
                 if call.argv[:2] == ["kubectl", "delete"]
                 and call.argv[-1].endswith((".traefik.io", ".hub.traefik.io"))]
    assert untouched == [], \
        "the cluster was not asked, so no definition of theirs could be claimed as deleted: %s" % untouched
    assert "would not answer" in caplog.text, caplog.text
    assert helm_uninstall_of(argv_of(calls), "openbao", "openbao"), \
        "the rest of the uninstall finished, which is what makes this tolerance and not an abort"
    assert namespaces_deleted(recorded_api), "the namespace sweep ran to the end"


def test_crds_are_asked_of_the_cluster_and_not_read_off_a_list(uninstaller, fake_binaries, recorded_api):
    """Nineteen strings in a script age. A group suffix does not.

    `tektonconfigs.tekton.dev` was never in the list, so it would have survived; anything
    outside kubementat's groups stays, because deleting somebody else's CRD is not an uninstall.
    The Traefik rows are here for the same reason: they arrived with a chart, not with a name in
    a script, and the group is what finds them.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_names(recorded_api, "delete_custom_resource_definition")
    assert "tektonconfigs.tekton.dev" in deleted, "a CRD no name in the script covered still belongs to it"
    assert "tasks.tekton.dev" in deleted
    assert "servers.linkerd.io" in deleted
    assert "middlewares.traefik.io" in deleted
    assert "prometheusrules.monitoring.coreos.com" not in deleted


def test_the_helm_deployer_cluster_role_goes_even_when_it_is_not_there(uninstaller, recorded_api):
    """Cluster-scoped, so no namespace sweep reaches it; a missing one is still a success.

    The lane asserts `no-helm-deployer-clusterrole`, and a re-run of uninstall is normal.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    assert ("delete_cluster_role", {"name": "helm-deployer-cluster-role"}) in recorded_api.calls


def test_an_uninstall_of_a_cluster_that_is_already_clean_reports_clean(uninstaller, fake_binaries,
                                                                      recorded_api, caplog):
    """The lane runs uninstall twice. The second pass must delete nothing and fail nothing.

    A residue list is not empty because a command failed for a good reason: a namespace that is
    already gone is not residue, and neither is a release that was never there.
    """
    on_a_cluster(recorded_api, sorted(CLUSTER_ADDONS))
    fake_binaries.respond("helm", "[]", subcommand="list")
    cluster_state(recorded_api, "crd", [])
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert namespaces_deleted(recorded_api) == []
    assert written(fake_binaries) == []
    # Deleting a cluster role that is not there is the one write a clean pass still asks for -
    # it is a lookup for absence, and the primitive's 404 branch is what makes it a success.
    assert client_writes(recorded_api) == ["delete_cluster_role"]
    assert "DONE" in "\n".join(record.message for record in caplog.records), \
        "a clean cluster deserves the word, and the operator is the reader"


def test_something_kubementat_owned_that_survives_is_an_error_and_not_a_DONE(
        uninstaller, fake_binaries, caplog):
    """The exit code is the operator's only signal, and a green `DONE` over live releases is a lie.

    `no-kubementat-releases` measures the cluster precisely because the tooling's own word is
    not trusted; the tooling now has to agree with the cluster or say what is left. And `DONE` is
    the log line that used to mean "the script finished", which is exactly the line that has to be
    withheld.
    """
    fake_binaries.respond("helm", "Command exited with code 1", subcommand="uninstall", exit_code=1)

    with caplog.at_level(logging.INFO), pytest.raises(UninstallIncompleteError) as raised:
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert "cert-manager" in str(raised.value)
    assert "DONE" not in caplog.text, "it said DONE over releases it did not delete"


def namespaces_that_are_still_deleting(recorded_api, present, stuck):
    """A cluster that accepted every delete and finished all but `stuck`.

    This is the state the watch log measured -- ten namespaces accepted, draining over roughly
    forty seconds -- with the slow one pinned in `Terminating` so a test can ask what the
    uninstall says when the cluster is not finished with its own work.
    """
    still_there = set(present)

    def delete(name=None, **kwargs):
        if name not in stuck:
            still_there.discard(name)

    recorded_api.answer("delete_namespace", delete)
    recorded_api.answer("list_namespace",
                        lambda **kwargs: NamespaceList(sorted(still_there),
                                                       phases={name: "Terminating" for name in stuck}))


def test_a_namespace_the_cluster_has_not_finished_deleting_is_residue_and_not_a_DONE(
        uninstaller, recorded_api, monkeypatch, caplog):
    """Deleting is not the same as deleted, and the measured gap was 42 seconds.

    The lane said it plainly: the deletes were fired, `still present` was logged four seconds
    later, and the watch log showed the same namespaces gone by 42 s. A tool that reports over an
    unfinished deletion is not reporting a failure -- it is reporting its own impatience. So a
    namespace the cluster is still removing has to appear as residue, named with its phase, and
    `DONE` has to be withheld.
    """
    present = set(constants.DEFAULT_NAMESPACES.values()) | CLUSTER_ADDONS
    namespaces_that_are_still_deleting(recorded_api, present, stuck={"grafana"})
    # The real ceiling is three minutes of polling; a unit test asserts the decision, not the clock.
    monkeypatch.setattr("kubementat.kubernetes_utils.NAMESPACE_GONE_WAIT_S", 0)

    with caplog.at_level(logging.INFO), pytest.raises(UninstallIncompleteError) as raised:
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert "grafana" in str(raised.value), str(raised.value)
    assert "Terminating" in str(raised.value), \
        "the residue says the namespace exists but not that the cluster is mid-deletion: %s" % raised.value
    assert "DONE" not in caplog.text, "it said DONE over a deletion the cluster had not finished"


def test_a_deletion_the_cluster_finishes_is_not_reported_as_residue(
        uninstaller, recorded_api, monkeypatch, caplog):
    """The other half: waiting is not the same as complaining.

    A namespace that vanishes while uninstall is polling is not residue, and a tool that invents
    residue here would train an operator to ignore the residue list. The fake answers the way a
    cluster a second later does: the delete was accepted, and the namespace is simply not reported
    again.
    """
    present = set(constants.DEFAULT_NAMESPACES.values()) | CLUSTER_ADDONS
    remaining = set(present)
    reads = {"count": 0}

    def delete(name=None, **kwargs):
        remaining.discard(name)

    def listing(**kwargs):
        reads["count"] += 1
        # Read one is the instant the delete was accepted; after that the cluster has caught up,
        # so a command that only ever looked once would have called this residue.
        return NamespaceList(sorted(present) if reads["count"] == 1 else sorted(CLUSTER_ADDONS))

    recorded_api.answer("delete_namespace", delete)
    recorded_api.answer("list_namespace", listing)
    # Poll rather than sleep, and with no ceiling that matters: the assertion is the poll, not the clock.
    monkeypatch.setattr("kubementat.kubernetes_utils.NAMESPACE_GONE_WAIT_S", 2)
    monkeypatch.setattr("kubementat.kubernetes_utils.NAMESPACE_POLL_INTERVAL_S", 0)

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert reads["count"] > 1, "the cluster was read once and believed instantly, which is the old bug"
    assert "still present: none" in caplog.text.lower(), caplog.text
    assert "DONE" in caplog.text


def test_answering_no_deletes_nothing(fixture_repo, dry_run_env, fake_binaries, recorded_api,
                                     monkeypatch):
    """`n` at the prompt is a cancellation, not a partial uninstall.

    The bash had the same rule, but it was inside the script, so the CLI had to run a script to
    find out. Now the answer is the library's and it is given before the first command.
    """
    present = set(constants.DEFAULT_NAMESPACES.values()) | {fixture_repo.pipeline_namespace,
                                                           fixture_repo.app_namespace} | CLUSTER_ADDONS
    on_a_cluster(recorded_api, present)
    monkeypatch.setattr("kubementat.automation.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    with pytest.raises(ClickException) as raised:
        Automation(configuration_only_context(fixture_repo.environment, None)).uninstall_kubementat()

    message = str(raised.value)
    assert "Cancelled uninstall script" in message
    assert "nothing was deleted" in message.lower(), \
        "the cancellation has to say what it did, and that is the half the operator is blind to"
    assert raised.value.exit_code != 0, "declining has to fail for a reason"
    assert client_writes(recorded_api) == [], "answering no deleted something through the client"
    assert written(fake_binaries) == [], "answering no deleted something with a command"


def test_a_run_with_no_terminal_fails_closed_and_names_the_flag(fixture_repo, dry_run_env,
                                                                fake_binaries, recorded_api,
                                                                monkeypatch):
    """No terminal, no `--yes` - and no deletion, no hang, no traceback.

    `kmt uninstall` answers this with a `UsageError` before the library is reached; a caller that
    built `Automation` directly gets the same answer from here, because the only safe reading of
    silence is "nobody agreed to this".
    """
    present = set(constants.DEFAULT_NAMESPACES.values()) | {fixture_repo.pipeline_namespace,
                                                           fixture_repo.app_namespace} | CLUSTER_ADDONS
    on_a_cluster(recorded_api, present)
    monkeypatch.setattr("kubementat.automation.sys.stdin.isatty", lambda: False)
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    with pytest.raises(ClickException) as raised:
        Automation(configuration_only_context(fixture_repo.environment, None)).uninstall_kubementat()

    assert "--yes" in str(raised.value)
    assert client_writes(recorded_api) == []
    assert written(fake_binaries) == []

# ------------------------------------------------------------ cluster-scoped RBAC
# Everything below lives outside a namespace, which is what makes it the residue an uninstall has
# to have a *rule* for: the script this file tests against deleted exactly one ClusterRole by name
# and said DONE over thirty-odd survivors, and the lane's
# `no-extra-cluster-scoped:clusterroles` assert read the result as a clean teardown.


def test_the_cluster_scoped_rbac_the_release_manifests_applied_is_swept(uninstaller, fixture_repo,
                                                                       fake_binaries, recorded_api,
                                                                       caplog):
    """The RBAC the Tekton releases put outside every namespace goes too, and it is not labelled.

    `TektonUtils.install()` applies the three release manifests with `kubectl apply -f`, so
    everything in them arrives with upstream's `app.kubernetes.io/part-of` and without kubementat's
    `managed-by` claim -- which is the reason a sweep that only reads its own label finds one object
    out of thirty-odd. What all sixteen of the bindings those manifests ship have in common is that
    every subject is a ServiceAccount inside a namespace `constants.DEFAULT_NAMESPACES` says
    kubementat owns and is about to delete, and that is what is read instead of a name list.

    `roleRef` is the other half of the read: `tekton-aggregate-view` is bound by nothing of its own
    and is reached only through the dashboard binding that grants it, so a sweep that stopped at the
    binding would leave the role behind with the log claiming it swept everything.
    """
    answering_the_cluster_scoped_rbac(
        recorded_api,
        RELEASE_RBAC["bindings"] + owned_rbac_bindings(fixture_repo) + FOREIGN_RBAC["bindings"],
        RELEASE_RBAC["roles"] + owned_rbac_roles(fixture_repo) + FOREIGN_RBAC["roles"])
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_rbac(recorded_api)
    expected = {("clusterrolebinding", binding["metadata"]["name"])
                for binding in RELEASE_RBAC["bindings"] + owned_rbac_bindings(fixture_repo)}
    expected |= {("clusterrole", role) for role in
                 ["tekton-pipelines-controller-cluster-access", "tekton-triggers-admin",
                  "tekton-events-controller-cluster-access", "tekton-aggregate-view",
                  "tekton-triggers-bindings-and-interceptors-role", "tekton-cleanup-cluster-role"]}
    assert expected <= deleted, \
        f"the release RBAC and kubementat's own are the half this ticket is about: {sorted(expected - deleted)}"
    assert "helm-deployer-cluster-role" in {name for kind, name in deleted}, \
        "it is bound through a namespaced RoleBinding, so the label is the only thing that reaches it"
    assert any("cluster-scoped RBAC" in record.message for record in caplog.records), \
        "the sweep that deleted thirty objects has to say so where the operator reads it"


def test_the_cluster_scoped_rbac_goes_before_the_namespaces_it_is_bound_into(uninstaller,
                                                                            fixture_repo,
                                                                            fake_binaries,
                                                                            caplog, recorded_api):
    """Order, for the reason that decides the whole read: a subject is only evidence while it exists.

    The sweep's second rule asks where a binding's ServiceAccounts live, and it answers it from the
    namespaces about to be deleted. Run it afterwards and the answer is a cluster that has forgotten
    what it deleted, which is the same question asked a beat late.
    """
    answering_the_cluster_scoped_rbac(
        recorded_api, RELEASE_RBAC["bindings"], RELEASE_RBAC["roles"])
    fake_binaries.take_calls()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    said = "\n".join(record.message for record in caplog.records)
    assert said.index("cluster-scoped RBAC object(s) kubementat applied") \
        < said.index("Deleting namespace: 'tekton-pipelines'"), \
        "the RBAC sweep read subjects inside namespaces that were already gone, which is how it " \
        "would report a clean cluster it had not looked at"


def test_a_cluster_role_kubementat_never_stamped_or_bound_is_left_where_it_is(uninstaller,
                                                                              fixture_repo,
                                                                              fake_binaries, recorded_api):
    """The other half: an uninstall that is complete because it deleted only its own.

    A ClusterRole kubementat wrote no claim on and bound to nothing is exactly the object the sweep
    must not touch, and the shape of it is threefold: `cluster-admin`, which is bound to a group and
    so has no namespace to ask about; `prometheus-k8s`, bound inside a namespace kubementat owns
    nothing about; and `gitlab-runner-cluster-admin`, whose binding points at somebody else's
    controller. Deleting any of them would turn a green residue assert into a broken cluster, and a
    sweep that could tell it had nothing to delete -- deleting nothing and deleting nothing that
    mattered are the same action only to a reader who cannot see the cluster.

    This is the unit evidence that the foreign-object half of the ticket is closed. Whether every
    one of *kubementat's* own thirty-odd objects went is a question for a `full` lane; what a unit
    can say, and what it says here without a name list, is that the rule cannot reach an object that
    kubementat neither wrote nor bound into a namespace of its own.
    """
    answering_the_cluster_scoped_rbac(
        recorded_api, RELEASE_RBAC["bindings"] + FOREIGN_RBAC["bindings"],
        RELEASE_RBAC["roles"] + FOREIGN_RBAC["roles"])
    fake_binaries.take_calls()

    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_rbac(recorded_api)
    for kind, name in [("clusterrole", "cluster-admin"), ("clusterrole", "prometheus-k8s"),
                       ("clusterrole", "gitlab-runner-cluster-admin"),
                       ("clusterrolebinding", "kubeadm:cluster-admin"),
                       ("clusterrolebinding", "prometheus-k8s-read"),
                       ("clusterrolebinding", "gitlab-runner-cluster-access")]:
        assert (kind, name) not in deleted, \
            f"kubementat never wrote or bound {kind}/{name}, so this sweep had no claim on it"
    assert deleted, "the sweep found nothing of its own either, which makes the pass above vacuous"


def test_a_cluster_role_bound_only_into_somebody_elses_namespace_is_not_swept(uninstaller,
                                                                             fake_binaries,
                                                                             caplog, recorded_api):
    """The rule is about namespaces kubementat owns, not about namespaces.

    A binding that grants a role to an account in `monitoring` is a monitoring thing, whatever the
    role it reaches is named, and a ClusterRole is swept because of how it got onto the cluster --
    here, not at all. This is the same object the test above plants, asked of the sweep alone and
    without the release rows beside it, so the answer cannot come from a cluster that had nothing to
    delete.
    """
    answering_the_cluster_scoped_rbac(recorded_api, FOREIGN_RBAC["bindings"],
                                         FOREIGN_RBAC["roles"])
    fake_binaries.take_calls()

    with caplog.at_level(logging.INFO):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert swept_rbac_deletes(recorded_api) == set(), \
        "not one of these was installed by kubementat, so not one of them was kubementat's to delete"
    assert ("clusterrole", kubementat.automation.Automation.HELM_DEPLOYER_CLUSTER_ROLE) \
        in deleted_rbac(recorded_api), \
        "the by-name line is kubementat's own claim and is not part of what the sweep decides"
    assert "is kubementat's to delete" in caplog.text, \
        "a sweep that found nothing on a cluster with objects in it has to say it found nothing: " \
        f"{caplog.text}"


def test_a_cluster_that_will_not_answer_for_its_rbac_claims_no_object_as_deleted(uninstaller,
                                                                                fake_binaries,
                                                                                caplog, recorded_api):
    """The read is not fatal, and it is not allowed to be silent.

    A kubeconfig pointed somewhere that will not answer replies with an exit code and no items,
    which is the same bytes as a cluster that has no RBAC. `_delete_crds_in_groups` gives the
    argument for why that difference is logged and not smoothed over -- a residue assert cannot tell
    a sweep that found nothing from one that could not ask, and here the second one is the difference
    between `DONE` and a cluster still holding everything.
    """
    answering_the_cluster_scoped_rbac(recorded_api, RELEASE_RBAC["bindings"],
                                      RELEASE_RBAC["roles"])
    cluster_state(recorded_api, "clusterrole", error="the cluster would not answer")
    cluster_state(recorded_api, "clusterrolebinding", error="the cluster would not answer")
    fake_binaries.take_calls()

    with caplog.at_level(logging.WARNING):
        uninstaller.uninstall_kubementat(assume_yes=True)

    assert swept_rbac_deletes(recorded_api) == set(), \
        "the cluster would not say what it had, so nothing here may say what it deleted"
    assert "would not answer" in caplog.text, \
        "the warning is the only thing standing between this run and a clean teardown it did not earn"


def test_the_cert_manager_definitions_are_swept_with_the_ones_kubementat_owns(uninstaller,
                                                                              fake_binaries,
                                                                              fixture_repo, recorded_api):
    """cert-manager is a component, so its definitions leave with the component sweep.

    The old list never covered `cert-manager.io` or `acme.cert-manager.io`, so a release the
    `helm uninstall` line could not take -- the one the script measured, where the namespace went
    with the release secret and nothing else went with it -- left six cluster-scoped definitions
    serving objects no release owns. Two API groups, and both: the chart declares six definitions
    across them (`helm pull jetstack/cert-manager --version v1.16.4 --untar`, then
    `templates/crds.yaml`; the same six on v1.10.1, re-run 2026-10-30), and
    `orders.acme.cert-manager.io` is the row that only the wider group notices, which is the shape a
    one-group guess would have missed.

    `cert-manager`'s own namespace is what carries them, and it is in the sweep already, so the
    difference this test is about is not *whether* they leave but *who* asks the cluster for them.
    """
    uninstaller.uninstall_kubementat(assume_yes=True)

    deleted = deleted_names(recorded_api, "delete_custom_resource_definition")
    for name in ("certificates.cert-manager.io", "orders.acme.cert-manager.io"):
        assert name in deleted, \
            f"{name} is a cert-manager definition and cert-manager is a component: {sorted(deleted)}"
    assert "prometheusrules.monitoring.coreos.com" not in deleted, \
        "sweeping two more groups is not the same as sweeping the cluster"
