"""A run that states a cluster and an environment that declares one have to agree, and when they do
not, kubementat refuses instead of choosing.
"""

import pytest
from cluster_fixture import (
    DEV_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from kubementat.core.identity.context import Context
from kubementat.errors import ClusterFileUnreadableError, StatedClusterMismatchError

# The cluster the configuration clone declares, and the cluster the pod is standing on. Two
# addresses, because a comparison that only ever sees one of them is a comparison nobody read.
DECLARED_SERVER = "https://10.0.4.2:6443"
IN_CLUSTER_SERVER = "https://10.43.0.1:6443"

CI_CONTEXT = "ci-cluster"


def test_a_stated_kubeconfig_that_does_not_have_the_declared_context_is_refused(
        tmp_path, monkeypatch):
    """A file is a claim about the cluster too, and here it is the *run* making it."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    clone_kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DECLARED_SERVER})
    mounted = write_kubeconfig(root / "mounted-kubeconfig", {"ci-worker": IN_CLUSTER_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=clone_kubeconfig)

    with pytest.raises(StatedClusterMismatchError) as refused:
        Context.build("ci", "the-team", kubeconfig=mounted)

    message = str(refused.value)
    assert "flag --kubeconfig" in message
    assert str(mounted) in message
    assert "ci-worker" in message, "the stated file's own claim is what the reader needs to see"
    assert CI_CONTEXT in message


def test_the_process_variable_stating_a_cluster_is_compared_like_a_flag(tmp_path, monkeypatch):
    """`CLUSTER_KUBECONFIG_PATH` is a dial the pipeline states with, not a hint."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    clone_kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DECLARED_SERVER})
    mounted = write_kubeconfig(root / "mounted-kubeconfig", {CI_CONTEXT: IN_CLUSTER_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=clone_kubeconfig)
    monkeypatch.setenv("CLUSTER_KUBECONFIG_PATH", str(mounted))

    with pytest.raises(StatedClusterMismatchError):
        Context.build("ci", "the-team", context="not-the-declared-one")


def test_a_run_that_states_nothing_leaves_the_answer_to_the_environment(tmp_path, monkeypatch):
    """Nothing stated, nothing compared: the ordinary laptop case is untouched."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DEV_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=kubeconfig)

    ctx = Context.build("ci", "the-team")

    assert ctx.target.address == DEV_SERVER
    assert "static.json" in ctx.resolution_report[0]


def test_an_environment_that_declares_nothing_can_be_stated_by_a_run_without_argument(
        tmp_path, monkeypatch):
    """Layer 1 keeps its one real job: answering for an environment that has no claim yet."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DEV_SERVER})
    write_environment(root, "ci", context=None)

    ctx = Context.build("ci", "the-team", context=CI_CONTEXT, kubeconfig=kubeconfig)

    assert ctx.target.address == DEV_SERVER


def test_the_check_does_not_rescue_a_dead_declaration_by_finding_a_different_cluster(
        tmp_path, monkeypatch):
    """The rule this replaces, measured so it stays replaced."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    dead_path = root / "kubeconfig-on-the-operators-laptop"
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=dead_path)

    with pytest.raises(ClusterFileUnreadableError) as refused:
        Context.build("ci", "the-team", context=CI_CONTEXT)

    assert str(dead_path) in str(refused.value)
