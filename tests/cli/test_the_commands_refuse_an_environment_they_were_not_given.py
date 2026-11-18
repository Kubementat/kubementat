"""The surface refuses what it was not told: no `--team`, no `ENVIRONMENT`, no run."""


import pytest
from click.testing import CliRunner

from cli.commands import kubernetes, tunnels, users

# Commands that must refuse rather than default to an environment, under both spellings of the argument.
DEFAULTED_POSITIONS = [
    (kubernetes.pvc_usage, []),
    (tunnels.tunnel_grafana, []),
    (tunnels.tunnel_tekton, []),
    (tunnels.tunnel_openbao_ui, []),
    (tunnels.tunnel_concourse, []),
    (users.create_grafana_accounts, []),
    (users.create_grafana_read_only_account, []),
]

DEFAULTED_OPTIONS = [
    (users.create_team_account, ["ops-account", "--yes"]),
    (users.create_team_account_read_only, ["ops-readonly", "--yes"]),
    (users.delete_team_account, ["ops-account", "--yes"]),
]


@pytest.mark.parametrize("command,args", DEFAULTED_POSITIONS,
                         ids=[command.name for command, _args in DEFAULTED_POSITIONS])
def test_a_positional_environment_that_was_never_given_is_refused(command, args, monkeypatch):
    """`kmt tunnel grafana` with nothing after it, on a shell that names no `ENVIRONMENT`."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    result = CliRunner().invoke(command, args)

    assert result.exit_code != 0, f"kmt {command.name} ran on an environment it invented"
    assert "ENVIRONMENT" in result.output, result.output
    assert "dev" not in result.output.lower(), result.output


@pytest.mark.parametrize("command,args", DEFAULTED_OPTIONS,
                         ids=[command.name for command, _args in DEFAULTED_OPTIONS])
def test_an_environment_option_that_was_never_given_is_refused_in_words(command, args, monkeypatch,
                                                                        resolvable_environment):
    """The same refusal, in kubementat's own sentence, for the commands that spell it as a flag."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    result = CliRunner().invoke(command, args)

    assert result.exit_code != 0, f"kmt {command.name} picked an environment nobody gave it"
    assert "no environment was named" in result.output, result.output


def test_the_variable_still_answers_when_the_command_does_not_name_one(monkeypatch,
                                                                       resolvable_environment):
    """The refusal is about the *default*, not about the operator's right to answer by variable."""
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.delenv("TEAM", raising=False)

    # The tunnel is a `ClickException` away from a cluster it cannot reach; what is asserted is that
    # the environment was accepted, which is why the failure below may be about a cluster and must
    # never be about the environment.
    result = CliRunner().invoke(tunnels.tunnel_grafana, [])

    assert "no environment was named" not in result.output, result.output
    assert "Missing argument 'ENVIRONMENT'" not in result.output, result.output
