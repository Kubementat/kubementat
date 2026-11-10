"""`kmt uninstall`: the whole teardown, in the order the cluster needs.

Eight steps and two reads before the first write, because neither half is enough alone: the
configuration says what kubementat *could* have created and the cluster says what it did create.
Cluster-scoped objects go before namespaces, releases go before the things that reference them,
and what survived is collected from every step and raised once -- an uninstall that prints `DONE`
while a release is still running is the failure this command exists to stop.

The deletes themselves are in `residue`, `linkerd` and `nginx_ingress`; the namespace answer is in
`sweep`; the team directory is read by `core.config.teams`. This module owns only the order, the
plan the operator is shown before being asked, and the summary that says which directories were
read as teams and which were not.
"""


import logging
import sys

import click

from kubementat import errors
from kubementat.core.config.teams import TEAM_NAMESPACE_KEYS
from kubementat.core.identity.context import context_name
from kubementat.infrastructure.kubernetes import KubernetesUtils
# The name moved out with the install half (P4-4). It is read through the module rather than
# imported, because a name imported past its home is a second binding and the seam a test patches
# is the module the code runs in -- and because `uninstall_kubementat` deletes exactly the object
# `install.install` writes, so the two halves have to agree on the spelling of one thing.
from kubementat.infrastructure.tekton import install as tekton_install

from kubementat.automations.uninstall.linkerd import (
    LINKERD_NAMESPACES,
    uninstall_linkerd,
)
from kubementat.automations.uninstall.nginx_ingress import (
    NGINX_INGRESS_NAMESPACES,
    uninstall_nginx_ingress,
)
from kubementat.automations.uninstall.residue import (
    delete_claims_in,
    delete_cluster_scoped_rbac_kubementat_owns,
    delete_crds_alloy_helm_leaves_behind,
    delete_crds_kubementat_owns,
    delete_crds_traefik_helm_leaves_behind,
    uninstall_helm_releases,
)
from kubementat.automations.uninstall.sweep import namespace_sweep


HELM_DEPLOYER_CLUSTER_ROLE = tekton_install.HELM_DEPLOYER_CLUSTER_ROLE_NAME


## uninstall kubementat
#
# This is the highest-risk command in the product, so the facts it acts on are written
# down once, in one place:
#
# * namespaces come from `Config.component_namespace` over the components that
#   `kubementat.core.constants` declares, plus every team the environment configures;
# * what is actually installed comes from the cluster (`helm list`, `kubectl get crd`), so
#   a component or a CRD nobody remembered is still swept;
# * every program kubementat starts goes through `kubementat.infrastructure.shell.run` as an argv list,
#   and every cluster mutation the generated client can express goes through
#   `KubernetesUtils`.


def uninstall_kubementat(ctx, assume_yes: bool = False) -> None:
    '''
    Delete every kubementat component, team namespace, release and claim from the cluster.

    The order is the product, not the presentation:

    1. Read the cluster and print it. Nothing is deleted before the person at the terminal
       has been told what they are about to lose, and the log says what *was* there rather
       than what a list written in a script expected.
    2. The cluster-scoped RBAC. `helm-deployer-cluster-role` by name, because kubementat
       creates it and can say so, and the rest by asking the cluster which of it is
       kubementat's: the `ClusterRole`s and `ClusterRoleBinding`s the Tekton, Triggers and
       Dashboard release manifests put there belong to no release and no namespace, so the
       line the script had (`kubectl delete clusterrole helm-deployer-cluster-role`) left
       every one of them, and the lane's
       `no-extra-cluster-scoped:clusterroles` assert reads what they leave as a clean
       teardown. It goes first, and before the namespaces, for the reason that sweep gives.
    3. Linkerd and the ingress controller, which this product does not install any more. Their
       CRDs / IngressClasses and their namespaces are taken off the cluster whether or not
       either tool is present, and something that was never there costs one line of log and
       nothing else.
    4. Helm releases, read from the cluster and uninstalled per namespace. Deleting the
       namespace does drop the release secret, but only as a side effect of a garbage
       collection nobody scheduled, which is how a real lane reported `cert-manager` and
       `nginx-ingress` as survivors of an otherwise green uninstall.
    5. What `helm uninstall` never removes: the CustomResourceDefinitions the Traefik chart
       applied with its `crds/` directory when it was installed. They are cluster-scoped, so no
       namespace delete reaches them, and they go before the namespaces for the same reason
       Linkerd's go first -- deleting a definition takes its instances with it.
    6. The claims in those namespaces. Printing advice about how to delete them by hand is
       not an uninstall, and the lane asserts their absence.
    7. The namespaces, teams before components, each tolerant of never having existed.
    8. The CRDs, asked of the cluster rather than taken from a list of nineteen names, so a
       CRD the pinned release installs that nobody remembered is still kubementat's.

    The plan is read from two sources before the first write, because neither one is
    sufficient alone: the configuration says what kubementat *could* have created, the
    cluster says what it did create.

    Parameters:

    - assume_yes: bool = False - Skip the interactive confirmation (`kmt uninstall --yes`).

    Raises:

    - click.ClickException: On "no" at the confirmation, so a cancellation is one line
      rather than a traceback of the CLI's own argument parsing.
    - kubementat.errors.UninstallIncompleteError: When the cluster still carries something
      kubementat owned, named in the message. An uninstall that says DONE while releases survive
      is the failure this command exists to stop.
    '''

    environment = ctx.environment
    kubernetes_utils = KubernetesUtils(ctx)

    plan = read_uninstall_plan(ctx)
    print_uninstall_plan(ctx, plan)
    if not assume_yes:
        confirm_uninstall()

    logging.info("#########################")
    logging.info(f"Uninstalling kubementat components and team spaces from environment: {environment}")
    logging.info("#########################")

    # Cluster-scoped first: it is the only object the namespace sweep below cannot reach.
    kubernetes_utils.delete_cluster_role(HELM_DEPLOYER_CLUSTER_ROLE)

    # ... and the rest of what the install path wrote. The release manifests
    # `infrastructure/tekton/install.py` applies put RBAC on the cluster that belongs to no release
    # secret, no namespace and no
    # name written down in this checkout, so the line above leaves every one of them and every
    # namespace delete below reaches none of it. Which ones are kubementat's is answered by the
    # cluster, before any namespace goes -- `delete_cluster_scoped_rbac_kubementat_owns` says
    # how, and says why the order is what makes the answer readable at all.
    residue = delete_cluster_scoped_rbac_kubementat_owns(ctx, plan)

    # Neither is a component any more, so both are swept before the component set rather than
    # among it: their namespaces are not in the plan's namespace list, and their cluster-scoped
    # objects are not in the groups the final sweep covers.
    residue += uninstall_linkerd(ctx, plan)
    residue += uninstall_nginx_ingress(ctx, plan)
    residue += uninstall_helm_releases(ctx, plan["namespaces"])
    residue += delete_claims_in(ctx, plan["namespaces"])

    # After the release, before the namespaces: helm applied the chart's `crds/` on install and
    # removes nothing on uninstall, so the definitions outlive the release the line above just
    # took. They are cluster-scoped, and an IngressRoute still pointing at them is how a
    # namespace being torn down gets stuck in `Terminating`. What the line above has just left
    # on the cluster decides whether they go, because they are shared with whatever proxy is
    # still running -- the node's own, on the cluster this suite runs on.
    residue += delete_crds_traefik_helm_leaves_behind(ctx)

    # Alloy's dependency chart is the second copy of the same trap, and it was found by the lane and
    # not by a review: `monitoring.grafana.com` is cluster-scoped, named by no release and no
    # namespace, so everything above reports clean over it. It runs in the same place for the same
    # reason -- the definitions are read before the namespaces they may be blocking go away, and a
    # foreign Alloy that is still collecting decides whether they go.
    residue += delete_crds_alloy_helm_leaves_behind(ctx)

    for namespace in plan["namespaces"]:
        kubernetes_utils.delete_namespace(namespace)

    # Namespace deletion is asynchronous and was measured at 42 s to drain on a 4 vCPU guest,
    # while this command used to report its own result about 4 s after firing the deletes. That
    # read as "the cluster still has what I just deleted" and, worse, let a run claim a clean
    # sweep over namespaces the cluster had not finished removing. Wait for it, bounded, and put
    # any survivor in the residue list with the phase the cluster reports.
    survivors = kubernetes_utils.wait_namespaces_gone(plan["namespaces"])
    if survivors:
        phases = kubernetes_utils.namespace_phases(survivors)
        residue += [
            "namespace '%s' is still reported by the cluster%s -- deletion was accepted, but it is"
            " not finished, so nothing here may claim the namespace is gone"
            % (name, f" (phase {phases[name]})" if name in phases else "")
            for name in survivors
        ]

    residue += delete_crds_kubementat_owns(ctx)

    print_uninstall_result(ctx, plan, residue)
    if residue:
        raise errors.UninstallIncompleteError(
            "uninstall left kubementat residue behind, so the cluster is not clean:\n  "
            + "\n  ".join(residue)
        )

def read_uninstall_plan(ctx) -> dict:
    '''
    What is on the cluster, and which of it kubementat is responsible for.

    Reads only. It runs before the confirmation so a human at the terminal sees what they
    are agreeing to, and the same reading drives the deletion.

    Raises:
    - click.ClickException: When the environment has no `static.json` to read the namespaces
      from. The script this replaced said so and exited 1; deleting a cluster on the strength
      of an environment name nobody configured is not what a typo should be able to do.
    '''

    config = ctx.config
    environment = ctx.environment
    kubernetes_utils = KubernetesUtils(ctx)

    if not config.env_static_config_path.is_file():
        raise click.ClickException(
            f"No environment configuration at {config.env_static_config_path}."
            f" Uninstall reads the namespaces it deletes from that file, so it cannot guess"
            f" them. Run kmt config init --environment {environment} --config-dir <configuration-root>, "
            f"or create"
            f" the file for the environment."
        )
    present = set(kubernetes_utils.list_namespaces())
    sweep = namespace_sweep(ctx)
    declared = sweep["namespaces"]
    # Linkerd and the ingress controller are not components this product installs, so their
    # namespaces are not part of the declared sweep. They are read off the cluster and carried
    # in the plan anyway, so the operator is told before the first delete that a mesh or a
    # controller this checkout no longer ships is coming off, and so each removal step works
    # from one read of the cluster instead of two.
    linkerd = [namespace for namespace in LINKERD_NAMESPACES if namespace in present]
    nginx_ingress = [namespace for namespace in NGINX_INGRESS_NAMESPACES
                     if namespace in present]
    return {
        "context": context_name(ctx),
        "nodes": [node.metadata.name for node in kubernetes_utils.get_nodes()],
        "declared": declared,
        "linkerd": linkerd,
        "nginx_ingress": nginx_ingress,
        "namespaces": [namespace for namespace in declared if namespace in present],
        "absent": [namespace for namespace in declared if namespace not in present],
        # The team directories this sweep could read, and what each of them said. Carried
        # through the plan so both the print before the deletion and the summary after it can
        # tell a swept team apart from a directory that turned out not to be one.
        "teams": sweep["teams"],
    }


def print_uninstall_plan(ctx, plan: dict) -> None:

    environment = ctx.environment

    logging.info("###################################")
    logging.info("You are going to uninstall kubementat from the following cluster:")
    logging.info("###################################")
    logging.info(f"Kube context: {plan['context']} (environment {environment},"
                 f" resolved from the cluster configuration this command read)")
    logging.info(f"Nodes: {', '.join(plan['nodes']) or 'none the cluster would report'}")
    logging.info(f"Namespaces to delete: {', '.join(plan['namespaces']) or 'none'}")
    if plan.get("linkerd"):
        logging.info(f"Linkerd namespaces to remove, which kubementat no longer installs: "
                     f"{', '.join(plan['linkerd'])}")
    if plan.get("nginx_ingress"):
        logging.info(f"Ingress controller namespaces to remove, which kubementat no longer "
                     f"installs: {', '.join(plan['nginx_ingress'])}")
    if plan["absent"]:
        logging.info(f"Declared by the configuration but not present: {', '.join(plan['absent'])}")
    for line in team_sweep_summary(ctx, plan.get("teams") or {}):
        logging.info(line)
    logging.info("This cannot be undone. Every resource inside those namespaces goes with them.")
    logging.info("###################################")


def confirm_uninstall() -> None:
    '''
    Ask. Failing closed is the whole contract: no terminal, no deletion.

    `kmt uninstall` keeps the CLI-side guard that raises a `UsageError` before this is
    reached, so the only way to arrive here without a terminal is a caller that built the
    library itself. The answer is the same either way: say what is wrong, name `--yes`,
    delete nothing.
    '''
    if not sys.stdin.isatty():
        raise click.ClickException(
            "uninstall requires confirmation, but stdin is not a terminal."
            " Re-run with --yes (or -y) to uninstall kubementat without prompting."
        )
    while True:
        answer = input("Do you really wish to uninstall all kubementat resources? ").strip().lower()
        if answer in ("y", "yes"):
            return
        if answer in ("n", "no"):
            # The message is what the operator and the next person reading a log have: the
            # bash printed `Cancelled uninstall script.` and exited 1, so declining had a
            # voice. Raising is the right shape for the library -- it gives the CLI a clean
            # one-line `Error:` and a non-zero exit -- but the answer still has to say what
            # happened and what would have happened, in the same words a test can grep.
            raise click.ClickException(
                "Cancelled uninstall script. Nothing was deleted and the cluster is untouched"
                "; re-run with --yes (or -y), or answer yes, to go ahead."
            )
        logging.info("Please answer yes or no.")


###########################

## uninstall kubementat, the deletion half


def team_sweep_summary(ctx, teams: dict) -> list:
    '''
    One line per team directory: what it contributed, or why it did not.

    This is the answer to the question a residue assert cannot ask of a cluster. "Nothing is
    left" and "nothing was swept" look identical on the cluster and are not the same fact
    about the installation, so the run's own log has to say which directories were read as
    teams, which were read as half a team, and which were not read as one at all.

    The tree that configures no teams at all gets that sentence too, and not a warning: a
    fresh `kmt config init` without `--team` writes no team directory, and deleting nothing
    there is the correct
    answer -- `Config.teams()` calls the empty list expected and refuses only for an
    environment nobody configured. What is not correct is returning no line and letting `DONE`
    stand, because "this root configures no teams" and "this root's teams were swept" then read
    the same, and the first one is the fact the run can actually vouch for. A sweep that found
    every component namespace and no team has to be able to say which is which, so the empty
    case is worded like the lines below it: not swept, nothing deleted, here is the root that
    was read.

    Returns:
        list[str]: Log lines, one per configured directory, ordered by team name. The
            environment that configures no directories at all gets the one line above.
    '''

    config = ctx.config
    environment = ctx.environment

    if not teams:
        return [f"No team directory is configured under"
                f" {config.platform_config_dir / environment}, so no team"
                f" namespaces were swept and nothing under one was deleted."]
    lines = []
    for team, entry in sorted(teams.items()):
        swept = entry["deletable"]
        # What the log says was swept is the list that was deleted, which is the file's list and
        # not the answered list. The difference has to be said in words, because a summary that
        # reads "swept for dev1-pipelines, dev1" when it only deleted one of the two is the
        # sentence that turns a partial teardown into a claim that the cluster is clean.
        answered_not_named = [name for name in entry["namespaces"] if name not in swept]
        if entry.get("unreadable"):
            lines.append(
                f"Team directory '{team}' declares no namespaces because {entry['path']} is not"
                f" readable as JSON, so it was not swept and nothing under it was deleted."
            )
        elif not swept and answered_not_named:
            verb = "is" if len(answered_not_named) == 1 else "are"
            lines.append(
                f"Team directory '{team}' declares none of"
                f" {' and '.join(TEAM_NAMESPACE_KEYS)}. {' and '.join(answered_not_named)}"
                f" {verb} answered by the team's name, which says where its objects live and not"
                f" that this run may remove them, so nothing under it was deleted"
                f" ({entry['path']})."
            )
        elif not swept:
            lines.append(
                f"Team directory '{team}' declared none of"
                f" {' and '.join(TEAM_NAMESPACE_KEYS)}, so it was not swept and nothing"
                f" under it was deleted ({entry['path']})."
            )
        elif entry["missing"]:
            lines.append(
                f"Team directory '{team}' was swept for"
                f" {', '.join(swept)} only; {' and '.join(entry['missing'])} is not declared"
                f" in {entry['path']}, so whatever it would have named was not deleted."
            )
        elif answered_not_named:
            lines.append(
                f"Team directory '{team}' was swept for {', '.join(swept)} only;"
                f" {' and '.join(answered_not_named)} is answered by the team's name and not"
                f" declared in {entry['path']}, so it was not deleted."
            )
        else:
            lines.append(
                f"Team '{team}' was swept for {', '.join(swept)}."
            )
    return lines


def print_uninstall_result(ctx, plan: dict, residue: list = ()) -> None:
    '''
    The namespace diff, so the log is evidence rather than a claim.

    `kubectl api-resources` is not dumped here. The old script printed the whole discovery
    table as the last thing before `DONE`, and a table nobody can read into a verdict is
    noise that hides the one line that mattered.

    `DONE` is earned by an empty residue list and printed for nothing otherwise: the script
    printed it unconditionally, which is how `DONE` came to mean "the script finished".
    '''

    config = ctx.config
    environment = ctx.environment
    kubernetes_utils = KubernetesUtils(ctx)

    declared = set(plan["declared"])
    still_present = [namespace for namespace in kubernetes_utils.list_namespaces()
                     if namespace in declared]
    logging.info("#########################")
    logging.info(f"Namespaces kubementat owned that are still present: {', '.join(still_present) or 'none'}")
    if plan["absent"]:
        logging.info(f"Never present, so nothing was deleted: {', '.join(plan['absent'])}")
    # Repeat the team-directory verdict where the operator is reading it as the verdict of the
    # run. A skipped directory does not make the uninstall incomplete -- there is nothing to
    # call incomplete about -- but a `DONE` that follows one has to say so, or the log reads
    # as if the sweep had covered every directory under the environment.
    incomplete = sorted(team for team, entry in (plan.get("teams") or {}).items()
                        if entry["missing"])
    unreadable = sorted(team for team, entry in (plan.get("teams") or {}).items()
                        if entry.get("unreadable"))
    if incomplete:
        logging.warning(
            f"The sweep did not cover every directory under"
            f" {config.platform_config_dir / environment}:"
            f" {', '.join(incomplete)} declared less than"
            f" {' and '.join(TEAM_NAMESPACE_KEYS)}"
            + (f" ({', '.join(unreadable)} could not be read as JSON at all)" if unreadable
               else "")
            + ". Everything kubementat's own configuration declares was deleted; what those"
              " directories may have owned was not guessed at."
        )
    if residue:
        logging.error("Uninstall did not finish: " + ", ".join(residue))
        logging.info("#########################")
        return
    logging.info("#########################")
    logging.info("DONE")

###########################

## tunnels
