import sys

import click

from cli.context import kmt_context
from kubementat.automation import Automation

# Install kubementat
@click.command(name='install', help='Install kubementat on a k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.option('--configure-tekton-pipelines/--no-configure-tekton-pipelines', default=True)
@click.option('--helmfile-installation-group', default='standard')
@click.option('--yes', '-y', is_flag=True, help="Do not ask for user input. For scripting purposes.")
@kmt_context
def install(ctx, environment, team, configure_tekton_pipelines, helmfile_installation_group, yes):
    automation = Automation(ctx)
    click.echo(f"Helmfile installation group: {helmfile_installation_group}")
    click.echo(f"Configure tekton pipelines: {configure_tekton_pipelines}")
    automation.install_kubementat(
        configure_tekton_pipelines=configure_tekton_pipelines,
        helmfile_installation_group=helmfile_installation_group,
        assume_yes=yes
    )

# Uninstall kubementat
@click.command(name='uninstall', help='Uninstall kubementat from a k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.option('--yes', '-y', is_flag=True, help="Do not ask for user input. For scripting purposes.")
@kmt_context
def uninstall(ctx, environment, yes):
    # The confirmation contract belongs to the CLI and it did not change when the bash did:
    # without `--yes` a run that has no terminal to ask in fails closed, naming the flag. The
    # deleted `uninstall_kubementat.sh` used to read stdin itself, which is why `kmt uninstall`
    # could hold a lane open - and why answering "no" meant running a script at all.
    if not yes and not sys.stdin.isatty():
        raise click.UsageError(
            "uninstall requires confirmation, but stdin is not a terminal. "
            "Re-run with --yes (-y) to uninstall kubementat without prompting.")
    # Environment-scoped: the sweep covers every team `platform_config/{environment}` configures,
    # so passing one team along would be a claim about the install that is not true. The `Context`
    # the decorator resolved is all this command hands `Automation`: a cluster to sweep, and no
    # team, whose shape is decided by the command and not defaulted from the caller's context.
    Automation(ctx).uninstall_kubementat(assume_yes=yes)


# --------------------------------------------
# SETUP SECRETS
@click.command(name='setup-secrets', help='Setup secrets for the given team in the k8s cluster. See platform_config/ENV/TEAM/static.encrypted.json -> SSH_DEPLOY_KEYS')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def setup_secrets(ctx, environment, team):
    automation = Automation(ctx)
    automation.setup_secrets()

# --------------------------------------------
# SETUP DOCKER REGISTRY ACCESS
@click.command(name='setup-docker-registry-access', help='Setup secrets for accessing docker registries')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def setup_docker_registry_access(ctx, environment, team):
    automation = Automation(ctx)
    automation.setup_docker_registry_access()