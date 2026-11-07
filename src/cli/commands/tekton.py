"""`kmt tekton` -- the pipelines kubementat runs, sets up, lists and takes away.

The grouping of this surface is not a tidy-up: `tekton-run-pipeline`, `tekton-list` and the
rest were already lines that all started with the same word, and the word was not the question.
Under `kmt tekton` the verb comes first -- `run-pipeline`, `list`, `cleanup-pipeline-runs` -- which
is the shape `kmt users`, `kmt routing` and `kmt component` already have.

**Renames, no aliases.** The old flat names are gone rather than shadowed: no hidden command, no
`deprecated` branch, no "did you mean" translator. This file is where that rule bites
hardest, because `kmt tekton-run-pipeline` is what a Tekton Task runs
(`tekton_ci/tasks/run-tekton-pipeline.yml`) -- a command that answers to two names is a Task that can
silently keep running the old one, which is the only way this migration has of making a lie out of a
capability report.

Six of the ten commands here were renamed by the grouping, because six are what the lane measures as
flat; the
cleanup job and the three generators stay under their old names for now, the same reason
`helm-version` gives on its own module -- no marker pins them, and a rename nothing measures is a
rename nobody can tell the difference about.
"""

import click

from cli.context import kmt_context
from cli.helpers import team_option
from kubementat.automations.install import pipelines as install_pipelines_slice
from kubementat.automations.uninstall import pipelines as uninstall_pipelines_slice
from kubementat.tekton_utils import TektonUtils
# The run path moved out of `tekton_utils.py` (P4-3), so it is reached through the module the same
# way `generation` is: a name imported past its home is a second binding, and the seam the tests
# patch is the module the code actually runs. The class stays behind for one thing only -- the
# state every one of these calls used to read off `self` -- and P4-6 deletes it with the file.
from kubementat.infrastructure.tekton import generation, runs


@click.group(name='tekton',
             help='Run, list, set up and remove the tekton pipelines and tasks of an environment.')
def tekton():
    pass

# --------------------------------------------
# RUN PIPELINE
# The help text is the Task's only documentation of the argument, so the rule that decides
# between a path and an identifier is written there and not only in a docstring: the Task hands
# an absolute path and a human at a terminal hands a name, and both arrive at the same slot.
@tekton.command(name='run-pipeline',
                help='Run a tekton pipeline. PIPELINE_RUN_IDENTIFIER is either a PATH or an '
                   'IDENTIFIER: an absolute path, or any argument that contains a path separator and '
                   'exists there, is used as it was given; a bare name resolves under the team\'s '
                   'platform_config/<environment>/<team>/tekton/pipeline-runs, and global/<name>.yml '
                   'resolves under tekton_ci/pipeline-runs. Without PIPELINE_RUN_IDENTIFIER, the runs '
                   'this team can start are listed and nothing is applied.')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.argument('pipeline_run_identifier', required=False)
@click.option('--parallel', is_flag = True, default=False,
              help="Allow parallel running of runs for the same pipeline?")
@click.option('--wait', 'wait', is_flag=True, default=False,
              help=f"Block until the started run finishes, instead of returning straight after it is "
                   f"applied. Exits non-zero when the run failed or was never started. Off by "
                   f"default, so a terminal behaves as it always did; this is what "
                   f"tekton_ci/tasks/run-tekton-pipeline.yml runs.")
@click.option('--poll-interval', 'poll_interval_seconds', type=int,
              default=runs.PIPELINE_RUN_POLL_INTERVAL_SECONDS, show_default=True,
              help='Seconds between reads of the run, with --wait.')
@click.option('--timeout', 'timeout_seconds', type=int,
              default=runs.PIPELINE_RUN_TIMEOUT_SECONDS, show_default=True,
              help='Seconds to wait for the run before giving up on it, with --wait.')
@kmt_context
def run_pipeline(ctx, environment, team, pipeline_run_identifier, parallel, wait,
                 poll_interval_seconds, timeout_seconds):
    # What the run path reads is still what a `Context` carries, so the object that carried it is
    # still built here -- it is also where a run with no team is refused. `runs` does the work.
    tekton_utils = TektonUtils(ctx)
    if not pipeline_run_identifier:
        # List available pipeline runs
        if team:
            runs.list_available_pipeline_runs(tekton_utils.config, tekton_utils.team)
        return 0

    started = runs.run_pipeline(tekton_utils.kubernetes_utils, tekton_utils.config,
                                tekton_utils.environment, tekton_utils.team,
                                pipeline_run_identifier, parallel)
    # The first command on this surface that propagates an exit code, and the reason it is worth
    # breaking the pattern for is on the other end of the line: `tekton_ci/tasks/run-tekton-pipeline.yml`
    # reads this command's status as its own. `runs.run_pipeline` has always had something to
    # say about a run it refused to start -- a parallel-run refusal, an unreadable file, a cluster that
    # took the object back -- and Click throws away whatever a command function returns, so `return 1`
    # was a no-op and an outer pipeline reported success over a run that never existed. Nothing else on
    # the surface changes today; this one command was the one with a caller that needed the answer.
    if not started:
        raise SystemExit(1)

    if wait:
        raise SystemExit(runs.wait_for_pipeline_run(
            tekton_utils.kubernetes_utils, tekton_utils.config, started,
            tekton_utils.pipeline_namespace, poll_interval_seconds, timeout_seconds))

# --------------------------------------------
# LIST TEKTON RESOURCES
@tekton.command(name='list', help='List tekton resources')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def list(ctx, environment, team):
    # The same construction for the same reason: the namespace the four kinds are listed in and the
    # client they are listed with are both what a `Context` answers through.
    tekton_utils = TektonUtils(ctx)
    runs.list_all_tekton_resources(tekton_utils.kubernetes_utils, tekton_utils.config,
                                   tekton_utils.pipeline_namespace)

# --------------------------------------------
# CLEANUP PIPELINE RUNS
@tekton.command(name='cleanup-pipeline-runs',
                help='Cleanup resources (containers, tekton resources) of executed pipeline runs')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.option('--filter', default='succeeded',
              help="Optional Filter for cleaning up specific pipeline runs. options are: succeeded (default), all")
@kmt_context
def cleanup_pipeline_runs(ctx, environment, team, filter):
    tekton_utils = TektonUtils(ctx)
    runs.cleanup_pipeline_runs(tekton_utils.kubernetes_utils, tekton_utils.config,
                               tekton_utils.pipeline_namespace, filter)

# --------------------------------------------
# SETUP PIPELINES
@tekton.command(name='setup-pipelines', help='Setup tekton-pipelines and tasks')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def setup_pipelines(ctx, environment, team):
    install_pipelines_slice.setup_pipelines(ctx)

# --------------------------------------------
# SETUP TEKTON TRIGGERS
@tekton.command(name='setup-triggers', help='Setup tekton webhook triggers')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def setup_triggers(ctx, environment, team):
    TektonUtils(ctx).setup_triggers()

# --------------------------------------------
# SETUP TEKTON CLEANUP JOB
@click.command(name='tekton-setup-cleanup-job',
               help='Setup the cronjob that prunes completed pipelineruns, in the namespace the '
                    'environment config names for cluster cronjobs')
@click.argument('environment',envvar='ENVIRONMENT')
@team_option()
@kmt_context
def setup_cleanup_job(ctx, environment, team):
    # `setup_cleanup_job` asks the namespace question of `config` and takes the client from the
    # `Context`, so the pair of them is what this command hands over.
    tekton_utils = TektonUtils(ctx)
    runs.setup_cleanup_job(tekton_utils.kubernetes_utils, tekton_utils.config,
                           tekton_utils.environment, tekton_utils.team)

# --------------------------------------------
# GENERATE DEPLOY PIPELINE
@click.command(name='tekton-generate-deploy-pipeline',
               help='Generate a deploy pipeline for an app from the template, into the team\'s own '
                    'pipeline directory. Review it: the generator cannot know what the app\'s '
                    'database setup and seeding need.')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.argument('app_name')
@click.option('--git-url', default=None,
              help="The git url of the app repository, e.g. ssh://github.com/julweber/nginx-example.git")
@click.option('--git-project-name', default=None,
              help="The local project directory name of the app repository, e.g. nginx-example")
@click.option('--git-server-host', default=None,
              help="The app repository's git server host, e.g. github.com")
@click.option('--git-server-port', default=None,
              help="The app repository's git server ssh port, e.g. 22")
@click.option('--git-server-ssh-user', default=None,
              help="The app repository's git ssh user, e.g. git")
@kmt_context
def generate_deploy_pipeline(ctx, environment, team, app_name, git_url, git_project_name,
                             git_server_host, git_server_port, git_server_ssh_user):
    # The generation half moved out of the class, so the call lands on the module and what the
    # method read from the instance arrives as `Config`. The class is still built first, and only
    # for its `config`: the team guard is a constructor line until P4-6 dissolves the class, and
    # skipping the constructor would generate a manifest that says `None` in the team fields
    # instead of refusing a run that never named one.
    generation.generate_deploy_pipeline(
        TektonUtils(ctx).config,
        app_name, git_url=git_url, git_project_name=git_project_name,
        git_server_host=git_server_host, git_server_port=git_server_port,
        git_server_ssh_user=git_server_ssh_user)

# --------------------------------------------
# GENERATE TRIGGER CONFIGURATION
@click.command(name='tekton-generate-triggers',
               help='Generate an app\'s webhook trigger manifests from the git host templates, into '
                    'the team\'s trigger directory that tekton-setup-triggers then reads.')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.argument('app_name')
@click.option('--pipeline-name', default=None,
              help="The pipeline a webhook for this app may start, e.g. build-nginx-example")
@click.option('--trigger-type', default=None,
              help="Which git host's templates to generate from: github or gitlab")
@kmt_context
def generate_trigger_configs(ctx, environment, team, app_name, pipeline_name, trigger_type):
    # Read the note on `generate_deploy_pipeline` — same reason, same shape.
    generation.generate_trigger_configs(TektonUtils(ctx).config,
                                        app_name, pipeline_name=pipeline_name,
                                        trigger_type=trigger_type)

# --------------------------------------------
# GENERATE PIPELINE RUN
@click.command(name='tekton-generate-pipeline-run',
               help='Generate a pipeline run for a pipeline the checkout has, into the team\'s own '
                    'pipeline-run directory that tekton-run-pipeline then reads. Review it: the '
                    'parameters that belong to the app rather than to the platform are written as '
                    'TODO, because kubementat cannot know them.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('team', envvar='TEAM')
@click.argument('pipeline_file')
@kmt_context
def generate_pipeline_run(ctx, environment, team, pipeline_file):
    # Read the note on `generate_deploy_pipeline` — same reason, same shape.
    generation.generate_pipeline_run(TektonUtils(ctx).config, pipeline_file)

# --------------------------------------------
# UNINSTALL PIPELINES
@tekton.command(name='uninstall-pipelines',
                help='Uninstall tekton-pipelines and tasks, including the teams pipeline namespace')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.option('--yes', '-y', is_flag=True, help="Do not ask for user input. For scripting purposes.")
@kmt_context
def uninstall_pipelines(ctx, environment, team, yes):
    if yes:
        uninstall_pipelines_slice.uninstall_pipelines(ctx)
        exit(0)
    
    yn = input(f"Do you really wish to uninstall pipeline and task configuration for env: {environment} and team: {team} on this cluster? (yes/no): ")
    if yn.lower() in ['yes', 'y']:
        uninstall_pipelines_slice.uninstall_pipelines(ctx)
    else:
        print("Cancelled pipeline/task config deletion.")
        exit(1)
