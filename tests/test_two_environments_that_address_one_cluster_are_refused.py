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
from click.testing import CliRunner

from cli.commands import environments
from kubementat.cluster import (
    Context,
    EnvironmentRegistry,
    ClusterTarget,
)
from kubementat.errors import (
    ClusterIdentityCollisionError,
    ClusterResolutionError,
)

from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    STAGING_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

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


def test_a_target_a_command_supplied_by_flag_is_checked_against_every_other_environment(tmp_path, monkeypatch):
    """Layer 1 does not exempt anything. A cluster aimed at by hand is still claimed twice.

    `dev` says nothing, so the operator passed `--context`; `staging` declares the same cluster in
    its own file. The check runs against the root, not against what the command chose to declare,
    which is the definition of "compared against".
    """
    root, kubeconfig = three_environments_on_three_clusters(tmp_path, monkeypatch)
    write_environment(root, "dev", context=None)

    with pytest.raises(ClusterIdentityCollisionError) as refused:
        Context.build("dev", "the-team", context="staging-cluster")

    assert "'staging'" in str(refused.value)
    assert "kmt environments list" in str(refused.value)


def test_the_refusal_is_not_something_a_caller_can_sweep_up_as_a_resolution_failure():
    """"Could not resolve" and "resolved to somewhere else's cluster" are different failures.

    A caller that reports unresolved environments and carries on (the skipped-and-named path)
    is the thing that must not be able to catch this exception and then run the install anyway.
    """
    assert not issubclass(ClusterIdentityCollisionError, ClusterResolutionError)


def test_the_registry_returns_the_environment_that_overlaps_and_not_the_one_being_built(tmp_path, monkeypatch):
    """1:1 means an environment is allowed to be the one that claims its cluster.

    `collision_check` is given `dev`'s own target and must not report `dev` — a check that found
    every environment matching itself would refuse every command in the product.
    """
    root, kubeconfig = three_environments_on_three_clusters(tmp_path, monkeypatch)
    registry = EnvironmentRegistry(root / "platform_config")
    target = ClusterTarget.from_kubeconfig(str(kubeconfig), "dev-cluster", "dev")

    assert registry.collision_check(target).collisions == ()
    write_environment(root, "prod", context="dev-cluster", kubeconfig_path=kubeconfig)

    overlapping = registry.collision_check(target).collisions
    assert [record.environment for record in overlapping] == ["prod"]
    assert overlapping[0].target.address == DEV_SERVER


def test_a_root_where_nobody_else_resolves_is_not_an_implicit_pass(tmp_path, monkeypatch):
    """An environment that could not be read cannot collide, and does not hide the one that can.

    `staging` is unconfigured, so the check between `dev` and `prod` still runs and still refuses.
    Skipping *unresolved* environments is what the rule asks for; skipping the comparison is what it
    refuses to be.
    """
    root, kubeconfig = three_environments_on_three_clusters(tmp_path, monkeypatch)
    write_environment(root, "staging", context=None)
    write_environment(root, "prod", context="dev-cluster", kubeconfig_path=kubeconfig)

    with pytest.raises(ClusterIdentityCollisionError):
        Context.build("dev", "the-team")

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


def test_the_collision_a_command_is_told_about_is_the_flagged_roots_and_not_the_variables(
        tmp_path, monkeypatch):
    """Both halves answer about root A: the refusal names the sibling, and the listing prints the pair.

    A is the tree only a flag can name, and it is the broken one — `dev` and `prod` address one
    cluster. B is what this process's own environment says, and it is clean. So the two answers are
    one fact apart, and any read of B would come out looking like nothing is wrong.

    * the library half: `Context.build(platform_config_dir=A)` raises, naming `prod`.
    * the command half: `kmt environments list --config-dir A` prints both rows carrying one
      address, which is how a listing that shares the check's primitive reports a collision it does
      not itself decide about.
    """
    variable_root = a_root(tmp_path, "root-the-variable-names",
                           {"dev": DEV_SERVER, "staging": STAGING_SERVER})
    redirect_checkout(monkeypatch, variable_root)
    monkeypatch.setenv(PLATFORM_CONFIG_VARIABLE, str(platform_of(variable_root)))
    flag_root = a_root(tmp_path, "root-the-flag-names", {"dev": DEV_SERVER, "prod": DEV_SERVER})

    with pytest.raises(ClusterIdentityCollisionError) as refused:
        Context.build("dev", "the-team", platform_config_dir=platform_of(flag_root))
    assert "'prod'" in str(refused.value), \
        "the refusal named no sibling from the flagged root, so it compared the variable's tree"
    assert DEV_SERVER in str(refused.value)

    result = CliRunner(mix_stderr=False).invoke(
        environments.environments, ["list", "--config-dir", str(platform_of(flag_root))])
    assert result.exit_code == 0, result.stderr
    assert STAGING_SERVER not in result.stdout, \
        f"the listing read the variable's root: {result.stdout}"
    assert result.stdout.count(DEV_SERVER) == 2, \
        f"the flagged root holds one address and two environments: {result.stdout}"


def test_a_command_told_a_root_sees_no_collision_the_root_the_variable_names_holds(
        tmp_path, monkeypatch):
    """The mirror, and the half a check that never consulted the root would still pass.

    The collision lives in B now, and A is clean. A command told about A must build, report
    *verified against 1 other environment*, and list two rows with two addresses — because the
    collision it is being handed is not in the tree it was given, and a check that swept B would
    refuse a command that has done nothing wrong.
    """
    variable_root = a_root(tmp_path, "root-the-variable-names",
                           {"dev": DEV_SERVER, "prod": DEV_SERVER})
    redirect_checkout(monkeypatch, variable_root)
    monkeypatch.setenv(PLATFORM_CONFIG_VARIABLE, str(platform_of(variable_root)))
    flag_root = a_root(tmp_path, "root-the-flag-names",
                       {"dev": DEV_SERVER, "staging": STAGING_SERVER})

    resolved = Context.build("dev", "the-team", platform_config_dir=platform_of(flag_root))

    assert resolved.target.address == DEV_SERVER
    assert "verified against 1 other environment" in resolved.resolution_report[0], \
        "the report line counted a sibling this command was never told about"

    result = CliRunner(mix_stderr=False).invoke(
        environments.environments, ["list", "--config-dir", str(platform_of(flag_root))])
    assert result.exit_code == 0, result.stderr
    assert STAGING_SERVER in result.stdout
    assert "prod" not in result.stdout, \
        f"the listing swept the variable's root: {result.stdout}"
