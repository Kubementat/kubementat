"""The 1:1 check compares the siblings in the root the command was told about — not the ones the
process finds for itself.

The whole claim about `Context.build` is that one root is threaded, and `cluster.py:1067` is
the half where that is easy to get wrong: the environment's own `static.json` and the sibling
sweep are two reads, and plumbing the flag to only the first would still resolve *this*
environment correctly. It would just compare it against a different tree's siblings, which is how
a second environment that addresses the same cluster goes unnoticed until a write lands on it.

So the assertion is a collision. Two trees:

* the one the process's own environment names — `dev` and `staging` on two clusters, so the check
  has nothing to refuse;
* the one only a flag can name — `dev` and `prod` on **one** cluster, which the rule refuses.

If the flag reaches `Context.build`'s registry, building `dev` against the second tree raises. If
it reaches only the `Config`, or not at all, the same call reads the first tree's siblings and
answers that nothing collides. That asymmetry is the test: the refusal is the only observable
difference, and nothing about it needs a cluster.
"""

import pytest

from cluster_fixture import (DEV_SERVER, PROD_SERVER, STAGING_SERVER, redirect_checkout,
                             write_environment, write_kubeconfig)
from kubementat.core.identity.context import Context
from kubementat.errors import ClusterIdentityCollisionError

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def a_root(tmp_path, name, declarations):
    """A `platform_config` with the environments and contexts the test lists, in its own tree.

    `declarations` is `{environment: server}`. Two environments sharing a server string is how a
    collision is written down here: the check compares addresses, not context names.
    """
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(root / "kubeconfig",
                                  {f"context-of-{env}": server
                                   for env, server in declarations.items()})
    for environment, _server in declarations.items():
        write_environment(root, environment, context=f"context-of-{environment}",
                          kubeconfig_path=kubeconfig)
    return root


def test_the_siblings_checked_against_a_command_are_the_ones_in_the_flagged_root(tmp_path,
                                                                                 monkeypatch):
    """One root, one `dev`, two answers: the refusal proves which tree's siblings were compared."""
    # The decoy: `dev` and `staging` on two clusters. Nothing here would ever raise.
    variable_root = a_root(tmp_path, "root-the-variable-names",
                           {"dev": DEV_SERVER, "staging": STAGING_SERVER})
    redirect_checkout(monkeypatch, variable_root)
    monkeypatch.setenv(PLATFORM_CONFIG_VARIABLE, str(variable_root / "platform_config"))

    # The flag's tree: two environments, one cluster.
    flag_root = a_root(tmp_path, "root-the-flag-names", {"dev": DEV_SERVER, "prod": DEV_SERVER})

    with pytest.raises(ClusterIdentityCollisionError) as raised:
        Context.build("dev", platform_config_dir=flag_root / "platform_config")
    assert "prod" in str(raised.value), \
        "the refusal named no sibling, so nothing here says which tree was compared"

    # The same command and the same `dev`, against the tree the process finds for itself. This is
    # the half that makes the refusal above evidence: a call that ignored the flag entirely would
    # answer *this* — and answering it is the bug.
    resolved = Context.build("dev")
    assert resolved.target.address == DEV_SERVER


def test_the_sibling_sweep_and_the_environment_file_are_one_root(tmp_path, monkeypatch):
    """The two reads `Context.build` performs must be the same root, not two.

    A root handed to the file read alone would resolve `dev` correctly and sweep the decoy's
    siblings: the collision test above catches that, and this one catches its mirror — a root
    threaded to the registry but re-derived for the environment's own `static.json` would say the
    environment does not exist at all.
    """
    decoy = tmp_path / "root-with-no-dev-in-it"
    decoy.mkdir(parents=True)
    (decoy / "platform_config").mkdir(parents=True)
    redirect_checkout(monkeypatch, decoy)

    declared = a_root(tmp_path, "root-the-flag-names", {"dev": PROD_SERVER})

    resolved = Context.build("dev", platform_config_dir=declared / "platform_config")

    assert resolved.target.address == PROD_SERVER, \
        "the environment's own file was read out of a tree this command never pointed at"
