"""`kmt helm` -- what helm does for an environment, and the newest version of one chart.

The grouping put this surface under `helm`, and `helm` is the group where a name that already said
*how* it is done could hide the thing it does: `helmfile-apply` is one command out of a family of
helm reads and applies, and it is the apply half of a group that now has a place to sit next to the
others.

**The rename is a rename.** `kmt helmfile-apply` is gone rather than aliased, by the rule against
aliases and by the grouping's own rule: a command with two names is a command with two answers, and
this one
deletes and re-creates releases. The pinned future argv lives in the lane's capability table
(`tests/e2e/harness/config.py`, row `helm.helmfile-apply`), and it says `helm apply` -- the verb is
the answer, `helmfile` is the mechanism, and the `--helmfile-*` options on the command keep naming
the mechanism for anyone who needs it.

`helm-version` is deliberately not here: the grouping's list is the 11 commands the lane measures
as still flat, and that read is not one of them. It stays flat rather than joining a group it was
never asked to join, which is the honest half of a partial grouping -- see the report.
"""

import sys

import click

from kubementat.helm_utils import HelmUtils
from cli.context import kmt_context


@click.group(name='helm',
             help='Apply and read the helm releases an environment runs, through helmfile.')
def helm():
    pass

# The latest version of a chart, printed and nothing else. It is named `helm-version`
# rather than `helm-latest-version` because the surface says "what is the version", and the
# answer is always the newest one helm can see -- an option for "give me 1.x" would be a second
# command, and the script it replaces had the same single question.
#
# No `--team`, and the reasoning is the same one applied one layer down: `get_latest_chart_version`
# runs `helm repo list` and `helm search`, reads no `Config` category at all, and never asks what a
# team is. An option here was a question the command cannot answer, and a command that cannot
# act on a team does not accept one.
@click.command(name='helm-version',
               help='Print the newest version of a helm chart that this machine can see.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('chart_path')
@kmt_context
def helm_version(ctx, environment, chart_path):
    # The argument keeps the script's shape (`repo/chart`) because that is what a reader of the
    # old usage line already knows, and because a chart has one address. Where it is split is
    # this command's business: HelmUtils answers a repo and a chart separately, so that a
    # failure can say which half was wrong -- an unadded repo is a different answer from an
    # unknown chart, and it has to be, because the fixes are `helm repo add` and a typo.
    #
    # The environment is the first positional: this command reads nothing from a cluster, but it
    # answers the question *for* an environment, and the rule admits no exception for the commands
    # that turn out not to have needed one.
    repo, _separator, chart = chart_path.partition("/")
    if not chart:
        raise click.ClickException(
            f"'{chart_path}' does not name a chart. The form is "
            f"`kmt helm-version <ENVIRONMENT> <repo>/<chart>`, e.g. "
            f"`kmt helm-version dev prometheus-community/prometheus`.")
    click.echo(f"environment: {environment}")
    version = HelmUtils(ctx).get_latest_chart_version(chart, repo)
    click.echo(version)


# No `--team` here either: everything `helmfile_apply` asks of configuration is environment-level
# -- `env_static_encrypted` for grafana's admin pair, `component_namespace` for every release
# namespace, and the packaged manifest `helmfile_paths` names for the releases themselves. `grep -n team_static src/kubementat/helm_utils.py` returns nothing, so the option had
# no code behind it, and a `require_team` that used to stand in `HelmUtils.__init__` made
# `kmt helm apply dev` refuse a release it was about to apply from one file. What the operator is
# told instead is the environment and the filter, both of which are what this command decides.
@helm.command(name='apply', help='Apply helmfile.yaml.gotmpl for the environment with helmfile.')
@click.argument('environment',envvar='ENVIRONMENT')
@click.option('--helmfile-label-filter', default='group=standard', help="e.g. group=standard or component_name=openbao")
@click.option('--yes', '-y', is_flag=True,
              help="Apply without asking. Defaults to asking when stdin is a terminal, and to not asking when it is not.")
@click.option('--helmfile-working-directory', default=None, help="The directory where helmfile cli should be executed")
@click.option('--helmfile-path', default=None, help="The path to the helmfile.yaml.gotmpl file to be applied")
@kmt_context
def helmfile_apply(ctx, environment, helmfile_label_filter, yes, helmfile_working_directory=None, helmfile_path=None):
    # helmfile prints the diff and then asks before it applies it. Asking is only a courtesy
    # when somebody is there to answer: on a pipe or under `< /dev/null` helmfile reads the
    # end of input as a refusal and exits 1, which is why every scripted use of this command
    # used to fail. So the decision is made here, where the terminal is visible, and never in
    # HelmUtils - which appends `--interactive` only when it is told to.
    interactive = sys.stdin.isatty() and not yes
    utils = HelmUtils(ctx)
    click.echo(f"environment: {environment}")
    click.echo(f"helmfile-label-filter: {helmfile_label_filter}")
    click.echo(f"interactive: {interactive}")
    utils.helmfile_apply(
      environment_name = environment,
      helmfile_label_filter = helmfile_label_filter,
      interactive=interactive,
      helmfile_working_directory=helmfile_working_directory, 
      helmfile_path=helmfile_path
    )
