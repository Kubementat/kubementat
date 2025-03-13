import click
import subprocess
import os

###########################
# HELPERS 
###########################
# TODO: #REFACTOR: use this for all script executions instead of copying it
def run_script(kubementat_main_dir, environment, team, script_name, execution_sub_path):
    click.echo('###')
    click.echo(f"ENVIRONMENT: {environment}")
    click.echo(f"TEAM: {team}")
    execution_path=os.path.join(kubementat_main_dir, execution_sub_path)
    script_path=os.path.join(kubementat_main_dir, execution_sub_path, script_name)
    click.echo(f"Execution path: {execution_path}")
    click.echo(f"Script path: {script_path}")
    click.echo('###')

    os.chdir(execution_path)
    subprocess.check_call(f"{script_path} {str(environment)} {str(team)}", shell=True)

# Install kubementat
@click.command(name='install', help='Install kubementat on a k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def install(config, environment, team):
    click.echo('###')
    click.echo(f"ENVIRONMENT: {environment}")
    click.echo(f"TEAM: {team}")

    run_script(config.kubementat_main_dir, environment, team, 'install_kubementat.sh', '')