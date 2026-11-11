"""Tests for the Kubernetes resource helpers: create-or-replace, delete, and lookups.

These are the primitives the install and uninstall commands run on, so the branches that
matter are the boring ones. Two defects are what the file exists to keep closed:

* **D4** — `create_or_replace_role` logged and replaced using a `name` that was never bound
  in that scope, so running a role-creating command a second time against a cluster that
  already had the role died with a `NameError` on the 409 path. The name is now an argument,
  so the 409 branch has something to say.
* **D5** — `get_role_binding` was defined twice and the second definition shadowed the
  first, which made the documented `-> V1RoleBinding` contract a fiction and quietly turned
  a missing binding into `None`. One definition now.

The fake clients below record what they were asked for and answer with whatever the test
configured, including a status that forces the branch under test.
"""

import base64
import ast
import inspect

import pytest
from kubernetes import client

from kubementat import shell
from kubementat.kubernetes_utils import KubernetesUtils, ServiceAccountTokenNotFoundError
from cluster_fixture import TARGET_DEV

# A status that is not 409/404: neither "exists" nor "already gone", so it has to reach
# the caller instead of being mistaken for a normal state.
UNEXPECTED_STATUS = 500


class FakeApi:
    """Stand-in for a generated kubernetes client.

    Every call records its keyword arguments, raises the status configured for its method
    (if any), and otherwise returns what `returns` says. That is enough to exercise the
    three outcomes a real API gives: created, already there, not there.
    """

    def __init__(self, raises=None, returns=None):
        self.calls = []
        self.raises = dict(raises or {})
        self.returns = dict(returns or {})

    def __getattr__(self, method):
        def call(**kwargs):
            self.calls.append((method, kwargs))
            status = self.raises.get(method)
            if status is not None:
                raise client.exceptions.ApiException(status=status, reason="fake")
            answer = self.returns.get(method)
            return answer(**kwargs) if callable(answer) else answer

        return call

    def called_as(self, method):
        """The keyword arguments of the last call to *method*, or None if never called."""
        matching = [kwargs for name, kwargs in self.calls if name == method]
        return matching[-1] if matching else None

    def names_called(self):
        return [name for name, _ in self.calls]


def make_utils(monkeypatch, **apis):
    """A `KubernetesUtils` with no cluster, and its API clients swapped for fakes.

    Nothing in the constructor reads a kubeconfig any more, so `apis` — the attribute names
    (`core_api`, `rbac_api`, …) mapped to a `FakeApi` — is all there is to do: the clients those fakes
    replace are never built.
    """
    utils = KubernetesUtils(TARGET_DEV)
    for attribute, api in apis.items():
        setattr(utils, attribute, api)
    return utils


# ---------------------------------------------------------------------------
# the 409 path that used to crash
# ---------------------------------------------------------------------------
def test_a_role_that_already_exists_is_replaced_and_not_recreated(monkeypatch):
    """Re-running a command on a cluster that already has the role must not crash (D4)."""
    rbac = FakeApi(raises={"create_namespaced_role": 409})
    utils = make_utils(monkeypatch, rbac_api=rbac)
    body = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "tekton-tunneling"}}

    utils.create_or_replace_role("dev1-pipelines", "tekton-tunneling", body)

    assert rbac.called_as("replace_namespaced_role") == {
        "namespace": "dev1-pipelines",
        "name": "tekton-tunneling",
        "body": body,
    }


def test_a_role_that_is_not_there_is_created_once(monkeypatch):
    rbac = FakeApi()
    utils = make_utils(monkeypatch, rbac_api=rbac)

    utils.create_or_replace_role("dev1-pipelines", "tekton-tunneling", {"kind": "Role"})

    assert rbac.names_called() == ["create_namespaced_role"]
    assert "replace_namespaced_role" not in rbac.names_called()


def test_a_failure_that_is_not_a_conflict_is_not_mistaken_for_exists(monkeypatch):
    """A 409 means "exists"; anything else is a real error and must not be swallowed."""
    rbac = FakeApi(raises={"create_namespaced_role": UNEXPECTED_STATUS})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    with pytest.raises(client.exceptions.ApiException) as excinfo:
        utils.create_or_replace_role("dev1-pipelines", "tekton-tunneling", {"kind": "Role"})

    assert excinfo.value.status == UNEXPECTED_STATUS
    assert "replace_namespaced_role" not in rbac.names_called()


def test_a_cluster_role_is_replaced_when_the_cluster_already_has_one(monkeypatch):
    """`helm-deployer-cluster-role` is cluster scoped: the 409 branch has no namespace in it."""
    rbac = FakeApi(raises={"create_cluster_role": 409})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    utils.create_or_replace_cluster_role("helm-deployer-cluster-role", {"kind": "ClusterRole"})

    assert rbac.called_as("replace_cluster_role") == {
        "name": "helm-deployer-cluster-role",
        "body": {"kind": "ClusterRole"},
    }


def test_a_cluster_role_binding_is_replaced_when_the_cluster_already_has_one(monkeypatch):
    rbac = FakeApi(raises={"create_cluster_role_binding": 409})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    utils.create_or_replace_cluster_role_binding("helm-deployer-cluster-role-binding", {"kind": "ClusterRoleBinding"})

    assert rbac.called_as("replace_cluster_role_binding") == {
        "name": "helm-deployer-cluster-role-binding",
        "body": {"kind": "ClusterRoleBinding"},
    }


# ---------------------------------------------------------------------------
# deletes, where "not there" is a result rather than an error
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("delete,api_method", [
    (("delete_role", ("dev1", "tekton-tunneling")), "delete_namespaced_role"),
    (("delete_role_binding", ("dev1", "helm-deployer-role-binding-app-deployment")), "delete_namespaced_role_binding"),
])
def test_deleting_a_namespaced_role_that_is_already_gone_is_a_pass(monkeypatch, delete, api_method):
    """An uninstall that sweeps namespaces will meet resources that were never created."""
    method, arguments = delete
    rbac = FakeApi(raises={api_method: 404})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    getattr(utils, method)(*arguments)  # must not raise

    assert rbac.called_as(api_method)["name"] == arguments[1]


def test_deleting_a_cluster_role_that_is_already_gone_is_a_pass(monkeypatch):
    rbac = FakeApi(raises={"delete_cluster_role": 404})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    utils.delete_cluster_role("helm-deployer-cluster-role")  # must not raise

    assert rbac.called_as("delete_cluster_role") == {"name": "helm-deployer-cluster-role"}


def test_a_deletion_that_fails_for_a_real_reason_still_reaches_the_caller(monkeypatch):
    """The 404 tolerance is not a blanket `|| true`: an RBAC error is a finding."""
    rbac = FakeApi(raises={name: UNEXPECTED_STATUS for name in (
        "delete_namespaced_role", "delete_namespaced_role_binding", "delete_cluster_role")})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    for call in (
        lambda: utils.delete_role("dev1", "helm-deployer-role"),
        lambda: utils.delete_role_binding("dev1", "helm-deployer-role-binding-app-deployment"),
        lambda: utils.delete_cluster_role("helm-deployer-cluster-role"),
    ):
        with pytest.raises(client.exceptions.ApiException) as excinfo:
            call()
        assert excinfo.value.status == UNEXPECTED_STATUS


# ---------------------------------------------------------------------------
# reading, where one definition is the whole point
# ---------------------------------------------------------------------------
def test_a_role_binding_is_read_from_the_namespace_it_lives_in(monkeypatch):
    binding = object()
    rbac = FakeApi(returns={"read_namespaced_role_binding": binding})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    assert utils.get_role_binding("dev1", "helm-deployer-role-binding-app-deployment") is binding
    assert rbac.called_as("read_namespaced_role_binding") == {
        "namespace": "dev1",
        "name": "helm-deployer-role-binding-app-deployment",
    }


def test_reading_a_role_binding_that_does_not_exist_raises_rather_than_answering_none(monkeypatch):
    """A second definition of this method returned None on 404; the caller has to hear."""
    rbac = FakeApi(raises={"read_namespaced_role_binding": 404})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    with pytest.raises(client.exceptions.ApiException) as excinfo:
        utils.get_role_binding("dev1", "helm-deployer-role-binding-app-deployment")

    assert excinfo.value.status == 404


def test_role_binding_lookup_is_defined_once_so_the_documented_return_is_the_real_one():
    """D5: two definitions meant the second, undocumented one answered for callers."""
    assert inspect.getsource(KubernetesUtils).count("def get_role_binding") == 1


# The generated client is the one part of a call this file's fakes cannot check: a method
# name that does not exist reads perfectly well, passes the fake, and fails the first time
# anything runs against a cluster. `list_namespaced_pods` was exactly that.
CLIENT_BY_ATTRIBUTE = {
    "core_api": client.CoreV1Api,
    "rbac_api": client.RbacAuthorizationV1Api,
    "apps_api": client.AppsV1Api,
    "custom_objects_api": client.CustomObjectsApi,
}


def test_every_generated_client_method_the_helpers_ask_for_is_a_real_one():
    methods = _client_methods_called_on_self()

    assert methods, "the source is not being read, so this test would prove nothing"
    missing = [
        f"self.{attribute}.{method}"
        for attribute, method in sorted(methods)
        if attribute in CLIENT_BY_ATTRIBUTE and not hasattr(CLIENT_BY_ATTRIBUTE[attribute], method)
    ]
    assert missing == []


def _client_methods_called_on_self():
    """Every `self.<api>.<method>` in the source of KubernetesUtils."""
    tree = ast.parse(inspect.getsource(KubernetesUtils))
    return {
        (node.value.attr, node.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def test_a_role_binding_is_listed_per_namespace(monkeypatch):
    """The helper hands back the items, because that is what its caller iterates.

    It used to return the whole `V1RoleBindingList`, which is not iterable, and the
    team-account flow met that `TypeError` in the step that prints what the account can now
    do - after it had written every Role and Binding. `list_service_accounts` and `list_roles`
    already return the items, so this is the same contract, not a new one.
    """
    bindings = [type("Binding", (), {"metadata": type("M", (), {"name": "ops-account-binding"})})()]
    rbac = FakeApi(returns={"list_namespaced_role_binding": type("List", (), {"items": bindings})()})
    utils = make_utils(monkeypatch, rbac_api=rbac)

    assert utils.list_role_bindings("dev1") == bindings


# ---------------------------------------------------------------------------
# namespaces
# ---------------------------------------------------------------------------
def test_every_namespace_is_named_so_a_before_and_after_diff_is_possible(monkeypatch):
    namespaces = [type("Ns", (), {"metadata": type("M", (), {"name": n})})() for n in ("dev1", "kube-system")]
    core = FakeApi(returns={"list_namespace": type("List", (), {"items": namespaces})()})
    utils = make_utils(monkeypatch, core_api=core)

    assert sorted(utils.list_namespaces()) == ["dev1", "kube-system"]


def test_a_missing_namespace_is_left_alone_when_that_is_the_deal(monkeypatch):
    core = FakeApi(raises={"delete_namespace": 404})
    utils = make_utils(monkeypatch, core_api=core)

    utils.delete_namespace("dev1")  # must not raise

    assert ("delete_namespace", {"name": "dev1"}) in core.calls


def test_a_missing_namespace_is_a_failure_when_absence_must_be_proven(monkeypatch):
    """An uninstall that claims the cluster is clean needs the 404 to be loud."""
    core = FakeApi(raises={"delete_namespace": 404})
    utils = make_utils(monkeypatch, core_api=core)

    with pytest.raises(client.exceptions.ApiException):
        utils.delete_namespace("dev1", ignore_not_found=False)
