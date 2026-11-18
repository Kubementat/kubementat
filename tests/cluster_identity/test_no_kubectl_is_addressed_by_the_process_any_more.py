"""A `kubectl` call that was not told which cluster is a bug, and the refusal is named."""

import pytest


def test_kubectl_target_args_refuses_a_target_that_cannot_be_addressed():
    """The refusal, named — because a rule enforced only at the call site has as many owners as the
    call site.
    """
    from kubementat.core.identity.target import ClusterTarget, kubectl_target_args

    with pytest.raises(Exception) as raised:
        kubectl_target_args(ClusterTarget(environment="dev", context=None, kubeconfig_path=None,
                                          address="https://example.test:6443",
                                          source="connection"))
    assert "kubecontext" in str(raised.value) or "kubeconfig" in str(raised.value).lower()

    assert kubectl_target_args(ClusterTarget(environment="dev", context="dev",
                                             kubeconfig_path=None, address="https://x:6443",
                                             source="static.json")) == ["--context", "dev"]
    assert kubectl_target_args(ClusterTarget(environment="dev", context=None,
                                             kubeconfig_path="/tmp/x", address="https://x:6443",
                                             source="kubeconfig_file")) == ["--kubeconfig", "/tmp/x"]
