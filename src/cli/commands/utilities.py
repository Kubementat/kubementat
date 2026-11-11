import click
from pathlib import Path

from kubementat.ci_utils import CiUtils
from kubementat.ingress_generator import (IngressArgumentNotSuppliedError, IngressGenerator,
                                         IngressPortNotUsableError, IngressTargetExistsError)
from cli.context import kmt_context

# --------------------------------------------
# RUN A CONTAINER WITH THE CI IMAGE
@click.command(name='run-ci-container', help='Runs a container with the automation image used by tekton in the given environment and team namespace')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def run_ci_container(ctx, environment, team):
    # Nothing here needs the checkout resolved through a context object, because there is no longer
    # one: `cli.kmt.Config` carried a single `kubementat_main_dir` and 32 commands accepted it and
    # none read it. What reads the root now is `kubementat.config.default_kubementat_main_dir`,
    # called at the one place a path is derived, and this command does not derive one — the image
    # comes from platform_config and the pod is started by argv.
    CiUtils(ctx).run_ci_container()

# GENERATE AN INGRESS MANIFEST
#
# `kmt create-ingress` is what `create_ingress_yml.sh` became: the same three arguments in the same
# order, which cluster the manifest is meant for named first as every cluster-touching command names
# it, the same manifest, and the same rule that the manifest is what arrives on stdout while
# the warning about the class goes to stderr. It is a command rather than a path handed to a runner,
# because the script had no caller in `src/` or `tekton_ci/` -- only an operator who knew the
# filename and which directory to stand in could reach it, and this CLI keeps no verb that takes a
# script path from a human.
#
# No `--team`: the class this manifest names is `TRAEFIK_INGRESS_CLASS`, which is read out of the
# environment's own `static.json`, and nothing else on the path touches configuration. The comment
# that used to stand here said the option belonged because a `Config` needs a team to be pointed at
# -- true of the paths `Config` derives, but `Config._team_scoped_path` refuses those at the read
# now, and this command reads none of them. `kmt create-ingress dev ...` therefore works with no
# team, and a `--team` would be a flag that changes nothing.
@click.command(name='create-ingress',
               help='Generate an Ingress manifest for a service, and say which ingress class it '
                    'routes through.\n\n'
                    'The manifest goes to stdout so it can be piped or redirected, and any warning '
                    'about the class goes to stderr so it cannot land in the YAML. The class is read '
                    'from TRAEFIK_INGRESS_CLASS for the environment; when nothing answers, '
                    'kubementat writes its own default and says so.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('domain')
@click.argument('service_name')
@click.argument('service_port')
@click.option('--output', '-o', 'output_path', type=click.Path(dir_okay=False, path_type=Path),
              default=None,
              help='Write the manifest to this file instead of stdout. Refuses if the file exists.')
@kmt_context
def create_ingress(ctx, environment, domain, service_name, service_port, output_path):
    # The environment is first and required: the class this manifest names is read from that
    # environment's `static.json`, so an empty one used to be a default the command had dropped
    # rather than a decision it made. `@kmt_context` refuses that state now, and the
    # generator's own "no environment" branch survives only for library callers.
    generator = IngressGenerator(ctx)
    try:
        generated = generator.generate(domain, service_name, service_port)
    except (IngressArgumentNotSuppliedError, IngressPortNotUsableError) as error:
        raise click.ClickException(str(error))

    for warning in generated.warnings:
        click.echo(f"WARNING: {warning}", err=True)

    if output_path is None:
        click.echo(generated.text(), nl=False)
        return
    try:
        generator.write(generated, Path(output_path))
    except IngressTargetExistsError as error:
        raise click.ClickException(str(error))
    click.echo(f"wrote {output_path}", err=True)
