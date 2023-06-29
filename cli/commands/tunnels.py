import click
import subprocess
import os

UTILITIES_SUB_DIRECTORY='utilities'

###########################
# HELPERS 
###########################
def open_tunnel(kubementat_main_dir, environment, service_name):
    execution_path=os.path.join(kubementat_main_dir, UTILITIES_SUB_DIRECTORY)
    script_path=os.path.join(kubementat_main_dir, UTILITIES_SUB_DIRECTORY, f"open_{service_name}_tunnel.sh")
    os.chdir(execution_path)
    subprocess.check_call(f"{script_path} {str(environment)}", shell=True)

##########################
#### TUNNEL SCRIPTS ######
##########################

# Tunnel grafana
@click.command(name='tunnel-grafana', help='open a network tunnel to the grafana UI')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.pass_obj
def tunnel_grafana(config, environment):
    click.echo(f"ENVIRONMENT: {environment}")
    open_tunnel(config.kubementat_main_dir, environment, 'grafana')

# Tunnel tekton
@click.command(name='tunnel-tekton', help='open a network tunnel to the tekton UI')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.pass_obj
def tunnel_tekton(config, environment):
    click.echo(f"ENVIRONMENT: {environment}")
    open_tunnel(config.kubementat_main_dir, environment, 'tekton_dashboard')
    
# Tunnel Polaris
@click.command(name='tunnel-polaris', help='open a network tunnel to the polaris UI')
@click.argument('environment',envvar='ENVIRONMENT',default='dev')
@click.pass_obj
def tunnel_polaris(config, environment):
    click.echo(f"ENVIRONMENT: {environment}")
    open_tunnel(config.kubementat_main_dir, environment, 'polaris')