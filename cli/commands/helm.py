import os
import sys
import click

# Add the lib directory to the sys.path
import_path=os.path.join(os.path.dirname(__file__), '../..', 'lib')
sys.path.append(import_path)
from kubementat.helm_utils import HelmUtils

# apply helmfile.yaml using helmfile cli
@click.command(name='helmfile-apply', help='apply helmfile.yaml using helm cli')
@click.argument('environment',envvar='ENVIRONMENT')
@click.option('--helmfile-label-filter', default='group=standard', help="e.g. group=standard or component_name=vault")
@click.option('--interactive/--non-interactive', is_flag=True, default=True)
@click.pass_obj
def helmfile_apply(config, environment, helmfile_label_filter, interactive=True):
    utils = HelmUtils(environment, 'dev1')
    click.echo(f"environment: {environment}")
    click.echo(f"helmfile-label-filter: {helmfile_label_filter}")
    click.echo(f"interactive: {interactive}")
    utils.helmfile_apply(
      environment_name = environment,
      helmfile_label_filter = helmfile_label_filter,
      interactive=bool(interactive)
    )
