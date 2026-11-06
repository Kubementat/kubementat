"""The grafana HTTP surface, as one class, with no CLI in front of it yet.

`scripts/user_management/grafana_helpers.sh` (69 LOC) is what this replaces. Read it and the two
scripts that source it and you get six things: two request senders that authenticate with basic
auth, a bearer header builder that nothing ever calls, a user search that takes `jq '.users[0]'`,
a user create that reads the new id out of the response with `jq -r '.id'`, a "skip if the search
said something" guard built on top of those two, and a password generator that shells out to gpg.
There is no login call and no session cookie anywhere in it -- the helper sends the admin pair on
every request, and the token path is a function with no caller -- and `create_new_user_for_team`
never adds anyone to a team: it takes a `team_id`, passes it into `create_user`, and `create_user`
builds a payload that does not mention it. So the two flows that look different ("create accounts"
and "create a read-only account") are one flow, called twice, and "read only" is a name rather
than a behaviour -- Grafana hands a new user whatever the org's default role is.

That last paragraph is the reason this class exists and is not just a curl wrapper. The
measurements TODOs item 4 recorded about `cap:create-grafana-accounts` say the same thing from the
other side:

* the base URL defaults to `http://127.0.0.1:31827`, a port no lane and no template opens, so
  every request went to something that was not grafana;
* the `curl` ran inside a command substitution, where `set -e` does not look;
* an empty response body made `jq '.users[0]'` exit **0** and print nothing;
* and `create_new_user_for_team` read that nothing as "the user already exists, skipping user
  creation!" and exited 0.

Four steps, each one losing information, and the row that came out green. So the rules here are
the four losses inverted: **a non-2xx is an exception** (with grafana's own `message` in the text,
because "401" alone does not tell you which key is wrong), **an empty or unparseable body is an
exception** (it is not an answer, and treating it as one is precisely the bug above), **"already
exists" is read from the status and body grafana actually sends** -- a 412 whose message says so,
which is what a real duplicate looks like, and not from the absence of output -- and **there is no
default base URL**, because the default was the first lost step.

The credential rules match `helm_utils._grafana_admin_credentials`, which reads the same file:
`platform_config/{environment}/static.encrypted.json` is the authority, and what an operator
happens to have exported in a shell is not consulted. A configured service-account token wins over
basic auth, which is the helper's own open TODO; otherwise the admin pair is used; with neither,
construction fails before a request is sent, naming the key.
"""

import json
import logging
import secrets
from typing import Any, NamedTuple, Optional

import requests

from kubementat.config import Config
from kubementat import errors

# The paths, spelled once. Every one of them appears in the bash, so nothing here is a guess about
# where the API lives; `LOGIN_SEARCH` is the only one whose query is assembled rather than copied,
# because the bash interpolated an email address straight into a URL.
ORG_USERS = "/api/org/users"
USER_SEARCH = "/api/users/search"
ADMIN_USERS = "/api/admin/users"
ADMIN_USER_PASSWORD = "/api/admin/users/{id}/password"
TEAM_MEMBERS = "/api/teams/{id}/members"

# Two seconds to notice nothing is listening, thirty to wait on a grafana that is doing real work.
DEFAULT_CONNECT_TIMEOUT = (5.0, 30.0)

# The words Grafana puts in the 412 for a duplicate email or login. Matched on the message rather
# than on the status alone, because 412 also comes back for other validation failures, and those
# deserve the "grafana said this" reading rather than "someone else already created this user".
ALREADY_EXISTS_MARKERS = ("already exist", "user with the same", "same email or login")


class GrafanaUser(NamedTuple):
    """One account, as grafana's directory reports it."""

    user_id: int
    email: str
    login: str
    name: str = ""


class GrafanaAccount(NamedTuple):
    """The outcome of asking for an account to exist.

    `created` is the half the bash never reported: it is the difference between this run making an
    account and this run confirming one, which is exactly what a capability row needs to stop
    being vacuous. `password` is what the account was given, or `None` when the account already
    existed and kubementat did not set one -- the honest answer to "what is its password", where
    the bash's `jq` would have printed an empty string and a caller would have stored that.
    """

    user: GrafanaUser
    password: Optional[str]
    created: bool

    @property
    def user_id(self) -> int:
        return self.user.user_id


class GrafanaClient:
    """The grafana admin API, with the four ways it used to lose an operation removed.

    Parameters
    ----------
    base_url : str
        The address grafana answers on. There is no default: the scripts defaulted to
        `http://127.0.0.1:31827`, which is a port-forward a person opens rather than a service, and
        every operation "succeeded" against a socket that was not there. Pass the URL of a tunnel
        this process started, or the service's ClusterIP -- something that is true.
    admin_user, admin_password : str, optional
        Basic auth, the pair `helm_utils` installs grafana with. Read one of them from
        `static.encrypted.json` rather than passing a literal; the constructor does not check that
        it did, but `from_environment` does.
    service_account_token : str, optional
        Wins over the pair when configured, per the helper's own TODO.
    session : requests.Session, optional
        Injectable so the suite can drive this class without a socket. Defaults to a real session.
    timeout : tuple, optional
        `(connect, read)`, handed to requests unchanged.
    """

    def __init__(self, base_url: str, admin_user: Optional[str] = None,
                 admin_password: Optional[str] = None,
                 service_account_token: Optional[str] = None,
                 session: Any = None, timeout: Any = DEFAULT_CONNECT_TIMEOUT):
        self.base_url = _checked_base_url(base_url)
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        token = (service_account_token or "").strip()
        user = (admin_user or "").strip()
        password = admin_password if admin_password is not None else ""

        if token:
            self._auth_header = {"Authorization": f"Bearer {token}"}
            self._credential_source = "GRAFANA_SERVICE_ACCOUNT_TOKEN"
        elif user:
            self._auth_header = {}
            self._basic = (user, password)
            self._credential_source = "GRAFANA_ADMIN_USER"
        else:
            # Before the first request, because "401 with no credential configured" and "this file
            # names no admin" are different sentences and only one of them is actionable.
            raise errors.GrafanaConfigurationError(
                "No credential to reach grafana with: neither GRAFANA_SERVICE_ACCOUNT_TOKEN nor "
                "GRAFANA_ADMIN_USER is set. Both are read from the environment's "
                "static.encrypted.json -- set one there, not in your shell, where kubementat "
                "cannot see it.")

    @classmethod
    def from_environment(cls, environment: str, team: str, base_url: Optional[str] = None, *,
                         config: Config) -> "GrafanaClient":
        """Build the client from `platform_config/{environment}/static.encrypted.json`.

        Same reader as `helm_utils._grafana_admin_credentials` -- `Config.get
        ('env_static_encrypted')`, the environment file and never a team file, because the pair is
        per environment and a team's file declares neither key.

        `config` is required. This used to fall back to `Config(environment, team,
        preload_configs=False)` when a caller handed none, which is a `Config` built from a pair
        and nothing else: the last construction in `src/` that answered the root question for
        itself instead of taking the one its caller had. It was reached **zero** times by the
        suite (census D, instrumented), because the one `src/` caller --
        `grafana_accounts._client_for` -- always hands its `Context`'s `Config` down, so the
        fallback was dead code with a live hazard: the day someone called it from a command that
        had been told which tree to read, that command would have read whichever tree its own
        environment named. The branch is deleted rather than guarded, so an omitted `config`
        is a `TypeError` at the call site and not a root this function goes and looks for.

        `team` stays only because the callers that resolve a `Context` hand it: `Config` is
        already the tree and the pair, so nothing here turns the name into a path. It is a
        formality and it is a stated one, not a placeholder for a read that no longer happens.

        Raises
        ------
        kubementat.errors.GrafanaConfigurationError
            When there is no address to use, or when an admin user is configured with no password.
        TypeError
            When no `Config` is handed, which is the shape of "this caller has no root".
        """
        encrypted = config.get("env_static_encrypted") or {}
        token = (encrypted.get("GRAFANA_SERVICE_ACCOUNT_TOKEN") or "").strip()
        admin_user = (encrypted.get("GRAFANA_ADMIN_USER") or "").strip()
        admin_password = (encrypted.get("GRAFANA_ADMIN_PASSWORD") or "").strip()
        environment_file = (f"{getattr(config, 'platform_config_dir', 'platform_config')}/"
                            f"{environment}/static.encrypted.json")

        if not admin_user and not token:
            raise errors.GrafanaConfigurationError(
                f"Neither GRAFANA_ADMIN_USER nor GRAFANA_SERVICE_ACCOUNT_TOKEN is set in "
                f"{environment_file}, so kubementat has no credential to open grafana's API with. "
                f"grafana installs its admin account from that pair, so the account is probably "
                f"there and the file is the thing that lost it. Set it there and run the command "
                f"again.")

        if admin_user and not token and not admin_password:
            # helm_utils only warns about this, because it is writing a release that can install
            # without a readable API. Here the whole point is the API, and an empty password sends
            # a basic-auth request that answers 401 -- which would blame the user for the password.
            raise errors.GrafanaConfigurationError(
                f"GRAFANA_ADMIN_USER is set but GRAFANA_ADMIN_PASSWORD is not in "
                f"{environment_file}, so there is no password to send with {admin_user!r}. The "
                f"release can install without a password; this command cannot read grafana "
                f"without one.")

        return cls(base_url=base_url, admin_user=admin_user, admin_password=admin_password,
                   service_account_token=token)

    @property
    def credential_source(self) -> str:
        """Which config key this client authenticated with, for the log line that says so."""
        return self._credential_source

    # -------------------------------------------------------------------------------------
    # reads
    # -------------------------------------------------------------------------------------

    def find_user(self, email: str) -> Optional[GrafanaUser]:
        """The account whose email is `email`, or None when grafana's directory has no such user.

        Grafana's `/api/users/search` is a *search*, not a lookup: it matches on login and name as
        well as email, and the bash took `.users[0]` -- so a fuzzy hit on someone whose name
        contains the string made `create_new_user_for_team` believe the address it asked about was
        taken and skip the create. That is a second way to be vacuous, and this is where it goes:
        the answer is filtered to the email that was asked for, case-insensitively, because
        grafana's own matching is case-insensitive and asking for `Foo@Bar.test` is the same
        account as `foo@bar.test`.
        """
        payload = self._request("GET", USER_SEARCH, params={
            "perpage": 10, "page": 1, "query": email, "sort": "email-asc"})
        wanted = email.strip().lower()
        found = []
        for row in _directory(payload, f"the user search for {email!r}"):
            if str(row.get("email", "")).strip().lower() == wanted:
                found.append(_user_from_payload(row))
        if not found:
            return None
        if len(found) > 1:
            # Two accounts, one email. Grafana's unique index says this cannot happen, so if it
            # does, something has been written around the index and picking one is a decision
            # nobody delegated to a library.
            raise errors.GrafanaError(
                f"grafana returned more than one account for {email!r} "
                f"({', '.join(str(user.user_id) for user in found)}). Two accounts with one email "
                f"is not something this client picks a winner for; delete or rename one.")
        return found[0]

    def org_members(self) -> list:
        """The org's roster -- the one read the probe makes, kept for parity of surface.

        `tests/e2e/harness/stages.py` probes `/api/org/users` as the "grafana's API is readable"
        assertion. This is that call, so the day the probe moves to Python there is no new path to
        write, and a row that measures readability does not have to go through a create.
        """
        return _directory(self._request("GET", ORG_USERS), "the org's roster")

    # -------------------------------------------------------------------------------------
    # writes
    # -------------------------------------------------------------------------------------

    def create_user(self, email: str, name: str, password: Optional[str] = None,
                    org_role: Optional[str] = None) -> GrafanaAccount:
        """Create the account, and say whether it was created or found.

        No password is not an error: the bash generated one with `gpg --gen-random`, which is a
        subprocess and therefore not this method's job, and `Automation.generate_password` already
        answers it in the stdlib. Callers that have no password say "no password please" and get
        the generated one back on the returned account, so it can be stored. `secrets` rather than
        gpg for the same reason: the generator has no reason to need a keyring.

        `org_role` is what makes the "read only" name true, and only that: the bash script called
        `create_grafana_read_only_account.sh` never set a role, so it created a user who got the
        org's default. A client whose method is named after a privilege has to grant it, or say
        otherwise; `create_read_only_account` passes `"Viewer"`, and a caller that wants bash's
        actual behaviour passes `org_role=None` and gets bash's actual behaviour.

        A 412 whose body says the account is already there is not a failure, it is the answer: the
        user is read back and reported `created=False`. A 412 that says anything else is grafana
        rejecting something else, and is raised with grafana's words.
        """
        generated = None
        if not password:
            generated = secrets.token_urlsafe(32)
            password = generated

        body = {"name": name, "email": email, "login": email, "password": password}
        if org_role:
            body["orgRole"] = org_role

        try:
            payload = self._request("POST", ADMIN_USERS, json_body=body)
        except errors.GrafanaError as error:
            if _says_already_exists(error):
                existing = self.find_user(email)
                if existing is None:
                    raise errors.GrafanaError(
                        f"grafana refused to create {email!r} as a duplicate, but would not name "
                        f"the account that is in the way when asked for it directly. Nothing was "
                        f"created. grafana's own answer was: {error}",
                        error.status_code) from error
                logging.info("grafana account %s already exists (user %s); nothing created",
                             email, existing.user_id)
                return GrafanaAccount(user=existing, password=None, created=False)
            raise

        user_id = payload.get("id") if isinstance(payload, dict) else None
        if user_id is None:
            raise errors.GrafanaError(
                "grafana answered the create call for "
                f"{email!r} with {self._shape_of(payload)} -- no user id in it, so no account is "
                "known to exist. Treating that as success is exactly how a create can be reported "
                "as done and do nothing, so this raises instead.")
        logging.info("created grafana account %s (user %s)", email, user_id)
        return GrafanaAccount(user=GrafanaUser(user_id=int(user_id), email=email, login=email,
                                               name=name),
                              password=generated or password, created=True)

    def ensure_account(self, email: str, name: str, password: Optional[str] = None,
                       org_role: Optional[str] = None) -> GrafanaAccount:
        """Create the account if it is not there, and report which of the two happened.

        `create_new_user_for_team`, in Python. The pre-check is kept because the create is the
        authority (the 412 path above) but a duplicate create is still a write that grafana
        rejects, and because the read is where "someone else's account matched the search" is
        caught rather than skipped over. The two together are the reason this method cannot answer
        "done" when nothing happened: the read raises on a body it cannot read, and the write
        raises on a body with no id in it.

        `team_id` is deliberately absent, because the helper's `team_id` was absent too -- passed
        in, threaded through, and dropped on the floor in the payload. Adding someone to a team is
        `add_user_to_team`, which has no caller until the configuration's rows carry a team, and
        inventing the argument back into this signature would put a name where there is
        no behaviour.
        """
        existing = self.find_user(email)
        if existing is not None:
            if password:
                logging.warning("grafana account %s already exists (user %s), so the password this "
                                "call named was not applied. Resetting a login nobody asked to "
                                "reset is not this method's business; set_password() is where that "
                                "is decided.", email, existing.user_id)
            else:
                logging.info("grafana account %s already exists (user %s); nothing created",
                             email, existing.user_id)
            # None, not the offered password: an account this call did not write has a password
            # that belongs to somebody else, and returning the candidate as though it were the
            # answer is how a config ends up storing a value that was never true.
            return GrafanaAccount(user=existing, password=None, created=False)
        return self.create_user(email, name, password=password, org_role=org_role)

    def create_read_only_account(self, email: str, name: str,
                                 password: Optional[str] = None) -> GrafanaAccount:
        """`create_grafana_read_only_account.sh`, with the privilege it always named.

        Viewer is what "read only" means in grafana's org model, so this asks for it. The script
        did not: it created a user in the org and let the org's default role stand, which means a
        "read only" account on a cluster whose default is Editor is not read only. If that drift is
        wanted for a rerun, `ensure_account(..., org_role=None)` is the script's behaviour
        unchanged, and it is tested.
        """
        return self.ensure_account(email, name, password=password, org_role="Viewer")

    def set_password(self, user_id: int, password: str) -> None:
        """Give an existing account a new password.

        Nothing in the bash did this -- it set the password inside the create and never came back
        to it -- so the honest description of this method is "the read the scripts never made". It
        is here for the one case the scripts had no answer to, which TODOs item 4 found: an account
        that exists but whose password was generated by a run that then exited, so nobody wrote it
        down. `ensure_account` reports that account with `password=None` and no caller should
        store that as a password; this is how it gets fixed. Setting it is a real change to a
        human's login, so this is only called when someone asks for it.
        """
        self._request("PUT", ADMIN_USER_PASSWORD.format(id=user_id),
                      json_body={"password": password})

    def add_user_to_team(self, user_id: int, team_id: int,
                         position: str = "") -> None:
        """Add an existing account to a team.

        The method the helper *claimed* to have: `create_user` took a `team_id` and never used it,
        and `create_new_user_for_team` printed "for team" while adding nobody. This is the call
        that would have made that sentence true. It has no caller in this tree yet, so it is
        recorded as a decision rather than wired into a flow that never asked for it: the
        config's `GRAFANA_USERS` rows carry email, name and password, and a team is not among the
        fields either script reads.
        """
        body = {"userId": user_id}
        if position:
            body["permission"] = position
        self._request("POST", TEAM_MEMBERS.format(id=team_id), json_body=body)

    # -------------------------------------------------------------------------------------
    # transport
    # -------------------------------------------------------------------------------------

    def _request(self, method: str, path: str, params: Optional[dict] = None,
                 json_body: Optional[dict] = None) -> Any:
        """One request, answered or raised. Every method above is this plus a shape check.

        Two things are deliberately absent: a retry, and a follow of anything but what requests
        does by default. A grafana write retried without an idempotency key is a second account,
        and a redirect followed to a login page returns HTML that no caller should mistake for a
        payload.
        """
        url = self.base_url + path
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(self._auth_header)
        auth = getattr(self, "_basic", None)
        logging.debug("grafana %s %s (%s)", method, url, self._credential_source)

        try:
            response = self.session.request(method, url, headers=headers,
                                            auth=auth, params=params,
                                            json=json_body, timeout=self.timeout)
        except requests.RequestException as error:
            raise errors.GrafanaError(
                f"Could not reach grafana at {url}: {error}. This is not a rejected credential, it "
                f"is no answer at all -- check the address, that the service is up, and that "
                f"something is tunnelled there. The scripts this replaced defaulted to "
                f"http://127.0.0.1:31827 and called that a success.",
                None) from error

        return self._answer(response, method, path)

    def _answer(self, response: Any, method: str, path: str) -> Any:
        """The decoded body, or an exception that quotes grafana's own words.

        The order matters: status first, then emptiness, then JSON. A 200 with an empty body is the
        case that made the bash row vacuous -- it is *not* "the user is not there" and it is not
        "the user was created", it is nothing -- so it is treated here as a failure to answer,
        which is the one reading that cannot be mistaken for an outcome.
        """
        what = f"grafana {method} {path}"
        if not response.ok:
            raise self._failure(response, what)

        text = (response.text or "").strip()
        if not text:
            raise errors.GrafanaError(
                f"{what} answered {response.status_code} with an empty body, and that is no "
                f"answer: the bash helper read one as though it had been told the account was "
                f"already there, and exited 0. Nothing here is known to have happened, so nothing "
                f"here is being claimed either.")
        try:
            payload = json.loads(text)
        except ValueError as error:
            raise errors.GrafanaError(
                f"{what} answered {response.status_code} with a body that is not JSON "
                f"({text[:120]!r}). Something other than grafana is on that address -- a proxy, a "
                f"login page, a port-forward to the wrong port.", status_code=response.status_code
                ) from error
        # No further guessing about a 2xx. A successful write really does carry a `message`
        # alongside the id ("User created"), so a rule here that read a 200-plus-message as a
        # refusal would fail calls that worked. What decides whether an operation happened is the
        # shape each caller needs: a create has to name a user id, a search has to name a
        # directory, and both raise when the body they were given cannot be read that way.
        return payload

    def _failure(self, response: Any, what: str) -> errors.GrafanaError:
        """The exception for a non-2xx, with grafana's message in it when it sent one."""
        detail = ""
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = str(body.get("message") or body.get("error") or "").strip()

        message = f"{what} failed with {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        status = response.status_code
        if status in (401, 403):
            return errors.GrafanaUnauthorizedError(
                f"{message}. That is grafana rejecting the credential, not the address, the "
                f"namespace or the user: this client sent its request as "
                f"{self._credential_source}. If the pair is right, the account it belongs to is "
                f"not an admin here, and an org role is not a grafana admin either.",
                status)
        return errors.GrafanaError(message, status)

    def _shape_of(self, payload: Any) -> str:
        if isinstance(payload, dict):
            return f"a JSON object with keys {sorted(payload)}"
        return repr(payload)[:120]


def _checked_base_url(base_url: str) -> str:
    """A scheme and a host, or an error that says why a default would be the bug.

    The empty string is what the scripts passed when the operator forgot the second positional
    argument, and the scripts answered it with `127.0.0.1:31827`. Refusing is the fix: an address
    that is not there is the difference between a failed command and a green row.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise errors.GrafanaConfigurationError(
            "No grafana address was given, and kubementat will not guess one. "
            "scripts/user_management defaulted to http://127.0.0.1:31827, a NodePort no template "
            "opens and no lane tunnels to, which is how an account create that created nothing "
            "printed a success. Give the command the URL grafana actually answers on -- a "
            "port-forward this process started, or the service's ClusterIP.")
    if "://" not in url:
        raise errors.GrafanaConfigurationError(
            f"grafana address {base_url!r} has no scheme, so it is not an address. Use "
            f"http:// or https://.")
    if url.startswith("http://127.0.0.1:31827") or url.startswith("http://localhost:31827"):
        logging.warning("grafana address %s is the script's old default. Nothing refuses this "
                        "because a tunnel on 31827 is a legitimate thing to have; it is warned "
                        "about because that number is what made the account row vacuous once.",
                        url)
    return url


def _directory(payload: Any, what: str) -> list:
    """The rows of a grafana directory answer, or nothing-but-an-error.

    Two shapes look alike and mean opposite things: `{"users": [], "totalCount": 0}` is grafana
    saying the account is not there, which is an answer and leads to a create; `{}` or
    `{"totalCount": 7}` is not a directory at all, and guessing "not there" from it is the step
    that either duplicates an account or -- on the path TODOs item 4 measured -- reports a create
    that never ran. So the key is required, and its absence is an error that says which question
    could not be answered.
    """
    if not isinstance(payload, dict) or "users" not in payload:
        raise errors.GrafanaError(
            f"{what} answered {repr(payload)[:160]}, which is not a directory: there is no 'users' "
            f"key, so there is no way to tell whether the account is there. Guessing 'not there' "
            f"from that is how an account gets created twice, or how a create gets reported as "
            f"done without happening.")
    rows = payload["users"]
    if not isinstance(rows, list):
        raise errors.GrafanaError(f"{what} put {type(rows).__name__} where a list of users was expected.")
    return rows


def _user_from_payload(row: dict) -> GrafanaUser:
    return GrafanaUser(user_id=int(row.get("id")), email=str(row.get("email", "")),
                       login=str(row.get("login", "")), name=str(row.get("name", "")))


def _says_already_exists(error: errors.GrafanaError) -> bool:
    """Is this exception grafana's duplicate-account answer?

    Status 412 plus one of the phrases grafana's users service puts in that message -- and, for an
    older grafana that answered 400, the same phrases with no status requirement. The empty body of
    a failed curl is not in the set, deliberately: the whole ticket is that "we got nothing" is not
    "we are already there".
    """
    text = str(error).lower()
    if error.status_code not in (412, 400, None):
        return False
    if error.status_code is None and "already" not in text:
        return False
    return any(marker in text for marker in ALREADY_EXISTS_MARKERS)
