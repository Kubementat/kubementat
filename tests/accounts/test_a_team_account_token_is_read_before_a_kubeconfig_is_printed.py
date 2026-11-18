"""The token behind a team account: how it is waited for, and what happens when it is not there."""

import base64
import json
import logging
import time

import pytest
from cluster_fixture import TARGET_DEV, UNREAD_ROOT, configuration_only_context

import kubementat.infrastructure.user_management as user_management
from kubementat.automations.accounts.accounts import create_team_account
from kubementat.consent import Consent
from kubementat.errors import ServiceAccountTokenNotFoundError
from kubementat.infrastructure.kubernetes import KubernetesUtils

NAMESPACE = "ops-deployments"
ACCOUNT = "ops-account"
TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZlYXR1cmUifQ.a-payload.a-signature"
SERVER = "https://the-cluster-under-test.example.test:6443"
AUTHORITY = "RlklWEFSVElKVFlURVNU="

# The flow's own patience, shortened. A test that proves a wait exists must not have to spend
# it: ten seconds of real cluster buys nothing, and the polling loop is what is under test.
FAST_WAIT_SECONDS = 0.4
POLL_INTERVAL_SECONDS = 0.05


def secret_holding(*items):
    return lambda **kwargs: type("SecretList", (), {"items": list(items)})()


def token_secret(name="ops-account-sa-secret", for_account=ACCOUNT, token=TOKEN,
                 secret_type="kubernetes.io/service-account-token"):
    """A token Secret as the client returns it: type and annotation say whose token it is."""
    return type("Secret", (), {
        "type": secret_type,
        "metadata": type("Meta", (), {
            "name": name,
            "annotations": {"kubernetes.io/service-account.name": for_account}})(),
        "data": None if token is None else {"token": base64.b64encode(token.encode())},
    })()


@pytest.fixture
def cluster_client(recorded_api):
    """A real `KubernetesUtils`, with its generated clients swapped for the recorder's."""
    return KubernetesUtils(TARGET_DEV, api_client=recorded_api)


@pytest.fixture
def waits_quickly(monkeypatch):
    """Shorten the token wait so that a test about waiting can finish inside a second."""
    monkeypatch.setattr(user_management, "TOKEN_WAIT_SECONDS", FAST_WAIT_SECONDS)
    monkeypatch.setattr(user_management, "TOKEN_POLL_INTERVAL_SECONDS", POLL_INTERVAL_SECONDS)


def test_a_secret_that_never_fills_in_raises_rather_than_returning_nothing(cluster_client,
                                                                          recorded_api,
                                                                          waits_quickly):
    """The wait is bounded, and the deadline is an error rather than a `None`."""
    recorded_api.answer("list_namespaced_secret", secret_holding(token_secret(token=None)))

    start = time.monotonic()
    with pytest.raises(ServiceAccountTokenNotFoundError):
        user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)

    elapsed = time.monotonic() - start
    assert FAST_WAIT_SECONDS <= elapsed < 5, f"the wait was not the wait: {elapsed:.2f}s"


def test_an_empty_token_in_the_secret_is_treated_as_no_token_at_all(cluster_client, recorded_api,
                                                                   waits_quickly):
    """A Secret with a key of `token` and no value is the same state as no Secret."""
    recorded_api.answer("list_namespaced_secret",
                        secret_holding(token_secret(token="")))

    with pytest.raises(ServiceAccountTokenNotFoundError):
        user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)


@pytest.fixture
def a_cluster_that_has_no_token_yet(fixture_repo, dry_run_env, fake_binaries, recorded_api,
                                    monkeypatch):
    """Everything the create needs except the thing the controller has not done."""
    for verb, resource in user_management.CLUSTER_ACCESS_CHECKS:
        fake_binaries.respond("kubectl", "yes\n", subcommand=f"auth.can-i.{verb}.{resource}")
    fake_binaries.respond("kubectl", json.dumps({"clusters": [{
        "name": "under-test", "cluster": {"server": SERVER,
                                          "certificate-authority-data": AUTHORITY}}]}),
        subcommand="config.view")
    cluster_holding(fixture_repo, recorded_api)
    monkeypatch.setattr(user_management, "TOKEN_WAIT_SECONDS", 0.0)
    recorded_api.answer("list_namespaced_secret", secret_holding(token_secret(token=None)))
    recorded_api.calls.clear()
    fake_binaries.take_calls()
    return fixture_repo


def cluster_holding(fixture_repo, recorded_api):
    """The namespaces the flow will look for: both of the team's, and all three tunnels."""
    namespaces = ["default", "kube-system", "kube-public", "kube-node-lease",
                  fixture_repo.app_namespace, fixture_repo.pipeline_namespace,
                  "tekton-pipelines", "grafana", "polaris"]
    recorded_api.answer("list_namespace", lambda **kwargs: type("List", (), {"items": [
        type("Namespace", (), {"metadata": type("M", (), {"name": n})})()
        for n in namespaces]})())


def test_a_run_that_cannot_read_a_token_prints_no_kubeconfig(
        a_cluster_that_has_no_token_yet, fixture_repo, recorded_api, fake_binaries, caplog):
    """The whole point of the wait, measured where the operator reads."""
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)

    with caplog.at_level(logging.INFO), pytest.raises(ServiceAccountTokenNotFoundError):
        create_team_account(context, ACCOUNT, consent=Consent.assumed())

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Please put the settings below" not in text, "it printed a credential it did not have"
    assert "token: " not in text and not text.rstrip().endswith("token:"), \
        "an empty credential reached the log"
    assert "ops-account-sa-secret" in "\n".join(
        str(kwargs) for _method, kwargs in recorded_api.calls), \
        "the Secret the token was expected in was never requested"


def test_the_emitter_refuses_a_credential_that_is_not_there():
    """The second gate: the function that prints the text will not print an empty one."""
    with pytest.raises(ValueError):
        user_management.kubeconfig_for_service_account(
            configuration_only_context("dev", "ops", platform_config_dir=UNREAD_ROOT),
            ACCOUNT, NAMESPACE, "   ", SERVER, AUTHORITY)
