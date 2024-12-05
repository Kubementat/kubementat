import click
import subprocess
import os
import glob

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

# Run any script in kubementat
@click.command(name='run-script', help='Runs a utility script')
@click.option('--args', '-a', multiple=True)
@click.pass_obj
def run_script(config, args):
    execution_path=os.path.join(config.kubementat_main_dir, UTILITIES_SUB_DIRECTORY)

    if len(args) < 1:
        all_helper_scripts = glob.glob(f"{execution_path}/*.sh") + glob.glob(f"{execution_path}/**/*.sh")
        all_automation_scripts = glob.glob(f"{config.kubementat_main_dir}/tekton_ci/automation/*.sh") + glob.glob(f"{config.kubementat_main_dir}/tekton_ci/automation/**/*.sh")
        click.echo('Showing all available utility scripts:')
        for script_name in all_helper_scripts:
            click.echo(script_name)
        click.echo('')

        click.echo('##############')

        click.echo('Showing all available automation scripts:')
        for script_name in all_automation_scripts:
            click.echo(script_name)
        click.echo('')
        click.echo('Run kmt run-script --args <SCRIPT_PATH> --args <SCRIPT_ARG_1> --args <SCRIPT_ARG_2> ...')
        exit(1)

    script_path=args[0]
    script_name = os.path.basename(script_path)
    script_directory=os.path.dirname(script_path)
    click.echo(f"Execution path: {script_directory}")
    click.echo(f"Script: {script_name}")
    os.chdir(script_directory)
    script_arguments=' '.join(args[1:])
    click.echo(f"Script arguments: {script_arguments}")
    script_call=f"{script_path}"
    if len(script_arguments) > 0:
        script_call=f"{script_call} {script_arguments}"
    subprocess.check_call(script_call, shell=True)
    