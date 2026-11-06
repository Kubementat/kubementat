"""`kmt tunnel` -- the port-forwards kubementat opens, and the one it cannot name for you.

The four tunnels the lane measures are grouped here, and the shape is the one `kmt users`,
`kmt routing` and `kmt component` already have: `tunnel` says what kind of command this is, the verb
says what it reaches. Four flat `tunnel-*` lines in `kmt --help` said the same thing four times, and
the thing a reader actually compares is the target -- `pod`, `grafana`, `tekton`, `openbao-ui`.

**Renames, no aliases**: `kmt tunnel-grafana` is gone rather than answered twice.

`tunnel-polaris` and `tunnel-mysql` are **retired**, not grouped (row 25). Neither had a lane
capability, so nothing measured them and nothing turns red when they go; the owner decision of
2026-10-30 was to delete exactly those two and leave every other command's layout alone. What keeps
them out is `tests/test_the_polaris_and_mysql_tunnel_commands_stay_retired.py` -- a deletion without
a guard is how a command comes back.
"""

import click

from kubementat.automation import Automation
from kubementat.errors import MysqlNotConfiguredError
from kubementat.errors import PodNotFoundError
from kubementat.infrastructure.kubernetes import KubernetesUtils
from cli.context import kmt_context

UTILITIES_SUB_DIRECTORY='scripts/utilities'


@click.group(name='tunnel',
             help='Open a network tunnel to a component kubementat runs, or to any pod you name.')
def tunnel():
    pass



def open_tunnel(function, *args):
    """Run a tunnel helper, and make a tunnel that did not open fail the command.

    Two halves, both learned from a failure rather than designed in:

    * "the component is not installed" becomes a readable CLI error (exit 1) instead of a
      traceback -- that is what `PodNotFoundError` is for.
    * a non-zero return code becomes a non-zero exit. These commands used to `return` the code,
      and Click ignores return values, so a `kubectl port-forward` that refused a port someone
      else already had printed its complaint and exited 0. The operator saw "tunnel open" in the
      log line above it and connected to the wrong database. A tunnel that is not a tunnel has to
      fail, whatever it prints. (Same rule `ci_utils` carries: an interactive command is judged
      by the code it exits with, not by the fact that it ran.)
    """
    try:
        result = function(*args)
    except (PodNotFoundError, MysqlNotConfiguredError) as error:
        raise click.ClickException(str(error))
    if result:
        raise click.exceptions.Exit(result)
    return 0

##########################
#### TUNNEL SCRIPTS ######
##########################

# These three reach a component by namespace and label, and every key that decides which namespace
# that is (`*_DEPLOYMENT_NAMESPACE`, `*_SERVICE_SELECTOR`) is read from the *environment's* own
# `static.json`. None of them reads one thing under `platform_config/{environment}/<team>/`, which is
# why they used to carry a `--team` they never used and echo `TEAM: None` at an operator who had
# named one. A command which cannot act on a team does not accept one, so the
# option is deleted rather than silenced: a flag that resolves to nothing is how the tunnels came to
# read `dev1`, and an option that does nothing is still a thing an operator can be wrong
# about. The `TEAM:` echo goes with it — printing a value the command was not given, or was given and
# did not use, is the same noise wearing a label. The cluster the forward is opened against is still
# resolved and reported, because that *is* what these commands are asked.

# Tunnel grafana
@tunnel.command(name='grafana', help='open a network tunnel to the grafana UI')
@click.argument('environment', envvar='ENVIRONMENT')
@click.option('--local-port', default='3001')
@click.option('--remote-port', default='3000')
@click.option('--host', default='0.0.0.0')
@kmt_context
def tunnel_grafana(ctx, environment, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    automation = Automation(ctx)
    click.echo("########################")
    click.echo(f"See GRAFANA_ADMIN_USER and GRAFANA_ADMIN_PASSWORD environment variables within platform_config/{environment}/static.encrypted.json")
    click.echo("########################")
    return open_tunnel(automation.open_tunnel_grafana, local_port, remote_port, host)

# Tunnel tekton
@tunnel.command(name='tekton', help='open a network tunnel to the tekton UI')
@click.argument('environment', envvar='ENVIRONMENT')
@click.option('--local-port', default='9097')
@click.option('--remote-port', default='9097')
@click.option('--host', default='0.0.0.0')
@kmt_context
def tunnel_tekton(ctx, environment, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    automation = Automation(ctx)
    return open_tunnel(automation.open_tunnel_tekton_dashboard, local_port, remote_port, host)

# Tunnel OpenBao UI
# NOTE: this command was once defined as `def tunnel_polaris` as well, which silently replaced the
# polaris command (Python keeps the last definition) and left `kmt tunnel-polaris` unregistered.
# Each command gets its own name; the polaris command itself is retired (row 25), so this is the
# only tunnel here that ever had that collision. The target it reaches is OpenBao now, and so is
# the verb's name: `kmt tunnel vault-ui` answered for a component kubementat does not install.
@tunnel.command(name='openbao-ui', help='open a network tunnel to the openbao UI')
@click.argument('environment', envvar='ENVIRONMENT')
@click.option('--local-port', default='8205')
@click.option('--remote-port', default='8200')
@click.option('--host', default='0.0.0.0')
@kmt_context
def tunnel_openbao_ui(ctx, environment, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    automation = Automation(ctx)
    return open_tunnel(automation.open_tunnel_openbao, local_port, remote_port, host)


# A tunnel to any pod, which is exactly what `open_pod_tunnel.sh` was: namespace, pod and both
# ports were arguments it forwarded, so there is nothing here to resolve from configuration --
# the operator names the target and kubectl addresses it. That is why the script is deleted
# rather than migrated: this command already reached the same `kubectl port-forward`, and a
# second generic tunnel command would be a second way to be wrong about the same pod.
# The environment is a positional and not an assumption: which cluster the forward is opened
# against is the operator's decision, and it is the one thing the script could not say.
@tunnel.command(name='pod', help='open a network tunnel to the specified pod')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('namespace',envvar='NAMESPACE')
@click.argument('pod-name',envvar='POD_NAME')
@click.option('--local-port',required=True)
@click.option('--remote-port', required=True)
@click.option('--host', default='0.0.0.0')
@kmt_context
def tunnel_pod(ctx, environment, namespace, pod_name, local_port, remote_port, host):
    click.echo(f"ENVIRONMENT: {environment}")
    click.echo(f"NAMESPACE: {namespace}")
    click.echo(f"POD_NAME: {pod_name}")
    # No team is read here: a port-forward is decided by the namespace and the pod name, not by
    # anything under platform_config/<environment>/<team>/. The Context is built anyway, because it
    # says which cluster the forward is opened against — and because the uniformity of one
    # `Context` type is what keeps the tunnel from resolving a cluster of its own. Nothing on this
    # path calls `require_team`, so a team-less run is not refused, it is simply not asked.
    utils = KubernetesUtils(ctx)
    return open_tunnel(utils.open_tunnel, namespace, pod_name, local_port, remote_port, host)
