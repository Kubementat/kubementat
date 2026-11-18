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


import pytest
from cluster_fixture import TARGET_DEV
from kubernetes import client

from kubementat.infrastructure.kubernetes import KubernetesUtils

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
