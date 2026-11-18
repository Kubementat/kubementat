"""Which layer gets to say where a command is going — and the answer is the highest one that speaks.
"""

import pytest
from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    STAGING_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from kubementat.core.constants import STATIC_SOURCE_PREFIX
from kubementat.core.identity.context import Context
from kubementat.errors import ClusterNotConfiguredError


def fixture_root(tmp_path, monkeypatch, kubeconfig_name="kubeconfig"):
    """One checkout with three environments on three clusters, and one cluster nobody claims."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(
        root / kubeconfig_name,
        {
            "dev-static": DEV_SERVER,
            "prod-static": PROD_SERVER,
            "staging-static": STAGING_SERVER,
            "unclaimed": "https://10.0.9.9:6443",
        },
        current_context="dev-static",
    )
    for environment, context in (
        ("dev", "dev-static"),
        ("prod", "prod-static"),
        ("staging", "staging-static"),
    ):
        write_environment(root, environment, context=context, kubeconfig_path=kubeconfig)
    return root


def test_the_kubeconfig_a_command_tells_outranks_the_variable_and_the_tree(tmp_path, monkeypatch):
    """Layer 1's path, over layer 2's path, over layer 3's path, in one run."""
    root = fixture_root(tmp_path, monkeypatch)
    process_kubeconfig = write_kubeconfig(root / "process", {"dev-static": "https://10.0.9.1:6443"})
    flag_kubeconfig = write_kubeconfig(root / "flagged", {"dev-static": "https://10.0.9.2:6443"})
    monkeypatch.setenv("CLUSTER_KUBECONFIG_PATH", str(process_kubeconfig))

    ctx = Context.build("dev", "the-team", kubeconfig=flag_kubeconfig)

    assert ctx.target.address == "https://10.0.9.2:6443"
    assert ctx.target.kubeconfig_path == flag_kubeconfig


def test_the_kubeconfig_a_process_variable_names_is_read_when_no_flag_names_one(tmp_path,
                                                                                monkeypatch):
    """Layer 2's path. This is the layer a pipeline uses to aim a checkout somewhere without committing
    the change, so the tree's own `static.json` must not outvote it — and the name still comes from
    the tree, which is what keeps this a *where* dial and not a *which environment* one.
    """
    root = fixture_root(tmp_path, monkeypatch)
    ci_kubeconfig = write_kubeconfig(root / "ci-kubeconfig", {"dev-static": "https://10.0.9.1:6443"})
    monkeypatch.setenv("CLUSTER_KUBECONFIG_PATH", str(ci_kubeconfig))

    ctx = Context.build("dev", "the-team")

    assert ctx.target.address == "https://10.0.9.1:6443"
    assert ctx.target.kubeconfig_path == ci_kubeconfig


def test_the_environment_configuration_answers_when_nothing_above_it_speaks(tmp_path, monkeypatch):
    """Layer 3, which is the normal case: the environment declares its own cluster."""
    root = fixture_root(tmp_path, monkeypatch)

    ctx = Context.build("prod", "the-team")
    static_path = root / "platform_config" / "prod" / "static.json"

    assert ctx.target.address == PROD_SERVER
    assert ctx.target.source == f"{STATIC_SOURCE_PREFIX}{static_path}"
    assert str(static_path) in ctx.resolution_report[0]


def test_a_process_variable_that_is_set_but_empty_is_refused_rather_than_read_as_unset(tmp_path, monkeypatch):
    """An empty `CLUSTER_KUBE_CONTEXT` is a mistake, not a way to say "skip this layer"."""
    fixture_root(tmp_path, monkeypatch)
    monkeypatch.setenv("CLUSTER_KUBE_CONTEXT", "   ")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert "set but empty" in str(refused.value)
    assert "CLUSTER_KUBE_CONTEXT" in str(refused.value)
