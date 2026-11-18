"""`kmt environments list` says, in one command, which environments this tree has and which cluster
each addresses, and its rows are the check's own answers.
"""

from click.testing import CliRunner
from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from cli.commands import environments
from kubementat.core.identity.registry import EnvironmentRegistry


def listed(root):
    """`kmt environments list`, run against *root* as its own command."""
    return CliRunner(mix_stderr=False).invoke(environments.environments, ["list"])


def a_root(tmp_path, monkeypatch, declarations):
    """One `platform_config` with the environments the test names, and the kubeconfigs they read."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    contexts = {f"context-of-{name}": server
                for name, server in declarations["addresses"].items()}
    kubeconfig = write_kubeconfig(root / "kubeconfig", contexts)
    for name in declarations["environments"]:
        write_environment(root, name, context=f"context-of-{name}", kubeconfig_path=kubeconfig)
    return root


def table_rows(result):
    """The table, split into columns — the shape a human reads, which is what is under test."""
    return [line.split() for line in result.stdout.splitlines() if line.strip()]


def test_one_row_per_environment_in_the_root_and_nothing_in_between(tmp_path, monkeypatch):
    """Depth 1 of `platform_config` with a `static.json` of its own, and no other rule."""
    root = a_root(tmp_path, monkeypatch,
                  {"environments": ["dev", "prod"], "addresses":
                      {"dev": DEV_SERVER, "prod": PROD_SERVER}})
    (root / "platform_config" / "kubementat_components").mkdir()
    (root / "platform_config" / "dev" / "dev1").mkdir()
    (root / "platform_config" / "dev" / "dev1" / "static.json").write_text("{}")

    result = listed(root=root)

    assert result.exit_code == 0, result.stderr
    rows = [row[0] for row in table_rows(result)[1:]]
    assert rows == ["dev", "prod"], result.stdout


def test_an_environment_that_resolves_to_nothing_is_named_with_the_reason_not_left_off(
        tmp_path, monkeypatch):
    """The rule that every environment is covered, on the command's own output."""
    root = a_root(tmp_path, monkeypatch,
                  {"environments": ["dev", "staging"], "addresses": {"dev": DEV_SERVER}})
    write_environment(root, "staging", context="staging-eu", kubeconfig_path=root / "kubeconfig")

    result = listed(root=root)

    assert "staging" in result.stdout, result.stdout
    assert "(unresolved)" in result.stdout, result.stdout
    assert "no such context 'staging-eu'" in result.stdout, result.stdout
    # The environment that did resolve is still answered in full; one bad file is not a reason to
    # say nothing about the machine's configuration.
    assert DEV_SERVER in result.stdout


def test_an_environment_that_declared_nothing_shows_its_context_as_not_declared(tmp_path,
                                                                                monkeypatch):
    """The state `kmt initialize` ships: the environment exists, and addresses no cluster."""
    root = a_root(tmp_path, monkeypatch, {"environments": ["dev"], "addresses": {"dev": DEV_SERVER}})
    write_environment(root, "fresh", context=None)

    result = listed(root=root)

    assert "fresh" in result.stdout
    assert "(not declared)" in result.stdout, result.stdout


def test_the_listing_and_the_check_agree_about_what_the_root_contains(tmp_path, monkeypatch):
    """The listing's identity, asserted as a fact rather than argued."""
    root = a_root(tmp_path, monkeypatch,
                  {"environments": ["dev", "prod"],
                   "addresses": {"dev": DEV_SERVER, "prod": PROD_SERVER}})

    the_command = listed(root=root)
    from_the_check = EnvironmentRegistry(root / "platform_config").all()

    assert from_the_check, "the primitive answered nothing, so the comparison means nothing"
    for record in from_the_check:
        assert record.environment in the_command.stdout
        assert record.target.address in the_command.stdout
        assert record.target.context in the_command.stdout
