"""`kmt install`: reach the cluster, say what is about to happen, then install.

The order is the product's, not the code's: dependencies first (a missing binary is not worth
asking about), then the cluster read that answers whether there is a cluster at all, then the
permission check that answers whether *this* service account can build on it, then the writes.
Two of those three stop the run and the version probe behind the first one does not; that
difference is documented on `dependencies`, and it is the reason the entry point reads like a
checklist rather than a sequence of log lines.

Everything past the checks is delegated, so nothing here holds state: the cluster's own answer
comes from `Context`, the Tekton and Helm services are built from that same `Context` when they
are needed rather than remembered here, and a build of `HelmUtils` is not kept as an attribute
because `kmt uninstall` is a command that has no team to satisfy its guard.

What the run leaves behind is a value and not a stream. It returns `InstallResult` -- the shape
`automations/routing/routing.py` returns for the same reason -- so the decision, the writes and the
two reads that had to pass are carried out of the function together, the CLI prints them, and a
caller that wants to branch does not have to read prose. The log still says the same things,
because something still has to read it.
"""


import logging
from dataclasses import dataclass
from typing import Optional

from kubementat import errors
from kubementat.infrastructure import shell
from kubementat.infrastructure.helm import HelmUtils
from kubementat.infrastructure.kubernetes import KubernetesUtils
# The preflight assertion moved out of `tekton_utils.py` (P4-2). The name it goes by is unchanged;
# what changed is the module it is read from, and it is read from that module rather than bound
# here because every tekton seam is patched by module object.
from kubementat.infrastructure.tekton import install as tekton_install
from kubementat.infrastructure.tekton import preflight, triggers

from kubementat.automations.install.dependencies import (
    KUBEMENTAT_DEPENDENCIES,
    check_dependencies_are_installed,
)
from kubementat.automations.install.pipelines import setup_pipelines
from kubementat.consent import (
    CONSENT_ANSWERED,
    CONSENT_ASSUMED,
    Consent,
)


# What the install preflight proves this service account may do, spelled as the API spells it.
#
# The names are plural on purpose. `kubectl auth can-i create deployment` takes the singular and
# maps it through the discovery table itself; a `SelfSubjectAccessReview` is the request that
# mapping produces, so it has to carry the resource name and not the kind. Handing it
# `deployment` instead of `deployments` answers "no" on every cluster and fails an install that
# would otherwise work, which is the only way this conversion could have gone quietly wrong.
CLUSTER_PERMISSION_CHECK_RESOURCES = (
    "namespaces", "deployments", "clusterroles", "roles", "daemonsets", "replicasets")

# The one state install's own consent can land in that the `Consent` object does not spell out.
# `--yes` answers with `CONSENT_ASSUMED` and a library caller that already settled it answers with
# `CONSENT_ANSWERED`; a terminal that was asked and said yes leaves `answer` at undecided and the
# question settled behind the `ask` callback, so the run's own record says `asked` -- the word
# `routing/routing.py` already uses for the same fact.
CONSENT_ASKED = "asked"


def _consent_of(consent: Consent) -> str:
    """How this run's permission was settled, in words the rest of the tree already uses.

    Read after `validate_cluster` has asked, never before: a consent that has not been settled is
    the same object at both ends of the call and means two different things.
    """
    if consent.is_assumed():
        return CONSENT_ASSUMED
    if consent.answer == CONSENT_ANSWERED:
        return CONSENT_ANSWERED
    return CONSENT_ASKED


def _and_then_the_last(items) -> str:
    """`a`, `a and b`, `a, b and c` -- the list read as a sentence and not as a traceback."""
    names = list(items)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InstallResult:
    """What this run installed, what it was told to leave alone, and what it proved first.

    The reason it is a value and not a log stream is the reason `RoutingInstallResult` gives:
    the CLI prints it, a later caller branches on it, and a report that lives only in prose is a
    report nobody can act on twice. `cluster_answered` and `permissions_granted` are carried with
    the writes on purpose -- the two gates *before* the first write are exactly the facts an
    operator cannot recover from the cluster afterwards, and an install that says "it worked"
    without them has said less than the run knew.

    The booleans are true where the step ran and false where it was skipped or not asked for, so
    a run that was told not to configure the pipeline half says so instead of printing the same
    sentence as a run that did.
    """

    environment: str
    team: Optional[str]
    helmfile_installation_group: str
    helmfile_label_filter: str
    cluster_answered: bool = False
    permissions_granted: bool = False
    tekton_installed: bool = False
    api_versions_preflighted: bool = False
    releases_applied: bool = False
    pipelines_configured: bool = False
    triggers_configured: bool = False
    consent: str = CONSENT_ASKED
    skipped: tuple = ()

    def command(self, *flags) -> str:
        """The same install, spelled the way it would be typed to change the outcome."""
        environment = self.environment or "<environment>"
        team = self.team or "<team>"
        return " ".join([f"kmt install {environment} {team}"] + list(flags))

    def what_was_written(self) -> tuple:
        """The writes in the order they happened, as sentences a reader can put on one line."""
        written = []
        if self.tekton_installed:
            written.append("the three pinned Tekton releases and the ClusterRole that lets "
                           "helm deploy")
        if self.releases_applied:
            written.append(f"the platform's Helm releases through {self.helmfile_label_filter}")
        if self.pipelines_configured:
            written.append("the team's pipeline, task and pipeline-run manifests")
        if self.triggers_configured:
            written.append("the webhook triggers' RBAC, Secrets and generated resources")
        return tuple(written)

    def summary(self) -> str:
        """A handful of lines for whoever ran the command. The detail is in the log.

        Deliberately not a restatement of the finish message below, which is where the next steps
        live: what is worth printing is *which* writes landed and which the run was told to skip,
        because that is what a scripted run has to be able to read out of the output and what an
        operator has to be able to see without scrolling through a helmfile log.
        """
        team = self.team if self.team else "<no team>"
        written = self.what_was_written()
        if written:
            lines = [f"kubementat installed for {self.environment} ({team}): "
                     f"{_and_then_the_last(written)}."]
        else:
            lines = [f"kubementat installed for {self.environment} ({team}): nothing was written."]
        if self.cluster_answered and self.permissions_granted:
            lines.append("the cluster answered, and this service account may create "
                         + _and_then_the_last(CLUSTER_PERMISSION_CHECK_RESOURCES) + ".")
        elif self.cluster_answered:
            lines.append("the cluster answered but refused at least one of the creates the "
                         "install needs: "
                         + _and_then_the_last(CLUSTER_PERMISSION_CHECK_RESOURCES) + ".")
        for what in self.skipped:
            lines.append(f"skipped: {what}. {self.command()} is the command that runs it.")
        if self.consent == CONSENT_ASSUMED:
            lines.append("--yes answered the confirmation, so nothing was asked.")
        return "\n".join(lines)


def install_kubementat(ctx, configure_tekton_pipelines=True, helmfile_installation_group='standard',
                       consent=None) -> InstallResult:
    '''
    Main installation logic for Kubementat components including Tekton and pipeline setup.

    Parameters:

    - configure_tekton_pipelines: bool = True - Whether to configure Tekton pipelines
    - helmfile_installation_group: str = 'standard' - Helmfile installation group
    - consent: kubementat.consent.Consent = undecided - What the caller decided about writing to
      this cluster. `kmt install --yes` hands down `Consent.assumed()`; a terminal hands down the
      way to ask. A caller that hands down nothing is refused before the first write.

    Returns:

    - InstallResult - What this run wrote, what it was told to leave alone, and the two reads that
      had to pass before the first apply. Reaching the return is the proof: every other outcome
      leaves this function through a refusal or an exit, so nothing here reports a partial install
      as a success. The side effects are still side effects -- this is a report about the run, not
      a receipt for the cluster.

    Raises:

    - Exception - If dependency checks fail or installation scripts encounter errors
    '''

    config = ctx.config
    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)


    check_dependencies_are_installed(KUBEMENTAT_DEPENDENCIES)

    # The consent is settled once, here, and read afterwards. `validate_cluster` is where it is
    # asked if it needs asking, and the answer belongs in the result rather than in a local that
    # went out of scope three lines later.
    effective_consent = consent or Consent.undecided()

    # Both lines below are gates, and each leaves this function by its own exit when it fails:
    # the cluster read by `exit(1)`, the permission probe by a named refusal. They answer with a
    # value for that reason -- what the caller wants to know is not *whether* the gate stopped the
    # run (the run would not be here) but that it passed, which until now lived in a log line.
    cluster_answered = validate_cluster(ctx, effective_consent)
    permissions_granted = check_cluster_permissions(ctx)

    logging.info("######################################################")
    logging.info(f"CONFIGURING: Kubementat for environment {environment} and team {team}")
    logging.info("######################################################")

    # Tekton installation: the three pinned releases and the ClusterRole that authorises
    # helm to deploy, run as a function of `infrastructure/tekton/install.py`. It is called through
    # the module rather than bound as an attribute, and it is handed exactly what its caller has.
    #
    # The team guard is asked here, two lines ahead of this call, and it is asked here because
    # this is where the install's first write happens. The three releases apply into cluster-wide
    # namespaces and read no team, so nothing below them would say anything: a library caller
    # holding a `Context` with no team would be refused three releases deep. The question is
    # therefore asked where the branch below needs the answer:
    # `configure_tekton_pipelines` is the half of the install that writes one team's namespaces,
    # directories and Secrets, so a run that named no team is refused before a manifest is
    # applied, and a run that asked for none of that half is not refused at all.
    if configure_tekton_pipelines:
        ctx.require_team("the namespaces and directories the Tekton configuration is written into")

    tekton_install.install(kubernetes_utils, config, environment)

    # That the applies succeeded is not the same fact as "the cluster now serves what
    # kubementat speaks": the release URLs are built from the TEKTON_VERSION_* pins, so a
    # pin that moved without the URL scheme, or an apply that landed against an old
    # controller, both leave the install green and the first pipeline run full of 404s. Ask
    # the discovery question for both groups before helmfile and before pipeline setup.
    preflight.preflight_installed_tekton_api_versions(kubernetes_utils, config)

    # helmfile apply
    # The install path applies without asking, exactly as before: `kmt install` has its own
    # confirmation (`validate_cluster`), and helmfile's `[y/n]` is not reachable from
    # `kmt install --yes`. Passing the flag explicitly keeps that promise if the CLI-side
    # rule ever changes its default.
    group_filter = f"group={helmfile_installation_group}"
    HelmUtils(ctx).helmfile_apply(
        environment_name=environment,
        helmfile_label_filter=group_filter, 
        interactive=False
    )

    # Setup pipelines and triggers
    skipped = ()
    if configure_tekton_pipelines:
        setup_pipelines(ctx)

        # The RBAC, Secrets and generated manifests a team's webhooks need.
        # Every operation `setup_triggers` does is create-or-replace, so a repeat run *is* the retry:
        # treating an `apply` failure as transient is how a script hides an object that is already
        # there in a shape `kubectl` disliked, and there is no retry loop here to inherit that claim.
        #
        # The `Context` goes to the function as well as the client this function built from it,
        # because the last half of the flow builds its own `KubernetesUtils` before it hands a
        # directory to `apply`: a client built out of anything else says which cluster the team's
        # manifests land in. The team is not a parameter any more -- `setup_triggers` asks the
        # `Context` for it, which is the refusal `TektonUtils.__init__` performed until the class
        # was deleted in P4-6, and it is asked above the first RBAC object it is needed for.
        triggers.setup_triggers(ctx, kubernetes_utils, config, environment)
    else:
        logging.info("Skipping Tekton pipeline configuration.")
        skipped = ("the Tekton pipeline configuration, which this run asked for with "
                   "--no-configure-tekton-pipelines",)

    # No service mesh is installed, and no flag asks for one: kubementat dropped Linkerd on
    # the owner's decision of 2026-10-28. `kmt uninstall` still sweeps a mesh left behind.

    _print_install_finish_message(ctx)

    # The result is built at the end and not along the way, for the reason the gates above give:
    # an install that got here wrote everything it was told to write, and a value assembled from
    # flags alone would report `--no-configure-tekton-pipelines` as a decision rather than as what
    # actually happened. `api_versions_preflighted` is set by the line that preflights and not by
    # the flag, because that is the one write in this function whose success is *not* the same
    # fact as having run -- it refuses when the controller serves the wrong thing.
    return InstallResult(
        environment=environment,
        team=team,
        helmfile_installation_group=helmfile_installation_group,
        helmfile_label_filter=group_filter,
        cluster_answered=bool(cluster_answered),
        permissions_granted=bool(permissions_granted),
        tekton_installed=True,
        api_versions_preflighted=True,
        releases_applied=True,
        pipelines_configured=configure_tekton_pipelines,
        triggers_configured=configure_tekton_pipelines,
        consent=_consent_of(effective_consent),
        skipped=skipped,
    )

def validate_cluster(ctx, consent) -> bool:
    """Validates cluster connectivity and carries the run's consent before proceeding.

    This method asks the API server for its version, which is the request that answers
    "is there a cluster here at all", and then settles whether this run is allowed to write to it.
    A cluster that does not answer stops the install before the first write.

    The read is the client's now: `kubectl cluster-info` printed four namespace names that mean
    nothing to kubementat and exit-coded on nothing, so it was standing in for a question the
    client answers directly. What it was standing in for is what is checked now.

    The question is not asked here. `--yes` hands the answer down (`kmt install --yes`), a terminal
    hands down the way to ask it, and a caller that hands down neither is refused before the first
    write instead of reading a line from a terminal it cannot see — which is the whole reason the
    prompt moved to the CLI and not just to a different function in this file.

    Returns:

    - True - the cluster answered. It answers with one value because every other outcome leaves
      this function somewhere else: an unreachable cluster through `exit(1)`, a refused consent
      through a named error. The caller wants the fact that the read happened, not a branch to
      take, and "did the cluster answer" is the one fact an install cannot recover afterwards.
    """

    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)


    if consent.is_assumed():
        logging.info("Confirmation: --yes was given, installing without prompting.")

    if not kubernetes_utils.cluster_is_reachable():
        logging.info("Error: the cluster did not answer. Cannot proceed without a valid"
                     " Kubernetes cluster connection.")
        exit(1)

    consent.decide(f"install kubementat on this cluster ({environment}/{team})")
    return True


def check_cluster_permissions(ctx) -> bool:
    '''
    Verifies necessary Kubernetes permissions for cluster operations.

    All six probes go through the client: `kubectl auth can-i <verb> <resource>` is a
    `SelfSubjectAccessReview` asked of the server, which is the same request kubectl sends and
    the same answer it parses. A review answers without stdin, so no `input="yes"` escape hatch is
    threaded through it -- a prompt kubectl would print is not in this path at all.

    Returns:

    - True - all six creates were granted. A denial is not a value and never was: it raises, so
      the answer this function hands back only ever says "the install may write here", which is
      the fact `InstallResult` reports and nothing the caller can re-derive from the cluster.
    '''

    kubernetes_utils = KubernetesUtils(ctx)

    denied = [
        f"create {resource}"
        for resource in CLUSTER_PERMISSION_CHECK_RESOURCES
        if not kubernetes_utils.self_can("create", resource)
    ]
    if denied:
        raise errors.CommandExecutionError(
            shell.CommandResult(["kubectl", "auth", "can-i", "create", "<cluster scope>"], 1),
            "this service account cannot create: " + ", ".join(denied))
    logging.info("Cluster permission check passed.")
    return True


def _print_install_finish_message(ctx):

    environment = ctx.environment
    team = ctx.team

    logging.info("")
    logging.info("###############################################")
    logging.info("###############################################")
    logging.info("###############################################")
    logging.info("Installed kubementat to cluster successfully :D")
    logging.info("")
    logging.info("Now you are ready to start making it your own.")
    logging.info("")
    logging.info("You can run your first hello world pipeline right now by using the kmt cli tool:")
    logging.info("# View kmt help section: ")
    logging.info("uv run kmt --help")
    logging.info("")
    logging.info("# execute pipeline run via kmt")
    logging.info(f"uv run kmt tekton run-pipeline {environment} {team} global/hello-world-pipeline-run.yml")
    logging.info("")
    logging.info("# View the results via the tekton dashboard by tunneling via the kmt cli:")
    logging.info("Once this command is executed you can visit http://127.0.0.1:9097 in your browser")
    logging.info("uv run kmt tunnel tekton")
    logging.info("###############################################")

###########################
# setup pipelines automation
###########################
