"""Docker registry access: the pull Secrets and the service accounts that use them.

The one decision here is about absence. A team with no `DOCKER_REGISTRY_CREDENTIALS` block is not
a team with a broken registry, and the bash this replaced read the block unconditionally and
produced a Secret full of nulls and a pod the registry refused without naming any of them. So the
loop is gated on the block, the empty case is logged as the correct answer it is, and no service
account is patched with an empty `imagePullSecrets` list that changes nothing and reads as if it
did.
"""


import logging

from kubementat.infrastructure.kubernetes import KubernetesUtils


def setup_docker_registry_access(ctx):
    '''
    Configures Docker registry access for specified namespaces.

    Parameters:

    - None (uses instance configuration)

    Returns:

    - None (performs side-effect operations)

    Raises:

    - Exception - If Docker secret creation fails
    '''

    config = ctx.config
    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)

    pipeline_namespace = config.get('team_static').get('PIPELINE_NAMESPACE')
    app_deployment_namespace = config.get('team_static').get('APP_DEPLOYMENT_NAMESPACE')
    helm_deployer_service_account_name = config.get('team_static').get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')
    docker_registry_credentials = config.get('team_static_encrypted').get('DOCKER_REGISTRY_CREDENTIALS')
    # Absence is a state and not an error. `tekton_utils` fixed exactly this for the cleaner's pull
    # secret and said why: the bash it replaced read the block unconditionally, so a team with no
    # credentials block got a Secret full of `null` and a pod the registry rejected without naming
    # any of the four nulls. A tree from `config init` is that team -- the block is something a
    # human adds when there is a private registry to reach -- so reaching the loop before this line
    # was reaching `None`, and the crash said `'NoneType' object is not iterable`, which names
    # neither the key nor the file nor the verb that would have written it.

    logging.info(f"Environment: {environment}")
    logging.info(f"Team: {team}")
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
                         f"use; {team} static.encrypted.json is the file that would name one.")
            continue

        logging.info("Configuring DOCKER_REGISTRY_CREDENTIALS ...")
        for credential in docker_registry_credentials:
            name = credential["NAME"]
            auth_url = credential["DOCKER_REGISTRY_AUTH_URL"]
            email = credential["DOCKER_REGISTRY_EMAIL"]
            password = credential["DOCKER_REGISTRY_PASSWORD"]
            username = credential["DOCKER_REGISTRY_USERNAME"]

            kubernetes_utils.create_or_replace_docker_secret(namespace, name, auth_url, username, password, email)

            # Add to image pull secrets only once
            if namespace == app_deployment_namespace and {"name": name} not in image_pull_secrets:
                image_pull_secrets.append({"name": name})

    # Patch the service accounts with image pull secrets. Only when there is something to patch:
    # a `imagePullSecrets: []` patch is accepted by the API and changes no state, which leaves a log
    # line saying it patched a service account that says nothing about the fact that nothing was
    # pulled with. The empty case was already logged while the namespaces were being walked.
    if has_credentials:
        # Patch the service accounts with image pull secrets
        kubernetes_utils.patch_service_account_with_pull_secrets(pipeline_namespace, helm_deployer_service_account_name, image_pull_secrets)
        # TODO: REFACTOR: Make the app deployment namespace service account name configurable via platform_config
        kubernetes_utils.patch_service_account_with_pull_secrets(app_deployment_namespace, 'default', image_pull_secrets)
    else:
        logging.info("No service account was patched: there were no registry credentials to name.")

    logging.info("Finished configuring docker registry access.")
    _setup_docker_registry_access_show_results(ctx, pipeline_namespace, app_deployment_namespace, helm_deployer_service_account_name)


def _setup_docker_registry_access_show_results(ctx, pipeline_ns, app_ns, helm_deployer_sa):
    """
    Display the results of the script execution.
    Args:
        pipeline_ns (str): The namespace of the pipeline
        app_ns (str): The namespace of the app
        helm_deployer_sa (str): The name of the helm deployer service account
    """

    kubernetes_utils = KubernetesUtils(ctx)

    logging.info("########################")
    logging.info("Results:")
    logging.info("########################")
    logging.info("")
    logging.info("=== Service Accounts ===")
    logging.info(f"Name: {helm_deployer_sa}")
    logging.info(f"Namespace: {pipeline_ns}")
    sa = kubernetes_utils.get_service_account(
        namespace=pipeline_ns,
        name=helm_deployer_sa
    )
    logging.info(sa)

    logging.info(f"Namespace: {app_ns}")
    logging.info(f"Name: default")
    sa = kubernetes_utils.get_service_account(
        namespace=app_ns,
        name="default"
    )
    logging.info(sa)

###########################

## team accounts
