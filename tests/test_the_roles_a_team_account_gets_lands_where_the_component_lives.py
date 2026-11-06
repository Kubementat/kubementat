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
from kubementat.automation import Automation
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
    return Automation(configuration_only_context(fixture_repo.environment, fixture_repo.team))


def writes(recorded_api):
    """Every call that would change the cluster, in the order it was asked for."""
    return [(method, kwargs) for method, kwargs in recorded_api.calls if is_a_write(method)]


def roles_created(recorded_api):
    """`(namespace, role name)` for each Role the flow wrote."""
    return [(kwargs.get("namespace"), (kwargs.get("body") or {}).get("metadata", {}).get("name"))
            for method, kwargs in recorded_api.calls if method == "create_namespaced_role"]


def bindings_created(recorded_api):
    return [(kwargs.get("namespace"), (kwargs.get("body") or {}).get("metadata", {}).get("name"))
            for method, kwargs in recorded_api.calls if method == "create_namespaced_role_binding"]


def rule_for(recorded_api, namespace, name):
    """The rules of one written Role, read back off the body that was sent."""
    for method, kwargs in recorded_api.calls:
        if method == "create_namespaced_role" and kwargs.get("namespace") == namespace:
            body = kwargs.get("body") or {}
            if (body.get("metadata") or {}).get("name") == name:
                return body.get("rules")
    return None


def test_the_namespace_admin_role_lands_in_both_namespaces_the_team_declares(account, recorded_api):
    """The team's own pair of namespaces, from that team's `static.json`.

    The bash spelled out `PIPELINE_NAMESPACE` and `APP_DEPLOYMENT_NAMESPACE` twice per script
    and hardcoded the `namespace-admin`/`namespace-readonly` names; here only the namespaces
    are read, so renaming one moves the Role and forgetting one is an error rather than an
    apply into `None`.
    """
    account.create_team_account("ops-account", assume_yes=True)

    expected = {("fixture-pipelines", "namespace-admin"), ("fixture-deployments", "namespace-admin")}
    assert expected.issubset(set(roles_created(recorded_api))), \
        f"the admin role did not reach both team namespaces: {sorted(roles_created(recorded_api))}"
    assert not any(name == "namespace-readonly" for _ns, name in roles_created(recorded_api)), \
        "an admin account was handed a read-only role as well"


def test_a_read_only_account_gets_the_read_only_role_and_the_same_access_everywhere_else(
        account, recorded_api):
    """One flow, one difference: which Role is bound.

    Two scripts that differed by two words is how one of them got fixed and the other did not,
    so the only legitimate difference between these two commands is the Role name and its verb
    list. Everything else — namespaces, tunnels, bindings, kubeconfig — has to be identical.
    """
    account.create_team_account_read_only("ops-account", assume_yes=True)

    assert {("fixture-pipelines", "namespace-readonly"),
            ("fixture-deployments", "namespace-readonly")}.issubset(set(roles_created(recorded_api)))
    assert not any(name == "namespace-admin" for _ns, name in roles_created(recorded_api))
    for namespace in ("fixture-pipelines", "fixture-deployments"):
        rules = rule_for(recorded_api, namespace, "namespace-readonly")
        by_resource = {tuple(rule["resources"]): rule["verbs"] for rule in rules}
        wildcards = by_resource[("*",)]
        assert wildcards == ["get", "list"], \
            f"a read-only account can write in {namespace}: {wildcards}"


def test_the_read_only_role_reads_pods_but_opens_tunnels_only_through_the_port_forward_rule(
        account, recorded_api):
    """`get` on a pod is not `create` on a port-forward, and the RBAC distinguishes them.

    A read-only account is still allowed to look at a component's logs and to sit behind a
    tunnel; that combination is two rules, and collapsing them into one either lets the account
    write or stops it reading.
    """
    account.create_team_account_read_only("ops-account", assume_yes=True)

    rules = rule_for(recorded_api, "fixture-deployments", "namespace-readonly")
    by_resource = {tuple(rule["resources"]): rule["verbs"] for rule in rules}

    assert by_resource[("*",)] == ["get", "list"]
    assert "create" in by_resource[("pods/portforward",)], "a read-only tunnel would not open"


def test_a_tunneling_role_names_the_component_and_lives_where_that_component_lives(
        account, recorded_api):
    """Three Roles, three namespaces, looked up per component.

    `tekton` tunnels into `tekton-pipelines`, `grafana` and `polaris` into their own namespaces
    — which is what the three `*_NAMESPACE` variables the bash carried were for, and the reason
    a renamed namespace used to leave the tunneling Role behind.
    """
    account.create_team_account("ops-account", assume_yes=True)

    written = {name: namespace for namespace, name in roles_created(recorded_api)}
    for component, role in user_management.TUNNEL_COMPONENTS:
        namespace = account._team_account_tunnel_roles()[component]
        assert written.get(role) == namespace, \
            f"{role} (for {component}) was written to {written.get(role)!r}, not {namespace!r}"


def test_a_renamed_component_namespace_moves_the_tunneling_role_with_it(account, fixture_repo,
                                                                        recorded_api):
    """The same key the lane's rename feature edits, asked of the role writer.

    `GRAFANA_DEPLOYMENT_NAMESPACE` is renamed and the old one is not on the cluster. The Role
    has to be written to the new one; if anything in this flow still spelled `grafana` out, it
    would either skip it (namespace not present) or write it to a namespace that no longer
    exists.
    """
    fixture_repo.environment_static(GRAFANA_DEPLOYMENT_NAMESPACE="grafana-in-a-new-place")
    cluster_holding(recorded_api, [ns for ns in CLUSTER_NAMESPACES if ns != "grafana"]
                    + ["grafana-in-a-new-place"])
    recorded_api.calls.clear()

    account.create_team_account("ops-account", assume_yes=True)

    written = roles_created(recorded_api)
    assert ("grafana-in-a-new-place", "grafana-tunneling") in written
    assert not any(namespace == "grafana" for namespace, _name in written), \
        f"the old namespace is still addressed: {sorted(written)}"


def test_a_component_that_is_not_installed_gets_no_role_and_no_binding(account, recorded_api,
                                                                       caplog):
    """A cluster where the polaris namespace was never created has no polaris, whatever the manifest says.

    A Role cannot be created in a namespace that does not exist. The bash applied the role
    anyway, so `cap:create-team-account` exited 1 on every lane — which is why the account that
    the whole `feature_user_accounts` group needs never existed. Here: the flow continues, the
    other tunnels are written, and the log says what this account cannot tunnel into. The case is
    still worth exercising even though `polaris` has a declared release since 2026-11-01: the
    namespace is configuration, so a renamed or skipped one still reaches this branch.
    """
    cluster_holding(recorded_api, [ns for ns in CLUSTER_NAMESPACES if ns != "polaris"])

    with caplog.at_level(logging.INFO):
        account.create_team_account("ops-account", assume_yes=True)

    assert not any(name == "polaris-tunneling" for _ns, name in roles_created(recorded_api))
    assert not any(name == "polaris-tunneling-ops-account-binding"
                   for _ns, name in bindings_created(recorded_api))
    assert ("tekton-pipelines", "tekton-tunneling") in roles_created(recorded_api)
    assert "polaris is not installed" in caplog.text


def test_the_tunneling_rule_opens_the_tunnel_on_the_subresource_and_reads_the_pod_on_its_own(
        account, recorded_api):
    """`create` belongs on `pods/portforward`; on `pods` it would be a write permission.

    The same two rules apply to all three tunnels, which is why they are one table and not
    three. Getting it wrong — `create` on `pods` — grants a team the right to delete a pod it
    was only meant to reach.
    """
    account.create_team_account("ops-account", assume_yes=True)

    by_resource = {tuple(rule["resources"]): rule["verbs"]
                   for rule in rule_for(recorded_api, "tekton-pipelines", "tekton-tunneling")}
    assert by_resource[("pods",)] == ["get", "list"]
    assert by_resource[("pods/portforward",)] == ["get", "list", "create"]


def test_the_account_and_its_token_secret_are_created_before_anything_is_bound_to_them(
        account, recorded_api):
    """A Binding that names a ServiceAccount which does not exist yet is a binding to nothing.

    The order is the bash's, and the order is the evidence: namespaces, account, token Secret,
    Roles, Bindings. Asserting it on the recorded calls is what stops the flow reordering
    itself into something that reads fine and writes a dangling subject.
    """
    account.create_team_account("ops-account", assume_yes=True)

    called = [method for method, _kwargs in recorded_api.calls]
    order = ["create_namespace", "create_namespaced_service_account", "create_namespaced_secret",
             "create_namespaced_role", "create_namespaced_role_binding"]
    positions = [called.index(method) for method in order if method in called]
    assert positions == sorted(positions), f"the flow wrote out of order: {called}"
    assert called.index("create_namespaced_service_account") < called.index(
        "create_namespaced_role_binding")


def test_a_role_binding_is_read_back_after_it_is_written(account, recorded_api):
    """Creating a Binding reports nothing about whether it took.

    An account with no Binding is an account silently allowed to do nothing, and every later
    "did it work" is answered by the cluster and not by this command. The read after the write
    is the only proof the command can give, so every Binding in the flow gets one.
    """
    account.create_team_account("ops-account", assume_yes=True)

    bindings = bindings_created(recorded_api)
    assert bindings, "the flow bound nothing"
    read_back = {(kwargs.get("namespace"), kwargs.get("name"))
                 for method, kwargs in recorded_api.calls if method == "read_namespaced_role_binding"}
    assert set(bindings) == read_back, (
        f"written: {sorted(set(bindings))}\nread back: {sorted(read_back)}")


def test_the_binding_binds_the_account_in_the_namespace_it_was_created_in(account, recorded_api):
    """The subject of a RoleBinding is the other half of the claim.

    `role_binding_body` gets the namespace from the same variable that created the account, so
    a binding whose subject lives in `kube-system` is a bug in this file's subject line.
    """
    account.create_team_account("ops-account", assume_yes=True)

    for method, kwargs in recorded_api.calls:
        if method != "create_namespaced_role_binding":
            continue
        subject = (kwargs.get("body") or {}).get("subjects", [{}])[0]
        assert subject == {"kind": "ServiceAccount", "name": "ops-account",
                           "namespace": "fixture-deployments"}, subject


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
        account.create_team_account("ops-account", assume_yes=True)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Current state of the namespaces this account touches" in text
    assert "serviceaccount: ops-account" in text
    assert "role: tekton-tunneling" in text
    assert "rolebinding: tekton-tunneling-ops-account-binding" in text


def test_a_binding_the_cluster_rejects_is_the_error_and_not_a_silent_success(account,
                                                                            recorded_api):
    """If the read-back says the Binding is not there, the command says so.

    Otherwise the operator gets a working-looking kubeconfig for an account that is allowed to
    do nothing, and the next person to use it debugs RBAC on a cluster they cannot see.
    """
    recorded_api.answer("read_namespaced_role_binding",
                        client.exceptions.ApiException(status=404, reason="not found"))

    with pytest.raises(client.exceptions.ApiException):
        account.create_team_account("ops-account", assume_yes=True)


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

    account.create_team_account("ops-account", assume_yes=True)

    replaced = sorted(method for method, _kwargs in recorded_api.calls
                      if method.startswith("replace_"))
    for method in ("replace_namespaced_role", "replace_namespaced_role_binding",
                   "replace_namespaced_service_account", "replace_namespaced_secret"):
        assert method in replaced, f"the cluster said 'already there' and nothing replaced it: {replaced}"
