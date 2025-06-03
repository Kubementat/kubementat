import click
import subprocess
import os
import sys

# Add the lib directory to the sys.path
import_path=os.path.join(os.path.dirname(__file__), '../..', 'lib')
sys.path.append(import_path)
from kubementat.automation import Automation

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

# Initialize kubementat
@click.command(name='initialize', help='Initialize kubementat configuration as initial install preparation')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def initialize(config, environment, team):
    run_script(config.kubementat_main_dir, environment, team, 'initialize_kubementat.sh', '')

# Install kubementat
@click.command(name='install', help='Install kubementat on a k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.option('--configure-tekton-pipelines/--no-configure-tekton-pipelines', default=True)
@click.option('--enable-linkerd/--disable-linkerd', default=False)
@click.option('--helmfile-installation-group', default='standard')
@click.pass_obj
def install(config, environment, team, configure_tekton_pipelines, enable_linkerd, helmfile_installation_group):
    automation = Automation(environment, team)
    click.echo(f"Helmfile installation group: {helmfile_installation_group}")
    click.echo(f"Configure tekton pipelines: {configure_tekton_pipelines}")
    click.echo(f"Linkerd enabled: {enable_linkerd}")
    automation.install_kubementat(
        configure_tekton_pipelines=configure_tekton_pipelines,
        enable_linkerd=enable_linkerd,
        helmfile_installation_group=helmfile_installation_group
    )

# Uninstall kubementat
@click.command(name='uninstall', help='Uninstall kubementat from a k8s cluster')
@click.argument('environment',envvar='ENVIRONMENT')
@click.pass_obj
def uninstall(config, environment):
    run_script(config.kubementat_main_dir, environment, '', 'uninstall_kubementat.sh', '')


# --------------------------------------------
# SETUP SECRETS
@click.command(name='setup-secrets', help='Setup secrets for the given team in the k8s cluster. See platform_config/ENV/TEAM/static.encrypted.json -> SSH_DEPLOY_KEYS')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def setup_secrets(config, environment, team):
    automation = Automation(environment, team)
    automation.setup_secrets()

# --------------------------------------------
# SETUP DOCKER REGISTRY ACCESS
@click.command(name='setup-docker-registry-access', help='Setup secrets for accessing docker registries')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.pass_obj
def setup_docker_registry_access(config, environment, team):
    automation = Automation(environment, team)
    automation.setup_docker_registry_access()