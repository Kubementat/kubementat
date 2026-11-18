"""The 1:1 check compares the siblings in the root the command was told about — not the ones the
process finds for itself.
"""

import pytest
from cluster_fixture import (
    DEV_SERVER,
    STAGING_SERVER,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from kubementat.core.identity.context import Context
from kubementat.errors import ClusterIdentityCollisionError

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def a_root(tmp_path, name, declarations):
    """A `platform_config` with the environments and contexts the test lists, in its own tree."""
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
