"""Measured: two clusters in one process, and the constructor that never asks."""

import pytest
from cluster_fixture import DEV_SERVER, PROD_SERVER, UNREAD_ROOT, write_kubeconfig
from kubernetes import client

from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget
from kubementat.errors import ClusterNotConfiguredError
from kubementat.infrastructure.kubernetes import KubernetesUtils


@pytest.fixture
def two_clusters(tmp_path):
    """One kubeconfig file holding two clusters, in a `tmp_path` no other test can see."""
    return write_kubeconfig(tmp_path / "kubeconfig", {"dev": DEV_SERVER, "prod": PROD_SERVER})


def target_on(kubeconfig, context):
    """The same construction a resolved `Context` would carry, minus the resolution."""
    return ClusterTarget.from_kubeconfig(str(kubeconfig), context, context)


def test_two_targets_in_one_process_never_share_a_client(two_clusters, monkeypatch):
    """Two `KubernetesUtils`, two clusters, neither one overwriting the other."""
    # Starting clean, so that the assertion at the end means what it says and not what some earlier
    # test left behind. No global `Configuration` may be installed to build either client, and
    # `load_kube_config` is one of the things that does exactly that (shown below), so a return to it
    # would land here and not only in the guard test that scans `src/`.
    monkeypatch.setattr(client.Configuration, "_default", None)
    installed = []
    monkeypatch.setattr(client.Configuration, "set_default",
                        classmethod(lambda cls, default: installed.append(default)))

    dev = KubernetesUtils(target_on(two_clusters, "dev"))
    prod = KubernetesUtils(target_on(two_clusters, "prod"))

    dev_core = dev.core_api
    prod_core = prod.core_api

    assert dev_core is not prod_core, "the two utilities share one API object, so one is not on dev"
    assert dev_core.api_client is not prod_core.api_client

    assert dev.core_api.api_client.configuration.host == DEV_SERVER
    assert prod.core_api.api_client.configuration.host == PROD_SERVER

    # ...and asked a second time, after the other cluster was built, `dev` still means `dev`.
    assert dev.core_api.api_client.configuration.host == DEV_SERVER
    assert dev.api_client is not prod.api_client

    assert installed == [], "building a target's client installed a process-wide Configuration"
    assert client.Configuration._default is None


def test_building_the_utils_resolves_nothing_so_initialize_can_run_without_a_kubeconfig(
        tmp_path, monkeypatch):
    """As a failure mode: the constructor may not touch a kubeconfig, in any of its forms."""
    loads = []

    def watching_loader(*args, config_file=None, context=None, **kwargs):
        loads.append((config_file, context))
        raise AssertionError("a kubeconfig was read")

    monkeypatch.setattr("kubernetes.config.new_client_from_config", watching_loader)

    nowhere = tmp_path / "there-is-no-kubeconfig-here"
    target = ClusterTarget(environment="dev", context="dev", kubeconfig_path=nowhere,
                           address="https://dev.example.test:6443", source="flag")

    utils = KubernetesUtils(target)
    assert loads == [], "the constructor went looking for a kubeconfig"

    with pytest.raises(AssertionError, match="a kubeconfig was read"):
        utils.core_api
    assert loads == [(str(nowhere), "dev")], "the client was not built when an API object was asked for"


def test_a_client_cannot_be_built_against_an_unresolved_target(monkeypatch):
    """`Context.require_target`'s first call site — the guard that must not be decorative."""
    asked = []
    monkeypatch.setattr(ClusterTarget, "client",
                        lambda self: asked.append(self) or client.ApiClient())

    utils = KubernetesUtils(Context.for_configuration("dev", "ops",
                                                     platform_config_dir=UNREAD_ROOT))
    assert asked == [], "a command that configures a tree also addressed a cluster"

    with pytest.raises(ClusterNotConfiguredError) as raised:
        utils.core_api

    assert "CLUSTER_KUBE_CONTEXT" in str(raised.value)
    assert asked == [], "the client was built against a target that was never resolved"

    # The same refusal from every API object, not just the one the constructor used to build. The
    # accessors are passed around rather than called, because calling one inside the collection
    # raises outside the `raises` block that is meant to be measuring it.
    for ask in (lambda: utils.apps_api,
                utils.get_rbac_api,
                utils.get_custom_objects_api,
                lambda: utils.get_core_api()):
        with pytest.raises(ClusterNotConfiguredError):
            ask()
