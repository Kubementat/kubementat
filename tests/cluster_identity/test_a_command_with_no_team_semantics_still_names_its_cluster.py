"""A command with no team still has to say which cluster, and must not be handed a team.

The rule is short and its consequences are not: `kmt uninstall`, `cluster-capacity`, `pvc-usage` and the
diagnostics reads have no team semantics, so under `@kmt_context` they get a `Context` whose team is
`None`. That is the same rule that deleted `DEFAULT_TEAM`, read from the other side — kubementat
does not guess what a command means, and inventing a team for a command that took none would be
inventing the namespace it acts in.

The middle part is the answer the ticket had to find in `config.py` rather than in the spec, and it
is the reason the rule is stated as a pair: `Config(environment, None)` is a real state that object
supports, where the environment-level files resolve and the team-scoped paths are never built. The
alternative was interpolation — `platform_config/dev/None/static.json`, a directory nobody created,
reported against a file name no configuration tree can contain — or, from an empty team, reading the
environment's own file and calling it the team's. Both of those are the defect the rule is about, so neither is
what happens: the read raises. And the Context carries the environment-level `Config` all the same,
because *what to deploy* is half of what a command is asked, and `kmt uninstall` sweeps namespaces
with no team to name. `require_team` remains the sentence that says what such a Context cannot do.
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

from cli.commands import kubementat, kubernetes

ENVIRONMENT = "dev"
TEAM = "fixture"


@pytest.fixture
def one_cluster_root(tmp_path, monkeypatch):
    """A checkout whose `dev` addresses one cluster, and whose team layer is untouched.

    Nothing here needs a team's configuration — that is the point of the fixture — so none is
    written, which is what makes "a `Config` would have read a directory called `None`" observable
    instead of theoretical.
    """
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
    """The surface check: no `--team` anywhere on the commands that cannot act on one.

    A flag here would be more than noise: `--team` resolves to a default today, and a command that
    accepted it and then ignored it is precisely the shape that made the tunnels read `dev1`'s
    namespaces (the reason the default was deleted at all). So the absence is the assertion,
    and the run proves the commands still work without it.
    """
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
