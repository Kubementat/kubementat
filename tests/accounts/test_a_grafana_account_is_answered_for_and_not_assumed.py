"""`GrafanaClient` answers, or it raises. Nothing in between, and no socket."""

import json
import logging

import pytest
import requests

import kubementat.infrastructure.grafana.client as grafana_client
from kubementat.core.config.config import Config
from kubementat.errors import (
    GrafanaConfigurationError,
    GrafanaError,
    GrafanaUnauthorizedError,
)
from kubementat.infrastructure.grafana.client import GrafanaClient

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
    """`requests.Session` for this module: remembers calls, answers from a queue or a callable."""

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
    """A client wired to the fake transport, with sane defaults the tests do not repeat."""
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
    """The same reader `infrastructure/helm.py` uses, over a real `Config` on a real path."""
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
    """`HelmUtils` in `infrastructure/helm.py` only warns about a missing password, and there the
    install can still run.
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
    """`http://127.0.0.1:31827` was the answer to an empty argument. This is the fix for that."""
    with pytest.raises(GrafanaConfigurationError) as raised:
        client_with(base_url="", session=FakeSession())
    assert "127.0.0.1:31827" in str(raised.value), str(raised.value)


def test_an_address_with_no_scheme_is_not_an_address():
    with pytest.raises(GrafanaConfigurationError) as raised:
        client_with(base_url="grafana.test:3000")
    assert "scheme" in str(raised.value)


# ---------------------------------------------------------------------------------------
# reading the directory
# ---------------------------------------------------------------------------------------


def test_two_accounts_sharing_one_email_is_not_resolved_by_picking_the_first():
    session = FakeSession(response=ok({"users": [
        {"id": 1, "login": "a", "email": EMAIL, "name": NAME},
        {"id": 2, "login": "b", "email": EMAIL, "name": NAME}]}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).find_user(EMAIL)
    assert "1" in str(raised.value) and "2" in str(raised.value)


def test_a_search_that_answers_no_directory_at_all_is_not_read_as_an_absent_account():
    """An empty `users` array is an answer. An object with no `users` key is not."""
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
    """`gpg --gen-random --armor 1 14` is a subprocess, and a library that needs no shell should not
    acquire one to make a string. `secrets` answers the same need; the assertion is about which
    generator is called, not about the randomness.
    """
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
    """The create side of the same hole. `jq -r '.id'` on nothing prints `null`, and the payload that
    came back with no id is not evidence that anybody exists.
    """
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
    """Silence here would be a lie: the config has a password, the account has a different one, and a
    rerun that quietly keeps the old login has to say so rather than store the fiction.
    """
    session = FakeSession(response=ok({"totalCount": 1, "users": [
        {"id": 17, "login": EMAIL, "email": EMAIL, "name": NAME}]}))
    with caplog.at_level(logging.WARNING):
        account = client_with(session).ensure_account(EMAIL, NAME, PASSWORD)
    assert account.password is None and account.created is False
    assert any("not applied" in record.message for record in caplog.records), caplog.text


def test_a_duplicate_that_grafana_refuses_to_name_is_reported_as_a_failure():
    """412 says duplicate, the search says nobody. Either way nothing was created, so the message has
    to say that and not blame an account that may not exist.
    """
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
    """`create_user` took `team_id`, threaded it along, and dropped it in the payload. This is the call
    that sentence describes, held back until a decision lands on whether the config's rows carry a
    team — so nothing in this tree calls it, and the test is what keeps it from rotting.
    """
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


def test_an_address_that_refuses_the_connection_is_not_reported_as_a_bad_credential():
    """`curl` inside `$(...)` lost this one entirely, and the row stayed green."""
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
    """The password goes in the request body, which is where it belongs; the exception is a message an
    operator pastes into a bug report, and a secret in a log line is a second leak.
    """
    session = FakeSession(response=FakeResponse(400, payload={"message": "email is invalid"}))
    with pytest.raises(GrafanaError) as raised:
        client_with(session).create_user(EMAIL, NAME, PASSWORD)
    message = str(raised.value)
    assert PASSWORD not in message, message
    assert "email is invalid" in message
    assert session.calls[0]["json"]["password"] == PASSWORD, "the request itself still carries it"
