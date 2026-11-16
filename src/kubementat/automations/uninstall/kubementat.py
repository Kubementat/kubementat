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

What that summary adds up to must be a value and not a sentence. A partially-succeeded uninstall
-- ten namespaces gone, one release still running -- reports through `SweepResult`, the shape
`RoutingInstallResult` and `InstallResult` already return: the operator gets the sentence, a caller
gets the list without catching anything, and the tree can ask "how much of this was actually
deleted". The residue is what makes the verdict, and the message an incomplete run raises with is
built from that value — which is why the sentence can stay the same while the reporting changes.
"""

import logging
from dataclasses import dataclass

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

from kubementat.errors import (
    EnvironmentConfigurationFileMissingError,
)
from kubementat.consent import Consent

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

# ---------------------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SweepResult:
    """What this run deleted, what it could not, and which directories it read as teams.

    The reason it is a value is the reason `RoutingInstallResult` gives, and here the reason is
    concrete: a sweep that deleted ten namespaces and left one release running is a run with a
    *partial* result, and partial results are the one thing a `None` cannot express. Carrying the
    residue as a field rather than as a joined message is what lets the CLI print what survived,
    a caller decide what to do about it, and a test ask which namespace was the one that stuck.

    `residue` is the verdict. Everything else in here is context for reading it: `"11 namespaces
    deleted"` is not the same fact with 1 item left behind as with 0, and an uninstall that reports
    only the count is a sweep that cannot tell the operator whether to trust the cluster. The
    residue strings themselves are the cluster's and the step's own sentences -- they are not
    summarised, because what an operator has to act on is the name of the thing that survived.
    """

    environment: str
    context: str
    namespaces_deleted: tuple = ()
    namespaces_absent: tuple = ()
    teams_swept: tuple = ()
    teams_not_swept: tuple = ()
    residue: tuple = ()

    @property
    def clean(self) -> bool:
        """True when the cluster holds nothing kubementat owned. `DONE` is this, and not the log."""
        return not self.residue

    def failure_message(self) -> str:
        """The sentence `kmt uninstall` fails with, assembled from the residue it found.

        The lead, the two-space indent and the order of the items are the message's: keep them, because
        the operator's terminal and the tests both read this exact sentence. The value owns the list,
        so the CLI, a caller and a test all read one list and none of them has to rejoin it.
        """
        return ("uninstall left kubementat residue behind, so the cluster is not clean:\n  "
                + "\n  ".join(self.residue))

    def raise_if_incomplete(self) -> None:
        """Raise the named refusal when residue is not empty, and do nothing when it is.

        This is the caller's half, not the sweep's: a run that deleted something and left something
        behind is not a run that deleted nothing, and the decision about whether that is an error is
        the command's to make. `kmt uninstall` makes it -- it prints the answer on the clean path
        and takes this call on the other one, which is what keeps the exit code at 1 and the
        message exactly where the operator already reads it.
        """
        if not self.clean:
            raise errors.UninstallIncompleteError(self.failure_message())

    def summary(self) -> str:
        """A handful of lines for whoever ran the command. The detail is in the log.

        Deliberately short of what the plan and the team summary already say, both of which stay in
        the log: what belongs on stdout is the verdict a scripted run can read -- which environment
        was swept, how many namespaces went, and whether anything kubementat owned survived.
        """
        lines = [f"kubementat uninstalled from {self.environment} ({self.context}): "
                 f"{len(self.namespaces_deleted)} namespaces deleted, "
                 f"{len(self.namespaces_absent)} were already absent."]
        if self.teams_swept:
            lines.append("team directories swept: " + ", ".join(self.teams_swept) + ".")
        if self.teams_not_swept:
            lines.append("team directories read as not-a-team, so nothing under them was deleted: "
                         + ", ".join(self.teams_not_swept) + ".")
        if self.clean:
            lines.append("nothing kubementat owned is left on the cluster.")
        else:
            lines.append(f"{len(self.residue)} things kubementat owned are still on the cluster.")
        return "\n".join(lines)


def uninstall_kubementat(ctx, consent: Consent = None) -> SweepResult:
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

    - consent: kubementat.consent.Consent = undecided - What the caller decided about this
      deletion. `kmt uninstall --yes` hands down `Consent.assumed()`; a run standing at a terminal
      hands down the way to ask. A caller that hands down neither is refused before the first
      delete.

    Returns:

    - SweepResult - What was deleted, what was already absent, which directories were read as
      teams, and `residue`: every one of them something kubementat owned that the cluster still
      carries. An empty `residue` is the whole of what "the uninstall worked" means; a non-empty one
      is the same fact named, so a run that deleted ten namespaces and left one release running can
      be read as a partial run instead of being reported as prose.

    Raises:

    - kubementat.errors.ConfirmationDeclinedError: On "no" at the confirmation, so a
      cancellation is one line rather than a traceback of the CLI's own argument parsing.
    - kubementat.errors.ConfirmationUnavailableError: When nothing decided it and nothing can ask.
      `kmt uninstall` cannot reach this one: its own no-terminal guard fires before this line is.
    - kubementat.errors.EnvironmentConfigurationFileMissingError: When the environment has no
      `static.json`, so there is no namespace list to delete.

    A surviving object is not raised here: the value carries it and
    `SweepResult.raise_if_incomplete` is how a caller turns it back into the refusal
    `kmt uninstall` still exits 1 with -- the message is the same because it is assembled from the
    same list.
    '''

    environment = ctx.environment
    kubernetes_utils = KubernetesUtils(ctx)

    plan = read_uninstall_plan(ctx)
    print_uninstall_plan(ctx, plan)
    (consent or Consent.undecided()).decide("uninstall all kubementat resources")

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

    # Namespace deletion is asynchronous and was measured at 42 s to drain on a 4 vCPU guest, while
    # this command reports its own result about 4 s after firing the deletes. That
    # reads as "the cluster still has what I just deleted" and, worse, lets a run claim a clean
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

    # The verdict is returned and not raised. `print_uninstall_result` above still writes the log --
    # the namespace diff, the team-directory caveat, `DONE` only when the residue list is empty --
    # because the log is evidence and this value is the summary of it, and neither is a substitute
    # for the other. What the caller decides to do about a non-empty residue is its own business:
    # the CLI takes `raise_if_incomplete`, which is what keeps exit 1 and the operator's sentence
    # exactly where they were.
    teams = plan.get("teams") or {}
    return SweepResult(
        environment=environment,
        context=plan["context"],
        namespaces_deleted=tuple(plan["namespaces"]),
        namespaces_absent=tuple(plan["absent"]),
        teams_swept=tuple(sorted(team for team, entry in teams.items() if entry["deletable"])),
        teams_not_swept=tuple(sorted(team for team, entry in teams.items() if not entry["deletable"])),
        residue=tuple(residue),
    )

def read_uninstall_plan(ctx) -> dict:
    '''
    What is on the cluster, and which of it kubementat is responsible for.

    Reads only. It runs before the confirmation so a human at the terminal sees what they
    are agreeing to, and the same reading drives the deletion.

    Raises:
    - kubementat.errors.EnvironmentConfigurationFileMissingError: When the environment
      has no `static.json` to read the namespaces from. The script this replaced said so and
      exited 1; deleting a cluster on the strength of an environment name nobody configured is
      not what a typo should be able to do.
    '''

    config = ctx.config
    environment = ctx.environment
    kubernetes_utils = KubernetesUtils(ctx)

    if not config.env_static_config_path.is_file():
        raise EnvironmentConfigurationFileMissingError(
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
    # controller this checkout does not ship is coming off, and so each removal step works
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
    """Print what is about to be deleted, before anything is deleted."""

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
