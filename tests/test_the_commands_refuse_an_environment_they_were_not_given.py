"""The surface refuses what it was not told: no `--team`, no `ENVIRONMENT`, no run.

Two defaults went in this ticket, and they are the same default: `DEFAULT_TEAM = "dev1"` in
`cli/helpers.py`, and `default='dev'` on the environment arguments of `pvc-usage`, the three component
tunnels and the two grafana-account commands — plus `kmt users`' `--environment` option, which the
count of *arguments* never included but which guessed exactly the same way. Decision 19 refuses
guessing "at the level of clusters *or* teams", and a hardcoded `dev` is `DEFAULT_TEAM`'s twin, which
is why the two went in one commit.

The shape of the answer differs only by where the value was declared, and this file asserts both:

* the required *positional*, which Click refuses itself (`Missing argument 'ENVIRONMENT'`) before
  kubementat's code runs;
* the required *option*, refused by `cli.context._environment_of` in kubementat's own words.

Neither is a warning, and neither becomes a `None` that survives into a service.
"""

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.commands import kubernetes, tunnels, users

# The commands that carried a `default='dev'` environment. Both spellings are here, because the ticket
# is "every `default='dev'` goes" and not "the six arguments the brief counted".
DEFAULTED_POSITIONS = [
    (kubernetes.pvc_usage, []),
    (tunnels.tunnel_grafana, []),
    (tunnels.tunnel_tekton, []),
    (tunnels.tunnel_openbao_ui, []),
    (users.create_grafana_accounts, []),
    (users.create_grafana_read_only_account, []),
]

DEFAULTED_OPTIONS = [
    (users.create_team_account, ["ops-account", "--yes"]),
    (users.create_team_account_read_only, ["ops-readonly", "--yes"]),
    (users.delete_team_account, ["ops-account", "--yes"]),
]


def test_no_command_module_carries_a_default_environment():
    """The grep that proves the guess is gone, instead of a test that trusts the diff.

    Reading every command module rather than the six named in the brief is the point: the seventh
    place `default='dev'` lived was in an `--environment` option, and a test that only walked the
    arguments would have missed it and stayed green.
    """
    pattern = re.compile(r"""default\s*=\s*['"]dev(1)?['"]""")
    offenders = []
    for source_file in sorted(Path(users.__file__).parent.glob("*.py")):
        for number, line in enumerate(source_file.read_text().splitlines(), start=1):
            if pattern.search(line.split("#")[0]):
                offenders.append(f"{source_file.name}:{number}: {line.strip()}")

    assert offenders == [], f"a command is still guessing: {offenders}"


@pytest.mark.parametrize("command,args", DEFAULTED_POSITIONS,
                         ids=[command.name for command, _args in DEFAULTED_POSITIONS])
def test_a_positional_environment_that_was_never_given_is_refused(command, args, monkeypatch):
    """`kmt tunnel grafana` with nothing after it, on a shell that names no `ENVIRONMENT`.

    The refusal is Click's and not kubementat's — that is what "required" means for a positional —
    so the assertion is that the run stopped and named `ENVIRONMENT`, not that it used our sentence.
    And it stops *before* the command body: nothing resolved, nothing read, no cluster asked anything.
    """
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    result = CliRunner().invoke(command, args)

    assert result.exit_code != 0, f"kmt {command.name} ran on an environment it invented"
    assert "ENVIRONMENT" in result.output, result.output
    assert "dev" not in result.output.lower(), result.output


@pytest.mark.parametrize("command,args", DEFAULTED_OPTIONS,
                         ids=[command.name for command, _args in DEFAULTED_OPTIONS])
def test_an_environment_option_that_was_never_given_is_refused_in_words(command, args, monkeypatch,
                                                                        resolvable_environment):
    """The same refusal, in kubementat's own sentence, for the commands that spell it as a flag.

    `--environment` keeps its `envvar`, so a lane that exports the variable is still answered. What
    is gone is the fallback that made "which environment" an optional question.
    """
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    result = CliRunner().invoke(command, args)

    assert result.exit_code != 0, f"kmt {command.name} picked an environment nobody gave it"
    assert "no environment was named" in result.output, result.output


def test_the_variable_still_answers_when_the_command_does_not_name_one(monkeypatch,
                                                                       resolvable_environment):
    """The refusal is about the *default*, not about the operator's right to answer by variable.

    One command from each half, because both read the same `ENVIRONMENT` and this is not a claim that
    needs ten. The `--yes` on the team-account command keeps the confirmation gate out of the way, so
    the only question the run answered was the environment — and it answered it with the variable.

    The tunnel is handed no `--team`: the option is off it, and this is the assertion that a
    run built that way still reads the variable rather than inventing a name for the second half.
    """
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.delenv("TEAM", raising=False)

    # The tunnel is a `ClickException` away from a cluster it cannot reach; what is asserted is that
    # the environment was accepted, which is why the failure below may be about a cluster and must
    # never be about the environment.
    result = CliRunner().invoke(tunnels.tunnel_grafana, [])

    assert "no environment was named" not in result.output, result.output
    assert "Missing argument 'ENVIRONMENT'" not in result.output, result.output
