import click
import subprocess
import os
import sys

# Add the lib directory to the sys.path
import_path=os.path.join(os.path.dirname(__file__), '../..', 'lib')
sys.path.append(import_path)
from kubementat.automation import Automation
from kubementat.kubernetes_utils import KubernetesUtils

UTILITIES_SUB_DIRECTORY='scripts/utilities'

##########################
#### TUNNEL SCRIPTS ######
##########################

# Tunnel kubernetes dashboard
@click.command(name='login-kubernetes-dashboard', help='display a token and connection information for accessing the kubernetes dashboard')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.pass_obj
def login_kubernetes_dashboard(config, environment):
    click.echo(f"ENVIRONMENT: {environment}")
    execution_path=os.path.join(config.kubementat_main_dir, UTILITIES_SUB_DIRECTORY)
    script_path=os.path.join(config.kubementat_main_dir, UTILITIES_SUB_DIRECTORY, f"login_kubernetes_dashboard.sh")
    os.chdir(execution_path)
    subprocess.check_call(f"{script_path} {str(environment)}", shell=True)

# Tunnel grafana
@click.command(name='tunnel-grafana', help='open a network tunnel to the grafana UI')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.option('--local-port', default='3001')
@click.option('--remote-port', default='3000')
@click.option('--host', default='0.0.0.0')
@click.pass_obj
def tunnel_grafana(config, environment, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    # the team is not needed for the current cluster grafana setup
    automation = Automation(environment, 'dev1')
    click.echo("########################")
    click.echo(f"See GRAFANA_ADMIN_USER and GRAFANA_ADMIN_PASSWORD environment variables within platform_config/{environment}/static.encrypted.json")
    click.echo("########################")
    return automation.open_tunnel_grafana(local_port, remote_port, host)

# Tunnel tekton
@click.command(name='tunnel-tekton', help='open a network tunnel to the tekton UI')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.option('--local-port', default='9097')
@click.option('--remote-port', default='9097')
@click.option('--host', default='0.0.0.0')
@click.pass_obj
def tunnel_tekton(config, environment, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    # the team is not needed for the current cluster grafana setup
    automation = Automation(environment, 'dev1')
    return automation.open_tunnel_tekton_dashboard(local_port, remote_port, host)
    
# Tunnel Polaris
@click.command(name='tunnel-polaris', help='open a network tunnel to the polaris UI')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.option('--local-port', default='8082')
@click.option('--remote-port', default='8080')
@click.option('--host', default='0.0.0.0')
@click.pass_obj
def tunnel_polaris(config, environment, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    # the team is not needed for the current cluster grafana setup
    automation = Automation(environment, 'dev1')
    return automation.open_tunnel_polaris(local_port, remote_port, host)

# Tunnel Vault UI
@click.command(name='tunnel-vault-ui', help='open a network tunnel to the vault UI')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.option('--local-port', default='8205')
@click.option('--remote-port', default='8200')
@click.option('--host', default='0.0.0.0')
@click.pass_obj
def tunnel_polaris(config, environment, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    # the team is not needed for the current cluster grafana setup
    automation = Automation(environment, 'dev1')
    return automation.open_tunnel_vault(local_port, remote_port, host)

@click.command(name='tunnel-pod', help='open a network tunnel to the specified pod')
@click.argument('namespace',envvar='NAMESPACE')
@click.argument('pod-name',envvar='POD_NAME')
@click.option('--local-port',required=True)
@click.option('--remote-port', required=True)
@click.option('--host', default='0.0.0.0')
@click.pass_obj
def tunnel_pod(config, namespace, pod_name, local_port, remote_port, host):
    click.echo(f"NAMESPACE: {namespace}")
    click.echo(f"POD_NAME: {pod_name}")
    # the team is not needed for the current cluster grafana setup
    utils = KubernetesUtils()
    return utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)