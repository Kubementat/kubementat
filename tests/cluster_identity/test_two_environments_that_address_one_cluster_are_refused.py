"""Two environments that address one cluster are refused, in the middle of the command, with no way to
talk kubementat out of it.
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

from kubementat.core.identity.context import Context
from kubementat.errors import ClusterIdentityCollisionError, ClusterResolutionError

CONTEXTS = {"dev-cluster": DEV_SERVER, "prod-cluster": PROD_SERVER, "staging-cluster": STAGING_SERVER}


def three_environments_on_three_clusters(tmp_path, monkeypatch):
    """The healthy root: `dev`, `prod`, `staging`, each declared in its own file, no collisions."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS, current_context="dev-cluster")
    for environment, context in (("dev", "dev-cluster"), ("prod", "prod-cluster"), ("staging", "staging-cluster")):
        write_environment(root, environment, context=context, kubeconfig_path=kubeconfig)
    return root, kubeconfig


def test_the_environment_that_turns_out_to_belong_to_someone_else_is_not_installed_into(tmp_path, monkeypatch):
    """The 1:1 check, run on a command that is about to do something."""
    root, kubeconfig = three_environments_on_three_clusters(tmp_path, monkeypatch)
    write_environment(root, "staging", context="prod-cluster", kubeconfig_path=kubeconfig)

    with pytest.raises(ClusterIdentityCollisionError) as refused:
        Context.build("staging", "the-team")

    assert "'staging'" in str(refused.value)
    assert "'prod'" in str(refused.value)
    assert PROD_SERVER in str(refused.value)
    assert "kmt environments list" in str(refused.value)


def test_a_collision_is_caught_from_either_sides_point_of_view(tmp_path, monkeypatch):
    """The check is not "does this environment look right", it is "who else says this"."""
    root, kubeconfig = three_environments_on_three_clusters(tmp_path, monkeypatch)
    write_environment(root, "staging", context="prod-cluster", kubeconfig_path=kubeconfig)

    with pytest.raises(ClusterIdentityCollisionError):
        Context.build("prod", "the-team")


def test_the_same_cluster_reached_through_two_different_declarations_is_still_one_cluster(
    tmp_path, monkeypatch
):
    """The comparison is the address, which is the only thing that can see through the synonyms."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_kubeconfig(root / "dev-kubeconfig", {"dev": f"{DEV_SERVER}/"})
    write_kubeconfig(root / "prod-kubeconfig", {"prod": DEV_SERVER})
    write_environment(root, "dev", context="dev", kubeconfig_path=root / "dev-kubeconfig")
    write_environment(root, "prod", context="prod", kubeconfig_path=root / "prod-kubeconfig")

    with pytest.raises(ClusterIdentityCollisionError) as refused:
        Context.build("prod", "the-team")

    assert "'dev'" in str(refused.value)
    assert DEV_SERVER in str(refused.value)


def test_the_refusal_is_not_something_a_caller_can_sweep_up_as_a_resolution_failure():
    """"Could not resolve" and "resolved to somewhere else's cluster" are different failures."""
    assert not issubclass(ClusterIdentityCollisionError, ClusterResolutionError)


# ------------------------------------------------------------- which root the one check asks about
#
# A consolidation moved the filter out of `Context.build` and into the method that already owned
# it, and the
# rule that made the duplication worth deleting is the root rule: the check compares the siblings in
# the tree the command was *given*, and so does `kmt environments list`. That is only observable
# with two trees, one of which is wrong, so both tests below carry a flagged root and a root the
# process's own environment names.
#
# They are two tests and not one because they are two different failures. A check that ignores the
# root it was handed answers about the variable's tree; a check that reads the root it was handed and
# nothing else answers about the flag's. Only the pair rules out both.

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def a_root(tmp_path, name, addresses):
    """One root of its own, declaring `{environment: address}` and nothing else."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(root / "kubeconfig",
                                  {f"context-of-{environment}": address
                                   for environment, address in addresses.items()})
    for environment in addresses:
        write_environment(root, environment, context=f"context-of-{environment}",
                          kubeconfig_path=kubeconfig)
    return root


def platform_of(root):
    return root / "platform_config"
