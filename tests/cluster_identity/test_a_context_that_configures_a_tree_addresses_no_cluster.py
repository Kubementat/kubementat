"""The one `Context` that is allowed to have no cluster, and the guard that keeps it that way."""

import pytest
from cluster_fixture import checkout, redirect_checkout, write_environment

from kubementat.core.identity.context import Context
from kubementat.errors import ClusterNotConfiguredError

ENVIRONMENT = "dev"
TEAM = "ops"


def test_the_configuration_context_carries_the_configuration_and_no_target(tmp_path, monkeypatch):
    """What `kmt initialize` is handed: the files it writes, and no claim about a cluster."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, ENVIRONMENT)

    context = Context.for_configuration(ENVIRONMENT, TEAM)

    assert context.environment == ENVIRONMENT
    assert context.require_team("the team directories initialize writes") == TEAM
    assert context.config is not None
    assert context.config.env_static_config_path.parent == root / "platform_config" / ENVIRONMENT
    assert context.target is None
    assert context.resolution_report == (), "nothing was resolved, so there is nothing to report"


def test_asked_for_the_cluster_it_never_addressed_the_context_refuses_by_name(tmp_path, monkeypatch):
    """`require_target` is the field's guard, and the sentence is the refusal's own."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, ENVIRONMENT)

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.for_configuration(ENVIRONMENT, TEAM).require_target("the kubectl call about to run")

    message = str(refused.value)
    assert ENVIRONMENT in message
    assert "CLUSTER_KUBE_CONTEXT" in message, "the refusal has to name the key that would settle it"
    assert "the kubectl call about to run" in message, "the guard has to say what it stopped"
