"""`kmt users` - the team accounts kubementat hands its teams.

These were `scripts/user_management/create_team_account.sh` and friends, invoked by handing
the path to a runner, which is why the lane could only reach them by shell: the
feature had no command of its own. The account is now a command because it is a thing an
operator does, and because running it that way passed the confirmation straight through to a script
that read stdin itself - so a lane run with no terminal got EOF, and `set -e` turned that
into an abort with no message.

The confirmation contract is the one `kmt uninstall` established: without `--yes` and without
a terminal to ask in, nothing is created. `--yes` is the scripted escape hatch.
"""

import logging
import sys

import click

from cli.context import kmt_context
from cli.helpers import team_option
import kubementat.infrastructure.grafana.accounts as grafana_accounts
from kubementat.automation import Automation
from kubementat.errors import GrafanaError


def _environment_option():
    # No default, and that is the whole point: `--environment` says which
    # platform_config/{environment} this command edits, so a run that names none is refused by
    # `cli.context._environment_of` rather than pointed at whichever directory the checkout
    # happens to have. The envvar stays, because that is how a lane names it.
    return click.option('--environment', envvar='ENVIRONMENT',
                        help="Environment whose platform_config this command edits. Required, "
                             "like every command that reaches a cluster; also reads ENVIRONMENT.")


def _yes_option():
    return click.option('--yes', '-y', is_flag=True,
                        help="Do not ask for user input. For scripting purposes.")


def _grafana_url_option():
    return click.option('--grafana-url', default=None, metavar='URL',
                        help="Address grafana answers on. Without it kubementat reads the "
                             "grafana service's ClusterIP through GRAFANA_SERVICE_SELECTOR, and "
                             "fails rather than guess one: the scripts this replaces defaulted to "
                             "http://127.0.0.1:31827, which is a port-forward nobody opens.")


def _require_confirmation(command: str, yes: bool) -> None:
    """Fail closed when there is nobody there to answer.

    Same shape as `kmt uninstall`: the guard is on the CLI, so a lane or a cron job cannot
    reach a script that reads stdin, answers itself with EOF, and carries on.
    """
    if not yes and not sys.stdin.isatty():
        raise click.UsageError(
            f"{command} requires confirmation, but stdin is not a terminal. "
            "Re-run with --yes (-y) to run it without prompting.")


# ---------------------------------------------------------------------------------------
# TEAM ACCOUNTS
# ---------------------------------------------------------------------------------------

@click.group(name='users', help='Manage the team accounts kubementat gives its teams.')
def users():
    pass


@users.command(name='create-team-account',
               help='Create a team account that can do anything in the team namespaces, and '
                    'print the kubeconfig to paste into ~/.kube/config.')
@click.argument('service-account-name')
@_environment_option()
@team_option()
@_yes_option()
@kmt_context
def create_team_account(ctx, service_account_name, environment, team, yes):
    _require_confirmation('kmt users create-team-account', yes)
    Automation(ctx).create_team_account(service_account_name, assume_yes=yes)


@users.command(name='create-team-account-read-only',
               help='Create a team account that can read everything and change nothing.')
@click.argument('service-account-name')
@_environment_option()
@team_option()
@_yes_option()
@kmt_context
def create_team_account_read_only(ctx, service_account_name, environment, team, yes):
    _require_confirmation('kmt users create-team-account-read-only', yes)
    Automation(ctx).create_team_account_read_only(service_account_name, assume_yes=yes)


@users.command(name='delete-team-account',
               help='Delete a team account, its token, and every binding that let it act.')
@click.argument('service-account-name')
@_environment_option()
@team_option()
@_yes_option()
@kmt_context
def delete_team_account(ctx, service_account_name, environment, team, yes):
    _require_confirmation('kmt users delete-team-account', yes)
    Automation(ctx).delete_team_account(service_account_name, assume_yes=yes)


# ---------------------------------------------------------------------------------------
# GRAFANA ACCOUNTS
# ---------------------------------------------------------------------------------------
# Both commands were `scripts/user_management/create_grafana_accounts.sh`,
# `create_grafana_read_only_account.sh` and the `grafana_helpers.sh` library they sourced, run
# by handing their paths to a runner. They are commands now for the reason the whole file exists: the
# lane could only reach them by shell, and the row that came out was green while nothing was
# created. The client that makes them answer is `kubementat.infrastructure.grafana.client`; the address it is
# handed is resolved by `kubementat.infrastructure.grafana.accounts`, whose docstring is the precedence.
#
# Neither takes `--team`, and it is not a symmetry choice: the rows, the credential the client
# authenticates with and the address it connects to are all read out of the *environment's* own
# `static.encrypted.json` -- `grafana_accounts.grafana_users_rows` and
# `GrafanaClient.from_environment` both go through `Config.get('env_static_encrypted')` and never
# ask for a team file. Two `require_team` guards refused these commands before the first request
# went out, and no code behind them had a team read to protect.


def _report_grafana_accounts(outcomes) -> None:
    """Say, per row, whether this run made the account or confirmed it.

    The bash printed one thing only -- "the user already exists, skipping" -- and printed it for
    an empty response too. `created` is the difference between a run that made an account and a
    run that read one, so it is printed; so is a password this run generated and nothing stored,
    because an operator needs to write it down and a config that says nothing is not a place to
    store it.
    """
    if not outcomes:
        click.echo("GRAFANA_USERS declares no accounts for this environment, so nothing was "
                   "created. Nothing failed either, which is the honest version of the same run.")
        return

    for row, account in outcomes:
        if account.created:
            click.echo(f"created grafana account {account.user.email} "
                       f"(user {account.user_id}, {account.user.name})")
            if account.password:
                click.echo(f"  generated password (this run made it, nothing stored it): "
                           f"{account.password}")
        else:
            click.echo(f"grafana account {row.email} already exists "
                       f"(user {account.user_id}); nothing changed and no password was applied")
        if account.created and not account.password:
            # Cannot happen with this flow's client, but a report that lies about a credential is
            # worth an assert-shaped line rather than a shrug.
            logging.warning("grafana account %s was created without a password being reported", row.email)


@users.command(name='create-grafana-accounts',
               help='Create every grafana account the environment\'s static.encrypted.json '
                    'declares, over HTTP, and report which of them this run made.')
@click.argument('environment', envvar='ENVIRONMENT')
@_grafana_url_option()
@_yes_option()
@kmt_context
def create_grafana_accounts(ctx, environment, grafana_url, yes):
    _require_confirmation('kmt users create-grafana-accounts', yes)
    try:
        outcomes = grafana_accounts.create_grafana_accounts(
            ctx,
            # The `Context` is handed down rather than the environment and the team, because this
            # command resolved the cluster and `grafana_accounts` must not resolve it again: a
            # module two levels down taking `(environment, team)` and reading a `Config` of its own
            # is the last place in `src/` where that rule was not simply true.
            grafana_url=grafana_url)
    except GrafanaError as error:
        # No exception escapes to a traceback here: everything this can fail on is something an
        # operator can read and fix -- a missing key, a selector that matches nothing, grafana
        # refusing the credential. A script could not say that, because the failure was
        # already lost by the time bash saw it.
        raise click.ClickException(str(error))
    _report_grafana_accounts(outcomes)


@users.command(name='create-grafana-read-only-account',
               help='Create one grafana account, the way the read-only script did. Note that the '
                    'script set no role, so this does not grant one either; see the plan.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('email')
@click.argument('name', required=False, default='')
@_grafana_url_option()
@_yes_option()
@kmt_context
def create_grafana_read_only_account(ctx, environment, email, name, grafana_url, yes):
    _require_confirmation('kmt users create-grafana-read-only-account', yes)
    try:
        account = grafana_accounts.create_read_only_account(
            ctx, email, name, password=None, grafana_url=grafana_url)
    except GrafanaError as error:
        raise click.ClickException(str(error))
    if account.created:
        click.echo(f"created grafana account {account.user.email} (user {account.user_id}, "
                   f"{account.user.name})")
        if account.password:
            click.echo(f"  generated password (this run made it, nothing stored it): "
                       f"{account.password}")
        click.echo("  no org role was set, so the org's default applies -- that is what the "
                   "script did, and granting Viewer is a decision this commit did not make")
    else:
        click.echo(f"grafana account {account.user.email} already exists "
                   f"(user {account.user_id}); nothing changed and no password was applied")
