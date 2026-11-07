"""`kmt tekton uninstall-pipelines`: the pipeline namespace, and nothing else.

The namespace has to be one a file named. `Config` will answer `PIPELINE_NAMESPACE` from a team's
name, which is where kubementat deploys a pipeline and not a licence to remove whatever that name
happens to be on this cluster -- so the one read here is `declared`, and a team that has not
written its namespace down gets the reason rather than a deletion.
"""


import logging

import click

from kubementat.infrastructure.kubernetes import KubernetesUtils


def uninstall_pipelines(ctx):
    '''
    Deletes the pipeline namespace to uninstall pipelines.

    Parameters:

    - None (uses instance configuration)

    Returns:

    - None (performs side-effect operations)

    Raises:

    - Exception - If namespace deletion fails
    '''

    config = ctx.config
    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)

    logging.info(f"Starting uninstall_pipelines execution with environment: {environment} and team: {team}")
    # The namespace this deletes must be one a file named. `team_static` would answer
    # PIPELINE_NAMESPACE from the team's name, which is where a pipeline is deployed and
    # not a licence to remove whatever that name happens to be on this cluster.
    pipeline_ns = config.declared('team_static').get('PIPELINE_NAMESPACE')
    if not pipeline_ns:
        raise click.ClickException(
            f"{config.team_static_config_path} does not declare PIPELINE_NAMESPACE, so no"
            f" namespace can be deleted for team '{team}'. The pipelines namespace follows"
            f" from a team's name when kubementat deploys into it, and from nothing at all when"
            f" kubementat is asked to remove one — write the namespace the pipelines are in, or"
            f" point at the team you mean."
        )
    logging.info(f"Pipeline Namespace: {pipeline_ns}")

    kubernetes_utils.delete_namespace(pipeline_ns)
    logging.info(f"Successfully uninstalled pipelines for environment: {environment} , team: {team}")
