"""`kmt routing` -- the ingress controller kubementat installs, read before it is changed.

Routing is its own command group because it is its own decision: `kmt install` does not install
Traefik, does not take `--update`, and passes nothing through (plan decision 1). The two commands on
the group are the two halves of the same question -- what is on the cluster, and whether to change
it -- and the order they are read in is the order they are run in: `status` before `install
--update`, because the report already names the flag that would change the answer.

**One command takes `--yes` and the other must not.** The confirmation contract belongs to commands
that write -- `kmt uninstall`, `kmt users create-team-account`. `status` runs three reads and has
nothing to ask, so giving it the flag would put a mutating-command signal on a command that cannot
mutate. `install` writes, so it takes the flag, and the one branch that needs consent (a foreign
controller being overwritten) fails closed naming it when there is no terminal to ask in. The rule
that decides which half applies is stated on the commands rather than assumed from the group name.

The environment arrives the way every other command takes it, as an argument that reads the
`ENVIRONMENT` variable.
"""

import click

from cli.context import kmt_context
from kubementat.automations.routing import routing as routing_install
from kubementat.automations.routing import traefik_presence


@click.group(name='routing',
             help='Read and install the routing kubementat installs: the traefik controller and '
                  'the cert-manager that fronts it. Not part of kmt install -- routing is applied '
                  'here, or not at all.')
def routing():
    pass


@routing.command(name='status',
                 help='Read the cluster and report which Traefik it is running, whose it is, and '
                      'what kmt routing install would do with it. Read-only: nothing is installed, '
                      'changed, or deleted, so there is no flag that skips a confirmation.')
@click.argument('environment', envvar='ENVIRONMENT')
@kmt_context
def status(ctx, environment):
    # The whole command is one call and one print, for the reason the report exists: the answer is
    # the product here, and a CLI that re-summarised it is a second place that could disagree with
    # the cluster.
    #
    # No `--team`, because the rule says so and not for symmetry with `install`: discovery is three
    # reads of the cluster plus one namespace read out of the environment's `static.json` - it never
    # resolves anything under `platform_config/{environment}/<team>/`. A flag here would be a
    # question the command cannot answer and the operator cannot decline.
    presence = traefik_presence.discover(ctx)
    click.echo(presence.report())


@routing.command(name='install',
                 help='Apply the routing group -- the traefik controller, the cert-manager that '
                      'fronts it, and the two ClusterIssuers -- installing Traefik only when the '
                      'cluster has none. A controller that is already there is left alone and named, '
                      'and the rest of the group is applied either way, because a skip that '
                      'suppressed the group would take the platform issuer down with it. This is not '
                      'part of kmt install, and kmt install takes neither routing nor --update.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.option('--yes', '-y', is_flag=True,
              help="Do not ask for confirmation. On a cluster that already runs a Traefik "
                   "kubementat did not install, it is also the answer to the second-controller "
                   "warning; without it and without a terminal to ask in, nothing is applied.")
@click.option('--update', is_flag=True,
              help="Upgrade Traefik in place instead of leaving it alone, applying the chart's "
                   "CustomResourceDefinitions before the release -- the half Helm will not do on "
                   "its own. On a foreign controller this installs kubementat's own release next to "
                   "it, which means a second controller, class and status writer.")
@kmt_context
def install(ctx, environment, yes, update):
    # The whole command is one call and one print, for the reason `install_routing` returns a
    # value: the CLI, a later caller and the log must read the same decision. Everything that
    # decides -- the discovery, the table, the ordering of the CRD step, the consent rule -- is
    # behind this line, so `kmt routing install` cannot drift from a caller that uses the library.
    #
    # No `--team`, and this is the same finding `status` made one layer down. What the
    # release, the issuers and the controller land in is `Config.component_namespace("traefik")`,
    # which is `env_static`, and the only other key read on the path is
    # `CLUSTER_MANAGER_EMAIL`, which is `env_static` too: nothing reachable from here reads
    # `team_static`, uses the team as an argv namespace, or interpolates it into a name. The rule is
    # explicit that a command which cannot act on a team does not accept one, so the option goes -
    # which is also what makes the e2e harness's `kmt routing install <env> --yes` correct rather
    # than a workaround.
    result = routing_install.install_routing(ctx, update=update, assume_yes=yes)
    click.echo(result.summary())
