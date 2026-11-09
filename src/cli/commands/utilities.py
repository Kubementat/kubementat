import click

from kubementat.automations.ci import CiUtils
from cli.context import kmt_context

# --------------------------------------------
# RUN A CONTAINER WITH THE CI IMAGE
#
# Flat, and it stays flat because nothing about it is a verb of another surface: one command that
# starts the automation image's container for an environment and a team, so it has no group to
# answer to and no group to join.
@click.command(name='run-ci-container', help='Runs a container with the automation image used by tekton in the given environment and team namespace')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def run_ci_container(ctx, environment, team):
    # Nothing here needs the checkout resolved through a context object, because there is no longer
    # one: `cli.kmt.Config` carried a single `kubementat_main_dir` and 32 commands accepted it and
    # none read it. What reads the root now is `kubementat.core.config.paths.default_kubementat_main_dir`,
    # called at the one place a path is derived, and this command does not derive one — the image
    # comes from platform_config and the pod is started by argv.
    CiUtils(ctx).run_ci_container()
