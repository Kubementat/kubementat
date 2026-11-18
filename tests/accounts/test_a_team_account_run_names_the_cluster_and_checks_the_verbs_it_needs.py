"""Before a team account is created, the cluster is named and its verbs are checked."""

import base64
import json
import logging
from types import SimpleNamespace

import pytest
from conftest import is_a_write

import kubementat.infrastructure.user_management as user_management
from cli.consent import team_account_consent
from kubementat.automations.accounts.accounts import (
    create_team_account,
    create_team_account_read_only,
)
from kubementat.consent import Consent
from kubementat.core.identity.context import Context
from kubementat.errors import (
    ClusterAccessDeniedError,
    ConfirmationDeclinedError,
    ConfirmationUnavailableError,
)

# The credential the cluster is holding for the account by the time the flow prints.
ACCOUNT_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZlYXR1cmUifQ.payload.signature"


# What the cluster has been told to refuse, in `(verb, resource)` pairs. A list rather than one
# registered answer per call, because a test that denies two verbs calls `cluster_refuses` twice
# and the second registration would replace the first — the assert that every denial is reported
# would then read a cluster that granted one of them.
DENIED_VERBS = []


def cluster_grants_everything(recorded_api):
    """Say `yes` to each verb the flow checks, and `no` to whatever `cluster_refuses` denied."""
    DENIED_VERBS.clear()
    recorded_api.answer("create_self_subject_access_review",
                        lambda **kwargs: _review(allowed=not _is_denied(kwargs.get("body"))))
    return recorded_api


def cluster_refuses(recorded_api, verb, resource):
    """One verb the cluster will not grant."""
    DENIED_VERBS.append((verb, resource))
    return recorded_api


def _attributes(body):
    """The review's `resource_attributes`, from either shape."""
    from recorded_operations import review_attributes

    found = review_attributes(body)
    return found


def _is_denied(body):
    attributes = _attributes(body)
    return (attributes["verb"], attributes["resource"]) in DENIED_VERBS


def _review(allowed):
    """The shape a `SelfSubjectAccessReview` comes back with, so `allowed` is a decision."""
    return SimpleNamespace(status=SimpleNamespace(allowed=allowed, reason=None))


def cluster_holding_a_token_for_the_account(recorded_api):
    """The token Secret, filled in -- the flow cannot print a kubeconfig without one."""
    def answer(namespace=None, **kwargs):
        return type("SecretList", (), {"items": [type("Secret", (), {
            "type": "kubernetes.io/service-account-token",
            "metadata": type("Meta", (), {
                "name": "ops-account-sa-secret",
                "annotations": {"kubernetes.io/service-account.name": "ops-account"}})(),
            "data": {"token": base64.b64encode(ACCOUNT_TOKEN.encode())},
        })()]})()

    recorded_api.answer("list_namespaced_secret", answer)


@pytest.fixture
def asked_cluster(fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """A cluster that says yes to everything, with both call logs emptied."""
    cluster_grants_everything(recorded_api)
    fake_binaries.respond("kubectl", json.dumps({"clusters": [{"name": "under-test", "cluster": {
        "server": "https://the-cluster-under-test.example.test:6443",
        "certificate-authority-data": "RlklWEFSVElKVFlURVNU="}}]}), subcommand="config.view")
    cluster_holding_a_token_for_the_account(recorded_api)
    recorded_api.calls.clear()
    fake_binaries.take_calls()
    return fixture_repo


@pytest.fixture
def on_a_cluster_that_grants_everything(asked_cluster, monkeypatch):
    """The same cluster, with a terminal that answers `y` when it is asked."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    return Context.build(asked_cluster.environment, asked_cluster.team)


def can_i_asked(recorded_api):
    """The verbs and resources the flow asked the cluster about, in order."""
    asked = []
    for method, kwargs in recorded_api.calls:
        if method != "create_self_subject_access_review":
            continue
        attributes = _attributes(kwargs.get("body"))
        asked.append((attributes["verb"], attributes["resource"]))
    return asked


def client_writes(recorded_api):
    """The calls that changed something, which excludes the permission reviews."""
    return [method for method, _kwargs in recorded_api.calls if is_a_write(method)]


def test_the_cluster_is_named_before_anything_on_it_is_changed(
        on_a_cluster_that_grants_everything, asked_cluster, fake_binaries, recorded_api, caplog):
    """The first thing the operator sees is the cluster, not a permission error."""
    with caplog.at_level(logging.INFO):
        create_team_account(on_a_cluster_that_grants_everything, "ops-account", consent=team_account_consent(yes=False))

    text = "\n".join(record.getMessage() for record in caplog.records)
    named = text.find("You are going to edit users on the following cluster")
    assert named != -1, "the cluster was never named"
    assert "fixture" in text[named:named + 400], (
        "the cluster named is not the one this run is configured for: " + text[named:named + 200])
    wrote = text.find("Creating namespace")
    assert wrote != -1 and named < wrote, "the cluster was named after it had been edited"


def test_a_denied_verb_stops_the_run_before_the_first_write_and_names_it(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """`no` from the cluster is a decision, not a note in the log."""
    cluster_refuses(recorded_api, "create", "rolebindings")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    ctx = Context.build(asked_cluster.environment, asked_cluster.team)

    with pytest.raises(ClusterAccessDeniedError) as raised:
        create_team_account(ctx, "ops-account", consent=team_account_consent(yes=False))

    assert "create rolebindings" in str(raised.value)
    assert client_writes(recorded_api) == [], "it wrote to the cluster after being told no"


def test_answering_no_at_the_prompt_creates_nothing(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The other refusal is the operator's, and it is final."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    ctx = Context.build(asked_cluster.environment, asked_cluster.team)

    with pytest.raises(ConfirmationDeclinedError) as raised:
        create_team_account(ctx, "ops-account", consent=team_account_consent(yes=False))

    assert "Cancelled" in str(raised.value)
    assert client_writes(recorded_api) == []
    assert [call for call in fake_binaries.calls() if "delete" in call.argv] == []


def test_an_answer_that_is_neither_yes_nor_no_is_asked_again(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The prompt loops until it gets a real answer, so typing `maybe` is not a no."""
    answers = iter(["maybe", "nope", "y"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    ctx = Context.build(asked_cluster.environment, asked_cluster.team)

    create_team_account(ctx, "ops-account", consent=team_account_consent(yes=False))

    assert client_writes(recorded_api), "the account was never created"


def test_yes_skips_the_question_and_changes_nothing_else(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The claim behind `--yes`: it answers the question, it does not change the work."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    by_hand = Context.build(asked_cluster.environment, asked_cluster.team)
    create_team_account(by_hand, "ops-account", consent=team_account_consent(yes=False))
    by_hand_operations = recorded_api.operations()
    by_hand_checks = can_i_asked(recorded_api)
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    def no_prompt(prompt=""):
        raise AssertionError("--yes reached a prompt it was told to skip")

    monkeypatch.setattr("builtins.input", no_prompt)
    create_team_account(Context.build(asked_cluster.environment, asked_cluster.team),
        "ops-account", consent=Consent.assumed())

    assert recorded_api.operations() == by_hand_operations
    assert can_i_asked(recorded_api) == by_hand_checks


def test_no_terminal_and_no_yes_is_refused_before_the_cluster_is_asked_anything(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The lane's whole problem, in one line of the contract."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    ctx = Context.build(asked_cluster.environment, asked_cluster.team)

    with pytest.raises(ConfirmationUnavailableError) as raised:
        create_team_account(ctx, "ops-account", consent=team_account_consent(yes=False))

    assert "--yes" in str(raised.value)
    assert client_writes(recorded_api) == []
    assert can_i_asked(recorded_api) == [], "it interrogated the cluster for a run it refused"


def test_the_checks_happen_before_any_write_whichever_command_is_standing_there(
        on_a_cluster_that_grants_everything, asked_cluster, fake_binaries, recorded_api):
    """The gate is the same gate for the read-only variant."""
    create_team_account_read_only(on_a_cluster_that_grants_everything, "ops-account", consent=team_account_consent(yes=False))

    assert can_i_asked(recorded_api) == list(user_management.CLUSTER_ACCESS_CHECKS)
    assert client_writes(recorded_api), "the read-only command wrote nothing"
