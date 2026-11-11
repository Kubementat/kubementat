"""Before a team account is created, the cluster is named and its verbs are checked.

`check_cluster_and_access` is the gate in front of every team-account write. The migration
duplicated that gate rather than porting it: `scripts/user_management/user_management_helpers.sh`
held one copy and `scripts/automation/tekton/create_team_account.sh` grew a second beside it,
so the `--yes` escape hatch `390bd61` added to the first never reached the second and
`cap:create-team-account` kept failing in a lane that had no terminal to answer a prompt it
could not skip. There is one implementation now; this is what it owes:

* the cluster is named first, so a person running this against the wrong cluster sees that
  before anything is written;
* a refusal is a cancellation, and it creates or deletes nothing;
* `--yes` skips the question and changes nothing else -- same cluster, same verbs checked,
  same writes;
* no terminal and no `--yes` means nobody agreed, so nothing happens;
* the verbs the flow will use are actually asked about. bash printed the answer of each
  `kubectl auth can-i` and threw it away, which is the same as not checking: a "no" printed is
  a "no" ignored, and the run then went on to fail mid-write on a permission it had just been
  told it did not have.
"""

import base64
import json
import logging
from types import SimpleNamespace

import pytest

from conftest import is_a_write
from click.exceptions import ClickException

from kubementat import user_management
from kubementat.automation import Automation
from kubementat.cluster import Context

# The credential the cluster is holding for the account by the time the flow prints.
ACCOUNT_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZlYXR1cmUifQ.payload.signature"


# What the cluster has been told to refuse, in `(verb, resource)` pairs. A list rather than one
# registered answer per call, because a test that denies two verbs calls `cluster_refuses` twice
# and the second registration would replace the first — the assert that every denial is reported
# would then read a cluster that granted one of them.
DENIED_VERBS = []


def cluster_grants_everything(recorded_api):
    """Say `yes` to each verb the flow checks, and `no` to whatever `cluster_refuses` denied.

    Built from `CLUSTER_ACCESS_CHECKS` rather than spelled out here: a verb the flow starts to
    need and nobody answered shows up as a denial instead of a passing test. This gate moved
    off `kubectl auth can-i` and onto the `SelfSubjectAccessReview` kubectl's answer is produced
    from, so the answer is registered once for every question the flow is allowed to ask.
    """
    DENIED_VERBS.clear()
    recorded_api.answer("create_self_subject_access_review",
                        lambda **kwargs: _review(allowed=not _is_denied(kwargs.get("body"))))
    return recorded_api


def cluster_refuses(recorded_api, verb, resource):
    """One verb the cluster will not grant.

    The review carries the plural resource name, so this is the comparison that catches a
    table left singular: a singular entry would never match the refusal, and the flow would sail
    past a permission it was denied.
    """
    DENIED_VERBS.append((verb, resource))
    return recorded_api


def _attributes(body):
    """The review's `resource_attributes`, from either shape.

    `cluster_parity.review_attributes` is the same reader the parity reducer uses, imported rather
    than copied here so the two cannot disagree about where the verb lives.
    """
    from cluster_parity import review_attributes

    found = review_attributes(body)
    return found


def _is_denied(body):
    attributes = _attributes(body)
    return (attributes["verb"], attributes["resource"]) in DENIED_VERBS


def _review(allowed):
    """The shape a `SelfSubjectAccessReview` comes back with, so `allowed` is a decision."""
    return SimpleNamespace(status=SimpleNamespace(allowed=allowed, reason=None))


def cluster_holding_a_token_for_the_account(recorded_api):
    """The token Secret, filled in -- the flow cannot print a kubeconfig without one.

    Left unset, every create in this file would spend the token's whole wait failing and then
    raise, which is a state no test here is about.
    """
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
    """A cluster that says yes to everything, with both call logs emptied.

    A shim with no response file prints an empty string, which to this gate is a *denial*, so
    this fixture is what lets the rest of the file mean "the cluster granted that" rather than
    "the test forgot to answer".
    """
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
    """The same cluster, with a terminal that answers `y` when it is asked.

    `input` is patched rather than the terminal so that a test which never reaches the prompt
    and one which does both get a definite answer, not whatever stdin happens to be.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    return Automation(Context.build(asked_cluster.environment, asked_cluster.team))


def can_i_asked(recorded_api):
    """The verbs and resources the flow asked the cluster about, in order.

    The conversion of the preflight, asserted at the only layer it now lives on: the
    `SelfSubjectAccessReview` the API server answers, where a singular resource name is a wrong
    answer and not merely a different spelling.
    """
    asked = []
    for method, kwargs in recorded_api.calls:
        if method != "create_self_subject_access_review":
            continue
        attributes = _attributes(kwargs.get("body"))
        asked.append((attributes["verb"], attributes["resource"]))
    return asked


def can_i_calls(fake_binaries):
    """The `kubectl auth can-i` argv the flow asked for, in order.

    Filtered to the checks, because the flow also asks `kubectl config view` -- for a different
    purpose, and after the writes.
    """
    return [call.argv for call in fake_binaries.calls()
            if call.argv[:3] == ["kubectl", "auth", "can-i"]]


def client_writes(recorded_api):
    """The calls that changed something, which excludes the permission reviews."""
    return [method for method, _kwargs in recorded_api.calls if is_a_write(method)]


def test_the_cluster_is_named_before_anything_on_it_is_changed(
        on_a_cluster_that_grants_everything, asked_cluster, fake_binaries, recorded_api, caplog):
    """The first thing the operator sees is the cluster, not a permission error.

    bash printed the context too, but the check that came after it was decorative, so the
    ordering was the only part of the check that actually worked. This keeps both halves: the
    cluster named is the one the client is bound to, and it is named before the first write.

    Which is why the `Automation` here is built from a resolved `Context` and not from
    `configuration_only_context`: the cluster this line names is now the *target*, so a fixture
    whose target is inert names nothing (`cluster_fixture` says the same thing about itself). The
    address that answers is the fixture's own — `CLUSTER_KUBE_CONTEXT` in its `static.json`, read
    through `Config` — and no kubeconfig on the machine running pytest is consulted.
    """
    automation = on_a_cluster_that_grants_everything

    with caplog.at_level(logging.INFO):
        automation.create_team_account("ops-account")

    text = "\n".join(record.getMessage() for record in caplog.records)
    named = text.find("You are going to edit users on the following cluster")
    assert named != -1, "the cluster was never named"
    assert "fixture" in text[named:named + 400], (
        "the cluster named is not the one this run is configured for: " + text[named:named + 200])
    wrote = text.find("Creating namespace")
    assert wrote != -1 and named < wrote, "the cluster was named after it had been edited"


def test_the_verbs_of_everything_the_flow_is_about_to_do_are_asked_about(
        on_a_cluster_that_grants_everything, asked_cluster, fake_binaries, recorded_api):
    """A check is only worth having if it asks about everything the run then does.

    Both sides are asserted: `CLUSTER_ACCESS_CHECKS` is what the flow plans, the recorded argv
    is what it asked, and the two have to agree verb for verb.
    """
    on_a_cluster_that_grants_everything.create_team_account("ops-account")

    asked = can_i_asked(recorded_api)
    assert asked == list(user_management.CLUSTER_ACCESS_CHECKS), (
        f"the table says {list(user_management.CLUSTER_ACCESS_CHECKS)}, the cluster was asked {asked}")


def test_the_table_names_the_verbs_this_flow_will_actually_use():
    """Namespace, service account, Role, RoleBinding: create, and then update or patch.

    Straight off the table, because the table is the claim. A flow that starts deleting Roles
    will need a verb in here, and a verb that stops being needed is what this says so.
    """
    verbs = set(user_management.CLUSTER_ACCESS_CHECKS)

    assert ("create", "namespaces") in verbs
    for resource in ("serviceaccounts", "roles", "rolebindings"):
        assert ("create", resource) in verbs, f"the flow creates {resource} but never checks it"
        assert ("update", resource) in verbs or ("patch", resource) in verbs, (
            f"the flow replaces {resource} but never checks it")
    # Plural, and said so here because it is the one way this conversion could fail silently:
    # `kubectl auth can-i create serviceaccount` accepts the kind and maps it through discovery,
    # while a `SelfSubjectAccessReview` carries the *resource* name and a singular one answers
    # "no" everywhere. A table that drifted back to singular would pass every other test here.
    assert not [resource for _verb, resource in verbs if resource.endswith("e")
                or not resource.endswith("s")], verbs


def test_a_denied_verb_stops_the_run_before_the_first_write_and_names_it(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """`no` from the cluster is a decision, not a note in the log.

    Without this the run creates the namespace and the account and then fails on the binding,
    leaving a half-configured account on a cluster and an operator guessing what was written.
    """
    cluster_refuses(recorded_api, "create", "rolebindings")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    automation = Automation(Context.build(asked_cluster.environment, asked_cluster.team))

    with pytest.raises(ClickException) as raised:
        automation.create_team_account("ops-account")

    assert "create rolebindings" in str(raised.value)
    assert client_writes(recorded_api) == [], "it wrote to the cluster after being told no"


def test_every_denied_verb_is_reported_and_not_only_the_first(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """One missing permission is an error; five of them deserve the whole list.

    Fixing the table and re-running is cheaper than discovering the next denial one run at a
    time, which is what the bash's per-verb output invited instead.
    """
    cluster_refuses(recorded_api, "create", "rolebindings")
    cluster_refuses(recorded_api, "patch", "roles")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    automation = Automation(Context.build(asked_cluster.environment, asked_cluster.team))

    with pytest.raises(ClickException) as raised:
        automation.create_team_account("ops-account")

    assert "create rolebindings" in str(raised.value)
    assert "patch roles" in str(raised.value)
    assert client_writes(recorded_api) == []


def test_answering_no_at_the_prompt_creates_nothing(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The other refusal is the operator's, and it is final.

    The bash's `set -e` turned a `n` into an abort with no message. A cancellation that says
    "Cancelled" and writes nothing is the same outcome with the explanation attached.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    automation = Automation(Context.build(asked_cluster.environment, asked_cluster.team))

    with pytest.raises(ClickException) as raised:
        automation.create_team_account("ops-account")

    assert "Cancelled" in str(raised.value)
    assert client_writes(recorded_api) == []
    assert [call for call in fake_binaries.calls() if "delete" in call.argv] == []


def test_an_answer_that_is_neither_yes_nor_no_is_asked_again(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The prompt loops until it gets a real answer, so typing `maybe` is not a no.

    A single read of stdin with a fallthrough is how a script answers EOF, and what it leaves
    behind is a run that carried on because nobody had said no.
    """
    answers = iter(["maybe", "nope", "y"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    automation = Automation(Context.build(asked_cluster.environment, asked_cluster.team))

    automation.create_team_account("ops-account")

    assert client_writes(recorded_api), "the account was never created"


def test_yes_skips_the_question_and_changes_nothing_else(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The claim behind `--yes`: it answers the question, it does not change the work.

    The flow runs twice against the same cluster, once with a terminal that says `y` and once
    with `--yes`, and every operation on the cluster is compared. An answer a lane gives that
    produces different work than an answer a human gives is the difference between testing the
    product and testing a mode.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    by_hand = Automation(Context.build(asked_cluster.environment, asked_cluster.team))
    by_hand.create_team_account("ops-account")
    by_hand_operations = recorded_api.operations()
    by_hand_checks = can_i_asked(recorded_api)
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    def no_prompt(prompt=""):
        raise AssertionError("--yes reached a prompt it was told to skip")

    monkeypatch.setattr("builtins.input", no_prompt)
    Automation(Context.build(asked_cluster.environment, asked_cluster.team)).create_team_account(
        "ops-account", assume_yes=True)

    assert recorded_api.operations() == by_hand_operations
    assert can_i_asked(recorded_api) == by_hand_checks


def test_no_terminal_and_no_yes_is_refused_before_the_cluster_is_asked_anything(
        asked_cluster, fake_binaries, recorded_api, monkeypatch):
    """The lane's whole problem, in one line of the contract.

    Silence is not consent. Where the bash got EOF and carried on under `set -e`, this names
    the flag that is missing and does nothing else -- no `can-i`, no namespace, no account.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    automation = Automation(Context.build(asked_cluster.environment, asked_cluster.team))

    with pytest.raises(ClickException) as raised:
        automation.create_team_account("ops-account")

    assert "--yes" in str(raised.value)
    assert client_writes(recorded_api) == []
    assert can_i_asked(recorded_api) == [], "it interrogated the cluster for a run it refused"


def test_the_question_asked_of_the_operator_says_what_the_command_is_about(
        asked_cluster, monkeypatch):
    """The action is an argument, not text hardcoded inside the gate.

    Three commands call one function. If the sentence were fixed, `delete-team-account` would
    ask "do you wish to create the team account" -- which is how the duplicated bash copy
    stayed vague about what it was doing.
    """
    asked = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "y")
    automation = Automation(Context.build(asked_cluster.environment, asked_cluster.team))

    user_management.check_cluster_and_access(
        automation.kubernetes_utils, False,
        "delete the team account 'ops-account' and everything it can do on this cluster")

    assert any("delete the team account" in prompt for prompt in asked)


def test_the_checks_happen_before_any_write_whichever_command_is_standing_there(
        on_a_cluster_that_grants_everything, asked_cluster, fake_binaries, recorded_api):
    """The gate is the same gate for the read-only variant.

    A second copy of this function is exactly what let one variant walk past it, so the
    read-only command is measured here rather than trusted to reuse it.
    """
    on_a_cluster_that_grants_everything.create_team_account_read_only("ops-account")

    assert can_i_asked(recorded_api) == list(user_management.CLUSTER_ACCESS_CHECKS)
    assert client_writes(recorded_api), "the read-only command wrote nothing"


def test_a_command_that_deletes_an_account_checks_the_same_verbs(asked_cluster, monkeypatch,
                                                                 fake_binaries, recorded_api):
    """Deleting is a write too, and the bash copy that skipped it was the lane's red row.

    The verbs are the flow's, not the command's: a delete still patches RoleBindings, so it
    asks about the same permissions.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    Automation(Context.build(asked_cluster.environment, asked_cluster.team)).delete_team_account("ops-account")

    assert can_i_asked(recorded_api) == list(user_management.CLUSTER_ACCESS_CHECKS)
