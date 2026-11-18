"""Two environments that address one cluster are refused, in the middle of the command, with no
way to talk kubementat out of it.

The invariant is "no two environments address the same cluster", and the check compares the
server *address* — not the context name, not the kubeconfig file, because two different
declarations can reach one cluster and the whole point of the check is that `dev` and `staging`
must not be the same place. The comparisons below are built that way on purpose: different
environment names, different contexts, sometimes different files, one address.

The refusal itself is the part worth locking:

* it is hard, with no override flag, so a `--force` cannot walk past it,
* the message names both environments and both addresses,
* and it points at `kmt environments list`, which is the command that shows the whole picture.

One shape is asserted that looks pedantic and is not: a collision is not a resolution failure, so
a caller that catches "could not resolve" and carries on must not swallow it.

The last two tests belong to the consolidation that collapsed the check from two spellings to
one. They ask a
different question — *which root* the one check compares — and they need two trees to ask it, so
they are written beside the collision tests rather than in the root-precedence file that proves the
flag reaches `Config`.
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
    """The 1:1 check, run on a command that is about to do something.

    `staging` is configured to be `prod` — same cluster, different name, and the operator does not
    know it. Nothing here is asked nicely: the command stops before any service exists.
    """
    root, kubeconfig = three_environments_on_three_clusters(tmp_path, monkeypatch)
    write_environment(root, "staging", context="prod-cluster", kubeconfig_path=kubeconfig)

    with pytest.raises(ClusterIdentityCollisionError) as refused:
        Context.build("staging", "the-team")

    assert "'staging'" in str(refused.value)
    assert "'prod'" in str(refused.value)
    assert PROD_SERVER in str(refused.value)
    assert "kmt environments list" in str(refused.value)


def test_a_collision_is_caught_from_either_sides_point_of_view(tmp_path, monkeypatch):
    """The check is not "does this environment look right", it is "who else says this".

    Building `prod` when `staging` was mis-declared finds it too, so the refusal does not depend
    on which environment the operator happened to type.
    """
    root, kubeconfig = three_environments_on_three_clusters(tmp_path, monkeypatch)
    write_environment(root, "staging", context="prod-cluster", kubeconfig_path=kubeconfig)

    with pytest.raises(ClusterIdentityCollisionError):
        Context.build("prod", "the-team")


def test_the_same_cluster_reached_through_two_different_declarations_is_still_one_cluster(
    tmp_path, monkeypatch
):
    """The comparison is the address, which is the only thing that can see through the synonyms.

    Two kubeconfigs, two contexts, two spellings of the same host — one trailing slash apart, which
    is what `https://host:6443/` and `https://host:6443` look like in files two people wrote.
    """
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
    """"Could not resolve" and "resolved to somewhere else's cluster" are different failures.

    A caller that reports unresolved environments and carries on (the skipped-and-named path)
    is the thing that must not be able to catch this exception and then run the install anyway.
    """
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
    """One root of its own, declaring `{environment: address}` and nothing else.

    One kubeconfig per root, one context per environment name, and the address written down as
    `write_kubeconfig` spells a cluster — so two environments sharing an address string is one
    collision, which is the only fact the check is allowed to care about.
    """
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
