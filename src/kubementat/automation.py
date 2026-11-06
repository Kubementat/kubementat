import logging
import os
import re
import time
import json
import base64
import shutil
import sys
from pathlib import Path
from kubernetes import client
import click
from kubementat import shell
from kubementat import errors
from kubementat import constants
from kubementat import config_bootstrap, config_keys
from kubementat.config import Config
from kubementat.kubernetes_utils import (DEPLOYMENT_LABEL, MANAGED_BY_KUBEMENTAT, MANAGED_BY_LABEL,
                                         KubernetesUtils, helm_target_args)
from kubementat.helm_utils import HelmUtils, helm_list_releases_argv
from kubementat.tekton_utils import (
    HELM_DEPLOYER_CLUSTER_ROLE_NAME,
    TektonUtils,
    preflight_installed_tekton_api_versions,
    preflight_tekton_api_version,
)
from kubementat import k8s_apply
from kubementat import user_management


# The only thing the binary-version check reads out of a `--version` answer: the first
# `major.minor.patch` triple. The lookbehind is what keeps `g0d08d0` in `v4.3.1+gc0d08d0` and a
# timestamp in a build banner from being mistaken for the version.
VERSION_TRIPLE = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)")


# How each pinned binary is asked for its version, measured here rather than guessed: `helm
# --version` is *rejected* by helm 4 (`unknown flag`), so it cannot be the probe for a product that
# says it serves both majors, while `helm version --short` answers the same shape on helm 3 and helm 4
# and needs no struct to unwind. helmfile answers on one line with `--version`, and the other shapes
# print a banner a parser would have to step over. Both answers are read by `first_binary_version`,
# which is why one check serves both binaries. This is an argv table and not the version table: the
# versions are `constants.PINNED_BINARY_VERSIONS`, which is what a CI image build reads later.
BINARY_VERSION_ARGV = {
    "helm": ["helm", "version", "--short"],
    "helmfile": ["helmfile", "--version"],
}


def first_binary_version(output):
    """The version inside a version answer, or None when there is nothing to read.

    The first line carrying a triple wins, because that is where the version lives on both pinned
    binaries and what follows it can be anything at all — an upgrade banner, a plugin list, a
    traceback. Returning None instead of raising is the whole point: a binary that does not answer
    in a recognisable shape is information for the operator, not a reason to stop a run that was
    otherwise going to succeed.
    """
    for line in (output or "").splitlines():
        found = VERSION_TRIPLE.search(line)
        if found:
            return found.group(0)
    return None


def _base64_encode_file(path) -> str:
    """The file's bytes under exactly one base64 layer -- the unit a Secret is built from.

    One layer, never two: the consumer on the other side decodes exactly once. Which Secret
    field the result belongs to -- `data` or `stringData` -- is the caller's decision about who
    applies the transport encoding, and the two deployer Secrets in `setup_pipelines` are where
    that decision is written down.
    """
    return base64.b64encode(Path(path).read_bytes()).decode("utf-8")


class Automation:
    '''
    A class that provides Kubernetes automation capabilities for installing Kubementat components, setting up pipelines, managing secrets, and configuring Docker registry access.
    
    Parameters:

    - environment: str - The target environment (e.g., dev, prod)
    - team: str - The team identifier

    Returns:

    - None (performs side-effect operations)
    '''  
    # What `kmt install` refuses to start without. The list describes the
    # *distribution*, not only this package's code: a binary belongs here when kubementat's own
    # Python execs it, or when a `scripts/**` file this repository ships execs it and the operator
    # is the one running that file. Row 21 deleted the last script that shipped here, so that
    # second half of the rule has no referent today -- it stays written down because a re-added
    # script is precisely what the check the rule is pinned to would catch.
    #
    # `yq` is gone. Nothing in `src/` execs it and nothing under `scripts/` names
    # it; the last thing that ran it was the Tekton pipeline-runner, deleted by `85f1ea8`,
    # and that work had already moved Python-side to `yaml.safe_load` / `safe_dump` long before the
    # deletion. It stayed declared only because it had once been true, and a check that fails for a
    # tool no part of the product runs is a check that trains the operator to work around it. (It
    # remains in the CI image, which is a different workstream's inventory and not this list's.)
    #
    # `jq` goes with it. It was kept deliberately rather than left over --
    # no `src/` code path execs it, but `retrieve_node_addresses.sh` did, two lines piping
    # `kubectl get nodes -o=json` through it, and that file shipped. The row deleted the file, and
    # the argument that kept the tool dies exactly when the file that carried it does: there is now
    # no shipped script for kubementat to run whose external requirement this check would have to
    # stop verifying, and a name kept for a thing that no longer exists is the same mistake `yq`
    # was. What answers the surface is Python, and it needs no parser on the way out: the node
    # addresses are `KubernetesUtils.get_nodes()`, whose `status.addresses` arrive as
    # `V1NodeAddress` objects and never as JSON text, and the cluster's resource list is
    # `KubernetesUtils.get_api_resources()`. Pinned both ways by
    # `tests/test_the_declared_dependencies_are_what_the_tree_needs.py`, which re-derives this list
    # from the tree and fails if a future script re-introduces a tool the check does not name.
    KUBEMENTAT_DEPENDENCIES = [
        "kubectl",
        "helm",
        "helmfile",
        "git",
        "gpg",
        "python3",
        "pip",
        "tkn",
        "helm diff"
    ]

    def __init__(self, ctx):
        # One `Context` and nothing else, the same shape `HelmUtils`, `CiUtils`
        # and `IngressGenerator` were given. `self.ctx` is kept because two of this class's own
        # services are handed the *same* object rather than a pair of strings rebuilt into a new one:
        # a second `Context.build` on the install path would be a second answer to "which cluster",
        # which is exactly what having one `Context` exists to stop, and it is the failure mode this
        # shape is judged on.
        self.ctx = ctx
        self.environment = ctx.environment
        self.team = ctx.team
        self.config = ctx.config
        self.kubernetes_utils = KubernetesUtils(ctx)
        # No `self.helm_utils`. The install path asks for one object, once, and hands it the
        # `Context` this class was given (`HelmUtils(self.ctx)`, below) — the shape `routing.py` has
        # used since the Context sweep, and the reason an attribute here would be a lie: `HelmUtils`
        # refuses to be built without a team, and `kmt uninstall` is a command that has none.
        # Building a service whose guard the command cannot satisfy, for a command that never calls
        # it, is the bug that was this class's `_helm_utils` scaffold wearing a cleaner name — and the
        # Context it would be handed is `self.ctx` either way, so nothing is gained by the second
        # answer to "which cluster" that one `Context` exists to stop.

    ###########################
    # kubementat cluster installation automation
    ###########################
    def install_kubementat(self, configure_tekton_pipelines=True,
        helmfile_installation_group='standard', assume_yes=False):
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

        self.check_dependencies_are_installed(self.KUBEMENTAT_DEPENDENCIES)

        self.validate_cluster_prompt(assume_yes=assume_yes)
        self.check_cluster_permissions()

        logging.info("######################################################")
        logging.info(f"CONFIGURING: Kubementat for environment {self.environment} and team {self.team}")
        logging.info("######################################################")

        # Tekton installation: the three pinned releases and the ClusterRole that authorises
        # helm to deploy. It used to be the last bash script on this path, which is why `kmt
        # install` still needed a checkout of `scripts/` to touch a cluster.
        TektonUtils(self.ctx).install()

        # That the applies succeeded is not the same fact as "the cluster now serves what
        # kubementat speaks": the release URLs are built from the TEKTON_VERSION_* pins, so a
        # pin that moved without the URL scheme, or an apply that landed against an old
        # controller, both leave the install green and the first pipeline run full of 404s. Ask
        # the discovery question for both groups before helmfile and before pipeline setup.
        preflight_installed_tekton_api_versions(self.kubernetes_utils, self.config)

        # helmfile apply
        # The install path applies without asking, exactly as before: `kmt install` has its own
        # confirmation (validate_cluster_prompt), and helmfile's `[y/n]` is not reachable from
        # `kmt install --yes`. Passing the flag explicitly keeps that promise if the CLI-side
        # rule ever changes its default.
        group_filter = f"group={helmfile_installation_group}"
        HelmUtils(self.ctx).helmfile_apply(
            environment_name=self.environment,
            helmfile_label_filter=group_filter, 
            interactive=False
        )

        # Setup pipelines and triggers
        if configure_tekton_pipelines:
            self.setup_pipelines()

            # The RBAC, Secrets and generated manifests a team's webhooks need. This call site was
            # the last bash script on the default install path. It also used to retry: `apply`
            # failures were treated as transient, which is how a script hid an object that was
            # already there in a shape `kubectl` disliked. Every operation setup_triggers does is
            # now create-or-replace, so a repeat run is the retry, and the retry is gone.
            TektonUtils(self.ctx).setup_triggers()
        else:
            logging.info("Skipping Tekton pipeline configuration.")

        # No service mesh is installed, and no flag asks for one: kubementat dropped Linkerd on
        # the owner's decision of 2026-10-28. `kmt uninstall` still sweeps a mesh left behind.

        self._print_install_finish_message()

    def _record_binary_version(self, dependency):
        """Say which version of a pinned binary this run is on, and warn — never exit — if it is
        not the one kubementat is measured against.

        Three rules, and the middle one is the whole ticket.

        * **This is a version check and not a presence check.** It runs after the binary is known to
          be there, from inside ``check_dependencies_are_installed``, so the entry point --
          ``kmt install`` -- gets it without the logic being forked.
        * **A mismatch warns and continues.** ``e57fed3`` made the release listing portable across
          helm 3 and helm 4 *deliberately*: the six statuses were chosen so the product would work
          on both majors. Refusing to run outside a declared range would take a working cluster and
          block it, paying for safety the code already provides in operator downtime. So the
          failure behaviour of this check is unchanged for a binary that is *absent*, and only the
          version path became a warning. Nothing here may grow an ``exit(1)``.
        * **A binary that cannot answer does not break anything.** No version, an unrecognisable
          answer, or a command that errors out is a warning naming what was seen, and the run goes
          on. Silence would be the same fail-closed mistake wearing different clothes, so the
          warning is unconditional; only the *claim* that the version is tested is withheld.
        """
        if dependency not in BINARY_VERSION_ARGV:
            return  # not a pinned binary: the presence check above is all it gets
        argv = BINARY_VERSION_ARGV[dependency]
        try:
            # Looked up from the table here instead of handed over as `argv`, so that the guard which
            # refuses an argv assembled from a path can read the program name off this call site.
            answer = shell.run(BINARY_VERSION_ARGV[dependency], capture=True, check=False)
            raw = f"{answer.stdout or ''}{answer.stderr or ''}"
            observed = first_binary_version(raw)
            pinned = constants.pinned_binary_version(dependency)
            if observed is None:
                logging.warning(f"{dependency} did not answer {' '.join(argv)} with a readable version; "
                                f"the run continues without claiming which version this is "
                                f"(saw: {raw.strip()[:120]!r}).")
                return
            if int(observed.split(".")[0]) not in constants.binary_majors_served(dependency):
                logging.warning(
                    f"{dependency} {observed} is not a version kubementat is tested against (pinned "
                    f"{pinned}, majors served {constants.binary_majors_served_label(dependency)}); the "
                    "run continues, because portability across majors is a decision and not an "
                    "accident (see e57fed3)."
                )
                return
            logging.info(f"{dependency} version {observed} is within the tested range (pinned {pinned}).")
        except Exception as exc:  # a version check that cannot be answered is not a reason to stop
            logging.warning(f"{dependency} could not be asked for its version ({exc}); the run "
                            "continues without claiming which version it is on.")

    def check_dependencies_are_installed(self, dependencies):
        try:
            for dep in dependencies:
                logging.info(f"Checking installation of: {dep} ...")
                if dep == 'helm diff':
                    shell.run(["helm", "diff", "version"])
                    continue
                if shutil.which(dep) is None:
                    logging.info(f"Error: {dep} not installed.")
                    raise Exception(f"Installation requirements not met: {dep} is not installed!")
                # Which version, not merely whether it exists. This half warns and carries on; the
                # raise directly above keeps exactly the behaviour it had before it was written.
                self._record_binary_version(dep)
        except Exception as e:
            logging.error("Could not find all required dependencies. Please check that all depdencies are installed correctly!")
            exit(1)

    def validate_cluster_prompt(self, assume_yes=False):
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

        if assume_yes:
            logging.info("Confirmation: --yes was given, installing without prompting.")
        else:
            if not sys.stdin.isatty():
                logging.error("install requires confirmation, but stdin is not a terminal.")
                logging.error("Re-run with --yes (or -y) to install kubementat without prompting:")
                logging.error(f"  kmt install {self.environment} {self.team} --yes")
                exit(1)

        if not self.kubernetes_utils.cluster_is_reachable():
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

    def check_cluster_permissions(self):
        '''
        Verifies necessary Kubernetes permissions for cluster operations.

        All six probes go through the client: `kubectl auth can-i <verb> <resource>` is a
        `SelfSubjectAccessReview` asked of the server, which is the same request kubectl sends and
        the same answer it parses. The six verbs were checked through `input="yes"` because
        kubectl's own stdin prompt made the check runnable from a lane; a review needs no stdin,
        which is why the escape hatch is no longer needed here.
        '''
        denied = [
            f"create {resource}"
            for resource in self.CLUSTER_PERMISSION_CHECK_RESOURCES
            if not self.kubernetes_utils.self_can("create", resource)
        ]
        if denied:
            raise errors.CommandExecutionError(
                shell.CommandResult(["kubectl", "auth", "can-i", "create", "<cluster scope>"], 1),
                "this service account cannot create: " + ", ".join(denied))
        logging.info("Cluster permission check passed.")

    def _print_install_finish_message(self):
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
        logging.info(f"uv run kmt tekton run-pipeline {self.environment} {self.team} global/hello-world-pipeline-run.yml")
        logging.info("")
        logging.info("# View the results via the tekton dashboard by tunneling via the kmt cli:")
        logging.info("Once this command is executed you can visit http://127.0.0.1:9097 in your browser")
        logging.info("uv run kmt tunnel tekton")
        logging.info("###############################################")

    ###########################
    # setup pipelines automation
    ###########################
    def setup_pipelines(self):
        """
        Configure pipeline infrastructure in a Kubernetes cluster

        This method sets up the complete pipeline environment by:
        1. Creating necessary directories for pipelines, tasks and pipeline-runs
        2. Setting up Kubernetes namespaces
        3. Creating SSH and GPG secrets
        4. Configuring service accounts and role bindings
        5. Applying Tekton tasks and pipelines from the three layers, product first, then
           environment, then team — the last layer applied answers a name that more than one layer
           declares
        6. Displaying setup results

        Parameters:
            self: Automation class instance with access to configuration and Kubernetes utilities
            
        Returns:
            None (performs side-effect operations)
            
        Raises:
            Exception: If any Kubernetes operation fails
        """    
        logging.info(f"Starting setup_pipelines execution with environment: {self.environment} and team: {self.team}")

        # The deployer's keypair is a property of the configuration directory, not of
        # an encrypted layer: `kmt config init` wrote the four files into the root this run was
        # given, and nothing answers GIT_DEPLOYER_PRIVATE_KEY_BASE64 /
        # GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64 in `static.encrypted.json` any more. Ask for the
        # pair before the first cluster contact of any kind, so a tree without one refuses here
        # by name instead of dying inside the Kubernetes client's serializer with
        # `raw_data={"ssh-privatekey": None}` (census L, Q2 item 8).
        deployer = config_keys.require_deployer_keypair(self.config.platform_config_dir)

        # setup_pipelines applies the product's Tasks and Pipelines and the team's own two
        # directories one by one, which is the shape a too-old controller answers in: a 404
        # per resource and no sentence saying why. Ask the cluster once, before that starts.
        preflight_tekton_api_version(self.kubernetes_utils, self.config)

        helm_deployer_sa = self.config.get('team_static').get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')
        pipeline_ns = self.config.get('team_static').get('PIPELINE_NAMESPACE')
        app_deployment_ns = self.config.get('team_static').get('APP_DEPLOYMENT_NAMESPACE')

        logging.info(f"Helm Deployer Service Account: {helm_deployer_sa}")
        logging.info(f"Pipeline Namespace: {pipeline_ns}")
        logging.info(f"App Deployment Namespace: {app_deployment_ns}")
        logging.info(f"Team Pipelines directory: {self.config.tekton_team_pipeline_dir}")
        logging.info(f"Team Tasks directory: {self.config.tekton_team_task_dir}")
        logging.info(f"Team Pipeline-runs directory: {self.config.tekton_team_pipeline_run_dir}")
        
        logging.info(f"Creating team pipelines directory if not existing...")
        os.makedirs(self.config.tekton_team_pipeline_dir,exist_ok=True)

        logging.info(f"Creating team tasks directory if not existing...")
        os.makedirs(self.config.tekton_team_task_dir,exist_ok=True)

        logging.info(f"Creating team pipeline runs directory if not existing...")
        os.makedirs(self.config.tekton_team_pipeline_run_dir,exist_ok=True)

        # Create namespaces
        self.kubernetes_utils.create_namespace(pipeline_ns)
        self.kubernetes_utils.create_namespace(app_deployment_ns)

        # Setup secrets
        logging.info("Setting up SSH secret: git-deployer-ssh-key")
        self.kubernetes_utils.create_or_replace_secret(
            namespace=pipeline_ns,
            secret_name="git-deployer-ssh-key",
            key_type="kubernetes.io/ssh-auth",
            # The git-clone-with-ssh-auth task writes this environment variable straight to
            # ~/.ssh/deploy_key, so the Secret's data is the key file's bytes under exactly one
            # base64 layer -- the transport encoding, which kubelet removes on the way in.
            raw_data={"ssh-privatekey": _base64_encode_file(deployer.ssh_private)}
        )
        logging.info("Setting up GPG secret: git-deployer-gpg-key")
        self.kubernetes_utils.create_or_replace_secret(
            namespace=pipeline_ns,
            secret_name="git-deployer-gpg-key",
            key_type="Opaque",
            # The git-clone tasks pipe this variable through exactly one `base64 -d` before
            # `gpg --import`, so what they see is the key file's bytes base64-encoded once. It
            # goes in as string_data -- the API server adds the transport encoding -- and a
            # second encoding on this side is the bug no unit test would catch before a
            # pipeline did.
            string_data={"private-key": _base64_encode_file(deployer.gpg_private)}
        )

        # The symmetric key that unlocks the configuration repository. It is a property of
        # the configuration directory like the deployer's pair is, but it is not the deployer's key: it is
        # git-crypt's own, exported by `config init` step 8 and written into the directory's root, where
        # it is gitignored and therefore **never reaches a clone**. So the clone that `git-clone-configuration`
        # makes arrives locked, and the only thing that can open it is a copy of this file that someone
        # carried past git. This Secret is that copy, and the unlock step reads it by `secretKeyRef`.
        #
        # It goes in as `string_data` with the bytes base64-encoded once -- the same shape as the GPG
        # secret two paragraphs above, and for the same reason: the step that consumes it pipes the value
        # through exactly one `base64 -d` and then into `git-crypt unlock -`. Measured against git-crypt
        # 0.7.0: that pipe yields exit 0 and plaintext, which is the whole contract this Secret has to
        # keep. A second layer here is indistinguishable from a wrong key, and it presents as
        # `Error: -: not a valid git-crypt key file`.
        #
        # Created only for a directory that says it is encrypted. `uses_git_crypt` reads `.gitattributes`,
        # which is the marker that survives a clone; the keystore under `.git/` does not, and neither does
        # the key file. A tree that never declared the filter has nothing to unlock, and failing its
        # install because a file is missing from a repository that never used git-crypt is a regression
        # this ticket would have introduced -- `config init` writes that file as its second step, so on a
        # tree that went through init the answer is yes and the refusal below still fires.
        if config_bootstrap.uses_git_crypt(self.config.platform_config_dir):
            logging.info("Setting up git-crypt secret: git-crypt-configuration-key")
            self.kubernetes_utils.create_or_replace_secret(
                namespace=pipeline_ns,
                secret_name="git-crypt-configuration-key",
                key_type="Opaque",
                string_data={"git-crypt-key": _base64_encode_file(config_bootstrap.require_symmetric_key(
                    self.config.platform_config_dir))}
            )
        else:
            logging.warning(
                f"{self.config.platform_config_dir} declares no git-crypt filter in its .gitattributes, so "
                "no git-crypt-configuration-key Secret was created. Pipelines clone the configuration and "
                "the unlock step needs that Secret, so if this configuration is encrypted the clone stays "
                "locked and every step that reads it sees ciphertext. Run `kmt config init` against this "
                "directory, or re-run install from one that declares the filter, if that is the case.")

        # Setup service account and roles
        logging.info(f"Setting up service account: {helm_deployer_sa}")
        self.kubernetes_utils.create_service_account(pipeline_ns, helm_deployer_sa,[
            {
                # add the git-deployer-ssh-key to the service account so it will be able to use the git-clone-with-ssh-auth task
                "name": "git-deployer-ssh-key"
            }
        ])

        logging.info(f"Setting up role bindings for {helm_deployer_sa}")
        self._setup_pipelines_setup_role_bindings(pipeline_ns, app_deployment_ns, helm_deployer_sa)

        # Apply the product's own Tasks and Pipelines. They are package data, not project state, so
        # they come from the asset accessors and not from `kubementat_main_dir`: a `kmt` installed
        # from a wheel has no `{root}/tekton_ci/tasks` to find, and the two inline path builds here
        # used to aim `apply_resources` at it. The cost of the old spelling was measured, not
        # assumed — with no root pointed at a checkout it raised `FileNotFoundError` naming the
        # venv, and with `KUBEMENTAT_HOME` at one it applied that checkout's manifests, so the same
        # install answered from two different trees depending on a variable that has nothing to do
        # with the cluster. Asking the accessor is the only answer that is true for both.
        # Three apply layers, in this order, last one wins: product, environment, team. The order
        # *is* the collision rule — a Task that two layers both name is answered by the layer that
        # is applied later, because `create_or_replace_custom_resource` patches on 409. There is no
        # name index and no ownership registry anywhere in this sequence on purpose (census E, "Same
        # shape for Tekton manifests"): a dictionary of manifest name -> layer would be a second
        # source of truth about an order that is already in the calls, and the day the two disagree
        # the log stops being evidence. So each layer states what it is applying and how much of it
        # it found, and that is what a reader debugging a cluster has to read.
        #
        # Nothing here deletes. kubementat applies what it ships and what the layers declare, and an
        # object that stopped being shipped stays on the cluster until the operator removes it —
        # pruning is `uninstall`'s job, never setup's. Asserted structurally by
        # `test_no_setup_path_prunes_a_manifest_a_layer_stopped_shipping` in
        # `tests/test_the_manifest_layers_apply_product_then_environment_then_team.py`.
        self._apply_manifest_layer("product", self.config.tekton_task_asset_dir, pipeline_ns,
                                   kind="Task", group=self.config.TEKTON_API_GROUP,
                                   version=self.config.TEKTON_API_VERSION)
        self._apply_manifest_layer("product", self.config.tekton_pipeline_asset_dir, pipeline_ns,
                                   kind="Pipeline", group=self.config.TEKTON_API_GROUP,
                                   version=self.config.TEKTON_API_VERSION)

        # The environment layer, which is the one the product does not ship. It is between the
        # product and the team
        # because that is the direction of override: an environment tunes what the product ships, a
        # team tunes what its environment says. No key, no flag, no `static.json` entry names it —
        # a directory is not an answer, and neither is a `triggers/` directory nothing reads (see
        # the comment on `Config.tekton_env_task_dir`).
        self._apply_manifest_layer("environment", self.config.tekton_env_task_dir, pipeline_ns,
                                   kind="Task", group=self.config.TEKTON_API_GROUP,
                                   version=self.config.TEKTON_API_VERSION)
        self._apply_manifest_layer("environment", self.config.tekton_env_pipeline_dir, pipeline_ns,
                                   kind="Pipeline", group=self.config.TEKTON_API_GROUP,
                                   version=self.config.TEKTON_API_VERSION)

        # The team's own two directories, still applied last, which is what makes a team's manifest
        # the answer for a Task the product also ships.
        self._apply_manifest_layer("team", self.config.tekton_team_task_dir, pipeline_ns,
                                   kind="Task", group=self.config.TEKTON_API_GROUP,
                                   version=self.config.TEKTON_API_VERSION)
        self._apply_manifest_layer("team", self.config.tekton_team_pipeline_dir, pipeline_ns,
                                   kind="Pipeline", group=self.config.TEKTON_API_GROUP,
                                   version=self.config.TEKTON_API_VERSION)

        logging.info("Tekton Pipeline and Task Setup completed successfully!")
        self._setup_pipelines_show_results(pipeline_ns, app_deployment_ns, helm_deployer_sa)

    def _setup_pipelines_setup_role_bindings(self, pipeline_ns, app_ns, sa_name):
        '''
        Creates role bindings for pipeline namespace and app deployment.

        Parameters:

        - pipeline_ns: str - Pipeline namespace
        - app_ns: str - Application deployment namespace
        - sa_name: str - Service account name

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If role binding creation fails
        '''
        # Define the Role Binding for the app deployment
        # Create within app namespace but point to service account within pipeline namespace
        logging.info(f"Creating role binding for cluster role helm-deployer-cluster-role in namespace {app_ns} pointing to service account {sa_name} in namespace {pipeline_ns}")
        binding = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(name="helm-deployer-role-binding-app-deployment", labels={"managed-by": "kubementat"}),
            subjects=[client.RbacV1Subject(kind="ServiceAccount", name=sa_name, namespace=pipeline_ns)],
            role_ref=client.V1RoleRef(kind="ClusterRole", name="helm-deployer-cluster-role", api_group="rbac.authorization.k8s.io")
        )
        self.kubernetes_utils.create_or_replace_role_binding(app_ns, "helm-deployer-role-binding-app-deployment", binding)

        # Define Role Binding for pipeline namespace
        logging.info(f"Creating role binding for cluster role helm-deployer-cluster-role in namespace {pipeline_ns} pointing to service account {sa_name} in namespace {pipeline_ns}")
        binding = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(name="helm-deployer-role-binding-pipeline-namespace-access", labels={"managed-by": "kubementat"}),
            subjects=[client.RbacV1Subject(kind="ServiceAccount", name=sa_name, namespace=pipeline_ns)],
            role_ref=client.V1RoleRef(kind="ClusterRole", name="helm-deployer-cluster-role", api_group="rbac.authorization.k8s.io")
        )
        self.kubernetes_utils.create_or_replace_role_binding(pipeline_ns, "helm-deployer-role-binding-pipeline-namespace-access", binding)

    def _apply_manifest_layer(self, layer, resource_dir, target_namespace, kind, group, version):
        '''
        One apply layer of a directory, obeying the rule that an absent layer is a no-op.

        This is the one place the absent-directory rule lives, so that the product layer, the two
        new environment layers and the team layers cannot disagree about what a missing directory
        means. The rule and why it is not the same thing as the primitive's rule:

        * `apply_resources` raises `FileNotFoundError` for a directory that is not there, because a
          path built out of a configuration value that points nowhere is kubementat's bug and must
          not be whispered about. That is correct for a root that is *supposed* to exist.
        * A layer that is not there is not that. `platform_config/{env}/tekton/` can be absent on a
          tree `kmt config init` just prepared -- it creates the directory, but a tree handed over
          from before that verb existed may not have it -- and a team that has generated nothing
          yet is a normal state and not a traceback waiting to interrupt an install. So the layer
          logs one line that
          names the directory and says it applied nothing, and the run continues.

        Both halves are one line long, name the layer and the directory, and say how many documents
        the walk found — which is the log a reader debugging a cluster reads to work out which layer
        answered for a Task.
        '''
        directory = Path(resource_dir)
        if not directory.is_dir():
            logging.info(f"{layer} layer: no {kind} directory at {directory}, so nothing is applied "
                         f"from it (0 documents). A layer that has not been populated yet is not an "
                         f"error; the layer below it is still the answer for every Task and Pipeline "
                         f"in this namespace.")
            return 0

        applied = k8s_apply.apply_resources(self.kubernetes_utils, str(directory),
                                            target_namespace, kind=kind,
                                            group=group, version=version)
        logging.info(f"{layer} layer: applied {applied} {kind} document(s) from {directory} — the "
                     f"last layer applied wins a name that appears in more than one layer.")
        return applied

    def _setup_pipelines_show_results(self, pipeline_ns, app_ns, helm_deployer_sa):
        """Display the results of the setup including service account, role-bindings,
        Secrets, Tasks and Pipeline Custom Tekton Resources."""

        logging.info("=== Service Account ===")
        logging.info(f"Name: {helm_deployer_sa}")
        logging.info(f"Namespace: {pipeline_ns}")
        sa = self.kubernetes_utils.get_service_account(
            namespace=pipeline_ns,
            name=helm_deployer_sa
        )
        logging.info(sa)

        logging.info("=== Role Bindings ===")
        logging.info("Pipeline Namespace Role Binding:")
        rb = self.kubernetes_utils.get_role_binding(
            namespace=pipeline_ns,
            name="helm-deployer-role-binding-pipeline-namespace-access"
        )
        logging.info(rb)

        logging.info("App Namespace Role Binding:")
        rb_app = self.kubernetes_utils.get_role_binding(
            namespace=app_ns,
            name="helm-deployer-role-binding-app-deployment"
        )
        logging.info(rb_app)

        logging.info("=== Secrets ===")
        secrets = self.kubernetes_utils.list_secrets(namespace=pipeline_ns)
        for secret in secrets.items:
            logging.info(f"- {secret.metadata.name}")

        logging.info("=== Tasks ===")
        tasks = self.kubernetes_utils.list_custom_resources(
            namespace=pipeline_ns,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier="tasks"
        )

        for task in tasks['items']:
            logging.info(f"- {task['metadata']['name']}")

        logging.info("=== Pipelines ===")
        pipelines = self.kubernetes_utils.list_custom_resources(
            namespace=pipeline_ns,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier="pipelines"
        )
        for pipeline in pipelines['items']:
            logging.info(f"- {pipeline['metadata']['name']}")

        return None
    ###########################

    # secret setup automation
    ###########################
    def setup_secrets(self):
        '''
        Configures SSH and GPG secrets in the pipeline namespace.

        Parameters:

        - None (uses instance configuration)

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If secret creation fails
        '''
        namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')
        logging.info(f"Environment: {self.environment}")
        logging.info(f"Team: {self.team}")
        logging.info(f"Namespace: {namespace}")

        try:
            # Get the SSH keys from the config
            ssh_keys = self.config.get('team_static_encrypted').get('SSH_DEPLOY_KEYS')  # Returns list of SSH key dicts

            # A team that declares no deploy key has nothing for this command to write, and that is
            # a legal answer rather than a hole to paper over: every field of an entry is the
            # operator's key material, so inventing a stub to have something to write would put a
            # Secret no one owns into the pipeline namespace. Stopping is the whole action here, so
            # it is said plainly and the run still ends without writing, rather than reaching the
            # loop with nothing to iterate and failing with a Python error that names no key and no
            # file. Compare `create-grafana-accounts`, which reports the same shape for
            # `GRAFANA_USERS`.
            if not ssh_keys:
                logging.warning("No layer declares SSH_DEPLOY_KEYS for %s/%s, so no Secret was "
                                "written. A deploy key is the operator's key material and "
                                "kubementat does not invent one to have something to write here: "
                                "declare the key in the team's encrypted file if this team is meant "
                                "to have one.", self.environment, self.team)
                return None

            logging.info("Setting up secrets in namespace %s", namespace)

            # Iterate through each SSH key and create secret
            for key_data in ssh_keys:
                name = key_data['NAME']
                namespace = key_data['TARGET_NAMESPACE']
                secret_name = key_data['TARGET_SECRET_NAME']
                private_key_base64 = key_data['PRIVATE_KEY_BASE64']

                logging.info(f"Configuring SSH key: {name} as secret: {secret_name} in namespace: {namespace}")

                self.kubernetes_utils.create_or_replace_secret(
                namespace=namespace,
                secret_name=secret_name,
                key_type="kubernetes.io/ssh-auth",
                # The ssh key is already base64 encoded so we don't need to encode it again
                raw_data={"ssh-privatekey": private_key_base64}
                )

            logging.info(f"Secrets configured successfully in namespace {namespace} .")
            self._setup_secrets_show_results(namespace)
        except Exception as e:
            logging.error(f"Failed to configure secrets: {str(e)}")
            raise
        return None

    def _setup_secrets_show_results(self, pipeline_ns):
        """Display the results of the secrets configured"""

        logging.info(f"=== Secrets within namespace {pipeline_ns} ===")
        secrets = self.kubernetes_utils.list_secrets(namespace=pipeline_ns)
        for secret in secrets.items:
            logging.info(secret.metadata.name)

        return None

    # docker registry access automation
    ###########################
    def setup_docker_registry_access(self):
        '''
        Configures Docker registry access for specified namespaces.

        Parameters:

        - None (uses instance configuration)

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If Docker secret creation fails
        '''
        pipeline_namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')
        app_deployment_namespace = self.config.get('team_static').get('APP_DEPLOYMENT_NAMESPACE')
        helm_deployer_service_account_name = self.config.get('team_static').get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')
        docker_registry_credentials = self.config.get('team_static_encrypted').get('DOCKER_REGISTRY_CREDENTIALS')
        # Absence is a state and not an error. `tekton_utils` fixed exactly this for the cleaner's pull
        # secret and said why: the bash it replaced read the block unconditionally, so a team with no
        # credentials block got a Secret full of `null` and a pod the registry rejected without naming
        # any of the four nulls. A tree from `config init` is that team -- the block is something a
        # human adds when there is a private registry to reach -- so reaching the loop before this line
        # was reaching `None`, and the crash said `'NoneType' object is not iterable`, which names
        # neither the key nor the file nor the verb that would have written it.

        logging.info(f"Environment: {self.environment}")
        logging.info(f"Team: {self.team}")
        logging.info(f"Pipeline Namespace: {pipeline_namespace}")
        logging.info(f"App Deployment Namespace: {app_deployment_namespace}")
        logging.info(f"Helm Deployer Service Account Name: {helm_deployer_service_account_name}")

        # Start constructing json array for service account patch calls
        image_pull_secrets = []
        has_credentials = bool(docker_registry_credentials)

        # Process each namespace
        for namespace in [app_deployment_namespace, pipeline_namespace]:
            logging.info(f"Configuring namespace: {namespace} ...")

            if not has_credentials:
                # No block answers this team, so there is nothing to pull with and nothing to create.
                # Silently skipping would be the worse half of the old bug: the namespace would look
                # configured in the log and be empty on the cluster.
                logging.info("No DOCKER_REGISTRY_CREDENTIALS answers this team, so no pull secret is "
                             f"created for {namespace}. This is correct until a private registry is in "
                             f"use; {self.team} static.encrypted.json is the file that would name one.")
                continue

            logging.info("Configuring DOCKER_REGISTRY_CREDENTIALS ...")
            for credential in docker_registry_credentials:
                name = credential["NAME"]
                auth_url = credential["DOCKER_REGISTRY_AUTH_URL"]
                email = credential["DOCKER_REGISTRY_EMAIL"]
                password = credential["DOCKER_REGISTRY_PASSWORD"]
                username = credential["DOCKER_REGISTRY_USERNAME"]

                self.kubernetes_utils.create_or_replace_docker_secret(namespace, name, auth_url, username, password, email)

                # Add to image pull secrets only once
                if namespace == app_deployment_namespace and {"name": name} not in image_pull_secrets:
                    image_pull_secrets.append({"name": name})

        # Patch the service accounts with image pull secrets. Only when there is something to patch:
        # a `imagePullSecrets: []` patch is accepted by the API and changes no state, which leaves a log
        # line saying it patched a service account that says nothing about the fact that nothing was
        # pulled with. The empty case was already logged while the namespaces were being walked.
        if has_credentials:
            # Patch the service accounts with image pull secrets
            self.kubernetes_utils.patch_service_account_with_pull_secrets(pipeline_namespace, helm_deployer_service_account_name, image_pull_secrets)
            # TODO: REFACTOR: Make the app deployment namespace service account name configurable via platform_config
            self.kubernetes_utils.patch_service_account_with_pull_secrets(app_deployment_namespace, 'default', image_pull_secrets)
        else:
            logging.info("No service account was patched: there were no registry credentials to name.")

        logging.info("Finished configuring docker registry access.")
        self._setup_docker_registry_access_show_results(pipeline_namespace, app_deployment_namespace, helm_deployer_service_account_name)

    def _setup_docker_registry_access_show_results(self, pipeline_ns, app_ns, helm_deployer_sa):
        """
        Display the results of the script execution.
        Args:
            pipeline_ns (str): The namespace of the pipeline
            app_ns (str): The namespace of the app
            helm_deployer_sa (str): The name of the helm deployer service account
        """
        logging.info("########################")
        logging.info("Results:")
        logging.info("########################")
        logging.info("")
        logging.info("=== Service Accounts ===")
        logging.info(f"Name: {helm_deployer_sa}")
        logging.info(f"Namespace: {pipeline_ns}")
        sa = self.kubernetes_utils.get_service_account(
            namespace=pipeline_ns,
            name=helm_deployer_sa
        )
        logging.info(sa)

        logging.info(f"Namespace: {app_ns}")
        logging.info(f"Name: default")
        sa = self.kubernetes_utils.get_service_account(
            namespace=app_ns,
            name="default"
        )
        logging.info(sa)

    ###########################

    ## team accounts

    def create_team_account(self, service_account_name: str, assume_yes: bool = False) -> None:
        """Create a team account that can do anything inside the team's namespaces.

        Parameters:

        - service_account_name: str - Name the account is created under.
        - assume_yes: bool = False - Skip the confirmation (`kmt users create-team-account --yes`).
        """
        self._ensure_team_account(
            service_account_name,
            role_name=user_management.NAMESPACE_ADMIN_ROLE,
            rules=user_management.ADMIN_RULES,
            assume_yes=assume_yes,
            action=f"create the team account '{service_account_name}' on this cluster",
        )

    def create_team_account_read_only(self, service_account_name: str, assume_yes: bool = False) -> None:
        """Create a team account that can read everything and change nothing.

        The only difference from `create_team_account` is which Role gets bound, so there is
        one implementation. The bash had two 96-line scripts that differed by the two words
        `namespace-admin` and `namespace-readonly`, which is how one of them came to be fixed
        and the other not.

        Parameters:

        - service_account_name: str - Name the account is created under.
        - assume_yes: bool = False - Skip the confirmation (`kmt users create-team-account-read-only --yes`).
        """
        self._ensure_team_account(
            service_account_name,
            role_name=user_management.NAMESPACE_READ_ONLY_ROLE,
            rules=user_management.READ_ONLY_RULES,
            assume_yes=assume_yes,
            action=f"create the read-only team account '{service_account_name}' on this cluster",
        )

    def _ensure_team_account(self, service_account_name, role_name, rules, assume_yes, action) -> None:
        """Write a team account and the Roles that say what it may do.

        The order is the bash's, because the order is what makes the result readable: name
        the cluster and check the verbs, name the namespaces, create the account and its
        token, create the Roles, bind them, show what is now there, hand over the kubeconfig.

        Raises:
        - click.ClickException: Refused at the confirmation, or a verb the cluster will not
          grant, or a namespace the team's configuration did not name.
        """
        user_management.check_cluster_and_access(self.kubernetes_utils, assume_yes, action)
        pipeline_ns, app_ns = self._team_namespaces(self.team)
        tunnels = self._team_account_tunnel_roles()
        present = set(self.kubernetes_utils.list_namespaces())

        logging.info("#########################")
        logging.info(f"Environment: {self.environment} | Team: {self.team}")
        logging.info(f"namespace: {app_ns}")
        logging.info(f"pipeline namespace: {pipeline_ns}")
        logging.info("#########################")

        for namespace in (pipeline_ns, app_ns):
            self.kubernetes_utils.create_namespace(namespace)

        logging.info(f"Configuring serviceaccount: {service_account_name} in namespace: {app_ns}")
        self.kubernetes_utils.create_or_replace_service_account(app_ns, service_account_name)
        secret_name = user_management.service_account_secret_name(service_account_name)
        self.kubernetes_utils.create_or_replace_service_account_token_secret(
            app_ns, secret_name, service_account_name)

        logging.info(f"Configuring role: {role_name} in namespaces: {app_ns}, {pipeline_ns}")
        bindings = []
        for namespace in (app_ns, pipeline_ns):
            self.kubernetes_utils.create_or_replace_role(
                namespace, role_name, user_management.role_body(role_name, rules))
            bindings.append((role_name, namespace))

        # A tunneling Role is only worth writing where the component it tunnels is installed.
        # The bash applied it unconditionally, and a Role cannot be created in a namespace
        # that is not there, so a cluster where that namespace was never made -- `extra`, or a
        # cluster that skipped `kmt install` -- could not have a team account at all.
        # That, and not the confirmation, is why `cap:create-team-account` has been red.
        # `polaris` is installed by `group=standard` since 2026-11-01, so on a normal lane run this
        # branch is the exception and not the rule; the guard stays, because the namespace is still
        # configuration and a renamed one may well not exist.
        for component, role in user_management.TUNNEL_COMPONENTS:
            namespace = tunnels[component]
            if namespace not in present:
                logging.info(f"{component} is not installed here (namespace '{namespace}' does not "
                             f"exist); not creating its {role} role. A team account on this cluster "
                             f"cannot tunnel into {component}.")
                continue
            self.kubernetes_utils.create_or_replace_role(
                namespace, role, user_management.role_body(role, user_management.TUNNELING_RULES))
            bindings.append((role, namespace))

        logging.info(f"Binding roles to serviceaccount: {service_account_name}")
        for role, namespace in bindings:
            binding_name = user_management.role_binding_name(role, service_account_name)
            self.kubernetes_utils.create_or_replace_role_binding(
                namespace, binding_name,
                user_management.role_binding_body(binding_name, role, service_account_name, app_ns))
            # The read after the write: creating a RoleBinding with the client reports nothing
            # about whether it took, and an account with no binding is an account that is
            # silently allowed to do nothing.
            self.kubernetes_utils.get_role_binding(namespace, binding_name)

        self._print_team_account_state(
            [app_ns, pipeline_ns] + [n for n in tunnels.values() if n in present])
        self._print_team_account_kubeconfig(service_account_name, app_ns)

    def delete_team_account(self, service_account_name: str, assume_yes: bool = False) -> None:
        """Remove a team account and every RoleBinding that let it act.

        Both Roles are unbound whether or not this account was created by the admin or the
        read-only command: the flow does not record which one it used, and guessing would
        leave a live binding behind for a person who is no longer a person.

        Parameters:

        - service_account_name: str - Account to delete.
        - assume_yes: bool = False - Skip the confirmation (`kmt users delete-team-account --yes`).

        Raises:
        - click.ClickException: Refused at the confirmation, or a verb the cluster will not grant.
        """
        user_management.check_cluster_and_access(
            self.kubernetes_utils, assume_yes,
            f"delete the team account '{service_account_name}' and everything it can do on this cluster")
        pipeline_ns, app_ns = self._team_namespaces(self.team)
        tunnels = self._team_account_tunnel_roles()
        present = set(self.kubernetes_utils.list_namespaces())

        logging.info(f"Deleting role bindings for serviceaccount: {service_account_name}")
        for role in (user_management.NAMESPACE_ADMIN_ROLE, user_management.NAMESPACE_READ_ONLY_ROLE):
            for namespace in (app_ns, pipeline_ns):
                self.kubernetes_utils.delete_role_binding(
                    namespace, user_management.role_binding_name(role, service_account_name))
        # The same table the create walks, so the name deleted is the name written. `tunnels`
        # is keyed by component, and a binding's name is built from the Role that tunnels it:
        # iterating the dictionary hands over `tekton` where the create wrote
        # `tekton-tunneling`, the delete then asks for a binding that never existed, and the
        # 404 that follows is one this primitive is deliberately quiet about. The account goes
        # and the tunnel stays open.
        for component, role in user_management.TUNNEL_COMPONENTS:
            namespace = tunnels[component]
            if namespace not in present:
                continue
            self.kubernetes_utils.delete_role_binding(
                namespace, user_management.role_binding_name(role, service_account_name))

        logging.info(f"Deleting serviceaccount: {service_account_name} from namespace: {app_ns}")
        self.kubernetes_utils.delete_service_account(app_ns, service_account_name)
        # The token outlives the account otherwise: the Secret holding it is what anyone with
        # the kubeconfig authenticates with, and deleting the service account does not delete
        # a Secret this flow created by hand. The bash deleted the account and left the
        # credential, so `kmt users delete-team-account` reported success while the kubeconfig
        # kept working.
        self.kubernetes_utils.delete_secret(
            app_ns, user_management.service_account_secret_name(service_account_name))

        self._print_team_account_state(
            [app_ns, pipeline_ns] + [n for n in tunnels.values() if n in present])

    def _team_account_tunnel_roles(self) -> dict:
        """Which Role tunnels which component, and in which namespace.

        The namespaces come from `Config`, not from the three `*_NAMESPACE="..."` lines the
        bash carried, so renaming a component's namespace moves the tunneling role with it.
        """
        return {
            "tekton": self.config.component_namespace("tekton"),
            "grafana": self.config.component_namespace("grafana"),
            "polaris": self.config.component_namespace("polaris"),
        }

    def _print_team_account_state(self, namespaces: list) -> None:
        """Show what is in the namespaces the account was made to use.

        The bash read them with `kubectl get`, three calls per namespace, and printed the
        tables. Here the reads go through the client and the point of them is unchanged: a
        person at the terminal sees what the command just made true, rather than what it
        claims it made true. Namespaces that are not installed are not read and are named.
        """
        logging.info("Current state of the namespaces this account touches:")
        for namespace in namespaces:
            accounts = self.kubernetes_utils.list_service_accounts(namespace)
            logging.info(f"namespace: {namespace}")
            for account in accounts:
                logging.info(f"  serviceaccount: {account.metadata.name}")
            for role in self.kubernetes_utils.list_roles(namespace):
                logging.info(f"  role: {role.metadata.name}")
            for binding in self.kubernetes_utils.list_role_bindings(namespace):
                logging.info(f"  rolebinding: {binding.metadata.name}")
        logging.info("")

    def _print_team_account_kubeconfig(self, service_account_name: str, namespace: str) -> None:
        """Print the kubeconfig a human pastes into `~/.kube/config`.

        This text is the product of the command, so it is built as data and dumped, not
        interpolated. The token is read back from the Secret the controller fills in, and the
        cluster address and CA come from the kubeconfig this command is running under, because
        that is the only place they are stated correctly.
        """
        token = user_management.read_service_account_token(
            self.kubernetes_utils, namespace, service_account_name)
        server, authority = user_management.local_cluster_settings(self.kubernetes_utils)
        text = user_management.kubeconfig_for_service_account(
            self.ctx, service_account_name, namespace, token, server, authority)
        logging.info("###########################")
        logging.info("Please put the settings below in your ~/.kube/config file:")
        logging.info("###########################")
        logging.info(text.rstrip("\n"))
        logging.info("##########")


    ###########################

    ## uninstall pipelines
    def uninstall_pipelines(self):
        '''
        Deletes the pipeline namespace to uninstall pipelines.

        Parameters:

        - None (uses instance configuration)

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If namespace deletion fails
        '''
        logging.info(f"Starting uninstall_pipelines execution with environment: {self.environment} and team: {self.team}")
        # The namespace this deletes must be one a file named. `team_static` would answer
        # PIPELINE_NAMESPACE from the team's name, which is where a pipeline is deployed and
        # not a licence to remove whatever that name happens to be on this cluster.
        pipeline_ns = self.config.declared('team_static').get('PIPELINE_NAMESPACE')
        if not pipeline_ns:
            raise click.ClickException(
                f"{self.config.team_static_config_path} does not declare PIPELINE_NAMESPACE, so no"
                f" namespace can be deleted for team '{self.team}'. The pipelines namespace follows"
                f" from a team's name when kubementat deploys into it, and from nothing at all when"
                f" kubementat is asked to remove one — write the namespace the pipelines are in, or"
                f" point at the team you mean."
            )
        logging.info(f"Pipeline Namespace: {pipeline_ns}")

        self.kubernetes_utils.delete_namespace(pipeline_ns)
        logging.info(f"Successfully uninstalled pipelines for environment: {self.environment} , team: {self.team}")

    ###########################

    ## uninstall kubementat
    #
    # This is the highest-risk command in the product, so the facts it acts on are written
    # down once, in one place:
    #
    # * namespaces come from `Config.component_namespace` over the components that
    #   `kubementat.constants` declares, plus every team the environment configures;
    # * what is actually installed comes from the cluster (`helm list`, `kubectl get crd`), so
    #   a component or a CRD nobody remembered is still swept;
    # * every program kubementat starts goes through `kubementat.shell.run` as an argv list,
    #   and every cluster mutation the generated client can express goes through
    #   `KubernetesUtils`.

    HELM_DEPLOYER_CLUSTER_ROLE = HELM_DEPLOYER_CLUSTER_ROLE_NAME

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
    # `_delete_crds_traefik_helm_leaves_behind` -- and what is deleted are only the definitions no
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

    # What the install preflight proves this service account may do, spelled as the API spells it.
    #
    # The names are plural on purpose. `kubectl auth can-i create deployment` takes the singular and
    # maps it through the discovery table itself; a `SelfSubjectAccessReview` is the request that
    # mapping produces, so it has to carry the resource name and not the kind. Handing it
    # `deployment` instead of `deployments` answers "no" on every cluster and fails an install that
    # would otherwise work, which is the only way this conversion could have gone quietly wrong.
    CLUSTER_PERMISSION_CHECK_RESOURCES = (
        "namespaces", "deployments", "clusterroles", "roles", "daemonsets", "replicasets")

    def uninstall_kubementat(self, assume_yes: bool = False) -> None:
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
        plan = self._read_uninstall_plan()
        self._print_uninstall_plan(plan)
        if not assume_yes:
            self._confirm_uninstall()

        logging.info("#########################")
        logging.info(f"Uninstalling kubementat components and team spaces from environment: {self.environment}")
        logging.info("#########################")

        # Cluster-scoped first: it is the only object the namespace sweep below cannot reach.
        self.kubernetes_utils.delete_cluster_role(self.HELM_DEPLOYER_CLUSTER_ROLE)

        # ... and the rest of the class it is one object from. The release manifests `TektonUtils`
        # applies put RBAC on the cluster that belongs to no release secret, no namespace and no
        # name written down in this checkout, so the line above leaves every one of them and every
        # namespace delete below reaches none of it. Which ones are kubementat's is answered by the
        # cluster, before any namespace goes -- `_delete_cluster_scoped_rbac_kubementat_owns` says
        # how, and says why the order is what makes the answer readable at all.
        residue = self._delete_cluster_scoped_rbac_kubementat_owns(plan)

        # Neither is a component any more, so both are swept before the component set rather than
        # among it: their namespaces are not in the plan's namespace list, and their cluster-scoped
        # objects are not in the groups the final sweep covers.
        residue += self.uninstall_linkerd(plan)
        residue += self.uninstall_nginx_ingress(plan)
        residue += self._uninstall_helm_releases(plan["namespaces"])
        residue += self._delete_claims_in(plan["namespaces"])

        # After the release, before the namespaces: helm applied the chart's `crds/` on install and
        # removes nothing on uninstall, so the definitions outlive the release the line above just
        # took. They are cluster-scoped, and an IngressRoute still pointing at them is how a
        # namespace being torn down gets stuck in `Terminating`. What the line above has just left
        # on the cluster decides whether they go, because they are shared with whatever proxy is
        # still running -- the node's own, on the cluster this suite runs on.
        residue += self._delete_crds_traefik_helm_leaves_behind()

        # Alloy's dependency chart is the second copy of the same trap, and it was found by the lane and
        # not by a review: `monitoring.grafana.com` is cluster-scoped, named by no release and no
        # namespace, so everything above reports clean over it. It runs in the same place for the same
        # reason -- the definitions are read before the namespaces they may be blocking go away, and a
        # foreign Alloy that is still collecting decides whether they go.
        residue += self._delete_crds_alloy_helm_leaves_behind()

        for namespace in plan["namespaces"]:
            self.kubernetes_utils.delete_namespace(namespace)

        # Namespace deletion is asynchronous and was measured at 42 s to drain on a 4 vCPU guest,
        # while this command used to report its own result about 4 s after firing the deletes. That
        # read as "the cluster still has what I just deleted" and, worse, let a run claim a clean
        # sweep over namespaces the cluster had not finished removing. Wait for it, bounded, and put
        # any survivor in the residue list with the phase the cluster reports.
        survivors = self.kubernetes_utils.wait_namespaces_gone(plan["namespaces"])
        if survivors:
            phases = self.kubernetes_utils.namespace_phases(survivors)
            residue += [
                "namespace '%s' is still reported by the cluster%s -- deletion was accepted, but it is"
                " not finished, so nothing here may claim the namespace is gone"
                % (name, f" (phase {phases[name]})" if name in phases else "")
                for name in survivors
            ]

        residue += self._delete_crds_kubementat_owns()

        self._print_uninstall_result(plan, residue)
        if residue:
            raise errors.UninstallIncompleteError(
                "uninstall left kubementat residue behind, so the cluster is not clean:\n  "
                + "\n  ".join(residue)
            )

    # The keys a team directory has to name for uninstall to be able to sweep it, in the order
    # they are read. Two keys and no more, because those are the two namespaces a team's work
    # lands in: the pipelines and the applications.
    TEAM_NAMESPACE_KEYS = ("PIPELINE_NAMESPACE", "APP_DEPLOYMENT_NAMESPACE")

    def namespaces_kubementat_owns(self) -> list:
        '''
        Every namespace kubementat would create or has created, in deletion order.

        This is the same answer `namespace_sweep` gives, reduced to its namespace list, and it
        exists so a caller that only wants the names does not have to know what the report looks
        like. The failure policy -- what happens to a directory that is not a team -- is
        documented there.

        Returns:
            list[str]: Namespace names, deduplicated, teams before components.

        Raises:
        - click.ClickException: When the environment has no configuration directory at all.
        '''
        return self.namespace_sweep()["namespaces"]

    def namespace_sweep(self) -> dict:
        '''
        The namespaces uninstall deletes, and what every team directory had to say about itself.

        Two sources, both of them configuration:

        * the pipeline and application namespace of every team the environment configures.
          `dev1` and `smoke` were two names somebody wrote down, not the set of teams, and a
          third team's namespaces were left on the cluster by a script that carried its own
          TODO about exactly that.
        * one namespace per component `kubementat.constants` declares, each read through
          `Config.component_namespace` so a renamed namespace is honoured. `uninstall_kubementat.sh`
          read fourteen of them and missed `cert-manager`, whose release then outlived it, and
          `nginx-ingress`, which is not a component at all any more and is therefore swept by
          `uninstall_nginx_ingress` rather than by anything reading this set.

        The team half is where the failure policy lives, and it is deliberately not the strict
        one. Discovery is a directory listing and a directory listing is allowed to contain
        things that are not teams -- a migration or a hand-made probe can leave a directory
        behind, and the retired `initialize` command used to write `kubementat_components` itself.
        Stopping the teardown
        on the first of those was the bug a real lane measured: one directory under a
        config-isolation probe aborted `kmt uninstall` twice and *nothing* was deleted, which is
        the worst possible outcome for the highest-risk command in the product. So each directory
        is swept for the namespaces it declares and for nothing else, and the ones it does not
        declare are named and warned about rather than guessed. A directory that declares nothing
        contributes nothing and is said to have contributed nothing.

        Since the rule over a team's name that sentence needs **declares** to mean what it says. A
        team's namespaces are
        answered by a rule even when no file wrote them down, and that answer is about placement: it
        says where `pandas` team's pipelines live. It is not permission to delete, because a namespace
        of that name may already exist on this cluster, made by something that was never kubementat.
        So the sweep deletes `entry["deletable"]` — the names a file actually set — and reads
        `entry["namespaces"]` only to say what it left alone and why.

        This is not a licence to be blind: the warning is the product's answer, it is printed
        every time, and the final summary repeats it so a `DONE` cannot be read as "everything was
        swept" when part of the tree was skipped.

        Returns:
            dict: {"namespaces": the deduplicated list, teams before components, and
                   "teams": {team: {"path", "namespaces", "missing"}} for every directory the
                   environment configured, including the ones that are not teams. "missing" holds
                   the namespace keys that file does not set, so an empty "namespaces" together
                   with two missing keys is the "directory that is not a team" case and one
                   missing key is a half-written team.

        Raises:
        - click.ClickException: When there is no configuration directory for the environment, or
          no environment `static.json` to read the namespaces out of. A cluster is never deleted
          on the strength of a name that was never configured.
        '''
        teams = {}
        namespaces = []
        for team in self.config.teams():
            try:
                entry = self._declared_team_namespaces(team)
            except ValueError as error:
                # A directory that cannot be read is decided the same way as one that says
                # nothing, and said out loud the same way -- see _unreadable_team_configuration.
                entry = self._unreadable_team_configuration(
                    error, team, self.config.platform_config_dir / self.environment / team / "static.json")
            teams[team] = entry
            namespaces += entry["deletable"]
            self._warn_about_team_directory(entry)
        for component in sorted(constants.DEFAULT_NAMESPACES):
            namespaces.append(self.config.component_namespace(component))
        # `dict.fromkeys` rather than a set: two teams that share a namespace still have to be
        # swept once, and the order is what the log has to be able to show.
        return {"namespaces": list(dict.fromkeys(namespaces)), "teams": teams}

    def _declared_team_namespaces(self, team: str) -> dict:
        '''
        What one team directory actually says about the namespaces it owns.

        Reading the file is the whole job here; deciding what to do about a short answer is
        `namespace_sweep`'s, so that the strict readers -- the team-account commands, which
        cannot bind a Role in a namespace nobody named -- can keep demanding the full pair while
        the sweep does not.

        The missing-environment case had the same asymmetry as before: `kmt uninstall` named the
        file and stopped, and the team-account commands reached into `Config` and came back with a
        `FileNotFoundError` traceback -- one rule, two answers, which is the pattern this
        migration keeps having to remove. So the check is here, in the one function that reads a
        team's namespaces for both uninstall and team accounts.

        The `Config` for one team is built from the root this object was given rather than from the
        pair alone. A sweep is one command, and its root was decided once, upstream: re-deriving it
        per team is how a sweep over `platform_config/` in one tree reads a `static.json` out of
        another and calls both `namespaces`.
        '''
        if not self.config.env_static_config_path.is_file():
            raise click.ClickException(
                f"No environment configuration at {self.config.env_static_config_path}. The"
                f" namespaces team '{team}' is addressed through are read from that file, so"
                f" nothing can be created or deleted without guessing. Run kmt config init"
                f" <dir> --environment {self.environment}, or create the file for the environment."
            )
        team_config = Config(self.environment, team,
                             kubementat_main_dir=self.config.kubementat_main_dir,
                             platform_config_dir=self.config.platform_config_dir)
        # Two reads of one file, because two questions are being asked of it. `answered` is where
        # things go: the rule over a team's name makes `pandas` team's pipelines namespace
        # `pandas-pipelines` with no
        # file saying so, which is what installing and binding an account need. `deletable` is what may
        # be removed, and it is the file and nothing else: the rule yields an address and not a deed,
        # and this cluster may already hold a namespace called `pandas` that nobody here created.
        answered = team_config.get('team_static')
        files = team_config.declared('team_static')
        namespaces, missing, deletable = [], [], []
        for key in self.TEAM_NAMESPACE_KEYS:
            value = answered.get(key)
            if isinstance(value, str) and value.strip():
                namespaces.append(value.strip())
            else:
                missing.append(key)
            named = files.get(key)
            if isinstance(named, str) and named.strip():
                deletable.append(named.strip())
        return {
            "team": team,
            "path": str(team_config.team_static_config_path),
            "namespaces": namespaces,
            "missing": missing,
            "deletable": deletable,
        }

    def _unreadable_team_configuration(self, error: Exception, team: str, path) -> dict:
        '''
        Treat a `static.json` that will not parse as one that declares nothing.

        The decision is the one `namespace_sweep` already makes, for the same reason: a file a
        directory listing found and `json` cannot read is the second way a single foreign
        directory can stop a sweep that has a whole cluster in front of it, and deleting nothing is
        the worse answer. What is not the same is the sentence -- the file could not be read is
        said in those words, so the operator is not sent looking for a key that is there. One
        warning per directory, whichever way it fails, is `_warn_about_team_directory`'s.

        The strict reader still refuses it, because a team the operator named by hand is a team
        whose config has to be right, and there "I could not read it" is an error and not a skip.

        Raises:
        - Exception: Anything that is not a bad document -- a missing file or a directory is not
          this case, and papering over it would hide the real failure.
        '''
        if not isinstance(error, ValueError):
            raise error
        return {"team": team, "path": str(path), "namespaces": [], "deletable": [],
                "missing": list(self.TEAM_NAMESPACE_KEYS), "unreadable": True}

    def _warn_about_team_directory(self, entry: dict) -> None:
        '''
        Say what a team directory that is not a team did, and what uninstall is doing about it.

        One warning per directory, and a sentence long enough to act on: the path, the key that is
        not set (or the fact that the file would not parse), the fact that the rest of the sweep is
        unaffected, and the two endings -- fix it, or delete it. A warning that only says "skipped"
        is a warning nobody can do anything about, and a directory that produces two warnings
        teaches the reader to stop counting them.
        '''
        missing = entry["missing"]
        # The rule over a team's name added a third shape a team directory can fail this sweep in. A
        # key can be set, unset
        # but answered by the rule over the team's name, or not answered at all, and the middle one is
        # the one that used to be invisible: the namespace is real, it is where the team's pipelines
        # live, and it is still not something kubementat may delete. Both halves are warned about here,
        # so a log line that says "swept" always means "a file named this".
        answered_but_not_named = [name for name in entry["namespaces"]
                                  if name not in entry["deletable"]]
        if not missing and not answered_but_not_named:
            return
        verb = "is" if len(missing) == 1 else "are"
        if entry.get("unreadable"):
            reason = "is not readable as JSON"
            fix = "fix the file"
        elif missing:
            reason = f"{' and '.join(missing)} {verb} not set there"
            fix = "add the key that is missing"
        else:
            names = ", ".join(answered_but_not_named)
            said = "is" if len(answered_but_not_named) == 1 else "are"
            reason = f"{names} {said} answered by the team's name and not set there"
            fix = "write the namespace into the file"
        if entry["deletable"]:
            names = ", ".join(entry["deletable"])
            swept = f"only {names} {verb} swept"
        else:
            swept = "nothing under that directory is swept"
        logging.warning(
            f"{entry['path']} is a team directory uninstall cannot sweep in full: {reason}, so"
            f" {swept} and the rest of the sweep continues. If '{entry['team']}' is a team, {fix};"
            f" if it is not a team, remove the directory."
        )

    def _team_namespaces(self, team: str) -> list:
        '''
        The two namespaces `team`'s own configuration declares, both of them required.

        The team-account commands take this answer, and for them a missing namespace is not a
        foreign directory being skipped -- it is a Role that would be bound in nowhere, or an
        account that would be created in the wrong place. So this is the strict reader, and the
        sweep below is the tolerant one. What they share is the file both read and the exception
        both raise, so the two cannot disagree about what `static.json` says.

        Raises:
        - click.ClickException: When the environment config is missing, when `team`'s
          configuration names fewer than both namespaces, or when the file it names cannot be read
          as JSON. The last of these is a skip for the sweep and a refusal here, and the difference
          is which namespaces the operator took responsibility for naming.
        '''
        try:
            entry = self._declared_team_namespaces(team)
        except ValueError as error:
            # A file that will not parse is not a file that says nothing: a command asked to edit
            # this team's namespaces cannot answer that question, and letting a `JSONDecodeError`
            # through is how a config problem arrives as a traceback of the tool's own stack.
            raise click.ClickException(
                f"{self.config.platform_config_dir / self.environment / team / 'static.json'} is"
                f" not readable as JSON, so team '{team}' names no namespaces and nothing can be"
                f" created or bound for it: {error}"
            )
        if entry["missing"]:
            raise click.ClickException(
                f"{entry['missing'][0]} is not set in {entry['path']}. This command reads the"
                f" namespaces it binds a team account in from that file, so team '{team}' has to"
                f" name both - add the key, or pass the team you actually mean."
            )
        return entry["namespaces"]

    def _read_uninstall_plan(self) -> dict:
        '''
        What is on the cluster, and which of it kubementat is responsible for.

        Reads only. It runs before the confirmation so a human at the terminal sees what they
        are agreeing to, and the same reading drives the deletion.

        Raises:
        - click.ClickException: When the environment has no `static.json` to read the namespaces
          from. The script this replaced said so and exited 1; deleting a cluster on the strength
          of an environment name nobody configured is not what a typo should be able to do.
        '''
        if not self.config.env_static_config_path.is_file():
            raise click.ClickException(
                f"No environment configuration at {self.config.env_static_config_path}."
                f" Uninstall reads the namespaces it deletes from that file, so it cannot guess"
                f" them. Run kmt config init <dir> --environment {self.environment}, or create"
                f" the file for the environment."
            )
        present = set(self.kubernetes_utils.list_namespaces())
        sweep = self.namespace_sweep()
        declared = sweep["namespaces"]
        # Linkerd and the ingress controller are not components this product installs, so their
        # namespaces are not part of the declared sweep. They are read off the cluster and carried
        # in the plan anyway, so the operator is told before the first delete that a mesh or a
        # controller this checkout no longer ships is coming off, and so each removal step works
        # from one read of the cluster instead of two.
        linkerd = [namespace for namespace in self.LINKERD_NAMESPACES if namespace in present]
        nginx_ingress = [namespace for namespace in self.NGINX_INGRESS_NAMESPACES
                         if namespace in present]
        return {
            "context": self.context_name(),
            "nodes": [node.metadata.name for node in self.kubernetes_utils.get_nodes()],
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

    def context_name(self) -> str:
        '''
        The kube context this command resolves, named once.

        The read this replaces is gone. `kubectl config current-context` answered from the
        local kubeconfig rather than from the command's own resolution, so on a machine whose
        kubeconfig says prod and whose command says dev the uninstall plan printed prod -- and it
        printed `unknown` whenever the read failed, which is a third state between "dev" and
        "prod" that no operator chose.

        The answer is now a property of the `Context`, asked the same way `Context.build` asked it
        before anything ran, so the plan and the resolution report cannot disagree. It is still
        `unknown`-free: a command that got this far has a target, and `require_target` says so when
        it has not.
        '''
        target = self.ctx.require_target("the cluster this uninstall plan is about")
        return str(target.context)

    def _print_uninstall_plan(self, plan: dict) -> None:
        logging.info("###################################")
        logging.info("You are going to uninstall kubementat from the following cluster:")
        logging.info("###################################")
        logging.info(f"Kube context: {plan['context']} (environment {self.environment},"
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
        for line in self._team_sweep_summary(plan.get("teams") or {}):
            logging.info(line)
        logging.info("This cannot be undone. Every resource inside those namespaces goes with them.")
        logging.info("###################################")

    def _confirm_uninstall(self) -> None:
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

    def uninstall_linkerd(self, plan: dict) -> list:
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
        residue = self._delete_crds_in_groups(self.LINKERD_CRD_GROUPS,
                                              absent="No Linkerd CRDs on the cluster.")
        installed = list(plan.get("linkerd") or [])
        if not installed:
            logging.info("Linkerd is not installed on this cluster - nothing to uninstall.")
            return residue

        logging.info(f"Removing Linkerd, which kubementat does not install any more: "
                     f"{', '.join(installed)}")
        for namespace in installed:
            self.kubernetes_utils.delete_namespace(namespace)

        survivors = self.kubernetes_utils.wait_namespaces_gone(installed)
        if survivors:
            phases = self.kubernetes_utils.namespace_phases(survivors)
            residue += [
                "namespace '%s' still carries Linkerd%s -- deletion was accepted, but it is not"
                " finished, so nothing here may claim the mesh is gone"
                % (name, f" (phase {phases[name]})" if name in phases else "")
                for name in survivors
            ]
        return residue

    def uninstall_nginx_ingress(self, plan: dict) -> list:
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
        residue = self._delete_ingress_classes_bound_to(self.NGINX_INGRESS_CONTROLLER_PREFIX)
        installed = list(plan.get("nginx_ingress") or [])
        if not installed:
            logging.info("nginx-ingress is not installed on this cluster - nothing to uninstall.")
            return residue

        logging.info(f"Removing the ingress controller kubementat does not install any more: "
                     f"{', '.join(installed)}")
        residue += self._uninstall_helm_releases(installed)
        for namespace in installed:
            self.kubernetes_utils.delete_namespace(namespace)

        survivors = self.kubernetes_utils.wait_namespaces_gone(installed)
        if survivors:
            phases = self.kubernetes_utils.namespace_phases(survivors)
            residue += [
                "namespace '%s' still carries an ingress controller%s -- deletion was accepted, but"
                " it is not finished, so nothing here may claim it is gone"
                % (name, f" (phase {phases[name]})" if name in phases else "")
                for name in survivors
            ]
        return residue

    def _delete_ingress_classes_bound_to(self, controller_prefix: str) -> list:
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
        try:
            classes = self.kubernetes_utils.list_ingress_classes()
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
            outcome = self.kubernetes_utils.delete_ingress_class(name)
            if outcome:
                continue
            survivors.append(f"ingressclass {name} (the cluster refused the delete"
                             + (f"): {outcome.reason}" if outcome.reason else ")"))
        return survivors

    def _uninstall_helm_releases(self, namespaces: list) -> list:
        '''
        Uninstall every release that lives in a kubementat namespace, and say what survived.

        Read from the cluster (`helm_list_releases_argv`) rather than from a component list, because the
        release that survived the last real uninstall was not on anybody's list. Anything
        inside a namespace kubementat owns is part of this installation, whoever applied it.

        A failing `helm uninstall` is reported and left to the namespace delete that follows:
        aborting in the middle would leave the platform half-deleted, which is a worse failure
        than a warning. It is still counted as residue, so the command cannot call it a success.
        '''
        if not namespaces:
            return []
        # The one release read the product has, read directly and not rebuilt here: which statuses
        # answer "what releases live here" is decided on helm_utils, and a sweep that asked its own
        # narrower question would uninstall less than the sweep's own residue assert claims. The
        # cluster goes on the command line and not in the environment, because a sweep that
        # deletes releases from whichever kubeconfig the operator's shell happens to point at is a
        # sweep that reports success against the wrong cluster.
        listing = shell.run(helm_list_releases_argv(self.kubernetes_utils.target), capture=True)
        rows = json.loads(listing.stdout or "[]")
        owned = set(namespaces)
        survivors = []
        for row in sorted((row for row in rows if row.get("namespace") in owned),
                          key=lambda row: (row.get("namespace", ""), row.get("name", ""))):
            name = row.get("name")
            namespace = row.get("namespace")
            logging.info(f"Uninstalling helm release '{name}' from namespace '{namespace}' ...")
            result = shell.run(["helm", "uninstall", *helm_target_args(self.kubernetes_utils.target),
                                name, "--namespace", namespace], capture=True, check=False)
            if not result.ok:
                logging.warning(f"helm could not uninstall '{name}' from '{namespace}' (exit"
                                f" {result.returncode}); deleting the namespace next takes it with it.")
                survivors.append(f"release {name} in namespace {namespace} (helm exit {result.returncode})")
        return survivors

    def _list_claims_in(self, namespace: str):
        '''The claims the namespace currently reports, as a list that may be empty.

        `None` means the namespace could not be read at all, which is not the same answer as a
        namespace that is empty: a teardown that reads the first as the second reports success on a
        cluster it never actually looked at.

        Said plainly: this is the read `helm_utils._delete_release_claims` used to
        ask with `kubectl get persistentvolumeclaims`, while this method asked it through the
        client. Same question, two answers, one uninstall. There is now one answer, in
        `KubernetesUtils.list_persistent_volume_claims`.
        '''
        try:
            return sorted(item.metadata.name for item
                          in self.kubernetes_utils.list_persistent_volume_claims(namespace))
        except client.exceptions.ApiException as error:
            logging.info(f"The cluster would not answer for the claims in namespace '{namespace}'"
                         f" (status {error.status}).")
            return None

    def _delete_claims_in(self, namespaces: list) -> list:
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
        residue = []
        for namespace in namespaces:
            claims = self._list_claims_in(namespace)
            if claims is None:
                logging.info(f"No claims to read in namespace '{namespace}'.")
                continue
            if not claims:
                continue
            logging.info(f"Deleting {len(claims)} claim(s) in namespace '{namespace}': {', '.join(claims)}")
            refused = {}
            for claim in claims:
                outcome = self.kubernetes_utils.delete_persistent_volume_claim(namespace, claim)
                if outcome:
                    continue
                refused[claim] = outcome.reason
            if not refused:
                continue
            # The delete is not the verdict. Whether the claim survived is a question to ask the
            # namespace, so a refusal is only counted as residue when it left something behind.
            remaining = self._list_claims_in(namespace) or []
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

    def _delete_cluster_scoped_rbac_kubementat_owns(self, plan: dict) -> list:
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
          `Automation._setup_pipelines_setup_role_bindings`). It is kubementat saying so, which is
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

        - plan: dict - What `_read_uninstall_plan` read. Only `plan["namespaces"]` matters here,
          and it is the set the subjects are checked against.

        Returns:
            list[str]: What survived, empty when everything went. A cluster that will not answer
                the read is answered the way `_delete_crds_in_groups` answers it -- warned about,
                not raised through -- because "there is none" and "I could not ask" are not the
                same fact and only one of them is a clean sweep.
        '''
        owned = set(plan["namespaces"])
        bindings = self._read_cluster_scoped_items("clusterrolebinding")
        roles = self._read_cluster_scoped_items("clusterrole")
        # Bound first, then the roles they bind: `roleRef` is the other half of the same fact, and
        # a `ClusterRole` a release installed has to go with the binding that made it live. Only a
        # binding this sweep already owns is read that way, so the rule cannot reach a role through
        # somebody else's binding -- which is the whole reason `cluster-admin` survives a
        # `kubeadm:cluster-admin` ClusterRoleBinding sitting on the same cluster.
        doomed = []
        bound_to = set()
        for item in bindings:
            name = k8s_apply._metadata_name(item)
            if not name:
                continue
            if (self._rbac_is_claimed_by_kubementat(item)
                    or self._subject_lands_in_a_kubementat_namespace(item, owned)):
                doomed.append(("clusterrolebinding", name))
                bound_to.add(((item.get("role_ref") or {}).get("name")) or "")
        for item in roles:
            name = k8s_apply._metadata_name(item)
            if not name:
                continue
            if self._rbac_is_claimed_by_kubementat(item) or name in bound_to:
                doomed.append(("clusterrole", name))
        doomed = {entry for entry in doomed if entry[1]}


        if not doomed:
            logging.info("No cluster-scoped RBAC on this cluster is kubementat's to delete.")
            return []
        logging.info(f"Deleting {len(doomed)} cluster-scoped RBAC object(s) kubementat applied: "
                     f"{', '.join(f'{kind}/{name}' for kind, name in sorted(doomed))}")
        survivors = []
        for kind, name in sorted(doomed):
            outcome = self.kubernetes_utils.delete_cluster_scoped(kind, name)
            if outcome:
                continue
            survivors.append(f"{kind} {name} (the cluster refused the delete"
                             + (f"): {outcome.reason}" if outcome.reason else ")"))
        return survivors

    def _read_cluster_scoped_items(self, kind: str) -> list:
        '''
        The items of one cluster-scoped kind, as plain documents, and an empty list the cluster
        was told nothing from.

        A failed read is not "there is nothing here", and the two must not be logged the same way
        -- `_delete_crds_in_groups` gives the argument, and the warning is repeated here for the
        same reason: a residue assert cannot tell a sweep that found nothing from one that could
        not ask.

        The documents are `to_dict()`-shaped rather than client models so that everything this
        sweep asks of an object -- its name, its `managed-by` label, its `roleRef`, its subjects --
        is asked the same way it was asked of the JSON `kubectl get` printed. Converting who answers
        does not change the rule the sweep applies, and that is the whole point.
        '''
        try:
            if kind == "clusterrolebinding":
                items = self.kubernetes_utils.list_cluster_role_bindings()
            else:
                items = self.kubernetes_utils.list_cluster_roles()
            return [item.to_dict() for item in items]
        except client.exceptions.ApiException as error:
            logging.warning(f"The cluster would not answer for its {kind}s"
                            f" (status {error.status}), so nothing here is claimed as deleted.")
            return []

    @staticmethod
    def _rbac_is_claimed_by_kubementat(item: dict) -> bool:
        '''
        Whether kubementat's own `managed-by` claim is on this object.

        The same label the install path stamps (`automation.py`'s role bindings,
        `tekton_utils._trigger_labels`, `CLEANUP_JOB_LABELS`), read off the object rather than
        matched against a name so a renamed object is still the object it is.
        '''
        labels = ((item.get("metadata") or {}).get("labels")) or {}
        return labels.get(MANAGED_BY_LABEL) == MANAGED_BY_KUBEMENTAT

    @staticmethod
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

    def _delete_crds_kubementat_owns(self) -> list:
        '''
        Delete the CRDs kubementat installed, asking the cluster which ones those are.

        Nineteen names were written in a script. A Tekton release that adds one is then not
        deleted, and the residue assert passes over a cluster that still serves
        `runs.tekton.dev`. Listing them by group means a CRD nobody remembered still goes.

        Returns:
            list[str]: What survived, empty when everything went.
        '''
        return self._delete_crds_in_groups(self.CRD_GROUPS_KUBEMENTAT_OWNS,
                                           absent="No kubementat CRDs on the cluster.")

    def _delete_crds_traefik_helm_leaves_behind(self) -> list:
        '''
        Delete the CustomResourceDefinitions a Traefik release leaves behind when it goes.

        `helm install` applies the chart's `crds/` directory before it runs the release, and
        `helm uninstall` removes none of it. So the release that `kmt uninstall` takes by the name
        the cluster reports is half of an install: the definitions, and every IngressRoute,
        Middleware or TLSOption written against them, stay on the cluster under no owner at all.
        They are cluster-scoped, so no namespace delete reaches them either, and nothing else in
        kubementat names them once the release is gone.

        The group is the rule and the cluster supplies the names, which is the shape of
        `_delete_ingress_classes_bound_to` rather than of a name list: read what is there, delete
        what belongs to one of `TRAEFIK_CRD_GROUPS`, and let a cluster that cannot answer be a
        cluster with nothing to delete. It is deliberately *not* a sweep of the IngressClass: the
        class kubementat's release created goes with that release, and the nginx sweep's controller
        string stays pointed at `k8s.io/ingress-nginx` so it never finds it.

        The third question is answered by the cluster too, and asked here rather than in a comment:
        whether these definitions are still somebody's. They are shared with whatever proxy is
        running, so the sweep runs only after `_uninstall_helm_releases` has had its turn and only
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
        listing = shell.run(helm_list_releases_argv(self.kubernetes_utils.target), capture=True,
                            check=False)
        if not listing.ok:
            # The same not-fatal read `_delete_crds_in_groups` gives, with a second reason to keep it
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
                               if self._release_runs_traefik_proxy(row))
        if still_running:
            logging.info("Leaving the Traefik CustomResourceDefinitions where they are:"
                         + "".join(f" release '{name}' in namespace '{namespace}' still runs the"
                                   f" chart '{chart}'," for namespace, name, chart in still_running)
                         + " and its controller is served by them.")
            return []
        return self._delete_crds_in_groups(self.TRAEFIK_CRD_GROUPS,
                                           absent="No Traefik CRDs on the cluster.")

    def _delete_crds_alloy_helm_leaves_behind(self) -> list:
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
        listing = shell.run(helm_list_releases_argv(self.kubernetes_utils.target), capture=True,
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
            if chart == self.ALLOY_CHART_NAME or chart.startswith(self.ALLOY_CHART_NAME + "-"):
                still_running.append("'%s' in namespace '%s' (chart %s)"
                                     % (row.get("name"), row.get("namespace"), chart))
        if still_running:
            logging.info("Leaving the Alloy CustomResourceDefinitions where they are:"
                         + "".join(f" release {row} still collects logs through them,"
                                   for row in still_running))
            return []
        return self._delete_crds_in_groups(self.ALLOY_CRD_GROUPS,
                                           absent="No Alloy CRDs on the cluster.")

    @staticmethod
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
        return name == Automation.TRAEFIK_PROXY_CHART_NAME and version[:1].isdigit()

    def _delete_crds_in_groups(self, groups, absent: str = "No CRDs to delete.") -> list:
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
        try:
            definitions = self.kubernetes_utils.list_custom_resource_definitions()
        except client.exceptions.ApiException as error:
            # The read is not fatal, for the reason `_delete_ingress_classes_bound_to` gives: a
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
            outcome = self.kubernetes_utils.delete_cluster_scoped("crd", name)
            if outcome:
                continue
            survivors.append(f"crd {name} (the cluster refused the delete"
                             + (f"): {outcome.reason}" if outcome.reason else ")"))
        return survivors

    def _team_sweep_summary(self, teams: dict) -> list:
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
        if not teams:
            return [f"No team directory is configured under"
                    f" {self.config.platform_config_dir / self.environment}, so no team"
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
                    f" {' and '.join(self.TEAM_NAMESPACE_KEYS)}. {' and '.join(answered_not_named)}"
                    f" {verb} answered by the team's name, which says where its objects live and not"
                    f" that this run may remove them, so nothing under it was deleted"
                    f" ({entry['path']})."
                )
            elif not swept:
                lines.append(
                    f"Team directory '{team}' declared none of"
                    f" {' and '.join(self.TEAM_NAMESPACE_KEYS)}, so it was not swept and nothing"
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

    def _print_uninstall_result(self, plan: dict, residue: list = ()) -> None:
        '''
        The namespace diff, so the log is evidence rather than a claim.

        `kubectl api-resources` is not dumped here. The old script printed the whole discovery
        table as the last thing before `DONE`, and a table nobody can read into a verdict is
        noise that hides the one line that mattered.

        `DONE` is earned by an empty residue list and printed for nothing otherwise: the script
        printed it unconditionally, which is how `DONE` came to mean "the script finished".
        '''
        declared = set(plan["declared"])
        still_present = [namespace for namespace in self.kubernetes_utils.list_namespaces()
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
                f" {self.config.platform_config_dir / self.environment}:"
                f" {', '.join(incomplete)} declared less than"
                f" {' and '.join(self.TEAM_NAMESPACE_KEYS)}"
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
    def open_tunnel_grafana(self, local_port=3001, remote_port=3000, host='0.0.0.0'):
        '''
        Opens a tunnel to the Grafana dashboard.

        Parameters:

        - local_port: int = 3001 - Local port for tunnel
        - remote_port: int = 3000 - Remote port (Grafana default)
        - host: str = '0.0.0.0' - Host IP

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If tunneling fails
        
        '''
        namespace = self.config.component_namespace('grafana')
        selector = self.config.component_service_selector('grafana')
        pod_name = self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
        return self.kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)

    def open_tunnel_tekton_dashboard(self, local_port=9097, remote_port=9097, host='0.0.0.0'):
        '''
        Opens a tunnel to the Tekton dashboard.

        Parameters:

        - local_port: int = 9097 - Local port for tunnel
        - remote_port: int = 9097 - Remote port (Tekton default)
        - host: str = '0.0.0.0' - Host IP

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If tunneling fails

        '''
        # 'tekton', not 'tekton-dashboard': the dashboard release lands in the tekton
        # namespace, so namespace and selector belong to the same component.
        namespace = self.config.component_namespace('tekton')
        selector = self.config.component_service_selector('tekton')
        pod_name = self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
        return self.kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)


    def open_tunnel_openbao(self, local_port=8205, remote_port=8200, host='0.0.0.0'):
        '''
        Opens a tunnel to the OpenBao service.

        Parameters:

        - local_port: int = 8205 - Local port for tunnel
        - remote_port: int = 8200 - Remote port (OpenBao's API port, the same 8200 Vault answered on)
        - host: str = '0.0.0.0' - Host IP

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If tunneling fails
        '''
        # 'openbao', not 'vault': the component that answers on 8200 now is the fork, and both
        # halves of the lookup come from the same configuration file for the same reason every
        # other component's do. Keeping the old key would send the tunnel to a namespace nothing
        # creates any more.
        namespace = self.config.component_namespace('openbao')
        selector = self.config.component_service_selector('openbao')
        pod_name = self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
        return self.kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)
