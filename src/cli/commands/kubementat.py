"""The whole-tree verbs: `kmt install` and `kmt uninstall`."""
import click

from cli.consent import install_consent, uninstall_consent
from cli.context import kmt_context
from kubementat.automations.install import kubementat as install_slice
from kubementat.automations.install import registry as registry_slice
from kubementat.automations.install import secrets as secrets_slice
from kubementat.automations.uninstall import kubementat as uninstall_slice

# Install kubementat
@click.command(name='install', help='Install kubementat on a k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.option('--configure-tekton-pipelines/--no-configure-tekton-pipelines', default=True)
@click.option('--helmfile-installation-group', default='standard')
@click.option('--yes', '-y', is_flag=True, help="Do not ask for user input. For scripting purposes.")
@kmt_context
def install(ctx, environment, team, configure_tekton_pipelines, helmfile_installation_group, yes):
    click.echo(f"Helmfile installation group: {helmfile_installation_group}")
    click.echo(f"Configure tekton pipelines: {configure_tekton_pipelines}")
    # The question is asked here, after the two lines this command prints before it
    # calls anything, and before it touches a cluster. Finding out about a missing terminal after the
    # dependency probes have run costs the same three lines either way, so the position of this guard
    # is about how much work a doomed run does before saying so. The library still decides *where*
    # the answer is needed -- after the
    # cluster has been read -- so the order of the operator's lines does not move.
    consent = install_consent(environment, team, yes)
    result = install_slice.install_kubementat(
        ctx,
        configure_tekton_pipelines=configure_tekton_pipelines,
        helmfile_installation_group=helmfile_installation_group,
        consent=consent
    )
    # One print of the value the install returned, for the same reason `kmt routing install` prints
    # its result: the log says what happened and this says which of it was *this run's* doing.
    # The library's own finish message still goes to the log, so this is not the next-steps block
    # moving -- it is the fact of what was written, on stdout, where a script can read it.
    click.echo(result.summary())

# Uninstall kubementat
@click.command(name='uninstall', help='Uninstall kubementat from a k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.option('--yes', '-y', is_flag=True, help="Do not ask for user input. For scripting purposes.")
@kmt_context
def uninstall(ctx, environment, yes):
    # The confirmation contract belongs to the CLI:
    # without `--yes` a run that has no terminal to ask in fails closed, naming the flag. A verb
    # that reads stdin itself can hold a lane open, and answering "no" there still meant running
    # something. The *question* is asked in `cli/consent.py` and the answer travels down, so the
    # library never asks on the operator's behalf after printing the deletion plan.
    # Environment-scoped, as
    # it was: the sweep covers every team the environment's layer configures, so passing one team
    # along would be a claim about the install that is not true, and the `Context` the decorator
    # resolved is all this command hands the uninstall slice.
    result = uninstall_slice.uninstall_kubementat(ctx, consent=uninstall_consent(environment, yes))
    # The answer is printed on the clean path, exactly as `kmt install` prints its own. The residue
    # path is neither printed nor silently accepted: it takes `raise_if_incomplete`, which raises
    # `UninstallIncompleteError` with the message the value builds, so the operator reads the same
    # sentence they always read and the command still exits 1. What changed is that a run which
    # deleted ten namespaces and left one release running is now a run with a *partial result* --
    # readable as one, and not only as a joined message.
    if not result.clean:
        result.raise_if_incomplete()
    click.echo(result.summary())


# --------------------------------------------
# SETUP SECRETS
@click.command(name='setup-secrets', help='Setup secrets for the given team in the k8s cluster. '
                                          'See the team layer of the configuration directory '
                                          '-> static.encrypted.json -> SSH_DEPLOY_KEYS')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def setup_secrets(ctx, environment, team):
    secrets_slice.setup_secrets(ctx)

# --------------------------------------------
# SETUP DOCKER REGISTRY ACCESS
@click.command(name='setup-docker-registry-access', help='Setup secrets for accessing docker registries')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def setup_docker_registry_access(ctx, environment, team):
    registry_slice.setup_docker_registry_access(ctx)