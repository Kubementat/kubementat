"""The token behind a team account: how it is waited for, and what happens when it is not there.

A service account carries no credentials. Since 1.24 a cluster does not mint a token for one;
the flow has to request a Secret that names the account, and a controller fills that Secret in
*after* it was created. That gap is the whole difference between the bash and the code: bash
read the Secret once, immediately, and if the controller had not reached it, interpolated
nothing into a heredoc and printed

    token:

as a kubeconfig, with exit code 0. Every later failure that produces it -- an authentication
error, a `can-i` that answers nothing -- traces back to a credential that was never there. This
module waits, and refuses rather than printing what it did not get.

What is pinned:

* the token is read from the Secret that *claims* the account, by annotation, and handed over
  whole;
* a Secret the controller has not filled in yet is waited for, with a bounded wait, so a
  control plane that is behind is a delay and not a wrong answer;
* a token that never arrives is an error where it is read, and the command prints no kubeconfig
  at all -- the failure this flow shipped with for its whole bash life.
"""

import base64
import json
import logging
import time

import pytest

from kubementat import user_management
from kubementat.automation import Automation
from kubementat.kubernetes_utils import KubernetesUtils, ServiceAccountTokenNotFoundError
from cluster_fixture import TARGET_DEV, UNREAD_ROOT, configuration_only_context

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
    """A real `KubernetesUtils`, with its generated clients swapped for the recorder's.

    The primitives take the utils object rather than an API, so that is what is constructed --
    a test that reached for the API directly would be testing something no caller can build.
    The recorder is injected rather than patched in, so nothing on this machine is read and
    no kubeconfig is opened by building this object at all.
    """
    return KubernetesUtils(TARGET_DEV, api_client=recorded_api)


@pytest.fixture
def waits_quickly(monkeypatch):
    """Shorten the token wait so that a test about waiting can finish inside a second."""
    monkeypatch.setattr(user_management, "TOKEN_WAIT_SECONDS", FAST_WAIT_SECONDS)
    monkeypatch.setattr(user_management, "TOKEN_POLL_INTERVAL_SECONDS", POLL_INTERVAL_SECONDS)


def test_the_token_is_read_from_the_secret_that_claims_the_account(cluster_client, recorded_api,
                                                                 waits_quickly):
    """The name is a convention, the annotation is the fact.

    The bash grepped `kubectl get secret` for a name ending in `-token`. A Secret named after
    the account but annotated for somebody else is the same failure with a nicer name, so the
    annotation wins here.
    """
    recorded_api.answer("list_namespaced_secret", secret_holding(token_secret()))

    token = user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)

    assert token == TOKEN


def test_a_secret_the_controller_has_not_filled_in_yet_is_waited_for(cluster_client, recorded_api,
                                                                    waits_quickly, caplog):
    """An empty Secret is the controller being behind, not a credential that is missing.

    bash read once and gave up, which is how `token: ` reached an operator's terminal. Here the
    read is repeated until the deadline, and the log says it is waiting -- the only way someone
    watching a slow control plane knows the command is not hung.
    """
    answers = [token_secret(token=None), token_secret(token=None), token_secret()]
    state = {"calls": 0}

    def answer(namespace=None, **kwargs):
        secret = answers[min(state["calls"], len(answers) - 1)]
        state["calls"] += 1
        return secret_holding(secret)(namespace=namespace)

    recorded_api.answer("list_namespaced_secret", answer)

    with caplog.at_level(logging.INFO):
        token = user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)

    assert token == TOKEN
    assert state["calls"] >= 3, "it did not wait for the controller at all"
    assert "not filled in yet" in caplog.text


def test_a_secret_that_never_fills_in_raises_rather_than_returning_nothing(cluster_client,
                                                                          recorded_api,
                                                                          waits_quickly):
    """The wait is bounded, and the deadline is an error rather than a `None`.

    An unbounded wait is a lane that hangs forever; a bounded wait that returns nothing is a
    kubeconfig with an empty `token:` field. Neither is what the command is for.
    """
    recorded_api.answer("list_namespaced_secret", secret_holding(token_secret(token=None)))

    start = time.monotonic()
    with pytest.raises(ServiceAccountTokenNotFoundError):
        user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)

    elapsed = time.monotonic() - start
    assert FAST_WAIT_SECONDS <= elapsed < 5, f"the wait was not the wait: {elapsed:.2f}s"


def test_a_token_secret_that_does_not_exist_names_the_call_that_creates_it(cluster_client,
                                                                           recorded_api,
                                                                           waits_quickly):
    """"No Secret" and "no token yet" are different problems, and the message says which.

    A cluster does not mint one by itself, so an operator told only "not found" will wait for a
    controller that was never asked to do anything.
    """
    recorded_api.answer("list_namespaced_secret", secret_holding())

    with pytest.raises(ServiceAccountTokenNotFoundError) as raised:
        user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)

    assert "create_or_replace_service_account_token_secret" in str(raised.value)


def test_a_secret_that_belongs_to_another_account_is_not_read_as_this_accounts(
        cluster_client, recorded_api, waits_quickly):
    """The annotation is the whole identity of the Secret.

    Two accounts in one namespace, one Secret: reading the wrong one yields a kubeconfig that
    works for somebody else, which is worse than one that does not work at all.
    """
    recorded_api.answer("list_namespaced_secret",
                        secret_holding(token_secret(for_account="somebody-else")))

    with pytest.raises(ServiceAccountTokenNotFoundError):
        user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)


def test_an_empty_token_in_the_secret_is_treated_as_no_token_at_all(cluster_client, recorded_api,
                                                                   waits_quickly):
    """A Secret with a key of `token` and no value is the same state as no Secret.

    `data: {token: ""}` is what a controller that is halfway through a write looks like, and it
    is the shape that decodes into a `token:` line. It has to be refused, not decoded.
    """
    recorded_api.answer("list_namespaced_secret",
                        secret_holding(token_secret(token="")))

    with pytest.raises(ServiceAccountTokenNotFoundError):
        user_management.read_service_account_token(cluster_client, NAMESPACE, ACCOUNT)


@pytest.fixture
def a_cluster_that_has_no_token_yet(fixture_repo, dry_run_env, fake_binaries, recorded_api,
                                    monkeypatch):
    """Everything the create needs except the thing the controller has not done.

    The Secret is there and empty, which is what a real cluster looks like in the half-second
    after it is written. The flow gets that state and has to decide what to do with it, and the
    only answer that is not a lie is to stop before printing anything.
    """
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
    """The whole point of the wait, measured where the operator reads.

    The account itself is written -- namespaces, ServiceAccount, Secret, Roles, Bindings -- and
    the credential is what is missing. What must not happen is the second half of the bash's
    answer: exit code 0, a banner, and a `token:` line to paste.
    """
    automation = Automation(configuration_only_context(fixture_repo.environment, fixture_repo.team))

    with caplog.at_level(logging.INFO), pytest.raises(ServiceAccountTokenNotFoundError):
        automation.create_team_account(ACCOUNT, assume_yes=True)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Please put the settings below" not in text, "it printed a credential it did not have"
    assert "token: " not in text and not text.rstrip().endswith("token:"), \
        "an empty credential reached the log"
    assert "ops-account-sa-secret" in "\n".join(
        str(kwargs) for _method, kwargs in recorded_api.calls), \
        "the Secret the token was expected in was never requested"


def test_the_emitter_refuses_a_credential_that_is_not_there():
    """The second gate: the function that prints the text will not print an empty one.

    `read_service_account_token` cannot hand back a blank today, so this is not a bug fix -- it
    is the invariant held by the one function whose output is pasted into `~/.kube/config`. A
    caller that later reads a token some other way still cannot write a document that
    authenticates with nothing.
    """
    with pytest.raises(ValueError):
        user_management.kubeconfig_for_service_account(
            configuration_only_context("dev", "ops", platform_config_dir=UNREAD_ROOT),
            ACCOUNT, NAMESPACE, "   ", SERVER, AUTHORITY)
