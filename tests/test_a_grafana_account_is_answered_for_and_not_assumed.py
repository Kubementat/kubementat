"""`GrafanaClient` answers, or it raises. Nothing in between, and no socket.

Four shapes of failure made `cap:create-grafana-accounts` green while it created nothing, and each
one is asserted here as a raised exception rather than remembered as a rule: a curl that could not
connect inside a command substitution, a 2xx with an empty body, a `jq` read of that empty body
that exited **0** with no output, and the `if` that read the nothing as "the user already exists".
The tests are driven by a fake session, so every one of them is about what the client decides, and
none of them is about whether something is listening on a port.

The second half of the file is the credential rule: the pair comes from the environment's
`static.encrypted.json`, which is the file `helm_utils` installs grafana from, and a key that is
not in that file is an error before a request, not a 401 afterwards.
"""

import json
import logging

import pytest
import requests

from kubementat import grafana_client
from kubementat.config import Config
from kubementat.errors import (GrafanaConfigurationError, GrafanaError,
                              GrafanaUnauthorizedError)
from kubementat.grafana_client import GrafanaClient

BASE_URL = "http://grafana.test:3000"
ADMIN = "grafadmin"
ADMIN_PASSWORD = "the-password-the-file-names"
TOKEN = "a-service-account-token"
EMAIL = "mranderson@something.test"
NAME = "Thomas Anderson"
PASSWORD = "a-password-for-this-account"


class FakeResponse:
    """An HTTP answer, in three parts: status, body text, and whether it parses."""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        if text is not None:
            self.text = text
        elif payload is not None:
            self.text = json.dumps(payload)
        else:
            self.text = ""

    def json(self):
        if not self.text.strip():
            raise ValueError("no body")
        return json.loads(self.text)


def ok(payload):
    return FakeResponse(200, payload=payload)


class FakeSession:
    """`requests.Session` for this module: remembers calls, answers from a queue or a callable.

    One `answers` per (method, path) is what a caller that makes three requests has to write, and a
    callable keyed on the call is what a test that needs *different* answers to the *same* call
    needs -- so both are here, and neither of them is a socket.
    """

    def __init__(self, answers=None, response=None, error=None):
        self.calls = []
        self.answers = answers or {}
        self.response = response
        self.error = error

    @property
    def requested(self):
        return [(call["method"], call["url"]) for call in self.calls]

    def request(self, method, url, headers=None, auth=None, params=None, json=None, timeout=None):
        call = {"method": method, "url": url, "headers": dict(headers or {}), "auth": auth,
                "params": dict(params or {}), "json": json, "timeout": timeout}
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        # Answers are keyed on the API path, not the whole URL, so a test never has to repeat the
        # host it was handed and cannot drift from it.
        path = url.split("?")[0]
        if path.startswith(BASE_URL):
            path = path[len(BASE_URL):]
        answer = self.answers.get((method, path))
        if callable(answer):
            return answer(call)
        if answer is not None:
            return answer
        if self.response is not None:
            return self.response
        raise AssertionError(f"no answer configured for {method} {path}; "
                             f"configured: {sorted(self.answers)}")


def client_with(session=None, **kwargs):
    """A client wired to the fake transport, with sane defaults the tests do not repeat.

    Any keyword wins, so a test that wants the token path or a different address is saying so and
    not building a second harness.
    """
    settings = {"base_url": BASE_URL, "admin_user": ADMIN, "admin_password": ADMIN_PASSWORD}
    settings.update(kwargs)
    return GrafanaClient(session=session or FakeSession(), **settings)


# ---------------------------------------------------------------------------------------
# what is sent, and with what credential
# ---------------------------------------------------------------------------------------


def test_a_configured_service_account_token_is_the_credential_that_is_sent():
    session = FakeSession(response=ok({"users": [], "totalCount": 0}))
    client = client_with(session=session, admin_user=None, admin_password=None,
                         service_account_token=TOKEN)

    assert client.find_user(EMAIL) is None
    call = session.calls[0]
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}", call
    assert call["auth"] is None, "the token path does not also send basic auth"


def test_the_admin_pair_is_sent_as_basic_auth_when_no_token_is_configured():
    session = FakeSession(response=ok({"users": [], "totalCount": 0}))
    client = client_with(session=session)

    assert client.credential_source == "GRAFANA_ADMIN_USER"
    client.find_user(EMAIL)
    assert session.calls[0]["auth"] == (ADMIN, ADMIN_PASSWORD), session.calls[0]
    assert "Authorization" not in session.calls[0]["headers"], "no bearer header is invented"


def test_the_token_wins_because_the_helper_had_the_same_todo_and_the_same_two_keys():
    """Both keys are configured. The token is what the API was meant to be read with."""
    session = FakeSession(response=ok({"users": [], "totalCount": 0}))
    client = client_with(session=session, service_account_token=TOKEN)

    assert client.credential_source == "GRAFANA_SERVICE_ACCOUNT_TOKEN"
    client.find_user(EMAIL)
    assert session.calls[0]["headers"].get("Authorization") == f"Bearer {TOKEN}"
    assert session.calls[0]["auth"] is None, \
        "sending both credentials means the 401 blames whichever one grafana happened to check"


def test_a_client_built_with_no_credential_names_the_key_instead_of_sending_a_blank_login():
    with pytest.raises(GrafanaConfigurationError) as raised:
        GrafanaClient(base_url=BASE_URL)
    message = str(raised.value)
    assert "GRAFANA_ADMIN_USER" in message and "GRAFANA_SERVICE_ACCOUNT_TOKEN" in message, message
    assert "static.encrypted.json" in message, "the file the operator can open, not the exception"


def test_the_credentials_are_read_from_the_environments_encrypted_file():
    asked = []

    class ScratchConfig:
        platform_config_dir = "/tmp/platform_config_under_test"

        def get(self, category):
            asked.append(category)
            return {"GRAFANA_ADMIN_USER": ADMIN, "GRAFANA_ADMIN_PASSWORD": ADMIN_PASSWORD}

    client = GrafanaClient.from_environment("dev", "dev1", base_url=BASE_URL,
                                            config=ScratchConfig())
    assert asked == ["env_static_encrypted"], \
        "a team-level file declares neither key, and reading it is how the probe lost the password"
    session = FakeSession(response=ok({"users": [], "totalCount": 0}))
    client.session = session
    client.find_user(EMAIL)
    assert session.calls[0]["auth"] == (ADMIN, ADMIN_PASSWORD)


def test_the_credentials_come_from_the_file_a_config_reader_actually_opens(tmp_path):
    """The same reader `helm_utils` uses, over a real `Config` on a real path.

    A recording config proves which category is asked for; only a file on disk proves that the key
    the operator wrote is the key the client sends.
    """
    environment_dir = tmp_path / "platform_config" / "dev"
    environment_dir.mkdir(parents=True)
    (environment_dir / "static.json").write_text(json.dumps(
        {"GRAFANA_DEPLOYMENT_NAMESPACE": "grafana"}))
    (environment_dir / "static.encrypted.json").write_text(json.dumps(
        {"GRAFANA_ADMIN_USER": ADMIN, "GRAFANA_ADMIN_PASSWORD": ADMIN_PASSWORD}))
    config = Config("dev", "dev1", kubementat_main_dir=str(tmp_path),
                    platform_config_dir=str(tmp_path / "platform_config"))

    client = GrafanaClient.from_environment("dev", "dev1", base_url=BASE_URL, config=config)
    session = FakeSession(response=ok({"users": [], "totalCount": 0}))
    client.session = session
    client.find_user(EMAIL)

    assert session.calls[0]["auth"] == (ADMIN, ADMIN_PASSWORD), \
        "the pair written to the environment's encrypted file is the pair sent"


def test_an_environment_that_names_no_admin_and_no_token_is_refused_before_anything_is_sent():
    class ScratchConfig:
        platform_config_dir = "/tmp/platform_config_under_test"

        def get(self, category):
            return {"GRAFANA_ADMIN_PASSWORD": "still-a-secret"}

    with pytest.raises(GrafanaConfigurationError) as raised:
        GrafanaClient.from_environment("dev", "dev1", base_url=BASE_URL, config=ScratchConfig())
    assert "GRAFANA_ADMIN_USER" in str(raised.value)
    assert "static.encrypted.json" in str(raised.value)


def test_an_admin_user_with_no_password_is_refused_rather_than_answered_with_a_401():
    """`helm_utils` only warns about a missing password, and there the install can still run.

    Here the read is the whole command, and an empty password sends a real request that grafana
    rejects for the wrong reason: it would say *the credential* is bad when what is missing is the
    password that belongs to a user who does exist.
    """
    class ScratchConfig:
        platform_config_dir = "/tmp/platform_config_under_test"

        def get(self, category):
            return {"GRAFANA_ADMIN_USER": ADMIN}

    session = FakeSession()
    with pytest.raises(GrafanaConfigurationError) as raised:
        client = GrafanaClient.from_environment("dev", "dev1", base_url=BASE_URL,
                                                config=ScratchConfig())
        client.session = session
        client.find_user(EMAIL)
    assert "GRAFANA_ADMIN_PASSWORD" in str(raised.value), str(raised.value)
    assert session.calls == [], "the refusal is before the request that would have answered 401"


def test_a_blank_base_url_is_refused_because_the_scripts_default_made_the_row_vacuous():
    """`http://127.0.0.1:31827` was the answer to an empty argument. This is the fix for that.

    It is not that a tunnel on 31827 is wrong — it is that a *default* is the wrong way to pick an
    address, because the address is the one fact here that a lane cannot check by reading the
    config.
    """
    with pytest.raises(GrafanaConfigurationError) as raised:
        client_with(base_url="", session=FakeSession())
    assert "127.0.0.1:31827" in str(raised.value), str(raised.value)


def test_an_address_with_no_scheme_is_not_an_address():
    with pytest.raises(GrafanaConfigurationError) as raised:
        client_with(base_url="grafana.test:3000")
    assert "scheme" in str(raised.value)


def test_the_default_session_is_the_one_the_suite_replaces_so_that_no_test_opens_a_socket(
        monkeypatch):
    """The seam is `requests.Session`, because that is the object that would bind a port.

    A client built with no session argument still goes through the fake, so the injectable session
    is not an accommodation for the tests — it is how the class reaches the network at all, and
    this is the proof that the class has no other door.
    """
    made = []

    def factory():
        session = FakeSession(response=ok({"users": [], "totalCount": 0}))
        made.append(session)
        return session

    monkeypatch.setattr(grafana_client.requests, "Session", factory)
    client = GrafanaClient(base_url=BASE_URL, admin_user=ADMIN, admin_password=ADMIN_PASSWORD)
    assert client.find_user(EMAIL) is None
    assert len(made) == 1 and made[0].calls, \
        "the client built its own session and the fake never ran"


# ---------------------------------------------------------------------------------------
# reading the directory
# ---------------------------------------------------------------------------------------


def test_the_search_is_asked_for_the_address_and_not_for_a_fragment_of_it():
    session = FakeSession(response=ok({"users": [], "totalCount": 0}))
    client_with(session).find_user(EMAIL)
    params = session.calls[0]["params"]
    assert params["query"] == EMAIL, params
    assert params["sort"] == "email-asc" and params["perpage"] == 10, params
    assert "?query" not in session.calls[0]["url"], \
        "the bash interpolated the address into the URL itself, which is how a character in an "\
        "email became a path nobody asked for"


def test_an_account_that_exists_is_found_by_its_email_and_reports_the_id_grafana_named():
    session = FakeSession(response=ok({"totalCount": 1, "users": [
        {"id": 17, "login": EMAIL, "email": EMAIL, "name": NAME}]}))
    found = client_with(session).find_user(EMAIL)
    assert found.user_id == 17 and found.email == EMAIL
    assert session.calls[0]["params"]["query"] == EMAIL


def test_a_search_that_matches_somebody_elses_name_does_not_report_the_address_as_taken():
    """Grafana's `query` matches login and name too, and bash took `.users[0]`.

    So `create_new_user_for_team` could believe a new address was occupied by a person whose name
    merely contains the string, skip the create, and exit 0. The filter is what makes "already
    exists" mean *this* account.
    """
    session = FakeSession(response=ok({"totalCount": 1, "users": [
        {"id": 42, "login": "grafadmin", "email": "root@other.test", "name": "mranderson fan"}]}))
    found = client_with(session).find_user(EMAIL)
    assert found is None, "a fuzzy hit is not the account asked for"


def test_the_email_match_is_case_insensitive_the_way_grafanas_is():
    session = FakeSession(response=ok({"users": [
        {"id": 17, "login": EMAIL, "email": EMAIL.upper(), "name": NAME}]}))
    assert client_with(session).find_user(EMAIL).user_id == 17


def test_two_accounts_sharing_one_email_is_not_resolved_by_picking_the_first():
    session = FakeSession(response=ok({"users": [
        {"id": 1, "login": "a", "email": EMAIL, "name": NAME},
        {"id": 2, "login": "b", "email": EMAIL, "name": NAME}]}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).find_user(EMAIL)
    assert "1" in str(raised.value) and "2" in str(raised.value)


def test_an_empty_body_from_the_search_is_a_failure_and_not_an_absent_user():
    """The row's own epitaph, as a single assertion.

    Bash: `curl` writes nothing into a command substitution, `jq '.users[0]'` on the empty string
    exits **0** and prints nothing, and `create_new_user_for_team` compares that nothing to the
    string `null`, decides the user exists, prints a skip and exits 0. This is the one case the
    ticket exists to close: nothing arrived, so nothing is claimed.
    """
    session = FakeSession(response=FakeResponse(200, text=""))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).find_user(EMAIL)
    message = str(raised.value)
    assert "empty body" in message, message
    assert "200" in message, "the status is the whole reason this is not a connection error"
    assert "already exists" not in message, \
        "and it must not be reported as a skip, which is precisely what the bash did"


def test_a_search_that_answers_no_directory_at_all_is_not_read_as_an_absent_account():
    """An empty `users` array is an answer. An object with no `users` key is not.

    Getting those two wrong is the same bug with a different face: one guesses "not there" and
    creates a duplicate, the other guesses "not there" and creates an account that grafana already
    has, and both come out green.
    """
    session = FakeSession(response=ok({"totalCount": 7}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).find_user(EMAIL)
    assert "users" in str(raised.value), str(raised.value)


def test_a_non_json_body_says_who_answered_rather_than_the_request_it_failed():
    page = "<html><body>grafana login</body></html>"
    session = FakeSession(response=FakeResponse(200, text=page))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).find_user(EMAIL)
    assert "not JSON" in str(raised.value) and "grafana login" in str(raised.value)


def test_the_org_roster_is_the_read_the_probe_makes_and_it_answers_or_raises():
    session = FakeSession(response=ok({"users": [{"login": EMAIL}], "totalCount": 1}))
    assert client_with(session).org_members() == [{"login": EMAIL}]
    assert "users" in session.calls[0]["url"]


# ---------------------------------------------------------------------------------------
# creating
# ---------------------------------------------------------------------------------------


def test_a_created_account_carries_the_id_grafana_named_and_the_password_it_was_given():
    session = FakeSession(answers={("POST", grafana_client.ADMIN_USERS): ok({"id": 17,
                                                              "message": "User created"})})
    account = client_with(session).create_user(EMAIL, NAME, PASSWORD)
    assert account.user_id == 17 and account.created is True
    assert account.password == PASSWORD
    body = session.calls[0]["json"]
    assert body == {"name": NAME, "email": EMAIL, "login": EMAIL, "password": PASSWORD}, body


def test_a_create_that_named_no_password_returns_the_one_this_run_generated():
    session = FakeSession(response=ok({"id": 17}))
    account = client_with(session).create_user(EMAIL, NAME)
    assert account.created is True
    assert account.password and len(account.password) > 20
    assert account.password == session.calls[0]["json"]["password"], \
        "the generated password is returned because the caller has to store it somewhere"


def test_a_generated_password_is_made_in_this_process_because_the_helper_used_gpg(
        monkeypatch):
    """`gpg --gen-random --armor 1 14` is a subprocess, and a library that needs no shell should
    not acquire one to make a string. `secrets` answers the same need; the assertion is about
    which generator is called, not about the randomness."""
    called = []

    def fake_token_urlsafe(length):
        called.append(length)
        return "generated-in-process"

    monkeypatch.setattr(grafana_client.secrets, "token_urlsafe", fake_token_urlsafe)
    session = FakeSession(response=ok({"id": 17}))
    account = client_with(session).create_user(EMAIL, NAME)
    assert called == [32], called
    assert account.password == "generated-in-process"


def test_a_200_that_named_no_user_is_a_failure_rather_than_a_success():
    """The create side of the same hole. `jq -r '.id'` on nothing prints `null`, and the payload
    that came back with no id is not evidence that anybody exists."""
    session = FakeSession(response=ok({"message": "User created"}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).create_user(EMAIL, NAME, PASSWORD)
    assert "no user id" in str(raised.value), str(raised.value)
    assert "no account is" in str(raised.value)


def test_an_empty_body_from_the_create_raises_too_because_nothing_was_written():
    session = FakeSession(response=FakeResponse(201, text=""))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).create_user(EMAIL, NAME, PASSWORD)
    assert "empty body" in str(raised.value) and "201" in str(raised.value)


def test_an_account_that_is_already_there_is_reported_as_existing_and_creates_nothing():
    session = FakeSession(answers={
        ("GET", grafana_client.USER_SEARCH): ok({"totalCount": 1, "users": [
            {"id": 17, "login": EMAIL, "email": EMAIL, "name": NAME}]}),
    })
    account = client_with(session).ensure_account(EMAIL, NAME, PASSWORD)
    assert account.created is False and account.user_id == 17
    assert account.password is None, \
        "an account this run did not write has a password it did not set"
    assert session.requested == [("GET", f"{BASE_URL}{grafana_client.USER_SEARCH}")], \
        "the existing account was not created a second time"


def test_an_account_that_exists_and_a_password_named_for_it_says_the_password_was_not_applied(
        caplog):
    """Silence here would be a lie: the config has a password, the account has a different one,
    and a rerun that quietly keeps the old login has to say so rather than store the fiction."""
    session = FakeSession(response=ok({"totalCount": 1, "users": [
        {"id": 17, "login": EMAIL, "email": EMAIL, "name": NAME}]}))
    with caplog.at_level(logging.WARNING):
        account = client_with(session).ensure_account(EMAIL, NAME, PASSWORD)
    assert account.password is None and account.created is False
    assert any("not applied" in record.message for record in caplog.records), caplog.text


def test_grafanas_duplicate_answer_is_read_as_existing_and_not_as_a_failure():
    """The other half of "already exists", from the response that actually says it: a 412 whose
    body names the collision. Bash recognised it from the absence of output."""
    found = FakeResponse(200, payload={"users": [
        {"id": 17, "login": EMAIL, "email": EMAIL, "name": NAME}], "totalCount": 1})
    session = FakeSession(answers={
        ("GET", grafana_client.USER_SEARCH): _then(ok({"users": [], "totalCount": 0}), found),
        ("POST", grafana_client.ADMIN_USERS): FakeResponse(
            412, payload={"message": "Error: User already exists"}),
    })

    account = client_with(session).ensure_account(EMAIL, NAME, PASSWORD)
    assert account.created is False and account.user_id == 17
    assert account.password is None


def test_a_412_that_says_something_else_is_grafanas_words_and_not_a_collision():
    session = FakeSession(answers={("POST", grafana_client.ADMIN_USERS): FakeResponse(
        412, payload={"message": "Password should meet complexity requirements"})})
    with pytest.raises(GrafanaError) as raised:
        client_with(session).create_user(EMAIL, NAME, PASSWORD)
    assert "complexity" in str(raised.value), "quote what grafana said, do not paraphrase it"
    assert raised.value.status_code == 412


def test_a_duplicate_that_grafana_refuses_to_name_is_reported_as_a_failure():
    """412 says duplicate, the search says nobody. Either way nothing was created, so the message
    has to say that and not blame an account that may not exist."""
    session = FakeSession(answers={
        ("GET", grafana_client.USER_SEARCH): ok({"users": [], "totalCount": 0}),
        ("POST", grafana_client.ADMIN_USERS): FakeResponse(
            412, payload={"message": "Error: User already exists"}),
    })
    with pytest.raises(GrafanaError) as raised:
        client_with(session).create_user(EMAIL, NAME, PASSWORD)
    assert "Nothing was created" in str(raised.value), str(raised.value)


# ---------------------------------------------------------------------------------------
# the two scripts' two entry points, as two methods
# ---------------------------------------------------------------------------------------


def test_a_read_only_account_is_created_with_the_viewer_role_the_name_always_meant():
    session = FakeSession(answers={("GET", grafana_client.USER_SEARCH): ok({"users": [],
                                                                            "totalCount": 0})},
                          response=ok({"id": 17}))
    account = client_with(session).create_read_only_account(EMAIL, NAME, PASSWORD)
    assert account.created is True
    body = session.calls[-1]["json"]
    assert body["orgRole"] == "Viewer", body


def test_the_scripts_read_only_account_sends_no_role_and_gets_whatever_the_org_defaults_to():
    """`create_grafana_read_only_account.sh` set no role, so "read only" was only ever a filename.

    The method is that behaviour unchanged when asked for it, so the drift is a choice an operator
    makes on the command line and not something the library decided quietly.
    """
    session = FakeSession(answers={("GET", grafana_client.USER_SEARCH): ok({"users": [],
                                                                            "totalCount": 0})},
                          response=ok({"id": 17}))
    account = client_with(session).ensure_account(EMAIL, NAME, PASSWORD, org_role=None)
    assert account.created is True, "the flow is read-then-write, which is what the script ran"
    assert session.calls[-1]["json"].get("orgRole") is None, session.calls[-1]["json"]


# ---------------------------------------------------------------------------------------
# what the helper's unused arguments would have done
# ---------------------------------------------------------------------------------------


def test_setting_a_password_addresses_the_account_grafana_named_and_not_the_email():
    session = FakeSession(response=ok({}))
    client_with(session).set_password(17, PASSWORD)
    assert session.calls[0]["method"] == "PUT"
    assert session.calls[0]["url"].endswith("/api/admin/users/17/password"), session.calls[0]["url"]
    assert session.calls[0]["json"] == {"password": PASSWORD}


def test_adding_an_account_to_a_team_is_the_call_the_helper_named_and_never_made():
    """`create_user` took `team_id`, threaded it along, and dropped it in the payload. This is the
    call that sentence describes, held back until a decision lands on whether the config's rows
    carry a
    team — so nothing in this tree calls it, and the test is what keeps it from rotting."""
    session = FakeSession(response=ok({"message": "Member added"}))
    client_with(session).add_user_to_team(17, 2)
    call = session.calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/api/teams/2/members"), call
    assert call["json"] == {"userId": 17}


# ---------------------------------------------------------------------------------------
# failures that are not grafana's fault, and failures that are
# ---------------------------------------------------------------------------------------


def test_a_401_is_reported_as_a_rejected_credential_and_says_which_key_was_sent():
    session = FakeSession(response=FakeResponse(401, payload={"message": "Unauthorized"}))
    with pytest.raises(GrafanaUnauthorizedError) as raised:
        client_with(session).find_user(EMAIL)
    message = str(raised.value)
    assert "401" in message and "Unauthorized" in message, message
    assert "GRAFANA_ADMIN_USER" in message, \
        "the advice has to name the credential that was used, or the operator guesses"
    assert raised.value.status_code == 401


def test_a_401_from_the_token_path_does_not_blame_the_admin_password():
    session = FakeSession(response=FakeResponse(401, payload={"message": "Unauthorized"}))
    client = client_with(session=session, service_account_token=TOKEN)
    with pytest.raises(GrafanaUnauthorizedError) as raised:
        client.find_user(EMAIL)
    assert "GRAFANA_SERVICE_ACCOUNT_TOKEN" in str(raised.value)


def test_a_forbidden_that_is_not_about_credentials_still_carries_grafanas_message():
    session = FakeSession(response=FakeResponse(403, payload={"message": "Permission denied"}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).create_user(EMAIL, NAME, PASSWORD)
    assert "403" in str(raised.value) and "Permission denied" in str(raised.value)


def test_a_500_is_not_read_as_an_absent_account():
    session = FakeSession(response=FakeResponse(500, payload={"message": "db is on fire"}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).find_user(EMAIL)
    assert "500" in str(raised.value) and "db is on fire" in str(raised.value)


def test_an_address_that_refuses_the_connection_is_not_reported_as_a_bad_credential():
    """`curl` inside `$(...)` lost this one entirely, and the row stayed green.

    The distinction the client has to get right: nothing answered is not a rejected credential,
    and the fix is an address rather than a password.
    """
    session = FakeSession(error=requests.ConnectionError("Connection refused"))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).find_user(EMAIL)
    message = str(raised.value)
    assert "not a rejected credential" in message, message
    assert "Connection refused" in message
    assert raised.value.status_code is None


def test_a_timeout_raises_the_same_kind_of_error_as_a_refused_connection():
    session = FakeSession(error=requests.Timeout("timed out"))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).org_members()
    assert "no answer" in str(raised.value).lower(), str(raised.value)


def test_a_rejected_create_does_not_repeat_the_password_it_was_refusing():
    """The password goes in the request body, which is where it belongs; the exception is a
    message an operator pastes into a ticket, and a secret in a log line is a second leak."""
    session = FakeSession(response=FakeResponse(400, payload={"message": "email is invalid"}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).create_user(EMAIL, NAME, PASSWORD)
    message = str(raised.value)
    assert PASSWORD not in message, message
    assert "email is invalid" in message
    assert session.calls[0]["json"]["password"] == PASSWORD, "the request itself still carries it"


def _then(*responses):
    """A call that answers differently every time, for the flow that reads, writes, reads again."""
    state = {"i": 0}

    def answer(_call):
        response = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        return response

    return answer
