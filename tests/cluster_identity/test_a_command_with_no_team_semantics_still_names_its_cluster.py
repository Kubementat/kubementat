"""A command with no team still has to say which cluster, and must not be handed a team."""


import pytest
from click.testing import CliRunner
from cluster_fixture import (
    DEV_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from cli.commands import kubementat, kubernetes

ENVIRONMENT = "dev"
TEAM = "fixture"


@pytest.fixture
def one_cluster_root(tmp_path, monkeypatch):
    """A checkout whose `dev` addresses one cluster, and whose team layer is untouched."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {"dev-cluster": DEV_SERVER})
    write_environment(root, ENVIRONMENT, context="dev-cluster", kubeconfig_path=kubeconfig)
    return root


# ------------------------------------------------------------------------------------------
# the same rule where an operator meets it: the commands that have no team do not grow one
# ------------------------------------------------------------------------------------------


def test_the_team_less_commands_carry_no_team_flag_and_still_resolve(tmp_path, monkeypatch,
                                                                     recorded_api):
    """The surface check: no `--team` anywhere on the commands that cannot act on one."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_kubeconfig(root / "kubeconfig", {"the-cluster": DEV_SERVER})
    write_environment(root, ENVIRONMENT, context="the-cluster",
                      kubeconfig_path=root / "kubeconfig")
    monkeypatch.setenv("ENVIRONMENT", ENVIRONMENT)

    for command in (kubernetes.cluster_capacity, kubernetes.pod_images, kubernetes.pvc_usage,
                    kubementat.uninstall):
        params = {param.name for param in command.params}
        assert "team" not in params, f"kmt {command.name} offers a team it cannot act on"

    # ...and `kmt kubernetes cluster-capacity`, whose one argument is the environment and nothing else, still
    # says which cluster it read. It is run here without naming it, because `ENVIRONMENT` is pinned
    # above — the argument carries an `envvar`, and which of the two a command reads is worth one
    # more assertion on the cheapest command on the surface.
    result = CliRunner(mix_stderr=False).invoke(kubernetes.cluster_capacity, [])
    assert f"cluster {ENVIRONMENT}: {DEV_SERVER}" in result.stderr, result.stderr
