"""An environment kubementat cannot read is named in the report, not dropped from it."""

from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    STAGING_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from kubementat.core.identity.context import Context

VERIFIED = "verified against"
NOT_VERIFIED = "not verified"


def healthy_root(tmp_path, monkeypatch, extra=()):
    """`dev` and `prod` on their own clusters, plus whatever `extra` environments the test adds."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(
        root / "kubeconfig",
        {"dev-cluster": DEV_SERVER, "prod-cluster": PROD_SERVER, "staging-cluster": STAGING_SERVER},
        current_context="dev-cluster",
    )
    write_environment(root, "dev", context="dev-cluster", kubeconfig_path=kubeconfig)
    write_environment(root, "prod", context="prod-cluster", kubeconfig_path=kubeconfig)
    for environment, context in extra:
        write_environment(root, environment, context=context, kubeconfig_path=kubeconfig)
    return root


def report_of(root, environment="dev"):
    return Context.build(environment, "the-team").resolution_report[0]


def test_the_environment_that_was_not_compared_is_named_with_the_reason(tmp_path, monkeypatch):
    """The second shape, line for line: the name, then the reason in parentheses."""
    root = healthy_root(tmp_path, monkeypatch, extra=[("staging", None)])
    static_path = root / "platform_config" / "staging" / "static.json"

    line = report_of(root)

    assert "staging could not be resolved" in line
    assert "CLUSTER_KUBE_CONTEXT" in line
    assert str(static_path) in line


def test_a_root_with_one_environment_in_it_claims_nothing(tmp_path, monkeypatch):
    """The edge case that would be an overstatement, and the shape it must not take."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {"dev-cluster": DEV_SERVER})
    write_environment(root, "dev", context="dev-cluster", kubeconfig_path=kubeconfig)

    line = report_of(root)

    assert VERIFIED not in line
    assert NOT_VERIFIED in line
    assert "no other environment" in line
