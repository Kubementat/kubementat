"""A run that resolves no configuration root stops at the sentence, and asks nothing of a cluster.
"""


import pytest
from click.testing import CliRunner
from cluster_fixture import (
    DEV_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from cli.commands import config as config_command
from cli.commands import kubementat
from cli.kmt import KmtGroup
from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget
from kubementat.errors import ConfigurationRootNotNamedError

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"
ENVIRONMENT = "dev"
TEAM = "fixture"


def a_tree_that_the_deleted_rule_would_have_answered_from(tmp_path, monkeypatch):
    """A checkout that declares one cluster, named by nothing but `KUBEMENTAT_HOME`."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {f"context-of-{ENVIRONMENT}": DEV_SERVER})
    write_environment(root, ENVIRONMENT, context=f"context-of-{ENVIRONMENT}",
                      kubeconfig_path=kubeconfig)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    return root


def test_context_build_refuses_before_it_builds_a_target(tmp_path, monkeypatch):
    """The library half: no root, and no `ClusterTarget` — which is the half that addresses a cluster.
    """
    a_tree_that_the_deleted_rule_would_have_answered_from(tmp_path, monkeypatch)
    built = []
    monkeypatch.setattr(ClusterTarget, "from_kubeconfig",
                        lambda *args, **kwargs: built.append(args) or None)

    with pytest.raises(ConfigurationRootNotNamedError) as raised:
        Context.build(ENVIRONMENT)

    assert built == [], "a run with no configuration root read a kubeconfig and addressed a cluster"
    assert PLATFORM_CONFIG_VARIABLE in str(raised.value)
    assert "--config-dir" in str(raised.value)


def test_a_command_that_refuses_asks_nothing_of_the_cluster(tmp_path, monkeypatch, fake_binaries):
    """The CLI half, on the command that would otherwise have started installing."""
    a_tree_that_the_deleted_rule_would_have_answered_from(tmp_path, monkeypatch)

    result = CliRunner().invoke(kubementat.install, [ENVIRONMENT, TEAM, "--yes"])

    assert result.exit_code == 1, result.output
    assert "Error:" in result.output, result.output
    assert PLATFORM_CONFIG_VARIABLE in result.output, "the variable was not named to the operator"
    assert "--config-dir" in result.output, "the flag was not named to the operator"
    assert "resolved" not in result.output.lower(), \
        f"a run that resolved nothing reported a resolution: {result.output}"
    assert fake_binaries.calls() == [], \
        f"the command reached for a cluster after refusing: {[c.argv for c in fake_binaries.calls()]}"


def test_config_init_names_no_root_and_scaffolds_nowhere(tmp_path, monkeypatch):
    """`config init` writes a root, and a root it was not given is refused before it writes."""
    root = tmp_path / "empty-checkout"
    root.mkdir(parents=True, exist_ok=True)
    redirect_checkout(monkeypatch, root)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    # Dispatched through the root group class, which is what `main()` builds before it runs, because
    # the root group is what renders a named refusal into the `Error:` line these assertions read.
    root_group = KmtGroup(name="kmt")
    root_group.add_command(config_command.config, "config")
    result = CliRunner().invoke(root_group,
                                ["config", "init", "--environment", ENVIRONMENT, "--team", TEAM])

    assert result.exit_code != 0, result.output
    assert "--config-dir" in result.output, \
        f"the refusal did not name the flag that would have answered it: {result.output}"
    assert PLATFORM_CONFIG_VARIABLE in result.output, \
        f"the refusal did not name the variable that would have answered it: {result.output}"
    assert not (root / "platform_config").exists(), \
        "config init scaffolded a tree it had been told nothing about"
