"""The address comes first, and nothing about this command is decided by a guess.

`kmt users create-grafana-accounts` and `kmt users create-grafana-read-only-account` are the
callers the client was waiting for. Two things are being checked here, and they are the two
things that made the capability row vacuous:

* **Where grafana is.** `--grafana-url` first, then the service's ClusterIP read through the
  config's own selector, then a failure that names both. What is *not* in the precedence is a
  default — `http://127.0.0.1:31827` was the scripts' answer, and a port nobody opens answers
  nothing, which is how a create that created nothing printed a success.
* **Whether an operation happened.** Every body is read as an answer or as a failure, so
  "grafana did not respond" and "grafana says that account is already there" are different
  outcomes with different exit codes. The bash could not tell them apart, and told them the same.

The rest is parity, not correctness: `org_role=None` is what the deleted script sent, which is
nothing. Keeping it is a recorded question, not a decision that "read only" means something.

Nothing here opens a socket. `requests.Session` is replaced with a recorder in every case, and
the cluster is asked for a service by a fake that answers with one string.
"""

import json

import pytest
from click.testing import CliRunner

import kubementat.infrastructure.grafana.accounts as grafana_accounts
import kubementat.infrastructure.grafana.client as grafana_client
from cli.commands import users
from kubementat.core.config.config import Config

CLUSTER_IP_URL = "http://10.43.19.7:80"
NO_ADDRESS = ""
SELECTOR = "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana"
EMAIL = "mranderson@something.test"
NAME = "Thomas Anderson"
PASSWORD = "the-password-the-row-declares"
USER_ID = 12


class RecordingSession:
    """Answers like grafana, and remembers every request that reached it.

    `responses` is keyed by `"<METHOD> <path>"` so a test can say what the search said and what
    the create said, and a test that asks for something unkeyed fails instead of getting an
    empty body — which is the one class of bug this whole flow is about.
    """

    def __init__(self, **responses):
        self.requests = []
        self.responses = responses
        self.sequences = {}
        self.fail_with = None

    def request(self, method, url, headers=None, auth=None, params=None, json=None, timeout=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        self.requests.append({"method": method, "url": url, "path": path, "params": params or {},
                              "json": json, "auth": auth, "headers": headers})
        if self.fail_with is not None:
            raise self.fail_with
        key = f"{method} {path}"
        if self.sequences.get(key):
            return self.sequences[key].pop(0)
        if key not in self.responses:
            raise AssertionError(f"the client asked for {key!r} and this session has no answer "
                                 f"for it; answered so far: {sorted(self.responses)}")
        return self.responses[key]

    def then(self, key, *answers):
        """Answer one endpoint differently the *n*th time, in order.

        A 412 that says "already exists" is only half the story: the client then asks who. A
        session that answers both times with the same body cannot express that, and a test that
        cannot express it cannot tell "found the account that was in the way" from "got nothing,"
        which is the exact pair the bash could not tell apart.
        """
        self.sequences[key] = list(answers)
        return self


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if not self.text.strip():
            raise ValueError("no body")
        return json.loads(self.text)


def no_such_user(**kwargs):
    return FakeResponse(200, payload={"users": [], "totalCount": 0})


def directory_with(user_id=USER_ID, email=EMAIL, name=NAME):
    """An answer that names one account, which is what "the account is already there" looks like."""
    return FakeResponse(200, payload={"users": [{"id": user_id, "email": email, "login": email,
                                                "name": name}], "totalCount": 1})


def created_user(user_id=USER_ID, **kwargs):
    return FakeResponse(201, payload={"id": user_id, "message": "User created"})


def exists_already(**kwargs):
    return FakeResponse(412, payload={"message": "user with the same email or login already exists"})


def cluster_answering(endpoint):
    """The cluster side of the address resolution: one service, or none.

    Patched on `grafana_accounts` and not on the command that used to hand a client down: the
    `Context` conversion moved the construction site into the module that uses the answer, so the
    seam is where the client is built and not where it is passed through.
    """

    class Utils:
        def __init__(self, *args, **kwargs):
            pass

        def get_service_endpoint(self, namespace, selector):
            Utils.namespace = namespace
            Utils.selector = selector
            return endpoint

    return Utils


@pytest.fixture
def checkout(fixture_repo, monkeypatch):
    """A checkout whose config declares two grafana accounts, and no token to distract from the pair."""
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    # The configuration root is named, not located: clearing the variable here would leave
    # this fixture with no root at all, and the refusal would arrive instead of the behaviour the
    # test is about. The fixture's own tree is what is named.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    fixture_repo.environment_static_encrypted(**{
        "GRAFANA_USERS": [
            {"email": EMAIL, "name": NAME, "password": PASSWORD},
            {"email": "readonly@something.test", "name": "Read Only"},
        ],
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "",
    })
    return fixture_repo


@pytest.fixture
def config_of(checkout):
    return Config(checkout.environment, checkout.team, preload_configs=False)


@pytest.fixture
def driven(monkeypatch, request):
    """Wire a recorder into the client so the command's requests land in `session.requests`."""
    session = RecordingSession(**dict(getattr(request, "param", None) or {}))
    monkeypatch.setattr(grafana_client.requests, "Session", lambda *a, **k: session)
    # The pair in the fixture's encrypted file is what `from_environment` must find; a machine
    # whose own environment has the keys exported must not change the answer.
    monkeypatch.delenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.delenv("GRAFANA_ADMIN_USER", raising=False)
    monkeypatch.delenv("GRAFANA_ADMIN_PASSWORD", raising=False)
    return session


def invoke(argv):
    """One `kmt users ...` run, as the lane runs it: no terminal, so `--yes` carries the consent."""
    return CliRunner().invoke(users.users, argv)


# ------------------------------------------------------------------------ the address


def test_an_unresolved_address_fails_and_names_the_flag_and_the_selector(checkout, driven,
                                                                         monkeypatch):
    """Where both sources come up empty, the command stops. That is the fix, stated as an assert.

    The scripts' shape of this moment was `http://127.0.0.1:31827` and exit 0. The only thing
    worse than no grafana is no grafana and a green row, so what is asserted here is the exit
    code plus the two names that let a person act on it, plus that not one request was sent.
    """
    monkeypatch.setattr(grafana_accounts, "KubernetesUtils", cluster_answering(NO_ADDRESS))

    result = invoke(["create-grafana-accounts", checkout.environment, "--yes"])

    assert result.exit_code == 1
    assert "--grafana-url" in result.output
    assert SELECTOR in result.output
    assert "127.0.0.1" not in result.output
    assert driven.requests == []


# --------------------------------------------------------------------- the failures


def test_a_412_that_says_the_account_exists_is_not_reported_as_a_creation(
        checkout, driven, monkeypatch):
    """`created=False` is the whole point of the row, and grafana's 412 is what says it.

    The old skip guard fired on an empty search result, which is why it lied. Here it fires on
    grafana's own duplicate answer, and the run reports the account as confirmed rather than
    made -- including the user id, which a `jq '.users[0]'` on nothing never had.
    """
    monkeypatch.setattr(grafana_accounts, "KubernetesUtils", cluster_answering(CLUSTER_IP_URL))
    (checkout.platform_config / checkout.environment / "static.encrypted.json").write_text(
        json.dumps({"GRAFANA_ADMIN_USER": "grafadmin", "GRAFANA_ADMIN_PASSWORD": "not-a-password",
                    "GRAFANA_USERS": [{"email": EMAIL, "name": NAME,
                                       "password": PASSWORD}]}))
    driven.then("GET /api/users/search", no_such_user(), directory_with(user_id=4))
    driven.responses.update({"POST /api/admin/users": exists_already()})

    result = invoke(["create-grafana-accounts", checkout.environment, "--yes"])

    assert result.exit_code == 0, result.output
    assert "already exists" in result.output
    assert "nothing changed and no password was applied" in result.output
    assert "user 4" in result.output


# ------------------------------------------------------------------ parity, recorded


def test_a_run_says_which_accounts_it_made_and_names_no_password_it_never_set(
        checkout, driven, monkeypatch):
    """The difference between making an account and confirming one, printed.

    `create_new_user_for_team` printed one sentence for both. Here a created account reports the
    password this run generated, because nothing stores it and the operator has to write it down,
    and an account that already existed reports *no* password: what it is on is not this run's
    business, and a config that stored the candidate would store a value that was never true.
    """
    monkeypatch.setattr(grafana_accounts, "KubernetesUtils", cluster_answering(CLUSTER_IP_URL))
    driven.responses.update({
        "GET /api/users/search": no_such_user(),
        "POST /api/admin/users": created_user(),
    })

    result = invoke(["create-grafana-accounts", checkout.environment, "--yes"])

    assert result.exit_code == 0, result.output
    created = [call for call in driven.requests if call["method"] == "POST"]
    assert len(created) == 2
    assert created[0]["json"]["password"] == PASSWORD
    # The second row declared none, so this run made one up and must say so.
    assert len(created[1]["json"]["password"]) >= 32
    assert "generated password" in result.output
    assert "created grafana account mranderson@something.test" in result.output


def test_the_read_only_command_reports_what_it_made_and_what_it_did_not_decide(
        checkout, driven, monkeypatch):
    monkeypatch.setattr(grafana_accounts, "KubernetesUtils", cluster_answering(CLUSTER_IP_URL))
    driven.responses.update({
        "GET /api/users/search": no_such_user(),
        "POST /api/admin/users": created_user(),
    })

    result = invoke(["create-grafana-read-only-account", checkout.environment, EMAIL, NAME,
                     "--yes"])

    assert result.exit_code == 0, result.output
    assert "no org role was set" in result.output
    assert "cluster dev:" in result.output, (
        f"the read-only command ran without resolving a cluster, or refused to run at all: {result.output}")


# --------------------------------------------------------------------- bad config


def test_a_row_that_names_no_email_stops_the_flow_before_the_first_request(checkout, monkeypatch):
    """A create with no address is not an account missing a field, and no request is sent.

    Half a list is the worst outcome this flow can produce: everything before the bad row is
    created, and the operator is left to find which accounts are on the cluster.
    """
    (checkout.platform_config / checkout.environment / "static.encrypted.json").write_text(
        json.dumps({"GRAFANA_ADMIN_USER": "grafadmin", "GRAFANA_ADMIN_PASSWORD": "not-a-password",
                    "GRAFANA_USERS": [{"name": "No Address"}]}))
    session = RecordingSession()
    monkeypatch.setattr(grafana_client.requests, "Session", lambda *a, **k: session)

    result = invoke(["create-grafana-accounts", checkout.environment, "--yes"])

    assert result.exit_code == 1
    assert "names no email" in result.output
    assert session.requests == []


def test_an_environment_that_declares_no_accounts_says_so_loudly(checkout, monkeypatch):
    """Nothing to create is a true answer, but not one a green row may whisper.

    A `GRAFANA_USERS` of `[]` is exactly as vacuous as the default address was, so it is reported
    rather than skipped: the row is only as honest as the config driving it.
    """
    fixture_repo = checkout
    fixture_repo.environment_static_encrypted(**{"GRAFANA_USERS": []})
    monkeypatch.setattr(grafana_client.requests, "Session",
                        lambda *a, **k: RecordingSession())
    monkeypatch.setattr(grafana_accounts, "KubernetesUtils", cluster_answering(CLUSTER_IP_URL))

    result = invoke(["create-grafana-accounts", fixture_repo.environment, "--yes"])

    assert result.exit_code == 0
    assert "declares no accounts" in result.output


def test_the_credential_is_the_ones_the_file_names_and_no_shell_export_is_consulted(
        checkout, driven, monkeypatch):
    """`static.encrypted.json` is the authority, and an operator's shell is not a second one.

    The pair the fixture file names is what reaches the request, even when the machine's own
    environment exports a different one -- the same contract `infrastructure/helm.py` and the lane's probe
    both read.
    """
    monkeypatch.setenv("GRAFANA_ADMIN_USER", "an-exported-user")
    monkeypatch.setenv("GRAFANA_ADMIN_PASSWORD", "an-exported-password")
    monkeypatch.setattr(grafana_accounts, "KubernetesUtils", cluster_answering(CLUSTER_IP_URL))
    driven.responses.update({
        "GET /api/users/search": no_such_user(),
        "POST /api/admin/users": created_user(),
    })

    invoke(["create-grafana-accounts", checkout.environment, "--yes"])

    auth = driven.requests[0]["auth"]
    assert auth == ("grafadmin", "not-a-password")


# -------------------------------------------------------------------- the surface


def test_the_command_refuses_a_run_that_cannot_confirm_itself(checkout, monkeypatch):
    """Same contract as `kmt uninstall` and the team accounts: no terminal, nothing created.

    `run-script` passed the question to a script that answered itself with EOF. A command is
    supposed to own that answer.
    """
    monkeypatch.setattr("sys.stdin", open("/dev/null"))

    result = invoke(["create-grafana-accounts", checkout.environment])

    assert result.exit_code == 2
    assert "Re-run with --yes" in result.output
