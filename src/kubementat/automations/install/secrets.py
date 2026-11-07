"""The deploy keys a team's own encrypted file declares.

`kmt install` writes the deployer's keypair from the configuration directory's files; this command
writes what the layers say about keys that belong to the operator. That difference is the module:
a team that declares no deploy key gets no Secret and one warning, because inventing key material
to have something to write is not a failure kubementat gets to choose.
"""


import logging

from kubementat.infrastructure.kubernetes import KubernetesUtils


def setup_secrets(ctx):
    '''
    Configures SSH and GPG secrets in the pipeline namespace.

    Parameters:

    - None (uses instance configuration)

    Returns:

    - None (performs side-effect operations)

    Raises:

    - Exception - If secret creation fails
    '''

    config = ctx.config
    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)

    namespace = config.get('team_static').get('PIPELINE_NAMESPACE')
    logging.info(f"Environment: {environment}")
    logging.info(f"Team: {team}")
    logging.info(f"Namespace: {namespace}")

    try:
        # Get the SSH keys from the config
        ssh_keys = config.get('team_static_encrypted').get('SSH_DEPLOY_KEYS')  # Returns list of SSH key dicts

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
                            "to have one.", environment, team)
            return None

        logging.info("Setting up secrets in namespace %s", namespace)

        # Iterate through each SSH key and create secret
        for key_data in ssh_keys:
            name = key_data['NAME']
            namespace = key_data['TARGET_NAMESPACE']
            secret_name = key_data['TARGET_SECRET_NAME']
            private_key_base64 = key_data['PRIVATE_KEY_BASE64']

            logging.info(f"Configuring SSH key: {name} as secret: {secret_name} in namespace: {namespace}")

            kubernetes_utils.create_or_replace_secret(
            namespace=namespace,
            secret_name=secret_name,
            key_type="kubernetes.io/ssh-auth",
            # The ssh key is already base64 encoded so we don't need to encode it again
            raw_data={"ssh-privatekey": private_key_base64}
            )

        logging.info(f"Secrets configured successfully in namespace {namespace} .")
        _setup_secrets_show_results(ctx, namespace)
    except Exception as e:
        logging.error(f"Failed to configure secrets: {str(e)}")
        raise
    return None


def _setup_secrets_show_results(ctx, pipeline_ns):
    """Display the results of the secrets configured"""

    kubernetes_utils = KubernetesUtils(ctx)


    logging.info(f"=== Secrets within namespace {pipeline_ns} ===")
    secrets = kubernetes_utils.list_secrets(namespace=pipeline_ns)
    for secret in secrets.items:
        logging.info(secret.metadata.name)

    return None

# docker registry access automation
###########################
