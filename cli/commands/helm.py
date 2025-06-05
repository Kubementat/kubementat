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
@click.option('--helmfile-working-directory', default=None, help="The directory where helmfile cli should be executed")
@click.option('--helmfile-path', default=None, help="The path to the helmfile.yaml file to be applied")
@click.pass_obj
def helmfile_apply(config, environment, helmfile_label_filter, interactive=True, helmfile_working_directory=None, helmfile_path=None):
    utils = HelmUtils(environment, 'dev1')
    click.echo(f"environment: {environment}")
    click.echo(f"helmfile-label-filter: {helmfile_label_filter}")
    click.echo(f"interactive: {interactive}")
    utils.helmfile_apply(
      environment_name = environment,
      helmfile_label_filter = helmfile_label_filter,
      interactive=bool(interactive),
      helmfile_working_directory=helmfile_working_directory, 
      helmfile_path=helmfile_path
    )
