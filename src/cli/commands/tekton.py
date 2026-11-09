"""`kmt tekton` -- the pipelines kubementat runs, sets up, lists and takes away.

The grouping of this surface is not a tidy-up: `tekton-run-pipeline`, `tekton-list` and the
rest were already lines that all started with the same word, and the word was not the question.
Under `kmt tekton` the verb comes first -- `run-pipeline`, `list`, `cleanup-pipeline-runs` -- which
is the shape `kmt users`, `kmt routing` and `kmt component` already have.

**Renames, no aliases.** The old flat names are gone rather than shadowed: no hidden command, no
`deprecated` branch, no "did you mean" translator. This file is where that rule bites
hardest, because `kmt tekton run-pipeline` is what a Tekton Task runs
(`tekton_ci/tasks/run-tekton-pipeline.yml`) -- a command that answers to two names is a Task that can
silently keep running the old one, which is the only way this migration has of making a lie out of a
capability report.

All ten commands here are on the group. They were not: the cleanup job and the three generators kept
their flat `tekton-*` names beside the group, on the argument that no marker measured them and a
rename nothing measures is a rename nobody can tell the difference about. That argument only ever
applied while the rest of the surface was flat beside it; once `kmt tekton` existed, four commands
whose first word was already `tekton` answering to a flat name was a surface that said one thing and
did another, so they are verbs on the group now -- `setup-cleanup-job`, `generate-deploy-pipeline`,
`generate-triggers`, `generate-pipeline-run` -- and their flat names are dead in the same way the
first six's were.

What the move did not touch is what each command takes. `setup-cleanup-job` still wants the
environment as a positional and the team as `--team`, because it addresses one namespace and no
team-scoped path; the three generators still take environment, team and their own positional. An
argv that means the same thing in two shapes is the defect grouping is supposed to remove, not
introduce.
"""

import click

from cli.context import kmt_context
from cli.helpers import team_option
from kubementat.automations.install import pipelines as install_pipelines_slice
from kubementat.automations.uninstall import pipelines as uninstall_pipelines_slice
# What the class that used to sit between these commands and the Tekton modules carried — the
# client, the configuration, the environment, the team and the namespace the objects are written
# into — is read off the `Context` now, and where a function needs a piece of it the piece is
# passed. The team refusal is not here: it moved into the functions that read a team-scoped
# namespace, so a library caller is refused by the module and not by this file.
from kubementat.infrastructure.kubernetes import KubernetesUtils
# The split's modules are reached as modules, never by name: the seam the suite patches is the
# module the code actually runs, and a name imported past its home is a second binding.
from kubementat.infrastructure.tekton import generation, runs, triggers


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
    # What the run path reads is what a `Context` answers: the client it applies with, the
    # configuration the namespace comes out of, and the team whose namespace that is. The refusal
    # for a run that named no team is not asked of this command -- `runs.run_pipeline` asks it as
    # its first statement, ahead of the file read and the apply, so a caller that never came
    # through `kmt` is refused by the same sentence.
    if not pipeline_run_identifier:
        # No identifier is the listing, and the listing is the one thing on this command that
        # applies nothing. It still reads a team-scoped directory, so it still refuses a run that
        # named no team; the guard that used to make that refusal happen before this line is the
        # class that is gone.
        runs.list_available_pipeline_runs(ctx.config, ctx.team)
        return 0

    started = runs.run_pipeline(ctx, KubernetesUtils(ctx), ctx.config, ctx.environment,
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
        # The namespace is not spelled out here: `wait_for_pipeline_run` reads the team's
        # `PIPELINE_NAMESPACE` itself when the caller does not name one, which is the same read
        # `run_pipeline` just applied into. Two answers to one namespace question is the defect
        # every namespace row of the census recites.
        raise SystemExit(runs.wait_for_pipeline_run(
            KubernetesUtils(ctx), ctx.config, started,
            poll_interval_seconds=poll_interval_seconds, timeout_seconds=timeout_seconds))

# --------------------------------------------
# LIST TEKTON RESOURCES
@tekton.command(name='list', help='List tekton resources')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@kmt_context
def list(ctx, environment, team):
    # The client comes from the `Context` and the namespace from the module: `runs` already reads
    # the team's `PIPELINE_NAMESPACE` for the four listings that share it, and spelling the read
    # again here would be a second answer to one namespace question.
    runs.list_all_tekton_resources(KubernetesUtils(ctx), ctx.config)

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
    runs.cleanup_pipeline_runs(KubernetesUtils(ctx), ctx.config, filter=filter)

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
    # The trigger flow is the one call on this surface that needs the raw `Context` as well as the
    # client: the last half applies the team's directory through a `KubernetesUtils` it builds from
    # a `Context`, and building it from anything else says which cluster those manifests land in.
    # The team refusal is the function's own now -- it is asked before the first RBAC object is
    # written, which is where `install`'s `--yes`-style library caller learns about it too.
    triggers.setup_triggers(ctx, KubernetesUtils(ctx), ctx.config, ctx.environment)

# --------------------------------------------
# SETUP TEKTON CLEANUP JOB
# The team stays an option and not a positional here, which is the one shape on this group that
# differs from its neighbours: this command creates nothing in a team's own directory and writes one
# cluster-scoped CronJob, so the team is the credential the job pulls with and not a directory it
# picks.
@tekton.command(name='setup-cleanup-job',
                help='Setup the cronjob that prunes completed pipelineruns, in the namespace the '
                     'environment config names for cluster cronjobs')
@click.argument('environment',envvar='ENVIRONMENT')
@team_option()
@kmt_context
def setup_cleanup_job(ctx, environment, team):
    # The namespace question is asked of `config` by the function and the client comes from the
    # `Context`, so the pair is what this command hands over. The team the `Context` was given is
    # what the function refuses a run without -- asked before the namespace it creates, not one
    # operation later on the way to the pull Secret.
    runs.setup_cleanup_job(ctx, KubernetesUtils(ctx), ctx.config, ctx.environment)

# --------------------------------------------
# GENERATE DEPLOY PIPELINE
@tekton.command(name='generate-deploy-pipeline',
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
    # The generation half moved out of the class (P4-1), and the object that used to be built here
    # only ever reached `Config` -- which is the `Context`'s, so it is read off that now. Nothing
    # else about the call changed, and nothing about the refusal changed either: a generated
    # manifest is one team's configuration, so the first thing a generator touches is a
    # team-scoped path (`Config.tekton_team_pipeline_dir`), and a `Config` built for no team
    # refuses that read by name before a byte is written. The `None` that would otherwise land in
    # the manifest's team fields is never written, because it is never reached.
    generation.generate_deploy_pipeline(
        ctx.config,
        app_name, git_url=git_url, git_project_name=git_project_name,
        git_server_host=git_server_host, git_server_port=git_server_port,
        git_server_ssh_user=git_server_ssh_user)

# --------------------------------------------
# GENERATE TRIGGER CONFIGURATION
@tekton.command(name='generate-triggers',
                help='Generate an app\'s webhook trigger manifests from the git host templates, into '
                     'the team\'s trigger directory that `kmt tekton setup-triggers` then reads.')
@click.argument('environment',envvar='ENVIRONMENT')
@click.argument('team',envvar='TEAM')
@click.argument('app_name')
@click.option('--pipeline-name', default=None,
              help="The pipeline a webhook for this app may start, e.g. build-nginx-example")
@click.option('--trigger-type', default=None,
              help="Which git host's templates to generate from: github or gitlab")
@kmt_context
def generate_trigger_configs(ctx, environment, team, app_name, pipeline_name, trigger_type):
    # Read the note on `generate_deploy_pipeline` — same reason, same shape, and the same
    # first read: `generate_trigger_configs` opens with `_refuse_obsolete_team_trigger_dir`, which
    # asks `Config` for the team's trigger directory and is refused first.
    generation.generate_trigger_configs(ctx.config,
                                        app_name, pipeline_name=pipeline_name,
                                        trigger_type=trigger_type)

# --------------------------------------------
# GENERATE PIPELINE RUN
@tekton.command(name='generate-pipeline-run',
                help='Generate a pipeline run for a pipeline the checkout has, into the team\'s own '
                     'pipeline-run directory that `kmt tekton run-pipeline` then reads. Review it: the '
                     'parameters that belong to the app rather than to the platform are written as '
                     'TODO, because kubementat cannot know them.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('team', envvar='TEAM')
@click.argument('pipeline_file')
@kmt_context
def generate_pipeline_run(ctx, environment, team, pipeline_file):
    # Read the note on `generate_deploy_pipeline` — same reason, same shape, and the target file is
    # the team's own pipeline-run directory, so the refusal lands before the document is written.
    generation.generate_pipeline_run(ctx.config, pipeline_file)

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
