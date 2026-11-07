"""The pipeline infrastructure `kmt install` and `kmt tekton setup-pipelines` write.

One rule about layers is written in one place, in `_apply_manifest_layer`, because four call sites
apply them and a rule that lives in four places drifts: a layer that is not there is a no-op that
names itself, and a layer that is there is applied in the order product, environment, team, so
the last layer applied answers a Task that two layers both name. Nothing here deletes, because
pruning is uninstall's job and never setup's.

The secrets and the keypair they are built from belong to this module rather than to `secrets`,
because they are written by the same verb: `setup_secrets` is a separate command that writes what
an operator's encrypted file declares, and the deployer's pair is a property of the configuration
directory written by `kmt install`.
"""


import base64
import logging
import os
from pathlib import Path

from kubernetes import client

from kubementat import config_bootstrap, config_keys
from kubementat.infrastructure.kubernetes import KubernetesUtils, apply
# The preflight moved out of `tekton_utils.py` (P4-2), so the call here reaches it through the
# module: the seam is patched by module object, and a name imported past its home is a second
# binding a patch on the module nobody runs would miss.
from kubementat.infrastructure.tekton import preflight


def _base64_encode_file(path) -> str:
    """The file's bytes under exactly one base64 layer -- the unit a Secret is built from.

    One layer, never two: the consumer on the other side decodes exactly once. Which Secret
    field the result belongs to -- `data` or `stringData` -- is the caller's decision about who
    applies the transport encoding, and the two deployer Secrets in `setup_pipelines` are where
    that decision is written down.
    """
    return base64.b64encode(Path(path).read_bytes()).decode("utf-8")

def setup_pipelines(ctx):
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

    config = ctx.config
    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)

    logging.info(f"Starting setup_pipelines execution with environment: {environment} and team: {team}")

    # The deployer's keypair is a property of the configuration directory, not of
    # an encrypted layer: `kmt config init` wrote the four files into the root this run was
    # given, and nothing answers GIT_DEPLOYER_PRIVATE_KEY_BASE64 /
    # GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64 in `static.encrypted.json` any more. Ask for the
    # pair before the first cluster contact of any kind, so a tree without one refuses here
    # by name instead of dying inside the Kubernetes client's serializer with
    # `raw_data={"ssh-privatekey": None}` (census L, Q2 item 8).
    deployer = config_keys.require_deployer_keypair(config.platform_config_dir)

    # setup_pipelines applies the product's Tasks and Pipelines and the team's own two
    # directories one by one, which is the shape a too-old controller answers in: a 404
    # per resource and no sentence saying why. Ask the cluster once, before that starts.
    preflight.preflight_tekton_api_version(kubernetes_utils, config)

    helm_deployer_sa = config.get('team_static').get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')
    pipeline_ns = config.get('team_static').get('PIPELINE_NAMESPACE')
    app_deployment_ns = config.get('team_static').get('APP_DEPLOYMENT_NAMESPACE')

    logging.info(f"Helm Deployer Service Account: {helm_deployer_sa}")
    logging.info(f"Pipeline Namespace: {pipeline_ns}")
    logging.info(f"App Deployment Namespace: {app_deployment_ns}")
    logging.info(f"Team Pipelines directory: {config.tekton_team_pipeline_dir}")
    logging.info(f"Team Tasks directory: {config.tekton_team_task_dir}")
    logging.info(f"Team Pipeline-runs directory: {config.tekton_team_pipeline_run_dir}")
    
    logging.info(f"Creating team pipelines directory if not existing...")
    os.makedirs(config.tekton_team_pipeline_dir,exist_ok=True)

    logging.info(f"Creating team tasks directory if not existing...")
    os.makedirs(config.tekton_team_task_dir,exist_ok=True)

    logging.info(f"Creating team pipeline runs directory if not existing...")
    os.makedirs(config.tekton_team_pipeline_run_dir,exist_ok=True)

    # Create namespaces
    kubernetes_utils.create_namespace(pipeline_ns)
    kubernetes_utils.create_namespace(app_deployment_ns)

    # Setup secrets
    logging.info("Setting up SSH secret: git-deployer-ssh-key")
    kubernetes_utils.create_or_replace_secret(
        namespace=pipeline_ns,
        secret_name="git-deployer-ssh-key",
        key_type="kubernetes.io/ssh-auth",
        # The git-clone-with-ssh-auth task writes this environment variable straight to
        # ~/.ssh/deploy_key, so the Secret's data is the key file's bytes under exactly one
        # base64 layer -- the transport encoding, which kubelet removes on the way in.
        raw_data={"ssh-privatekey": _base64_encode_file(deployer.ssh_private)}
    )
    logging.info("Setting up GPG secret: git-deployer-gpg-key")
    kubernetes_utils.create_or_replace_secret(
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
    if config_bootstrap.uses_git_crypt(config.platform_config_dir):
        logging.info("Setting up git-crypt secret: git-crypt-configuration-key")
        kubernetes_utils.create_or_replace_secret(
            namespace=pipeline_ns,
            secret_name="git-crypt-configuration-key",
            key_type="Opaque",
            string_data={"git-crypt-key": _base64_encode_file(config_bootstrap.require_symmetric_key(
                config.platform_config_dir))}
        )
    else:
        logging.warning(
            f"{config.platform_config_dir} declares no git-crypt filter in its .gitattributes, so "
            "no git-crypt-configuration-key Secret was created. Pipelines clone the configuration and "
            "the unlock step needs that Secret, so if this configuration is encrypted the clone stays "
            "locked and every step that reads it sees ciphertext. Run `kmt config init` against this "
            "directory, or re-run install from one that declares the filter, if that is the case.")

    # Setup service account and roles
    logging.info(f"Setting up service account: {helm_deployer_sa}")
    kubernetes_utils.create_service_account(pipeline_ns, helm_deployer_sa,[
        {
            # add the git-deployer-ssh-key to the service account so it will be able to use the git-clone-with-ssh-auth task
            "name": "git-deployer-ssh-key"
        }
    ])

    logging.info(f"Setting up role bindings for {helm_deployer_sa}")
    _setup_pipelines_setup_role_bindings(ctx, pipeline_ns, app_deployment_ns, helm_deployer_sa)

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
    _apply_manifest_layer(ctx, "product", config.tekton_task_asset_dir, pipeline_ns,
                               kind="Task", group=config.TEKTON_API_GROUP,
                               version=config.TEKTON_API_VERSION)
    _apply_manifest_layer(ctx, "product", config.tekton_pipeline_asset_dir, pipeline_ns,
                               kind="Pipeline", group=config.TEKTON_API_GROUP,
                               version=config.TEKTON_API_VERSION)

    # The environment layer, which is the one the product does not ship. It is between the
    # product and the team
    # because that is the direction of override: an environment tunes what the product ships, a
    # team tunes what its environment says. No key, no flag, no `static.json` entry names it —
    # a directory is not an answer, and neither is a `triggers/` directory nothing reads (see
    # the comment on `Config.tekton_env_task_dir`).
    _apply_manifest_layer(ctx, "environment", config.tekton_env_task_dir, pipeline_ns,
                               kind="Task", group=config.TEKTON_API_GROUP,
                               version=config.TEKTON_API_VERSION)
    _apply_manifest_layer(ctx, "environment", config.tekton_env_pipeline_dir, pipeline_ns,
                               kind="Pipeline", group=config.TEKTON_API_GROUP,
                               version=config.TEKTON_API_VERSION)

    # The team's own two directories, still applied last, which is what makes a team's manifest
    # the answer for a Task the product also ships.
    _apply_manifest_layer(ctx, "team", config.tekton_team_task_dir, pipeline_ns,
                               kind="Task", group=config.TEKTON_API_GROUP,
                               version=config.TEKTON_API_VERSION)
    _apply_manifest_layer(ctx, "team", config.tekton_team_pipeline_dir, pipeline_ns,
                               kind="Pipeline", group=config.TEKTON_API_GROUP,
                               version=config.TEKTON_API_VERSION)

    logging.info("Tekton Pipeline and Task Setup completed successfully!")
    _setup_pipelines_show_results(ctx, pipeline_ns, app_deployment_ns, helm_deployer_sa)


def _setup_pipelines_setup_role_bindings(ctx, pipeline_ns, app_ns, sa_name):
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

    kubernetes_utils = KubernetesUtils(ctx)

    # Define the Role Binding for the app deployment
    # Create within app namespace but point to service account within pipeline namespace
    logging.info(f"Creating role binding for cluster role helm-deployer-cluster-role in namespace {app_ns} pointing to service account {sa_name} in namespace {pipeline_ns}")
    binding = client.V1RoleBinding(
        metadata=client.V1ObjectMeta(name="helm-deployer-role-binding-app-deployment", labels={"managed-by": "kubementat"}),
        subjects=[client.RbacV1Subject(kind="ServiceAccount", name=sa_name, namespace=pipeline_ns)],
        role_ref=client.V1RoleRef(kind="ClusterRole", name="helm-deployer-cluster-role", api_group="rbac.authorization.k8s.io")
    )
    kubernetes_utils.create_or_replace_role_binding(app_ns, "helm-deployer-role-binding-app-deployment", binding)

    # Define Role Binding for pipeline namespace
    logging.info(f"Creating role binding for cluster role helm-deployer-cluster-role in namespace {pipeline_ns} pointing to service account {sa_name} in namespace {pipeline_ns}")
    binding = client.V1RoleBinding(
        metadata=client.V1ObjectMeta(name="helm-deployer-role-binding-pipeline-namespace-access", labels={"managed-by": "kubementat"}),
        subjects=[client.RbacV1Subject(kind="ServiceAccount", name=sa_name, namespace=pipeline_ns)],
        role_ref=client.V1RoleRef(kind="ClusterRole", name="helm-deployer-cluster-role", api_group="rbac.authorization.k8s.io")
    )
    kubernetes_utils.create_or_replace_role_binding(pipeline_ns, "helm-deployer-role-binding-pipeline-namespace-access", binding)


def _apply_manifest_layer(ctx, layer, resource_dir, target_namespace, kind, group, version):
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

    kubernetes_utils = KubernetesUtils(ctx)

    directory = Path(resource_dir)
    if not directory.is_dir():
        logging.info(f"{layer} layer: no {kind} directory at {directory}, so nothing is applied "
                     f"from it (0 documents). A layer that has not been populated yet is not an "
                     f"error; the layer below it is still the answer for every Task and Pipeline "
                     f"in this namespace.")
        return 0

    applied = apply.apply_resources(kubernetes_utils, str(directory),
                                        target_namespace, kind=kind,
                                        group=group, version=version)
    logging.info(f"{layer} layer: applied {applied} {kind} document(s) from {directory} — the "
                 f"last layer applied wins a name that appears in more than one layer.")
    return applied


def _setup_pipelines_show_results(ctx, pipeline_ns, app_ns, helm_deployer_sa):
    """Display the results of the setup including service account, role-bindings,
    Secrets, Tasks and Pipeline Custom Tekton Resources."""

    config = ctx.config
    kubernetes_utils = KubernetesUtils(ctx)


    logging.info("=== Service Account ===")
    logging.info(f"Name: {helm_deployer_sa}")
    logging.info(f"Namespace: {pipeline_ns}")
    sa = kubernetes_utils.get_service_account(
        namespace=pipeline_ns,
        name=helm_deployer_sa
    )
    logging.info(sa)

    logging.info("=== Role Bindings ===")
    logging.info("Pipeline Namespace Role Binding:")
    rb = kubernetes_utils.get_role_binding(
        namespace=pipeline_ns,
        name="helm-deployer-role-binding-pipeline-namespace-access"
    )
    logging.info(rb)

    logging.info("App Namespace Role Binding:")
    rb_app = kubernetes_utils.get_role_binding(
        namespace=app_ns,
        name="helm-deployer-role-binding-app-deployment"
    )
    logging.info(rb_app)

    logging.info("=== Secrets ===")
    secrets = kubernetes_utils.list_secrets(namespace=pipeline_ns)
    for secret in secrets.items:
        logging.info(f"- {secret.metadata.name}")

    logging.info("=== Tasks ===")
    tasks = kubernetes_utils.list_custom_resources(
        namespace=pipeline_ns,
        group=config.TEKTON_API_GROUP,
        version=config.TEKTON_API_VERSION,
        plural_identifier="tasks"
    )

    for task in tasks['items']:
        logging.info(f"- {task['metadata']['name']}")

    logging.info("=== Pipelines ===")
    pipelines = kubernetes_utils.list_custom_resources(
        namespace=pipeline_ns,
        group=config.TEKTON_API_GROUP,
        version=config.TEKTON_API_VERSION,
        plural_identifier="pipelines"
    )
    for pipeline in pipelines['items']:
        logging.info(f"- {pipeline['metadata']['name']}")

    return None
###########################

# secret setup automation
###########################
