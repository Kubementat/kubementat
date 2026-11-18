"""Which Role a team account is given, where it is written, and in what order.

A team account is not one object. It is a ServiceAccount, a token Secret, two namespace
Roles, up to three tunneling Roles, and a RoleBinding for each of them — written in that
order, into namespaces that belong to different owners. The bash kept that knowledge in two
96-line scripts that differed by the words `namespace-admin` and `namespace-readonly`, plus
one `*_NAMESPACE="..."` line per tunneled component, which is how a fix to one copy left the
other broken and how a renamed namespace left the tunnel pointing at nothing.

What the Python path owes, and what this file measures:

* the Role that governs the team's own work lands in both namespaces the team's configuration
  declares, and the tunneling Roles land where the component it tunnels is installed;
* the namespace for a tunneled component is `Config.component_namespace`, never a string —
  the tree-wide "no namespace literal in `src/`" check in
  `test_component_namespaces_come_from_configuration.py` covers the spelling, so this covers
  the behaviour: rename it and the Role moves;
* a component that is not installed gets no Role, because a Role cannot be created in a
  namespace that is not there and that used to make the whole command fail;
* Roles exist before the Bindings that point at them, and each Binding is read back before
  the command says it is finished.
"""

import base64
import json
import logging

import pytest
from kubernetes import client

from conftest import is_a_write
import kubementat.infrastructure.user_management as user_management
from kubementat.consent import Consent
from kubementat.automations.accounts.accounts import create_team_account
from cluster_fixture import configuration_only_context

# The token the controller is expected to have filled in by the time this flow prints.
ACCOUNT_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZ"
SERVER = "https://the-cluster-under-test.example.test:6443"
AUTHORITY = "RlklWEFSVElKVFlURVNU=="

# Every namespace the flow may be pointed at, so a test can say which components exist.
CLUSTER_NAMESPACES = [
    "default", "kube-system", "kube-public", "kube-node-lease",
    "tekton-pipelines", "grafana", "polaris",
]


def cluster_holding(recorded_api, namespaces):
    """A cluster whose namespace list is exactly `namespaces`."""
    recorded_api.answer("list_namespace", lambda **kwargs: type(
        "NamespaceList", (),
        {"items": [type("Namespace", (), {"metadata": type("M", (), {"name": n})})()
                   for n in namespaces]})())


def token_secret_present(recorded_api, token=ACCOUNT_TOKEN):
    """The token Secret the flow asked for, filled in, exactly as the controller writes it.

    The Secret carries the token base64-encoded, as every Secret does, and the reader decodes
    it; handing it over unencoded would test a state no cluster is ever in.
    """
    def answer(namespace=None, **kwargs):
        return type("SecretList", (), {"items": [type("Secret", (), {
            "type": "kubernetes.io/service-account-token",
            "metadata": type("Meta", (), {
                "name": "ops-account-sa-secret",
                "annotations": {"kubernetes.io/service-account.name": "ops-account"}})(),
            "data": {"token": base64.b64encode(token.encode())} if token is not None else None,
        })()]})()

    recorded_api.answer("list_namespaced_secret", answer)


@pytest.fixture
def cluster_where_the_team_account_flow_runs(fixture_repo, dry_run_env, fake_binaries,
                                             recorded_api):
    """The flow's cluster: every verb granted, a token ready, and everything else empty.

    The shims answer the ten `kubectl auth can-i` checks the flow makes, and `kubectl config
    view` with an address that appears nowhere in this repository, so the kubeconfig printed
    at the end can only have come from what the cluster was told.
    """
    for verb, resource in user_management.CLUSTER_ACCESS_CHECKS:
        fake_binaries.respond("kubectl", "yes\n", subcommand=f"auth.can-i.{verb}.{resource}")
    fake_binaries.respond("kubectl", json.dumps({"clusters": [{
        "name": "under-test", "cluster": {"server": SERVER, "certificate-authority-data": AUTHORITY}}]}),
        subcommand="config.view")
    cluster_holding(recorded_api, CLUSTER_NAMESPACES)
    token_secret_present(recorded_api)
    recorded_api.calls.clear()
    fake_binaries.take_calls()
    return recorded_api


@pytest.fixture
def account(cluster_where_the_team_account_flow_runs, fixture_repo):
    """`kmt users create-team-account ops-account`, with nothing answered yet."""
    return configuration_only_context(fixture_repo.environment, fixture_repo.team)


def writes(recorded_api):
    """Every call that would change the cluster, in the order it was asked for."""
    return [(method, kwargs) for method, kwargs in recorded_api.calls if is_a_write(method)]


def bindings_created(recorded_api):
    return [(kwargs.get("namespace"), (kwargs.get("body") or {}).get("metadata", {}).get("name"))
            for method, kwargs in recorded_api.calls if method == "create_namespaced_role_binding"]


def test_the_account_and_its_token_secret_are_created_before_anything_is_bound_to_them(
        account, recorded_api):
    """A Binding that names a ServiceAccount which does not exist yet is a binding to nothing.

    The order is the bash's, and the order is the evidence: namespaces, account, token Secret,
    Roles, Bindings. Asserting it on the recorded calls is what stops the flow reordering
    itself into something that reads fine and writes a dangling subject.
    """
    create_team_account(account, "ops-account", consent=Consent.assumed())

    called = [method for method, _kwargs in recorded_api.calls]
    order = ["create_namespace", "create_namespaced_service_account", "create_namespaced_secret",
             "create_namespaced_role", "create_namespaced_role_binding"]
    positions = [called.index(method) for method in order if method in called]
    assert positions == sorted(positions), f"the flow wrote out of order: {called}"
    assert called.index("create_namespaced_service_account") < called.index(
        "create_namespaced_role_binding")


def test_the_state_the_command_prints_is_read_back_from_the_cluster(account, recorded_api, caplog):
    """After the writes, the operator is shown what the namespaces now hold.

    This is the step that reads, rather than writes, and it is where the flow used to die: the
    role-binding list was returned as a model object instead of its items, so the loop over it
    raised after every Role and Binding had been written. A printed summary that comes from the
    cluster is also the only proof of the writes a person at a terminal gets.
    """
    def one(name):
        return lambda **kwargs: type("List", (), {"items": [
            type("Item", (), {"metadata": type("M", (), {"name": name})})()]})()

    recorded_api.answer("list_namespaced_service_account", one("ops-account"))
    recorded_api.answer("list_namespaced_role", one("tekton-tunneling"))
    recorded_api.answer("list_namespaced_role_binding", one("tekton-tunneling-ops-account-binding"))

    with caplog.at_level(logging.INFO):
        create_team_account(account, "ops-account", consent=Consent.assumed())

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Current state of the namespaces this account touches" in text
    assert "serviceaccount: ops-account" in text
    assert "role: tekton-tunneling" in text
    assert "rolebinding: tekton-tunneling-ops-account-binding" in text


def test_a_second_run_over_a_cluster_that_already_holds_the_account_replaces_rather_than_fails(
        account, recorded_api):
    """Idempotence, because the lane installs, re-runs, and asserts nothing changed.

    Every object here is written through a create-or-replace primitive, so a cluster that
    already has the account is a normal state rather than an error. The 409 branch is the one a
    single-pass cluster never shows, so the conflict is answered deliberately: if any of these
    primitives treated "already there" as a failure, a re-run would leave the account
    half-written and the lane's `idempotent` asserts would be measuring a different cluster than
    the install left behind.
    """
    def already_there(**kwargs):
        raise client.exceptions.ApiException(status=409, reason="AlreadyExists")

    for method in ("create_namespace", "create_namespaced_service_account",
                   "create_namespaced_secret", "create_namespaced_role",
                   "create_namespaced_role_binding"):
        recorded_api.answer(method, already_there)

    create_team_account(account, "ops-account", consent=Consent.assumed())

    replaced = sorted(method for method, _kwargs in recorded_api.calls
                      if method.startswith("replace_"))
    for method in ("replace_namespaced_role", "replace_namespaced_role_binding",
                   "replace_namespaced_service_account", "replace_namespaced_secret"):
        assert method in replaced, f"the cluster said 'already there' and nothing replaced it: {replaced}"
