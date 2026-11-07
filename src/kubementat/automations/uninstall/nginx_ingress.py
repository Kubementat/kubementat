"""The ingress controller this product no longer installs, swept without its own tooling.

nginx-ingress was dropped on the same decision that gave routing to Traefik. Nothing in this
checkout names it any more, so the residue is found by reading the cluster: the IngressClasses
whose controller field is the old chart's (cluster-scoped, and left behind by every namespace
delete), the release under the name the cluster reports, and the namespace its pods ran in. The
controller string is pointed deliberately away from Traefik's own class, which is live on the
cluster right now and goes with kubementat's own release.
"""


import logging

from kubernetes import client

from kubementat.infrastructure.kubernetes import KubernetesUtils

from kubementat.automations.uninstall.residue import uninstall_helm_releases


# The ingress controller, left behind rather than installed. kubementat stopped shipping
# nginx-ingress on the owner's decision of 2026-10-28, so there is no component entry, no
# `static.json` key and no chart for it. Traefik took the routing role on the same decision and
# is a component now (`constants.DEFAULT_NAMESPACES`, `constants.TRAEFIK_CHART`), so nothing
# below has to anticipate it: what this step owns is the residue of the controller this checkout
# does *not* install, which is a different object from anything Traefik puts on the cluster. The
# names below are the ones the released chart used, which is what a cluster that got it while
# kubementat installed it carries; they are not namespace configuration, for the same reason
# Linkerd's are not - a team that had renamed them would have been reading them out of a key
# this checkout does not write, and guessing at a rename is how an uninstall deletes the wrong
# namespace.
NGINX_INGRESS_NAMESPACES = ("nginx-ingress",)

# The controller field that binds an IngressClass to the image the release ran. Cluster-scoped,
# so no namespace delete takes it, and it is matched on the controller rather than on a class
# name: the default class is `nginx` and a second release names its own.
#
# It is `k8s.io/ingress-nginx` and nothing else. Traefik's own class carries
# `constants.TRAEFIK_CONTROLLER_NAME` (`traefik.io/ingress-controller`), which is not a prefix
# of this string and must never be made one: kubementat's release creates that class, so
# `helm uninstall` removes it, and a sweep that matched it would delete a class that is live on
# the cluster right now.
NGINX_INGRESS_CONTROLLER_PREFIX = "k8s.io/ingress-nginx"


def uninstall_nginx_ingress(ctx, plan: dict) -> list:
    '''
    Take an ingress controller kubementat does not install off the cluster.

    nginx-ingress was dropped from the product on 2026-10-28. A cluster that got it while
    kubementat still shipped it keeps three things this checkout no longer names: the
    IngressClasses bound to its controller, which are cluster-scoped and so survive any number
    of namespace deletes, the helm release, and the namespace its pods ran in. Nothing else in
    `uninstall_kubementat` reads any of them - the component sweep has no ingress controller to
    find, and its CRD sweep covers `tekton.dev` only - so this step owns all three, and it owns
    them without starting a tool the product stopped shipping.

    The release is addressed by reading the cluster's own list rather than by naming
    `nginx-ingress` and hoping: the chart's default release name is `ingress-nginx`, and a name
    somebody gave a release is exactly the kind of thing a name list gets wrong. A release that
    is not there is simply absent from a list, which is why no part of this depends on a command
    that errors when a release does not exist.

    What it does, in the order the cluster needs it:

    1. The IngressClasses whose controller is the old chart's, whether or not the namespace
       survives. They are cluster-scoped, and leaving one behind is how a cluster ends up
       advertising a class nothing serves.
    2. The release in its namespace, uninstalled by the name the cluster reports.
    3. The namespace, tolerant of never having existed, then a bounded wait so the run cannot
       report a namespace the cluster is still draining.

    Absence is a normal answer and not a warning. An install that never had a controller logs
    that there is nothing to uninstall, deletes nothing, and contributes no residue.

    Parameters:

    - plan: dict - What `uninstall_kubementat` read off the cluster and the configuration
      before it asked. `plan["nginx_ingress"]` is what says whether this cluster has a
      controller at all, and the namespaces the old `static.json` key had renamed to are not
      in it - guessing at a rename would be deleting somebody else's namespace.

    Returns:
        list[str]: What survived, empty when everything went.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    residue = delete_ingress_classes_bound_to(ctx, NGINX_INGRESS_CONTROLLER_PREFIX)
    installed = list(plan.get("nginx_ingress") or [])
    if not installed:
        logging.info("nginx-ingress is not installed on this cluster - nothing to uninstall.")
        return residue

    logging.info(f"Removing the ingress controller kubementat does not install any more: "
                 f"{', '.join(installed)}")
    residue += uninstall_helm_releases(ctx, installed)
    for namespace in installed:
        kubernetes_utils.delete_namespace(namespace)

    survivors = kubernetes_utils.wait_namespaces_gone(installed)
    if survivors:
        phases = kubernetes_utils.namespace_phases(survivors)
        residue += [
            "namespace '%s' still carries an ingress controller%s -- deletion was accepted, but"
            " it is not finished, so nothing here may claim it is gone"
            % (name, f" (phase {phases[name]})" if name in phases else "")
            for name in survivors
        ]
    return residue


def delete_ingress_classes_bound_to(ctx, controller_prefix: str) -> list:
    '''
    Delete the IngressClasses whose controller is the one kubementat stopped shipping.

    An IngressClass is cluster-scoped, so no namespace delete takes it away, and it is named by
    nothing in kubementat now that the controller is gone - the chart set its name, and a
    release that used a different one would have named a class nobody can find from here. So
    the cluster answers for the names, and what is matched is the controller field rather than
    a class name: `nginx` is the default class and a second release names its own.

    A class bound to somebody else's controller is not kubementat's to touch, and a cluster
    that cannot answer is a cluster with nothing to delete: the read is not fatal, because a
    cluster old enough to predate `IngressClass` answers with an error and no class to remove.

    Returns:
        list[str]: What survived, empty when everything went.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    try:
        classes = kubernetes_utils.list_ingress_classes()
    except client.exceptions.ApiException as error:
        # Not fatal, for the reason this method gives in full: a cluster old enough to predate
        # `IngressClass` answers with an error and no class to remove. It is said out loud and
        # not logged as "nothing to delete", because a residue assert cannot tell the two apart.
        logging.warning(f"The cluster would not answer for its IngressClasses"
                        f" (status {error.status}), so nothing here is claimed as deleted.")
        return []
    names = sorted(item.metadata.name for item in classes
                   if str(getattr(getattr(item, "spec", None), "controller", None) or "")
                   .startswith(controller_prefix))
    if not names:
        logging.info("No IngressClass is bound to the controller kubementat removed.")
        return []
    logging.info(f"Deleting {len(names)} IngressClass(es) bound to the controller kubementat "
                 f"removed: {', '.join(names)}")
    survivors = []
    for name in names:
        outcome = kubernetes_utils.delete_ingress_class(name)
        if outcome:
            continue
        survivors.append(f"ingressclass {name} (the cluster refused the delete"
                         + (f"): {outcome.reason}" if outcome.reason else ")"))
    return survivors
