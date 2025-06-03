import click
import subprocess
import os
import sys

# Add the lib directory to the sys.path
import_path=os.path.join(os.path.dirname(__file__), '../..', 'lib')
sys.path.append(import_path)
from kubementat.automation import Automation
from kubementat.tekton_utils import TektonUtils

TEKTON_AUTOMATION_SUB_DIRECTORY='tekton_ci/automation'

###########################
# HELPERS 
###########################
def run_script(kubementat_main_dir, environment, team, script_name):
    click.echo('###')
    click.echo(f"ENVIRONMENT: {environment}")
    click.echo(f"TEAM: {team}")
    execution_path=os.path.join(kubementat_main_dir, TEKTON_AUTOMATION_SUB_DIRECTORY)
    script_path=os.path.join(kubementat_main_dir, TEKTON_AUTOMATION_SUB_DIRECTORY, script_name)
    click.echo(f"Execution path: {execution_path}")
    click.echo(f"Script path: {script_path}")
    click.echo('###')

    os.chdir(execution_path)
    subprocess.check_call(f"{script_path} {str(environment)} {str(team)}", shell=True)

# --------------------------------------------
# RUN PIPELINE
@click.command(name='tekton-run-pipeline', help='Run a tekton pipeline')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.argument('pipeline_run_identifier', required=False)
@click.option('--parallel', is_flag = True, default=False,
              help="Allow parallel running of runs for the same pipeline?")
@click.pass_obj
def run_pipeline(config, environment, team, pipeline_run_identifier, parallel):
    tekton_utils = TektonUtils(environment, team)
    if not pipeline_run_identifier:
        # List available pipeline runs
        if team:
            tekton_utils.list_available_pipeline_runs()
        return 0

    tekton_utils.run_pipeline(pipeline_run_identifier, parallel)

# --------------------------------------------
# LIST TEKTON RESOURCES
@click.command(name='tekton-list', help='List tekton resources')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def list(config, environment, team):
    tekton_utils = TektonUtils(environment, team)
    tekton_utils.list_all_tekton_resources()

# --------------------------------------------
# CLEANUP PIPELINE RUNS
@click.command(name='tekton-cleanup-pipeline-runs', 
               help='Cleanup resources (containers, tekton resources) of executed pipeline runs')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.option('--filter', default='succeeded',
              help="Optional Filter for cleaning up specific pipeline runs. options are: succeeded (default), all")
@click.pass_obj
def cleanup_pipeline_runs(config, environment, team, filter):
    tekton_utils = TektonUtils(environment, team)
    tekton_utils.cleanup_pipeline_runs(filter)

# --------------------------------------------
# SETUP PIPELINES
@click.command(name='tekton-setup-pipelines', help='Setup tekton-pipelines and tasks')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def setup_pipelines(config, environment, team):
    automation = Automation(environment, team)
    automation.setup_pipelines()

# --------------------------------------------
# SETUP TEKTON TRIGGERS
@click.command(name='tekton-setup-triggers', help='Setup tekton webhook triggers')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def setup_triggers(config, environment, team):
    run_script(config.kubementat_main_dir, environment, team, 'setup_triggers.sh')

# --------------------------------------------
# UNINSTALL PIPELINES
@click.command(name='tekton-uninstall-pipelines', help='Uninstall tekton-pipelines and tasks, including the teams pipeline namespace')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def uninstall_pipelines(config, environment, team):
    automation = Automation(environment, team)
    automation.uninstall_pipelines()