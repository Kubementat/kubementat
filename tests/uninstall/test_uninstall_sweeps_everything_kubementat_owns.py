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
from cluster_fixture import configuration_only_context
from conftest import as_k8s_object, cluster_deletes, cluster_state
from kubernetes.client.exceptions import ApiException

from cli.consent import uninstall_consent
from cli.context import RENDERED_REFUSALS
from kubementat.automations.uninstall import kubementat as uninstall_slice
from kubementat.automations.uninstall.kubementat import uninstall_kubementat
from kubementat.consent import Consent
from kubementat.core import constants
from kubementat.errors import (
    ConfirmationDeclinedError,
    EnvironmentConfigurationFileMissingError,
    UninstallIncompleteError,
)
from kubementat.infrastructure import shell
from kubementat.infrastructure.kubernetes import KubernetesUtils

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
# because a `chart` is the field the router sweep reads (`automations/uninstall/residue.py`'s `_release_runs_traefik_proxy`):
# helm writes it as `<name>-<version>`, so it says which controller the release ran and not what
# anybody chose to call it. The Traefik rows are sourced rather than written -- `42.1.3` is the pin
# this checkout's helmfile carries for kubementat's own release, and k3s's bundled controller is
# `traefik-41.6.x`, of which the row below is one patch -- and the other four are what some chart
# would answer with: none of them is Traefik-shaped, which is the whole of what is asked of them.


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
# The release half is sourced, not imagined: `install()` in `infrastructure/tekton/install.py`
# `kubectl apply`s the three
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

# kubementat's own writes: stamped, as `infrastructure/tekton/triggers.py`'s `_trigger_labels` and
# `automations/install/pipelines.py`'s `_setup_pipelines_setup_role_bindings` stamp them, and bound wherever the team's
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
    named = uninstall_slice.HELM_DEPLOYER_CLUSTER_ROLE
    roles = {("clusterrole", kwargs["name"])
             for method, kwargs in recorded_api.calls
             if method == "delete_cluster_role" and kwargs["name"] != named}
    bindings = {("clusterrolebinding", kwargs["name"])
                for method, kwargs in recorded_api.calls if method == "delete_cluster_role_binding"}
    return roles | bindings


def deleted_rbac(recorded_api):
    """Everything cluster-scoped the run deleted, the sweep and the by-name line together."""
    return swept_rbac_deletes(recorded_api) | {
        ("clusterrole", uninstall_slice.HELM_DEPLOYER_CLUSTER_ROLE)}


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
    real_run = shell.run

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
    monkeypatch.setattr("kubementat.automations.uninstall.residue.shell.run", a_helm_that_forgets_what_it_uninstalled(fake_binaries))
    fake_binaries.respond("helm", json.dumps(HELM_RELEASES), subcommand="list")
    cluster_state(recorded_api, "crd", CRD_ITEMS)
    cluster_state(recorded_api, "persistentvolumeclaims", {})
    # IngressClasses answer empty, because that is what the shim's silence used to mean and what
    # the cluster this fixture stands for reports: kubementat does not install a controller, so a
    # default cluster has no class of its own. The tests that want one say so.
    cluster_state(recorded_api, "ingressclass", [])
    return configuration_only_context(fixture_repo.environment, None)


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

    uninstall_kubementat(uninstaller, consent=Consent.assumed())

    deleted = namespaces_deleted(recorded_api)
    assert "dev2-workloads" in deleted, "the namespace it did declare was not swept, so the assert cannot tell a skip from a stall"
    assert fixture_repo.app_namespace in deleted and fixture_repo.pipeline_namespace in deleted


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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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

    with pytest.raises(EnvironmentConfigurationFileMissingError) as raised:
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

    assert "static.json" in str(raised.value)
    assert client_writes(recorded_api) == []
    assert written(fake_binaries) == []


def test_the_cluster_is_printed_before_a_single_object_is_deleted(uninstaller, caplog):
    """A human with a terminal has to be able to object, so the log says the cluster first.

    Ordering is the whole contract here, so it is asserted on the log the operator reads rather
    than on the calls, which arrive in two logs and cannot be interleaved.
    """
    with caplog.at_level(logging.INFO):
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

    printed = [index for index, record in enumerate(caplog.records)
               if "You are going to uninstall kubementat from the following cluster" in record.message]
    first_deletion = [index for index, record in enumerate(caplog.records)
                      if "Deleting namespace" in record.message or "Deleting cluster role" in record.message]
    assert printed, "the cluster was never printed"
    assert first_deletion, "nothing was deleted, so this test is proving nothing"
    assert printed[0] < min(first_deletion)
    assert "k3s-server-1" in caplog.text, "the nodes are what say this is the right cluster"


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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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

    # The def hands back the residue now and the raise belongs to the value, so this asserts both
    # halves in order: the list the run returned, then the sentence the CLI renders from it.
    result = uninstall_kubementat(uninstaller, consent=Consent.assumed())
    assert any("prometheus-server" in item for item in result.residue), result.residue
    with pytest.raises(UninstallIncompleteError) as raised:
        result.raise_if_incomplete()

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
    # Spied on the class rather than an instance: the command builds its own client from the
    # `Context` now, so an instance-level spy would watch a client the run never uses.
    real_delete = KubernetesUtils.delete_namespace

    def deleting(self, namespace, **kwargs):
        order.append(("namespace", namespace))
        return real_delete(self, namespace, **kwargs)

    real_crd = KubernetesUtils.delete_cluster_scoped

    def deleting_crd(self, kind, name, **kwargs):
        if kind == "crd":
            order.append(("crd", name))
        return real_crd(self, kind, name, **kwargs)

    monkeypatch.setattr(KubernetesUtils, "delete_cluster_scoped", deleting_crd)
    monkeypatch.setattr(KubernetesUtils, "delete_namespace", deleting)
    on_a_cluster(recorded_api, sorted(set(constants.DEFAULT_NAMESPACES.values())
                                      | {fixture_repo.pipeline_namespace,
                                         fixture_repo.app_namespace,
                                         "linkerd", "linkerd-viz"}
                                      | CLUSTER_ADDONS))

    uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

    assert not [name for name in namespaces_deleted(recorded_api) if "nginx" in name], \
        "a namespace that was never there must not be named as if it had been deleted"
    assert not [call for call in recorded(fake_binaries)
                if call.argv[:2] == ["helm", "uninstall"] and "nginx-ingress" in call.argv], \
        "there is no controller here, so there is no release to uninstall"
    assert not deleted_names(recorded_api, "delete_ingress_class"), \
        "no class here is bound to what kubementat removed, so nothing is deleted"
    assert "nginx-ingress is not installed" in caplog.text


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
    uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
    # Spied on the class rather than an instance: the command builds its own client from the
    # `Context` now, so an instance-level spy would watch a client the run never uses.
    real_delete = KubernetesUtils.delete_namespace

    def deleting(self, namespace, **kwargs):
        order.append(("namespace", namespace))
        return real_delete(self, namespace, **kwargs)

    real_crd = KubernetesUtils.delete_cluster_scoped

    def deleting_crd(self, kind, name, **kwargs):
        if kind == "crd":
            order.append(("crd", name))
        return real_crd(self, kind, name, **kwargs)

    monkeypatch.setattr(KubernetesUtils, "delete_cluster_scoped", deleting_crd)
    monkeypatch.setattr(KubernetesUtils, "delete_namespace", deleting)

    uninstall_kubementat(uninstaller, consent=Consent.assumed())

    router_crds = [index for index, (kind, name) in enumerate(order)
                   if kind == "crd" and (name.endswith(".traefik.io")
                                         or name.endswith(".hub.traefik.io"))]
    router_namespaces = [index for index, (kind, name) in enumerate(order)
                         if kind == "namespace" and name == TRAEFIK_NAMESPACE]
    assert router_crds and router_namespaces, \
        "neither the definitions nor the namespace was deleted, so the order proves nothing"
    assert max(router_crds) < min(router_namespaces), \
        "a definition deleted after its namespace is how the namespace gets stuck"


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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
    real_run = shell.run
    listings = []

    def a_cluster_that_goes_quiet(argv, **kwargs):
        if argv[:2] == ["helm", "list"]:
            listings.append(argv)
            if len(listings) > 1:
                fake_binaries.respond("helm", "connection refused\n", exit_code=1,
                                      subcommand="list")
        return real_run(argv, **kwargs)

    monkeypatch.setattr("kubementat.automations.uninstall.residue.shell.run", a_cluster_that_goes_quiet)
    fake_binaries.take_calls()
    recorded_api.calls.clear()

    with caplog.at_level(logging.INFO):
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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

    # `residue` is the value's own field now; see the note on
    # `test_the_residue_is_a_field_and_not_only_an_exception_sentence`.
    result = uninstall_kubementat(uninstaller, consent=Consent.assumed())
    assert result.residue and any("cert-manager" in item for item in result.residue), result.residue
    assert not result.clean
    with caplog.at_level(logging.INFO), pytest.raises(UninstallIncompleteError):
        result.raise_if_incomplete()
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
    monkeypatch.setattr("kubementat.infrastructure.kubernetes.namespaces.NAMESPACE_GONE_WAIT_S", 0)

    # The one case here that is about *which* namespace stuck, so the residue is read as a list and
    # not only as the exception's text.
    result = uninstall_kubementat(uninstaller, consent=Consent.assumed())
    assert any("grafana" in item and "Terminating" in item for item in result.residue), result.residue
    with caplog.at_level(logging.INFO), pytest.raises(UninstallIncompleteError) as raised:
        result.raise_if_incomplete()
    assert "Terminating" in str(raised.value), \
        "the residue says the namespace exists but not that the cluster is mid-deletion: %s" % raised.value
    assert "DONE" not in caplog.text, "it said DONE over a deletion the cluster had not finished"


def test_answering_no_deletes_nothing(fixture_repo, dry_run_env, fake_binaries, recorded_api,
                                     monkeypatch):
    """`n` at the prompt is a cancellation, not a partial uninstall.

    The bash had the same rule, but it was inside the script, so the CLI had to run a script to
    find out. Now the answer is the library's and it is given before the first command.
    """
    present = set(constants.DEFAULT_NAMESPACES.values()) | {fixture_repo.pipeline_namespace,
                                                           fixture_repo.app_namespace} | CLUSTER_ADDONS
    on_a_cluster(recorded_api, present)
    # The question is the CLI's now, so this is the CLI's consent builder doing the asking: the
    # real prompt, the real loop and the real cancellation sentence, with the terminal made out of
    # two monkeypatches. Stubbing the ask instead would raise whatever this test wrote down and call
    # that a proof.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    with pytest.raises(ConfirmationDeclinedError) as raised:
        uninstall_kubementat(configuration_only_context(fixture_repo.environment, None),
                             consent=uninstall_consent(fixture_repo.environment, yes=False))

    message = str(raised.value)
    assert "Cancelled uninstall script" in message
    assert "nothing was deleted" in message.lower(), \
        "the cancellation has to say what it did, and that is the half the operator is blind to"
    # "Fails for a reason" is no longer a number on the exception: since the library names its own
    # refusals, the exit code belongs to the CLI's rendering of them. A refusal that is in that
    # list exits 1; one that is not reaches the terminal as a traceback.
    assert type(raised.value) in RENDERED_REFUSALS, \
        "declining has to be a refusal kmt prints, and printing is what decides the exit code"
    assert client_writes(recorded_api) == [], "answering no deleted something through the client"
    assert written(fake_binaries) == [], "answering no deleted something with a command"


# ------------------------------------------------------------ cluster-scoped RBAC
# Everything below lives outside a namespace, which is what makes it the residue an uninstall has
# to have a *rule* for: the script this file tests against deleted exactly one ClusterRole by name
# and said DONE over thirty-odd survivors, and the lane's
# `no-extra-cluster-scoped:clusterroles` assert read the result as a clean teardown.


def test_the_cluster_scoped_rbac_the_release_manifests_applied_is_swept(uninstaller, fixture_repo,
                                                                       fake_binaries, recorded_api,
                                                                       caplog):
    """The RBAC the Tekton releases put outside every namespace goes too, and it is not labelled.

    `install()` (`infrastructure/tekton/install.py`) applies the three release manifests with
    `kubectl apply -f`, so
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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

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

    uninstall_kubementat(uninstaller, consent=Consent.assumed())

    deleted = deleted_rbac(recorded_api)
    for kind, name in [("clusterrole", "cluster-admin"), ("clusterrole", "prometheus-k8s"),
                       ("clusterrole", "gitlab-runner-cluster-admin"),
                       ("clusterrolebinding", "kubeadm:cluster-admin"),
                       ("clusterrolebinding", "prometheus-k8s-read"),
                       ("clusterrolebinding", "gitlab-runner-cluster-access")]:
        assert (kind, name) not in deleted, \
            f"kubementat never wrote or bound {kind}/{name}, so this sweep had no claim on it"
    assert deleted, "the sweep found nothing of its own either, which makes the pass above vacuous"


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
        uninstall_kubementat(uninstaller, consent=Consent.assumed())

    assert swept_rbac_deletes(recorded_api) == set(), \
        "the cluster would not say what it had, so nothing here may say what it deleted"
    assert "would not answer" in caplog.text, \
        "the warning is the only thing standing between this run and a clean teardown it did not earn"
