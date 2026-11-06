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

import json

import pytest
from click.testing import CliRunner

from cli.commands import kubernetes, kubementat
from cluster_fixture import DEV_SERVER, checkout, redirect_checkout, write_environment, write_kubeconfig
from kubementat.cluster import Context
from kubementat.core.constants import DEFAULT_NAMESPACES
from kubementat.errors import TeamNotSuppliedError

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


def test_a_context_built_without_a_team_still_resolves_the_cluster_it_addresses(one_cluster_root):
    """The half that makes these commands useful: `target` is set, and it names the address.

    The claim is that a command without team semantics still answers "which cluster", so the
    test asks for exactly that and nothing else.
    """
    context = Context.build(ENVIRONMENT, None)

    assert context.environment == ENVIRONMENT
    assert context.team is None
    assert context.target.address == DEV_SERVER
    assert context.target.context == "dev-cluster"
    assert context.resolution_report[0].startswith(f"cluster {ENVIRONMENT}: {DEV_SERVER}")


def test_a_team_less_context_resolves_its_cluster_and_answers_an_environment_read(one_cluster_root):
    """Both halves of what a command is asked, from a Context that was given no team.

    `config` answers *what to deploy* and `target` answers *where*, and these commands need both:
    `kmt uninstall` cannot delete namespaces without knowing which ones the environment declared,
    and it must not need a team to find out. So this asserts the pair — the address it resolved,
    and a read that only the environment layer can answer — with no team written into the fixture
    at all.
    """
    context = Context.build(ENVIRONMENT, None)

    assert context.team is None
    assert context.target.address == DEV_SERVER
    assert context.target.context == "dev-cluster"
    assert context.config is not None, "no team is not the same thing as no configuration"
    assert context.config.environment == ENVIRONMENT
    assert context.config.team is None
    # An environment-level read, on a tree with no team directory to be found.
    assert context.config.get("env_static")["CLUSTER_MANAGER_EMAIL"] == "cluster-manager@example.test"
    assert context.config.component_namespace("grafana") == DEFAULT_NAMESPACES["grafana"]


def test_a_team_scoped_read_through_the_same_context_names_the_team_it_was_given_no_name_for(
        one_cluster_root):
    """The half that keeps the first test honest: the environment layer answers, and nothing else does.

    A `Config` with no team that quietly returned something would be worse than one that raised,
    because the value would come from the environment's own `static.json` and be read as the team's.
    So every team-scoped read — the category, and the paths the category is read out of — refuses,
    naming the environment and the missing name rather than a path made out of the word `None`.
    """
    context = Context.build(ENVIRONMENT, None)

    with pytest.raises(TeamNotSuppliedError) as refused:
        context.config.get("team_static")

    message = str(refused.value)
    assert f"platform_config/{ENVIRONMENT}/<team>" in message, message
    assert "team_static" in message, "the refusal has to say which read it stopped"
    assert "/None/" not in message, message

    for name in ("team_static_config_path", "team_static_encrypted_config_path",
                 "tekton_team_pipeline_dir", "tekton_team_task_dir",
                 "tekton_team_pipeline_run_dir", "tekton_team_trigger_dir"):
        with pytest.raises(TeamNotSuppliedError):
            getattr(context.config, name)


def test_a_service_that_needs_a_teams_configuration_refuses_the_context_it_was_given(one_cluster_root):
    """`require_team` is where the uniform type stops being an answer.

    One `Context` type means a service cannot be told about the cluster a command resolved and left
    in the dark about the team it did not, so the service asks. The refusal names the environment,
    the file the decision lives in, and the flag that would have settled it — and there is no
    default behind it, because the default that used to be there is gone.
    """
    context = Context.build(ENVIRONMENT, None)

    with pytest.raises(TeamNotSuppliedError) as refused:
        context.require_team("creating a team account")

    message = str(refused.value)
    assert f"platform_config/{ENVIRONMENT}/<team>" in message, message
    assert "--team" in message, message
    assert "creating a team account" in message, "the refusal has to say what it stopped"


def test_a_context_that_did_get_a_team_is_answered_by_the_same_call(one_cluster_root):
    """`require_team` is a guard and not a punishment: with a team it hands the team back.

    Otherwise the check would be a second rule about which commands are allowed to exist, which is
    exactly the kind of knowledge this spec keeps trying to put in one place.
    """
    context = Context.build(ENVIRONMENT, TEAM)

    assert context.require_team("a read that needs namespaces") == TEAM
    assert context.config is not None, "a team-bearing Context carries the configuration it reads"


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

    # ...and `kmt cluster-capacity`, whose one argument is the environment and nothing else, still
    # says which cluster it read. It is run here without naming it, because `ENVIRONMENT` is pinned
    # above — the argument carries an `envvar`, and which of the two a command reads is worth one
    # more assertion on the cheapest command on the surface.
    result = CliRunner(mix_stderr=False).invoke(kubernetes.cluster_capacity, [])
    assert f"cluster {ENVIRONMENT}: {DEV_SERVER}" in result.stderr, result.stderr


def test_the_uninstall_command_sweeps_the_environment_and_deliberately_takes_no_team(
        tmp_path, monkeypatch):
    """`kmt uninstall` is the in-tree proof this is a real shape and not a theory.

    It deletes what every team in the environment configured, so naming one team would be a claim
    about the install that is not true — and the `Context` it builds is the team-less one: a target
    it can name, the environment's own configuration, and no invented team anywhere in the run.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_kubeconfig(root / "kubeconfig", {"the-cluster": DEV_SERVER})
    write_environment(root, ENVIRONMENT, context="the-cluster",
                      kubeconfig_path=root / "kubeconfig")

    context = Context.build(ENVIRONMENT, None)

    assert context.team is None
    assert context.target.address == DEV_SERVER
    assert context.config.team is None, "the sweep reads the environment, and no team is named"
    params = {param.name for param in kubementat.uninstall.params}
    assert "team" not in params
