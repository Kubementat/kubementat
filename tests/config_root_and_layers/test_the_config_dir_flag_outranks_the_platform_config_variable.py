"""Which root one command reads, when two dials disagree — and only one of them is typed now."""

import pytest
from click.testing import CliRunner
from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    STAGING_SERVER,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from cli.commands import environments
from cli.kmt import KmtGroup
from kubementat.core.identity.context import Context
from kubementat.errors import ConfigurationRootNotNamedError

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def two_roots(tmp_path, monkeypatch, *, environment, servers):
    """Two roots, both declaring *environment*, pointing at different clusters."""
    roots = {}
    for which, server in servers.items():
        root = tmp_path / f"root-the-{which}-names"
        root.mkdir(parents=True, exist_ok=True)
        kubeconfig = write_kubeconfig(root / "kubeconfig", {
            f"context-of-{environment}-in-{which}": server,
        })
        write_environment(root, environment,
                          context=f"context-of-{environment}-in-{which}",
                          kubeconfig_path=kubeconfig)
        roots[which] = root
    # The variable's tree is also `KUBEMENTAT_HOME`'s, so a root that never got read at all shows
    # up as the decoy's answer rather than as an error about a missing file.
    redirect_checkout(monkeypatch, roots["variable"])
    monkeypatch.setenv(PLATFORM_CONFIG_VARIABLE, str(roots["variable"] / "platform_config"))
    return roots


def platform_of(root):
    return root / "platform_config"


def test_the_listing_and_a_command_answer_one_root_because_they_read_one_rule(tmp_path,
                                                                              monkeypatch):
    """Two readers of one root, on every layer, because the rule has one owner now."""
    roots = two_roots(tmp_path, monkeypatch, environment="dev",
                      servers={"variable": DEV_SERVER, "flag": PROD_SERVER})

    # Layer 1: the flag. Same value in, same tree out, for a command that builds a `Config` and
    # for one that never does.
    with_the_flag = CliRunner(mix_stderr=False).invoke(
        environments.environments, ["list", "--config-dir", str(platform_of(roots["flag"]))])
    resolved_with_the_flag = Context.build("dev", platform_config_dir=platform_of(roots["flag"]))
    assert with_the_flag.exit_code == 0, with_the_flag.stderr
    assert str(platform_of(roots["flag"]) / "dev" / "static.json") in with_the_flag.stdout
    assert str(resolved_with_the_flag.config.platform_config_dir / "dev" / "static.json") \
        in with_the_flag.stdout, \
        "the listing and the command named two different files, so one of them is a second owner"
    assert resolved_with_the_flag.target.address == PROD_SERVER

    # Layer 2: the variable, with nothing typed. Both readers must still answer from it.
    with_the_variable = CliRunner(mix_stderr=False).invoke(environments.environments, ["list"])
    resolved_with_the_variable = Context.build("dev")
    assert with_the_variable.exit_code == 0, with_the_variable.stderr
    assert DEV_SERVER in with_the_variable.stdout, \
        "the listing stopped answering from the variable when the rule moved"
    assert resolved_with_the_variable.target.address == DEV_SERVER
    assert str(resolved_with_the_variable.config.platform_config_dir / "dev" / "static.json") \
        in with_the_variable.stdout

    # Layer 3: neither dial. One refusal, printed twice — the sentence is the evidence.
    root = tmp_path / "root-with-no-variables"
    root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(root / "kubeconfig",
                                  {"context-of-dev-in-checkout": STAGING_SERVER})
    write_environment(root, "dev", context="context-of-dev-in-checkout",
                      kubeconfig_path=kubeconfig)
    redirect_checkout(monkeypatch, root)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    with pytest.raises(ConfigurationRootNotNamedError) as raised:
        Context.build("dev")

    # Through the root group class, the same dispatch `main()` builds: the sentence is rendered by
    # the group that decides how a refusal is printed, not by the sub-group that raised it.
    root_group = KmtGroup(name="kmt")
    root_group.add_command(environments.environments, "environments")
    refusal = CliRunner(mix_stderr=False).invoke(root_group, ["environments", "list"])
    assert refusal.exit_code != 0, "the listing answered a run that named no root"
    assert str(raised.value) in refusal.stderr, \
        "the listing refuses in its own words now, which is what a second owner of the rule does"
