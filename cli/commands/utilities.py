import click
import subprocess
import os

UTILITIES_SUB_DIRECTORY='utilities'

# --------------------------------------------
# RUN A CONTAINER IPIPELINE
@click.command(name='run-ci-container', help='Runs a container with the automation image used by tekton in the given environment and team namespace')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def run_ci_container(config, environment, team):
    click.echo(f"ENVIRONMENT: {environment}")
    click.echo(f"TEAM: {team}")
    
    execution_path=os.path.join(config.kubementat_main_dir, UTILITIES_SUB_DIRECTORY)
    script_path=os.path.join(config.kubementat_main_dir, UTILITIES_SUB_DIRECTORY, 'run_ci_container.sh')
    click.echo(f"Execution path: {execution_path}")
    click.echo(f"Script path: {script_path}")
    click.echo('###')
    
    os.chdir(execution_path)
    subprocess.check_call(f"{script_path} {str(environment)} {str(team)}", shell=True)