"""The service mesh this product no longer installs, swept without its own tooling.

Linkerd was dropped on the owner's decision of 2026-10-28. What remains is the residue a cluster
keeps after the thing that put it there is gone: the `linkerd.io` definitions, which are
cluster-scoped and outlive any number of namespace deletes, and the two namespaces the control
plane and the observability stack lived in. Handing that to `linkerd uninstall` was the old
behaviour and it is the product's own fault waiting to be found, because the CLI is not a
kubementat dependency any more -- so this does it with the client.
"""


import logging

from kubementat.infrastructure.kubernetes import KubernetesUtils

from kubementat.automations.uninstall.residue import delete_crds_in_groups


# Linkerd, left behind rather than installed. kubementat stopped installing the mesh on the
# owner's decision of 2026-10-28, so nothing here configures it: no component in
# `constants`, no `static.json` key, no flag on `kmt install`. What is left is the residue a
# cluster keeps after the product that put it there is gone, and it is swept by its own step
# rather than by Linkerd's own tooling, which kubementat no longer ships and may not have on
# the PATH.
#
# These are the names Linkerd itself uses, which is what a cluster that once had the mesh
# carries. They are not namespace configuration: a team that had renamed them would have
# been reading them out of a `static.json` key this product does not write any more, and
# guessing at a rename is how an uninstall deletes the wrong namespace.
LINKERD_NAMESPACES = ("linkerd-viz", "linkerd")

# Where the mesh's CustomResourceDefinitions live. Cluster-scoped, invisible to a namespace
# delete, and named by nothing else in kubementat now that the mesh is out - which is why
# `uninstall_linkerd` deletes them itself instead of leaving them to the Tekton sweep.
LINKERD_CRD_GROUPS = ("linkerd.io",)


def uninstall_linkerd(ctx, plan: dict) -> list:
    '''
    Take a service mesh kubementat does not install off the cluster.

    Linkerd was dropped from the product on 2026-10-28. A cluster that got the mesh while it
    was still there keeps two things this checkout no longer names: the
    CustomResourceDefinitions under `linkerd.io`, which are cluster-scoped and so survive any
    number of namespace deletes, and the two namespaces the control plane and the observability
    stack live in. Nothing else in `uninstall_kubementat` reads either one - the component
    sweep has no Linkerd component to find, and its CRD sweep covers `tekton.dev` only - so
    this step owns both, and it owns them without asking the `linkerd` binary for help.

    Handing deletion to Linkerd's own uninstallers was the old behaviour, and it is the
    product's own fault waiting to be found: the CLI is not a kubementat dependency any more,
    so on the machine that runs `kmt uninstall` it is either absent or the wrong version, and
    the mesh would be left on the cluster with a log full of `command not found`.

    What it does, in the order the cluster needs it:

    1. The `linkerd.io` CRDs, listed by the cluster rather than taken from a name list, so a
       mesh that installed a definition nobody remembered still goes. They are deleted first:
       a mesh resource inside a namespace that is being torn down is how a namespace gets
       stuck in `Terminating`, and deleting the definition takes its instances with it.
    2. The two namespaces, each tolerant of never having existed, then a bounded wait so the
       run cannot report a namespace the cluster is still draining.

    Absence is a normal answer and not a warning. An install that never had a mesh logs that
    there is nothing to uninstall, deletes nothing, and contributes no residue.

    Parameters:

    - plan: dict - What `uninstall_kubementat` read off the cluster and the configuration
      before it asked. `plan["linkerd"]` is what says whether this cluster has a mesh at all.

    Returns:
        list[str]: What survived, empty when everything went.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    residue = delete_crds_in_groups(ctx, LINKERD_CRD_GROUPS,
                                          absent="No Linkerd CRDs on the cluster.")
    installed = list(plan.get("linkerd") or [])
    if not installed:
        logging.info("Linkerd is not installed on this cluster - nothing to uninstall.")
        return residue

    logging.info(f"Removing Linkerd, which kubementat does not install any more: "
                 f"{', '.join(installed)}")
    for namespace in installed:
        kubernetes_utils.delete_namespace(namespace)

    survivors = kubernetes_utils.wait_namespaces_gone(installed)
    if survivors:
        phases = kubernetes_utils.namespace_phases(survivors)
        residue += [
            "namespace '%s' still carries Linkerd%s -- deletion was accepted, but it is not"
            " finished, so nothing here may claim the mesh is gone"
            % (name, f" (phase {phases[name]})" if name in phases else "")
            for name in survivors
        ]
    return residue
