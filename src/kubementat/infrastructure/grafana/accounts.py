"""The grafana accounts a platform_config environment declares, created over real HTTP.

This is `scripts/user_management/create_grafana_accounts.sh` (40 LOC) and
`create_grafana_read_only_account.sh` (52), with `grafana_helpers.sh` (69) as the library they
sourced. All three are deleted by this commit; the client they needed landed as
`infrastructure/grafana/client.py`, and these two verbs as the module you are reading, so the only
thing missing was a caller and an address.

That second half is the ticket. The bash resolved an address by *guessing* one:

* `GRAFANA_BASE_URL` defaulted to `http://127.0.0.1:31827`, a port-forward nobody opens;
* the `curl` ran inside a `$( )`, where `set -e` does not look;
* an empty body made `jq '.users[0]'` exit **0** and print nothing;
* and `create_new_user_for_team` read that nothing as "the user already exists, skipping" and
  exited 0.

Four lost steps, and the capability row that came out of them was green. So the address is
**resolved, never assumed**, in this precedence and no other:

1. `--grafana-url` on the command, which is what an operator who opened their own tunnel uses;
2. grafana's service ClusterIP, read through the same `Config` selector `kmt tunnel grafana`
   uses (`component_service_selector('grafana')` + `component_namespace('grafana')`, resolved by
   `KubernetesUtils.get_service_endpoint`) -- a ClusterIP is routable from where these commands
   run, which the in-cluster DNS name is not;
3. an exception that names the flag and the selector, and refuses to run.

There is no default, and no silent success behind it: a Grafana that cannot be reached fails the
command, because "the config has no address and nothing is listening" is not a fact a flow gets
to report as done.

Two behaviours were carried over rather than "fixed", and both are recorded as open product
questions in `docs/plans/finish-python-migration.md`:

* **The read-only account gets no role.** The script's name says read-only; its `create_user`
  payload never mentioned a role, so the account got the org's default. `create_read_only_account`
  on the client would grant `Viewer`; this module calls `ensure_account(org_role=None)` instead,
  which is what the script did. Starting to grant `Viewer` is a decision for the owner, not a
  side effect of a migration.
* **No team membership is ever created.** The helper took a `team_id`, threaded it into
  `create_user`, and dropped it into a payload that did not mention it, so nobody was ever added
  to anything. `GRAFANA_USERS` rows carry email, name and password, so there is nothing to add a
  membership from, and `GrafanaClient.add_user_to_team` ships with no caller. Adding a `--team-id`
  argument to a command whose config cannot supply one would be inventing behaviour.
"""

import logging
from typing import Any, Optional

from kubementat.core.config.config import Config
from kubementat.errors import GrafanaConfigurationError
from kubementat.infrastructure.grafana.client import GrafanaAccount, GrafanaClient
from kubementat.infrastructure.kubernetes import KubernetesUtils

# The key whose rows this flow creates. Same file the admin credentials come from, because a
# list of accounts and the pair that installs them are both environment-level facts. The
# component is named inline at the two places that need a namespace and a selector, exactly the
# way `automations/tunnels/tunnels.py` does, so this module adds no namespace-shaped constant of its
# own.
GRAFANA_USERS_KEY = "GRAFANA_USERS"


class GrafanaAccountRow:
    """One `GRAFANA_USERS` entry, as the config actually writes it.

    `email` is required, because it is both the login and the unique index Grafana enforces.
    `name` is not: the bash interpolated a missing one as the string `"null"` and created an
    account named "null", so a missing name now falls back to the email rather than to the word.
    `password` is `None` when the row declares none -- which is not the same as an empty password,
    and the difference is what the client's password generation turns on.
    """

    def __init__(self, email: str, name: str, password: Optional[str] = None):
        self.email = email
        self.name = name
        self.password = password

    def __repr__(self) -> str:
        return f"<GrafanaAccountRow {self.email!r}>"

    @classmethod
    def from_payload(cls, payload: Any, environment: str, location: str) -> "GrafanaAccountRow":
        if not isinstance(payload, dict):
            raise GrafanaConfigurationError(
                f"{GRAFANA_USERS_KEY} in {location} contains {type(payload).__name__} "
                f"({repr(payload)[:80]}), where every entry is supposed to be an object with an "
                f"email, a name and an optional password. Nothing was created.")
        email = str(payload.get("email") or "").strip()
        if not email:
            raise GrafanaConfigurationError(
                f"An entry in {GRAFANA_USERS_KEY} in {location} names no email: "
                f"{payload!r}. Grafana identifies an account by its address, so a row without one "
                f"is not an account that is missing a field, it is an account that cannot exist.")
        name = str(payload.get("name") or "").strip()
        if not name:
            logging.warning("%s row %s declares no name, so the account is named after its email. "
                            "The bash put the literal string 'null' there, which is the only "
                            "behaviour worth not replacing.", email, GRAFANA_USERS_KEY)
        password = str(payload.get("password") or "").strip()
        return cls(email=email, name=name or email, password=password or None)


def grafana_users_rows(config: Config, environment: str) -> list:
    """The rows to create, validated, from the environment's `static.encrypted.json`.

    The same file `HelmUtils` installs grafana from and `GrafanaClient.from_environment` reads the
    credentials out of -- one authority, read the same way in all three places. An absent key is an
    empty list, because "this environment declares no grafana accounts" is a fact and not a failure;
    it is reported loudly rather than quietly, since a green row that created nothing because
    nothing was asked for is the vacuity this ticket exists to remove.
    """
    encrypted = config.get("env_static_encrypted") or {}
    location = f"platform_config/{environment}/static.encrypted.json"
    rows = encrypted.get(GRAFANA_USERS_KEY)
    if rows is None:
        logging.info("%s is not set in %s, so this environment declares no grafana accounts.",
                     GRAFANA_USERS_KEY, location)
        return []
    if not isinstance(rows, list):
        raise GrafanaConfigurationError(
            f"{GRAFANA_USERS_KEY} in {location} is {type(rows).__name__}; it has to be a list of "
            f"objects, which is what the bash read it as and what this flow can loop over.")
    return [GrafanaAccountRow.from_payload(row, environment, location) for row in rows]


def resolve_grafana_base_url(ctx, grafana_url: Optional[str] = None) -> str:
    """Where grafana answers, resolved rather than assumed.

    `--grafana-url` first: someone who port-forwarded to 3001 knows something this process cannot.
    Then the service's ClusterIP, through the selector every other grafana lookup uses. Then an
    exception -- see the module docstring for why a default would put the bug back.

    The cluster read goes through a `KubernetesUtils` built from the `Context` the command already
    resolved. That is not the second resolution the design refuses, because nothing in it resolves
    anything: the target came from the `Context`, and building a client from a target is the
    binding it asks for, not a question asked of the machine.

    Returns
    -------
    str
        An address with a scheme, ready for `GrafanaClient(base_url=...)`.

    Raises
    ------
    GrafanaConfigurationError
        When neither source produced an address. Says which two were tried, names the flag, and
        names the selector and the namespace it asked the cluster for.
    """
    url = (grafana_url or "").strip()
    if url:
        logging.debug("grafana address %s came from --grafana-url", url)
        return url

    namespace = ctx.config.component_namespace("grafana")
    selector = ctx.config.component_service_selector("grafana")
    utils = KubernetesUtils(ctx)
    endpoint = utils.get_service_endpoint(namespace, selector)
    if endpoint:
        logging.info("grafana address %s resolved from the grafana service in namespace %s",
                     endpoint, namespace)
        return endpoint

    raise GrafanaConfigurationError(
        f"Could not resolve a grafana address for environment "
        f"{ctx.environment}: no --grafana-url was given, and asking the "
        f"cluster for a service matching '{selector}' in namespace '{namespace}' found nothing "
        f"with a ClusterIP this process can open. The selector is GRAFANA_SERVICE_SELECTOR in "
        f"platform_config/{ctx.environment}/static.json, read through "
        f"Config.component_service_selector. Either that "
        f"component is not installed, it was installed headless, or its labels are not what "
        f"config says -- and a cluster that could not be reached at all looks identical here. "
        f"Pass --grafana-url <url> for the address you know works, including a tunnel you opened "
        f"yourself, or fix {selector} to match the service that is there. Guessing an address is "
        f"how this flow used to print a success for a create that never happened.")


def create_grafana_accounts(ctx, grafana_url: Optional[str] = None,
                           client: Optional[GrafanaClient] = None) -> list:
    """Every account the environment declares, in the order the config lists them.

    `create_grafana_accounts.sh`, minus the four lost steps. Each row goes through
    `ensure_account`, which reads the directory first and lets the create be the authority, so a
    rerun reports what it confirmed rather than what it thinks it made. `org_role=None` is the
    script's behaviour, deliberately; see the module docstring for the question that leaves open.

    One `Context` and no `(environment, team)` pair: the pair used to arrive as two strings
    and a `Config` built down here when neither was handed, which was the last place in `src/` that
    resolved a configuration for itself. Nothing here needs a team to resolve it: the rows are read
    out of the environment's own `static.encrypted.json`, and so is the credential the client
    authenticates with, which is why the `require_team` that used to stand in front of the first
    request is gone -- it refused a run that was about to read a file no team's directory holds.

    Returns
    -------
    list[tuple[GrafanaAccountRow, GrafanaAccount]]
        One entry per row asked for, so a caller can report created-versus-existing and the
        password a run generated. Nothing here stores that password anywhere, which is the honest
        statement of a gap the bash also had.

    Raises
    ------
    GrafanaConfigurationError
        A bad row, or no address to use. Raised before any request goes out.
    kubementat.errors.GrafanaError
        Anything grafana refused or did not answer. The command fails; it does not continue to
        the next row and call that a partial success, because a half-created set of accounts is
        not a set an operator can reason about.
    """
    rows = grafana_users_rows(ctx.config, ctx.environment)
    if not rows:
        logging.warning("GRAFANA_USERS declares no accounts for %s, so nothing was created. The "
                        "row is only as honest as the config that drives it: a green run with "
                        "zero rows asks for zero accounts.", ctx.environment)
        return []

    resolved_client = _client_for(ctx, grafana_url, client)
    outcomes = []
    for row in rows:
        logging.info("ensuring grafana account %s (%s) in %s", row.email, row.name,
                     ctx.environment)
        account = resolved_client.ensure_account(row.email, row.name, password=row.password,
                                                org_role=None)
        outcomes.append((row, account))
    return outcomes


def create_read_only_account(ctx, email: str, name: str = "",
                             password: Optional[str] = None,
                             grafana_url: Optional[str] = None,
                             client: Optional[GrafanaClient] = None) -> GrafanaAccount:
    """One account, with the script's role -- which is none.

    `create_grafana_read_only_account.sh`, and the single-account half of the loop above. The name
    is kept because operators and the lane know it by that name, not because it describes the
    behaviour: the bash set no role, so this calls `ensure_account(org_role=None)` and reports
    whatever the org's default grants. The client's own `create_read_only_account`, which does
    grant `Viewer`, is deliberately not called here -- granting a privilege in a migration commit
    is a change of product, not a change of implementation, and it is written down as an open
    question in the plan instead of shipped.

    The email is checked here rather than left to grafana, because a caller that passes nothing
    gets an error that reads like a transport failure otherwise. The `Config` comes from the
    `Context`, the same single resolution `create_grafana_accounts` reads and for the same reason:
    one address and one credential, both read out of the environment's file, neither of them asked
    of a team.
    """
    if not (email or "").strip():
        raise GrafanaConfigurationError(
            "No email was given, so there is no account to create. Grafana identifies an account "
            "by its address, and 'read only' is a privilege rather than a login.")
    resolved_client = _client_for(ctx, grafana_url, client)
    return resolved_client.ensure_account(email.strip(), (name or "").strip() or email.strip(),
                                          password=(password or None), org_role=None)


def _client_for(ctx, grafana_url: Optional[str],
                client: Optional[GrafanaClient]) -> GrafanaClient:
    """The client, addressed the way this module resolves addresses.

    An injected `client` wins, because a caller that already has one is the only authority on
    where its requests go. Otherwise the address is resolved first and only then constructed, so
    "no address" is a config error and never a request to a socket that is not there.

    The `Config` handed to `from_environment` is the one the `Context` carries. It used to be built
    down here when the caller handed none, which is the last `Config(environment, team)` in `src/`
    that answered a configuration question for itself. A consolidation deleted that branch rather
    than guarding it: `from_environment` now takes a `Config` or it takes a `TypeError`, so there is no
    route left by which this module could locate a root for a command that had been told one. The
    `team` that argument position takes is therefore a formality
    -- this call always supplies the `Config`, so `from_environment` never builds one and never asks
    the team anything -- and it is handed `ctx.team`, which may be `None` for a command that runs
    without one. That is the honest value and not a placeholder: nothing on this path turns it into
    a path, and a `require_team` that refused here was refusing a read the code never performs.
    """
    if client is not None:
        return client
    base_url = resolve_grafana_base_url(ctx, grafana_url)
    return GrafanaClient.from_environment(ctx.environment, ctx.team, base_url=base_url,
                                          config=ctx.config)
