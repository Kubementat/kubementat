"""The one `Context` that is allowed to have no cluster, and the guard that keeps it that way.

`kmt initialize` writes the configuration that a cluster resolution reads. The same rule reads from
the other side: a constructor that resolves fails a command that touches no cluster. The
two reasons meet in one object, so this file pins it from both ends — what it is, and what it must
not be allowed to mean.

The guard is the interesting half. `Context.target` is nullable in exactly one state, and a nullable
field is how "this environment has not addressed a cluster yet" quietly becomes "there is no
cluster", which is the bug class the refusal of `current-context` exists to close. So the state is
not left to a field somebody reads: `require_target` is asked where a target would be *used*, and it
names the environment and the key that was never declared.

"""

import pytest

from cluster_fixture import (DEV_SERVER, checkout, configuration_only_context, redirect_checkout,
                             write_environment, write_kubeconfig)
from kubementat.core.identity.context import Context
from kubementat.errors import (
    ClusterNotConfiguredError,
    TeamNotSuppliedError,
)

ENVIRONMENT = "dev"
TEAM = "ops"


def test_the_configuration_context_carries_the_configuration_and_no_target(tmp_path, monkeypatch):
    """What `kmt initialize` is handed: the files it writes, and no claim about a cluster.

    Asserted as the three absences that make the state legible — no target, no report, no
    resolution run — rather than as a field that reads as None, because the field is the symptom.
    """
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
    """`require_target` is the field's guard, and the sentence is the refusal's own.

    An operator who runs a command that addresses a cluster against a tree that has never declared
    one needs to be told which environment, and which key would have answered. What must not happen
    is the quiet reading: no cluster, and a client pointed somewhere nobody named.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, ENVIRONMENT)

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.for_configuration(ENVIRONMENT, TEAM).require_target("the kubectl call about to run")

    message = str(refused.value)
    assert ENVIRONMENT in message
    assert "CLUSTER_KUBE_CONTEXT" in message, "the refusal has to name the key that would settle it"
    assert "the kubectl call about to run" in message, "the guard has to say what it stopped"


def test_a_context_that_did_address_a_cluster_is_answered_by_the_same_call(tmp_path, monkeypatch):
    """Like `require_team`: a guard, not a second rule about which commands may exist.

    A `Context` that resolved hands its target back untouched, so the call adds no policy — it only
    refuses the one state where there is nothing to hand back.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {"dev-cluster": DEV_SERVER})
    write_environment(root, ENVIRONMENT, context="dev-cluster", kubeconfig_path=kubeconfig)

    assert Context.build(ENVIRONMENT, TEAM).require_target("anything").address == DEV_SERVER


def test_the_configuration_context_still_refuses_a_team_as_usually(tmp_path, monkeypatch):
    """The two guards do not overlap: `for_configuration` has a team, and does not need one to exist.

    A `Config` built here is the ordinary team-bearing kind, so this asserts only that adding a
    target guard did not bend the team rule underneath it.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, ENVIRONMENT)

    assert Context.for_configuration(ENVIRONMENT, None).config.team is None
    with pytest.raises(TeamNotSuppliedError) as refused:
        Context.for_configuration(ENVIRONMENT, None).require_team("a namespace")
    assert f"`{ENVIRONMENT}/<team>` layer of the configuration directory" in str(refused.value)
