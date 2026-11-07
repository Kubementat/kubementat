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
"""


import logging
import sys

from kubementat import errors
from kubementat.infrastructure import shell
from kubementat.infrastructure.helm import HelmUtils
from kubementat.infrastructure.kubernetes import KubernetesUtils
# The preflight assertion moved out of `tekton_utils.py` (P4-2). The name it goes by is unchanged;
# what changed is the module it is read from, and it is read from that module rather than bound
# here because every tekton seam is patched by module object.
from kubementat.infrastructure.tekton import preflight
from kubementat.tekton_utils import TektonUtils

from kubementat.automations.install.dependencies import (
    KUBEMENTAT_DEPENDENCIES,
    check_dependencies_are_installed,
)
from kubementat.automations.install.pipelines import setup_pipelines


# What the install preflight proves this service account may do, spelled as the API spells it.
#
# The names are plural on purpose. `kubectl auth can-i create deployment` takes the singular and
# maps it through the discovery table itself; a `SelfSubjectAccessReview` is the request that
# mapping produces, so it has to carry the resource name and not the kind. Handing it
# `deployment` instead of `deployments` answers "no" on every cluster and fails an install that
# would otherwise work, which is the only way this conversion could have gone quietly wrong.
CLUSTER_PERMISSION_CHECK_RESOURCES = (
    "namespaces", "deployments", "clusterroles", "roles", "daemonsets", "replicasets")


def install_kubementat(ctx, configure_tekton_pipelines=True, helmfile_installation_group='standard', assume_yes=False):
    '''
    Main installation logic for Kubementat components including Tekton and pipeline setup.

    Parameters:

    - configure_tekton_pipelines: bool = True - Whether to configure Tekton pipelines
    - helmfile_installation_group: str = 'standard' - Helmfile installation group
    - assume_yes: bool = False - Skip the interactive confirmation (`kmt install --yes`)

    Returns:

    - None (performs side-effect operations)

    Raises:

    - Exception - If dependency checks fail or installation scripts encounter errors
    '''

    config = ctx.config
    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)


    check_dependencies_are_installed(KUBEMENTAT_DEPENDENCIES)

    validate_cluster_prompt(ctx, assume_yes=assume_yes)
    check_cluster_permissions(ctx)

    logging.info("######################################################")
    logging.info(f"CONFIGURING: Kubementat for environment {environment} and team {team}")
    logging.info("######################################################")

    # Tekton installation: the three pinned releases and the ClusterRole that authorises
    # helm to deploy. It used to be the last bash script on this path, which is why `kmt
    # install` still needed a checkout of `scripts/` to touch a cluster.
    TektonUtils(ctx).install()

    # That the applies succeeded is not the same fact as "the cluster now serves what
    # kubementat speaks": the release URLs are built from the TEKTON_VERSION_* pins, so a
    # pin that moved without the URL scheme, or an apply that landed against an old
    # controller, both leave the install green and the first pipeline run full of 404s. Ask
    # the discovery question for both groups before helmfile and before pipeline setup.
    preflight.preflight_installed_tekton_api_versions(kubernetes_utils, config)

    # helmfile apply
    # The install path applies without asking, exactly as before: `kmt install` has its own
    # confirmation (validate_cluster_prompt), and helmfile's `[y/n]` is not reachable from
    # `kmt install --yes`. Passing the flag explicitly keeps that promise if the CLI-side
    # rule ever changes its default.
    group_filter = f"group={helmfile_installation_group}"
    HelmUtils(ctx).helmfile_apply(
        environment_name=environment,
        helmfile_label_filter=group_filter, 
        interactive=False
    )

    # Setup pipelines and triggers
    if configure_tekton_pipelines:
        setup_pipelines(ctx)

        # The RBAC, Secrets and generated manifests a team's webhooks need. This call site was
        # the last bash script on the default install path. It also used to retry: `apply`
        # failures were treated as transient, which is how a script hid an object that was
        # already there in a shape `kubectl` disliked. Every operation setup_triggers does is
        # now create-or-replace, so a repeat run is the retry, and the retry is gone.
        TektonUtils(ctx).setup_triggers()
    else:
        logging.info("Skipping Tekton pipeline configuration.")

    # No service mesh is installed, and no flag asks for one: kubementat dropped Linkerd on
    # the owner's decision of 2026-10-28. `kmt uninstall` still sweeps a mesh left behind.

    _print_install_finish_message(ctx)

def validate_cluster_prompt(ctx, assume_yes=False):
    """Validates cluster connectivity and prompts user confirmation before proceeding.

    This method asks the API server for its version, which is the request that answers
    "is there a cluster here at all", then asks for confirmation to continue. A cluster that
    does not answer stops the install before the first write.

    The read is the client's now: `kubectl cluster-info` printed four namespace names that mean
    nothing to kubementat and exit-coded on nothing, so it was standing in for a question the
    client answers directly. What it was standing in for is what is checked now.

    With assume_yes the prompt is skipped, which is what `kmt install --yes` does.
    Without it, a run that has no terminal to ask in fails closed instead of dying
    inside `input()`.
    """

    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)


    if assume_yes:
        logging.info("Confirmation: --yes was given, installing without prompting.")
    else:
        if not sys.stdin.isatty():
            logging.error("install requires confirmation, but stdin is not a terminal.")
            logging.error("Re-run with --yes (or -y) to install kubementat without prompting:")
            logging.error(f"  kmt install {environment} {team} --yes")
            exit(1)

    if not kubernetes_utils.cluster_is_reachable():
        logging.info("Error: the cluster did not answer. Cannot proceed without a valid"
                     " Kubernetes cluster connection.")
        exit(1)

    if assume_yes:
        return

    while True:
        yn = input("Do you really wish to install kubementat on this cluster? (yes/no): ")
        if yn.lower() in ['yes', 'y']:
            break
        elif yn.lower() in ['no', 'n']:
            logging.info("Cancelled install script.")
            exit(1)
        else:
            logging.info("Please answer yes or no.")


def check_cluster_permissions(ctx):
    '''
    Verifies necessary Kubernetes permissions for cluster operations.

    All six probes go through the client: `kubectl auth can-i <verb> <resource>` is a
    `SelfSubjectAccessReview` asked of the server, which is the same request kubectl sends and
    the same answer it parses. The six verbs were checked through `input="yes"` because
    kubectl's own stdin prompt made the check runnable from a lane; a review needs no stdin,
    which is why the escape hatch is no longer needed here.
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
