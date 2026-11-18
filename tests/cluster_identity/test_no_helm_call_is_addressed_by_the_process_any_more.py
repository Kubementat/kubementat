"""A helm or helmfile call that is not told which cluster is a bug, and the refusal says so."""

import pytest


def test_helm_target_args_refuses_a_target_that_cannot_be_addressed():
    """The refusal the splat depends on, named."""
    from kubementat.core.identity.target import ClusterTarget, helm_target_args

    with pytest.raises(Exception) as raised:
        helm_target_args(ClusterTarget(environment="dev", context=None, kubeconfig_path=None,
                                       address="https://example.test:6443",
                                       source="connection"))
    assert "kubeconfig" in str(raised.value).lower() or "kubecontext" in str(raised.value).lower()

    assert helm_target_args(ClusterTarget(environment="dev", context="dev", kubeconfig_path=None,
                                          address="https://x:6443",
                                          source="static.json")) == ["--kube-context", "dev"]
    assert helm_target_args(ClusterTarget(environment="dev", context=None,
                                          kubeconfig_path="/tmp/x", address="https://x:6443",
                                          source="kubeconfig_file")) == ["--kubeconfig", "/tmp/x"]
