"""The deletes: releases, claims, cluster-scoped RBAC, and the CRDs helm never takes back.

Every function here returns what survived rather than raising. An uninstall that stops at the first
thing it cannot delete is a half-deleted platform, and a run that reports `DONE` over what survived
is the failure this command exists to stop -- so the verdict is collected and raised once, by the
command that owns the order.

The shape each delete shares is a read asked of the cluster and not of a name list, for one reason
stated once: the pinned release changes what is on a cluster, and a name written here is a name
that goes stale the day upstream renames one. Where a read may fail (`CustomResourceDefinition`,
`IngressClass`, a cluster that will not answer), the answer is a warning and not a silence, because
"there is none" and "I could not ask" look identical to a residue assert and are not the same fact.
"""


import json
import logging

from kubernetes import client

from kubementat.core.identity.target import helm_target_args
from kubementat.infrastructure import shell
from kubementat.infrastructure.helm import helm_list_releases_argv
from kubementat.infrastructure.kubernetes import KubernetesUtils, apply
from kubementat.infrastructure.kubernetes.constants import (
    MANAGED_BY_KUBEMENTAT,
    MANAGED_BY_LABEL,
)


# The API groups the `jetstack/cert-manager` chart's definitions land in. cert-manager is a
# component (`constants.DEFAULT_NAMESPACES`, `templates/.../helmfile.yaml.gotmpl:163`), so a
# `helm uninstall` does reach these: the chart renders its definitions from `templates/`
# rather than shipping a `crds/` directory, which is the opposite of the Traefik case below
# and the reason this is a *component* group and not one of the "helm never took this back"
# sweeps. What it is not immune to is the failure the release sweep reports and then lets the
# namespace delete paper over: `helm uninstall` exits non-zero, the namespace goes with the
# release secret, and a cluster-scoped definition owned by no release outlives a delete that
# never reaches it. So they are swept by the component sweep, the same way `tekton.dev` is.
#
# Both groups, and not only the first, because the chart ships both. The evidence is the chart
# this checkout resolves (`helm pull jetstack/cert-manager --version <v> --untar`, re-run here
# on 2026-10-30 for v1.10.1 and v1.16.4 -- the same six definitions):
#   cert-manager.io       - certificates, certificaterequests, clusterissuers, issuers
#   acme.cert-manager.io  - challenges, orders
#
# Listing `acme.cert-manager.io` beside `cert-manager.io` is not what widens the sweep -- a
# name like `orders.acme.cert-manager.io` ends in `.cert-manager.io` too, so the first group
# already matches all six -- it is what says so. Six definitions, two API groups, and a reader
# who does not have to know kubernetes suffix rules to check that count: that is the same
# argument `TRAEFIK_CRD_GROUPS` makes about its fifteen.
CERT_MANAGER_CRD_GROUPS = ("cert-manager.io", "acme.cert-manager.io")

# API groups kubementat installs CRDs into, and whose CRDs it therefore has to take away
# again. `tekton.dev` covers the subdomains the CRD names carry: `triggers.tekton.dev` and
# `resolution.tekton.dev` both end with their parent, which is the same set the lane's
# residue assert measures. cert-manager joins it from the component side, for the reason its
# own block gives.
CRD_GROUPS_KUBEMENTAT_OWNS = ("tekton.dev",) + CERT_MANAGER_CRD_GROUPS

# The API groups the `traefik/traefik` chart's `crds/` directory lands in, and the residue a
# `helm uninstall` never takes back: `helm install` applies that directory before the release
# and `helm uninstall` removes none of it, so the release the namespace sweep deletes by name
# is only half of a Traefik install. What is left is cluster-scoped, owned by no release secret
# and named by nothing in kubementat, which is precisely the shape of thing a residue assert
# reads as a clean teardown.
#
# Both groups, and not only the first, because the directory ships both. The evidence is
# `helm show crds traefik/traefik --version 42.1.3` (re-run here on 2026-10-28, 25 definitions):
#   traefik.io     - ingressroutes, ingressroutetcps, ingressrouteudps, middlewares,
#                    middlewaretcps, serverstransports, serverstransporttcps, tlsoptions,
#                    tlsstores, traefikservices
#   hub.traefik.io - accesscontrolpolicies, aiservices, apiauths, apibundles,
#                    apicatalogitems, apiplans, apiportalauths, apiportals, apiratelimits,
#                    apis, apiversions, contentitems, managedapplications,
#                    managedsubscriptions, uplinks
# Sweeping `traefik.io` alone would delete ten definitions, log a clean sweep and leave fifteen
# serving objects nobody owns, which is the failure a guessed group name produces and the reason
# this is written down rather than inferred from the v2-to-v3 migration notes.
#
# What this does *not* sweep, stated where the sweep is: k3s's bundled Traefik keeps its release
# and its namespace in `kube-system`, and the IngressClass kubementat's own release created goes
# with that release rather than with anything here. The definitions are the one Traefik object
# that is neither namespaced nor released, so they are the only Traefik thing whose ownership
# the cluster has to answer for: they are shared with whatever proxy is running, and on the
# everyday cluster that is the node's own controller in `kube-system`, which is exactly what
# applying a chart's `crds/` into a cluster that already had them produces. So the sweep is
# gated on a read -- see `TRAEFIK_PROXY_CHART_NAME` and
# `delete_crds_traefik_helm_leaves_behind` -- and what is deleted are only the definitions no
# running release is serving. What may not be touched is written down as releases and
# namespaces and not as definitions, which is the gap that read closes.
TRAEFIK_CRD_GROUPS = ("traefik.io", "hub.traefik.io")

# The chart name that says a Traefik proxy is running, read from the `chart` field of a
# `helm list` row rather than from a release name. helm prints that field as
# `<name>-<version>`, so k3s's bundled controller answers `traefik-41.6.1` and kubementat's own
# pinned release (`constants.TRAEFIK_CHART`) answers `traefik-42.1.3` -- the same name, a
# different origin, and the only one of the two that is about to be gone when this is asked.
#
# It is deliberately the chart and not the release: what the definitions serve is the controller
# and not the label somebody put on it, and a release that was renamed is exactly the case a name
# list gets wrong. The deprecated companion is *not* matched, because its chart is named
# `traefik-crds`, it ships no proxy, and kubementat never installed it
# (`constants.TRAEFIK_CHART`) -- a cluster carrying only that one has no controller left for
# these definitions to serve, so counting it would turn a clean teardown into a residue assert
# that passes over definitions nobody owns.
TRAEFIK_PROXY_CHART_NAME = "traefik"

# The API group Alloy's `crds/` subchart installs and `helm uninstall` never takes back.
#
# This one is easy to miss, because the definition is not where you look for it. The `grafana/alloy`
# chart pulls in a dependency chart named `crds` whose whole contents are
# `charts/crds/crds/monitoring.grafana.com_podlogs.yaml` -- one definition, one group, verified
# against 1.15.0 on 2026-11-01. Helm applies a dependency's `crds/` on install and removes nothing
# from it on uninstall, so the definition outlives the release, the namespace, and the log line that
# said the teardown was clean. The full lane found this: after `kmt uninstall`,
# `assert:no-extra-cluster-scoped:customresourcedefinitions` came back
# `unexpected-1 (podlogs.monitoring.grafana.com)`.
#
# One group and one definition, and both are written here rather than counted, because the count is
# what changes the day a second Alloy chart adds one.
ALLOY_CRD_GROUPS = ("monitoring.grafana.com",)

# The chart field that says an Alloy is running, read the same way Traefik's is: helm prints
# `<name>-<version>`, so this matches `alloy-1.15.0` whatever the release is called and whatever
# namespace it is in.
#
# Why the gate exists when kubementat is the only thing in this product that installs Alloy:
# deleting a CustomResourceDefinition deletes every object of that kind with it. A Grafana Alloy or
# Grafana Agent installed by someone else, in a namespace kubementat does not own and therefore
# would not have swept, still has `PodLogs` resources it is collecting logs with, and those are not
# kubementat's to take away. The release sweep cannot tell you that: it looks inside
# kubementat's own namespaces, and this cluster read is deliberately every namespace on the cluster.
ALLOY_CHART_NAME = "alloy"


def delete_cluster_scoped_rbac_kubementat_owns(ctx, plan: dict) -> list:
    '''
    Delete the ClusterRoles and ClusterRoleBindings kubementat put on the cluster.

    A namespace delete takes none of them, a `helm uninstall` takes only the ones that happen
    to live inside a release, and `helm-deployer-cluster-role` -- the one object this checkout
    can name -- is a single line out of thirty-odd. So the set is asked of the cluster and not
    read off a list of names, for the same reason the CRDs are: the release the pinned
    configuration installs changes what is in it, and the sweep has to agree.

    Two rules answer "whose is this?", and which one applies is decided by where the object
    came from:

    * **kubementat's own claim.** `metadata.labels["managed-by"] == "kubementat"`, the label
      every writer in this product sends (`tekton_utils.helm_deployer_cluster_role`,
      `bindings_and_interceptors_cluster_role`, `cleanup_job_cluster_role`,
      `_setup_pipelines_setup_role_bindings`). It is kubementat saying so, which is
      the only claim a teardown needs before it deletes something.
    * **who the binding binds.** A `ClusterRoleBinding` every one of whose subjects is a
      ServiceAccount inside a namespace `plan["namespaces"]` says kubementat owns. That is how
      the release manifests get swept, and it is why the sweep runs while the namespaces are
      still there: the subject namespace is the whole of the evidence, and it is read from the
      object rather than from a name that would have to be updated every time upstream renames
      one. The `ClusterRole`s reached through it go too -- `roleRef` is the other half of the
      same fact -- because an orphan of a role nothing binds is exactly the residue the assert
      reads.

    Neither rule is a licence to sweep the whole namespace set. A ServiceAccount kubementat
    never created can be bound by somebody else's controller, and what that binding grants is
    not kubementat's to take away; a `ClusterRole` reached by neither rule, from
    `cluster-admin` down, is not touched either. That is the cost of not carrying a name list,
    and it is paid on purpose: deleting somebody else's RBAC to make a residue assert go green
    is a worse uninstall than leaving it. What went and what stayed are therefore two different
    facts, and the log line this prints carries the first of them by name.

    Deleting a `ClusterRoleBinding` whose subjects all point into namespaces this run is about
    to delete anyway is not the false positive it looks like: the accounts it grants are gone,
    so what is left behind is a binding that can never be exercised again. That is what makes
    this rule safe enough to run against a shared cluster, and it is the sentence a later
    reader should replace rather than the rule they should quietly widen.

    What it does *not* reach, stated where the sweep is: read this way, the three pinned release
    manifests give up sixteen of their sixteen `ClusterRoleBinding`s and fifteen of their
    seventeen `ClusterRole`s (`pipeline/v1.17.1/release.yaml`, `triggers/v0.37.2/release.yaml`
    and `dashboard/v0.73.0/release-full.yaml`, read 2026-10-30). The two it does not are
    `tekton-triggers-eventlistener-roles` and `...-clusterroles`, which no binding in the
    release references because the EventListener controller binds them at run time, and which
    carry no `managed-by` for the same reason. A rule that reached them would have to know
    upstream's role names, which is the one thing this method exists to refuse: they are a
    residue the lane will still see, they are named here rather than discovered there, and a
    lane that goes green without them is not yet measuring what this paragraph says.

    Parameters:

    - plan: dict - What `read_uninstall_plan` read. Only `plan["namespaces"]` matters here,
      and it is the set the subjects are checked against.

    Returns:
        list[str]: What survived, empty when everything went. A cluster that will not answer
            the read is answered the way `delete_crds_in_groups` answers it -- warned about,
            not raised through -- because "there is none" and "I could not ask" are not the
            same fact and only one of them is a clean sweep.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    owned = set(plan["namespaces"])
    bindings = read_cluster_scoped_items(ctx, "clusterrolebinding")
    roles = read_cluster_scoped_items(ctx, "clusterrole")
    # Bound first, then the roles they bind: `roleRef` is the other half of the same fact, and
    # a `ClusterRole` a release installed has to go with the binding that made it live. Only a
    # binding this sweep already owns is read that way, so the rule cannot reach a role through
    # somebody else's binding -- which is the whole reason `cluster-admin` survives a
    # `kubeadm:cluster-admin` ClusterRoleBinding sitting on the same cluster.
    doomed = []
    bound_to = set()
    for item in bindings:
        name = apply._metadata_name(item)
        if not name:
            continue
        if (_rbac_is_claimed_by_kubementat(item)
                or _subject_lands_in_a_kubementat_namespace(item, owned)):
            doomed.append(("clusterrolebinding", name))
            bound_to.add(((item.get("role_ref") or {}).get("name")) or "")
    for item in roles:
        name = apply._metadata_name(item)
        if not name:
            continue
        if _rbac_is_claimed_by_kubementat(item) or name in bound_to:
            doomed.append(("clusterrole", name))
    doomed = {entry for entry in doomed if entry[1]}


    if not doomed:
        logging.info("No cluster-scoped RBAC on this cluster is kubementat's to delete.")
        return []
    logging.info(f"Deleting {len(doomed)} cluster-scoped RBAC object(s) kubementat applied: "
                 f"{', '.join(f'{kind}/{name}' for kind, name in sorted(doomed))}")
    survivors = []
    for kind, name in sorted(doomed):
        outcome = kubernetes_utils.delete_cluster_scoped(kind, name)
        if outcome:
            continue
        survivors.append(f"{kind} {name} (the cluster refused the delete"
                         + (f"): {outcome.reason}" if outcome.reason else ")"))
    return survivors


def read_cluster_scoped_items(ctx, kind: str) -> list:
    '''
    The items of one cluster-scoped kind, as plain documents, and an empty list the cluster
    was told nothing from.

    A failed read is not "there is nothing here", and the two must not be logged the same way
    -- `delete_crds_in_groups` gives the argument, and the warning is repeated here for the
    same reason: a residue assert cannot tell a sweep that found nothing from one that could
    not ask.

    The documents are `to_dict()`-shaped rather than client models so that everything this
    sweep asks of an object -- its name, its `managed-by` label, its `roleRef`, its subjects --
    is asked the same way it was asked of the JSON `kubectl get` printed. Converting who answers
    does not change the rule the sweep applies, and that is the whole point.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    try:
        if kind == "clusterrolebinding":
            items = kubernetes_utils.list_cluster_role_bindings()
        else:
            items = kubernetes_utils.list_cluster_roles()
        return [item.to_dict() for item in items]
    except client.exceptions.ApiException as error:
        logging.warning(f"The cluster would not answer for its {kind}s"
                        f" (status {error.status}), so nothing here is claimed as deleted.")
        return []


def _rbac_is_claimed_by_kubementat(item: dict) -> bool:
    '''
    Whether kubementat's own `managed-by` claim is on this object.

    The same label the install path stamps (`automation.py`'s role bindings,
    `tekton_utils._trigger_labels`, `CLEANUP_JOB_LABELS`), read off the object rather than
    matched against a name so a renamed object is still the object it is.
    '''
    labels = ((item.get("metadata") or {}).get("labels")) or {}
    return labels.get(MANAGED_BY_LABEL) == MANAGED_BY_KUBEMENTAT


def _subject_lands_in_a_kubementat_namespace(item: dict, owned: set) -> bool:
    '''
    Whether every subject of a ClusterRoleBinding sits inside a namespace kubementat owns.

    "Every" and not "any", and ServiceAccount only: a binding that also grants a group or a
    user is somebody else's by the same argument that makes a namespace-granted one
    kubementat's, and a binding with no subjects binds nothing and goes with nothing.
    `Group` subjects carry no namespace at all, which is why `cluster-admin` -- bound to
    `kubeadm:cluster-admin` and nothing else -- stays exactly where it is.
    '''
    subjects = item.get("subjects") or []
    if not subjects:
        return False
    return all(subject.get("kind") == "ServiceAccount"
               and subject.get("namespace") in owned and subject.get("namespace")
               for subject in subjects)


def uninstall_helm_releases(ctx, namespaces: list) -> list:
    '''
    Uninstall every release that lives in a kubementat namespace, and say what survived.

    Read from the cluster (`helm_list_releases_argv`) rather than from a component list, because the
    release that survived the last real uninstall was not on anybody's list. Anything
    inside a namespace kubementat owns is part of this installation, whoever applied it.

    A failing `helm uninstall` is reported and left to the namespace delete that follows:
    aborting in the middle would leave the platform half-deleted, which is a worse failure
    than a warning. It is still counted as residue, so the command cannot call it a success.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    if not namespaces:
        return []
    # The one release read the product has, read directly and not rebuilt here: which statuses
    # answer "what releases live here" is decided on helm_utils, and a sweep that asked its own
    # narrower question would uninstall less than the sweep's own residue assert claims. The
    # cluster goes on the command line and not in the environment, because a sweep that
    # deletes releases from whichever kubeconfig the operator's shell happens to point at is a
    # sweep that reports success against the wrong cluster.
    listing = shell.run(helm_list_releases_argv(kubernetes_utils.target), capture=True)
    rows = json.loads(listing.stdout or "[]")
    owned = set(namespaces)
    survivors = []
    for row in sorted((row for row in rows if row.get("namespace") in owned),
                      key=lambda row: (row.get("namespace", ""), row.get("name", ""))):
        name = row.get("name")
        namespace = row.get("namespace")
        logging.info(f"Uninstalling helm release '{name}' from namespace '{namespace}' ...")
        result = shell.run(["helm", "uninstall", *helm_target_args(kubernetes_utils.target),
                            name, "--namespace", namespace], capture=True, check=False)
        if not result.ok:
            logging.warning(f"helm could not uninstall '{name}' from '{namespace}' (exit"
                            f" {result.returncode}); deleting the namespace next takes it with it.")
            survivors.append(f"release {name} in namespace {namespace} (helm exit {result.returncode})")
    return survivors


def list_claims_in(ctx, namespace: str):
    '''The claims the namespace currently reports, as a list that may be empty.

    `None` means the namespace could not be read at all, which is not the same answer as a
    namespace that is empty: a teardown that reads the first as the second reports success on a
    cluster it never actually looked at.

    Said plainly: this is the read `helm_utils._delete_release_claims` used to
    ask with `kubectl get persistentvolumeclaims`, while this method asked it through the
    client. Same question, two answers, one uninstall. There is now one answer, in
    `KubernetesUtils.list_persistent_volume_claims`.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    try:
        return sorted(item.metadata.name for item
                      in kubernetes_utils.list_persistent_volume_claims(namespace))
    except client.exceptions.ApiException as error:
        logging.info(f"The cluster would not answer for the claims in namespace '{namespace}'"
                     f" (status {error.status}).")
        return None


def delete_claims_in(ctx, namespaces: list) -> list:
    '''
    Delete the claims kubementat created, instead of telling the operator about them.

    The old script printed advice and deleted nothing, so the uninstall's claim about claims
    could not be true and the lane's assert was measuring a paragraph of prose. They go by name,
    here, now.

    One claim per delete, and the verdict comes from the cluster afterwards rather
    than from the response that came back. Both halves of that are load-bearing. A bulk delete
    of several names fails if *any* of them cannot be deleted, so a teardown that trusts that
    verdict reports a failure that never happened: while the namespaces are going away, the API
    is still releasing volumes and one of the names it was handed may already be gone. What a
    claim's absence means is settled by asking it, and an absent object is what a teardown
    wanted, not what it should report as residue.

    Not-found is therefore *not* residue, but it is still said out loud: silence is how this
    function previously hid the real refusal behind a bare `exit 1`, and the lane had to be read
    backwards to find which namespace was involved. Anything the cluster refuses for any other
    reason is residue, is logged with the status it answered with rather than only a verdict,
    and is named in the error the command raises.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    residue = []
    for namespace in namespaces:
        claims = list_claims_in(ctx, namespace)
        if claims is None:
            logging.info(f"No claims to read in namespace '{namespace}'.")
            continue
        if not claims:
            continue
        logging.info(f"Deleting {len(claims)} claim(s) in namespace '{namespace}': {', '.join(claims)}")
        refused = {}
        for claim in claims:
            outcome = kubernetes_utils.delete_persistent_volume_claim(namespace, claim)
            if outcome:
                continue
            refused[claim] = outcome.reason
        if not refused:
            continue
        # The delete is not the verdict. Whether the claim survived is a question to ask the
        # namespace, so a refusal is only counted as residue when it left something behind.
        remaining = list_claims_in(ctx, namespace) or []
        for claim, reason in refused.items():
            if claim not in remaining:
                logging.info(f"Claim '{claim}' in namespace '{namespace}' is not there anymore, "
                             "for all that the delete refused it.")
                continue
            residue.append(f"claim {claim} in namespace {namespace} (the cluster refused the "
                           f"delete, and it is still there): {reason}" if reason else
                           f"claim {claim} in namespace {namespace} "
                           "(the cluster refused the delete, and it is still there)")
    return residue


def delete_crds_kubementat_owns(ctx) -> list:
    '''
    Delete the CRDs kubementat installed, asking the cluster which ones those are.

    Nineteen names were written in a script. A Tekton release that adds one is then not
    deleted, and the residue assert passes over a cluster that still serves
    `runs.tekton.dev`. Listing them by group means a CRD nobody remembered still goes.

    Returns:
        list[str]: What survived, empty when everything went.
    '''
    return delete_crds_in_groups(ctx, CRD_GROUPS_KUBEMENTAT_OWNS,
                                       absent="No kubementat CRDs on the cluster.")


def delete_crds_traefik_helm_leaves_behind(ctx) -> list:
    '''
    Delete the CustomResourceDefinitions a Traefik release leaves behind when it goes.

    `helm install` applies the chart's `crds/` directory before it runs the release, and
    `helm uninstall` removes none of it. So the release that `kmt uninstall` takes by the name
    the cluster reports is half of an install: the definitions, and every IngressRoute,
    Middleware or TLSOption written against them, stay on the cluster under no owner at all.
    They are cluster-scoped, so no namespace delete reaches them either, and nothing else in
    kubementat names them once the release is gone.

    The group is the rule and the cluster supplies the names, which is the shape of
    `delete_ingress_classes_bound_to` rather than of a name list: read what is there, delete
    what belongs to one of `TRAEFIK_CRD_GROUPS`, and let a cluster that cannot answer be a
    cluster with nothing to delete. It is deliberately *not* a sweep of the IngressClass: the
    class kubementat's release created goes with that release, and the nginx sweep's controller
    string stays pointed at `k8s.io/ingress-nginx` so it never finds it.

    The third question is answered by the cluster too, and asked here rather than in a comment:
    whether these definitions are still somebody's. They are shared with whatever proxy is
    running, so the sweep runs only after `uninstall_helm_releases` has had its turn and only
    when that read finds no Traefik release left -- which is the whole of the k3s case, where the
    node's controller stayed in `kube-system` and its IngressRoutes are read out of these very
    definitions, so deleting one is the garbage collection that takes them down. The read is
    `helm_list_releases_argv` over every namespace, the same call the release sweep makes, because the answer
    has to be the cluster's: a list of namespaces where Traefik may be hiding is a list, and this
    is the one decision here that a stale list gets backwards.

    Returns:
        list[str]: What survived -- nothing when the sweep ran and everything went, nothing when
            there was nothing here, nothing when a proxy is still serving from the definitions
            (they are not residue this command created), and nothing when helm would not say,
            which is the one case reported as a warning rather than as a clean sweep.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    listing = shell.run(helm_list_releases_argv(kubernetes_utils.target), capture=True,
                        check=False)
    if not listing.ok:
        # The same not-fatal read `delete_crds_in_groups` gives, with a second reason to keep it
        # that way: a cluster that will not answer this question may well be running k3s's
        # controller, and the only safe answer to "are these mine?" when the cluster will not
        # say is "no". It is warned and not logged as absent, for the reason that method gives:
        # a sweep that found nothing and a sweep that could not ask look the same to the log.
        logging.warning(f"helm would not answer for the releases on the cluster (exit"
                        f" {listing.returncode}), so no Traefik CustomResourceDefinitions here"
                        f" are claimed as deleted.")
        return []
    still_running = sorted((str(row.get("namespace") or ""), str(row.get("name") or ""),
                            str(row.get("chart") or ""))
                           for row in json.loads(listing.stdout or "[]")
                           if _release_runs_traefik_proxy(row))
    if still_running:
        logging.info("Leaving the Traefik CustomResourceDefinitions where they are:"
                     + "".join(f" release '{name}' in namespace '{namespace}' still runs the"
                               f" chart '{chart}'," for namespace, name, chart in still_running)
                     + " and its controller is served by them.")
        return []
    return delete_crds_in_groups(ctx, TRAEFIK_CRD_GROUPS,
                                       absent="No Traefik CRDs on the cluster.")


def delete_crds_alloy_helm_leaves_behind(ctx) -> list:
    '''
    Take away the Alloy CRDs that `helm uninstall` does not, and leave them alone if an Alloy lives.

    Alloy installs `podlogs.monitoring.grafana.com` through a dependency chart whose `crds/`
    directory helm applies and never removes, so a release sweep and a namespace delete both come
    back clean while a cluster-scoped definition remains named by nothing. That is residue, so it
    is swept -- but only when nothing is still collecting logs with it, because deleting a
    CustomResourceDefinition deletes every `PodLogs` object of every namespace with it, including
    ones kubementat never made.

    The read is every release on the cluster and not the releases inside kubementat's namespaces:
    a foreign Alloy installed somewhere this tool would not sweep is exactly the case this guards,
    and the release sweep above has already had its turn at those namespaces.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    listing = shell.run(helm_list_releases_argv(kubernetes_utils.target), capture=True,
                        check=False)
    if not listing.ok:
        logging.warning(f"helm would not answer for the releases on the cluster (exit"
                        f" {listing.returncode}), so no Alloy CustomResourceDefinitions here are"
                        f" claimed as deleted.")
        return []
    still_running = []
    for row in json.loads(listing.stdout or "[]"):
        chart = str(row.get("chart") or "")
        # `alloy-1.15.0` and bare `alloy` both collect; `alloy-crds-1.0` would not, and is not a
        # chart kubementat knows, so a prefix match on `alloy-` rather than on `alloy` is the rule.
        if chart == ALLOY_CHART_NAME or chart.startswith(ALLOY_CHART_NAME + "-"):
            still_running.append("'%s' in namespace '%s' (chart %s)"
                                 % (row.get("name"), row.get("namespace"), chart))
    if still_running:
        logging.info("Leaving the Alloy CustomResourceDefinitions where they are:"
                     + "".join(f" release {row} still collects logs through them,"
                               for row in still_running))
        return []
    return delete_crds_in_groups(ctx, ALLOY_CRD_GROUPS,
                                       absent="No Alloy CRDs on the cluster.")


def _release_runs_traefik_proxy(row) -> bool:
    '''
    Whether one `helm list` row describes a Traefik proxy that is running.

    The field read is `chart` and not `name`, because the question is which controller the
    release ran and not what anybody chose to call it. helm prints the field as
    `<name>-<version>`, so the name is everything before the first hyphen and the version is
    what begins with a digit: `traefik-41.6.1` is the node's bundled controller and
    `traefik-42.1.3` is kubementat's own pinned chart, and either of them is still a proxy if
    somebody renamed the release around it -- which is exactly the case a release-name list gets
    wrong, and the reason the field is read rather than a name compared.

    Requiring the digit is also what keeps the deprecated `traefik-crds` companion out: its
    chart is named `traefik-crds`, so its version slot begins with `crds` rather than with a
    number, and it ships no proxy. A cluster carrying only that one has nothing left serving
    these definitions, so counting it as a controller that stayed leaves them behind and lets a
    residue assert call the teardown clean over fifteen objects nobody owns.
    '''
    name, _, version = str(row.get("chart") or "").partition("-")
    return name == TRAEFIK_PROXY_CHART_NAME and version[:1].isdigit()


def delete_crds_in_groups(ctx, groups, absent: str = "No CRDs to delete.") -> list:
    '''
    Delete every CustomResourceDefinition whose name ends in one of *groups*.

    The cluster answers for the names: a group is a rule about what belongs to kubementat,
    not a list of objects, and a definition the pinned release added that nobody wrote down
    is still kubementat's. Deleting a definition takes its instances with it, which is why a
    group that is being removed outright is deleted before its namespaces and not after.

    Parameters:

    - groups: The API groups to sweep, compared against the CRD name's suffix.
    - absent: The line to log when the cluster has none of them, so a sweep that found
      nothing reads differently from one that found nothing to delete.

    Returns:
        list[str]: What survived, empty when everything went. A CRD that will not delete is
            reported and not fatal, because the rest of the uninstall still has to run, and a
            cluster that will not answer the read at all is answered the same way rather than
            raised through: the uninstall has no use for a definition it could not ask for.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    try:
        definitions = kubernetes_utils.list_custom_resource_definitions()
    except client.exceptions.ApiException as error:
        # The read is not fatal, for the reason `delete_ingress_classes_bound_to` gives: a
        # cluster old enough to predate `CustomResourceDefinition`, or a kubeconfig pointed
        # somewhere that will not answer, replies with an error and no items. It is said out
        # loud rather than logged as `absent`, because "I could not ask" and "there is nothing
        # here" look the same to a residue assert and are not the same fact.
        logging.warning(f"The cluster would not answer for its CustomResourceDefinitions"
                        f" (status {error.status}), so nothing here is claimed as deleted.")
        return []
    names = sorted(item.metadata.name
                   for item in definitions
                   if any(str(item.metadata.name).endswith("." + group)
                          for group in groups))
    if not names:
        logging.info(absent)
        return []
    logging.info(f"Deleting {len(names)} CRD(s) kubementat installed: {', '.join(names)}")
    survivors = []
    for name in names:
        outcome = kubernetes_utils.delete_cluster_scoped("crd", name)
        if outcome:
            continue
        survivors.append(f"crd {name} (the cluster refused the delete"
                         + (f"): {outcome.reason}" if outcome.reason else ")"))
    return survivors
