import click
import subprocess
import os

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
@click.argument('pipeline_run_file', required=False)
@click.pass_obj
def tekton_run_pipeline(config, environment, team, pipeline_run_file):
    click.echo('###')
    click.echo(f"ENVIRONMENT: {environment}")
    click.echo(f"TEAM: {team}")
    
    execution_path=os.path.join(config.kubementat_main_dir, TEKTON_AUTOMATION_SUB_DIRECTORY)
    script_path=os.path.join(config.kubementat_main_dir, TEKTON_AUTOMATION_SUB_DIRECTORY, 'run_pipeline.sh')
    click.echo(f"Execution path: {execution_path}")
    click.echo(f"Script path: {script_path}")

    if pipeline_run_file == None:
        pipeline_run_file=''
    click.echo(f"Pipeline Run File: {pipeline_run_file}")
    click.echo('###')
    
    os.chdir(execution_path)
    subprocess.check_call(f"{script_path} {str(environment)} {str(team)} {str(pipeline_run_file)}", shell=True)

# --------------------------------------------
# LIST TEKTON RESOURCES
@click.command(name='tekton-list', help='List tekton resources')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def list(config, environment, team):
    run_script(config.kubementat_main_dir, environment, team, 'list_tekton_resources.sh')

# --------------------------------------------
# CLEANUP PIPELINE RUNS
@click.command(name='tekton-cleanup-pipeline-runs', 
               help='Cleanup resources (containers, tekton resources) of executed pipeline runs')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.option('--all', is_flag=True, 
              help="Cleanup all pipeline runs including errored pipeline runs?")
@click.pass_obj
def tekton_cleanup_pipeline_runs(config, environment, team, all):
    if all == True:
        run_script(config.kubementat_main_dir, environment, team, 'cleanup_all_pipeline_runs.sh')
    else:
        run_script(config.kubementat_main_dir, environment, team, 'cleanup_successful_pipeline_runs.sh')


# --------------------------------------------
# CONFIGURE SECRETS
@click.command(name='tekton-configure-secrets', help='Configure tekton secrets in the k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def tekton_configure_secrets(config, environment, team):
    run_script(config.kubementat_main_dir, environment, team, 'configure_secrets.sh')

# --------------------------------------------
# CONFIGURE DOCKER REGISTRY ACCESS
@click.command(name='tekton-configure-docker-registry-access', help='Configure tekton secrets for accessing docker registries')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def tekton_configure_docker_registry_access(config, environment, team):
    run_script(config.kubementat_main_dir, environment, team, 'configure_docker_registry_access.sh')

# --------------------------------------------
# SETUP PIPELINES
@click.command(name='tekton-setup-pipelines', help='Setup tekton-pipelines and tasks')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def tekton_setup_pipelines(config, environment, team):
    run_script(config.kubementat_main_dir, environment, team, 'setup_pipelines.sh')

# --------------------------------------------
# SETUP TEKTON TRIGGERS
@click.command(name='tekton-setup-triggers', help='Setup tekton webhook triggers')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def tekton_setup_triggers(config, environment, team):
    run_script(config.kubementat_main_dir, environment, team, 'setup_triggers.sh')
