"""`--team` carries no default: the team a command runs against is a choice."""


import pytest
from click.testing import CliRunner

from cli import helpers
from cli.commands import component, helm, kubernetes, routing, tekton, tunnels, users

# The run path's verbs are module functions, so this is the seam to intercept.
from kubementat.infrastructure.tekton import runs

TEAM = "smoke"

# The commands whose own argument is an environment and which still take a `--team`, recorded here
# because these tests hand the pair to the CLI as argv and the recorder reads it back. The list got
# shorter twice over: the default came off every option on it, and the option itself came off the
# off the four commands whose code never consulted a team. What is left is the one command whose
# `--team` is an option rather than a positional argument and whose service really does read
# `team_static` through it.
TEAM_FLAG_COMMANDS = [
    (tekton.setup_cleanup_job, ("dev",)),
]

# Commands that reach a team-scoped path before they reach a cluster, so a run with no team is
# refused by `require_team` at the service boundary and nothing is written. These are real invocations
# against `resolvable_environment` — no recorder, because the point is the refusal and a recorder
# refuses nothing.
TEAM_REFUSING_COMMANDS = [
    (tekton.setup_cleanup_job, ("dev",)),
]

# The commands that are left without a `--team` at all: they cannot act on one, so they are not given
# one to resolve to nothing. Listed apart because what an operator hits without the flag is not a
# refusal but a clean `Error: No such option: --team`, which is the other half of the same rule.
TEAM_LESS_COMMANDS = [
    # The three the earlier finding made, and the tunnel command added beside them.
    (tunnels.tunnel_grafana, ("dev",)),
    (tunnels.tunnel_tekton, ("dev",)),
    (tunnels.tunnel_openbao_ui, ("dev",)),
    (tunnels.tunnel_concourse, ("dev",)),
    (routing.status, ("dev",)),
    # And the seven 12b made it for, one layer down: the guard was in the service constructor
    # rather than in the command, so the refusal arrived before the code that could have read a
    # team found out it had nothing to read.
    (helm.helmfile_apply, ("dev",)),
    (helm.latest_version, ("dev", "prometheus-community/prometheus")),
    (component.install, ("dev", "grafana")),
    (component.uninstall, ("dev", "grafana")),
    (routing.install, ("dev",)),
    (kubernetes.create_ingress, ("dev", "svc.example.test", "the-service", "80")),
    (users.create_grafana_accounts, ("dev", "--yes")),
    (users.create_grafana_read_only_account, ("dev", "someone@something.test")),
]


# The four verbs `kmt tunnel` opens component tunnels for, so the recorder below can be attached to
# the names the commands actually look up: the tunnels layer handed them functions rather than an
# object.
TUNNEL_FUNCTIONS = ("open_tunnel_grafana", "open_tunnel_tekton_dashboard", "open_tunnel_openbao",
                    "open_tunnel_concourse")


class RecordingVerb:
    """The run path's verb, recorded instead of run."""

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, ctx, kubernetes_utils, config, environment, *args, **kwargs):
        self.calls.append((environment, ctx.team))
        return None


class RecordingTunnels:
    """One of the three tunnel entry points, recorded instead of run."""

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, ctx, *args):
        self.calls.append((ctx.environment, ctx.team))
        return None


@pytest.fixture
def recorder(monkeypatch):
    calls = []
    # The run-path verb receives the environment and team the command resolved, so intercepting it
    # records what the command passed. A patch that only covered a name the command never looks up would keep this file
    # green while saying nothing, which is why the assertion below compares what the recorder saw.
    monkeypatch.setattr(runs, "setup_cleanup_job", RecordingVerb(calls))
    for name in TUNNEL_FUNCTIONS:
        monkeypatch.setattr(tunnels, name, RecordingTunnels(calls))
    return calls


def invoke(command, args, **runner_kwargs):
    """The command as an operator runs it, with no context object handed in."""
    return CliRunner().invoke(command, args, **runner_kwargs)


def test_the_option_offers_no_default_of_any_kind():
    """`--team` has no default, and the module that offered one no longer holds it."""
    assert not hasattr(helpers, "DEFAULT_TEAM"), \
        "the deleted default-team constant is back, so an import can still reach it"

    for command, _args in TEAM_FLAG_COMMANDS:
        parameter = next(p for p in command.params if p.name == "team")
        assert parameter.default is None, (
            f"kmt {command.name} still carries a default team: {parameter.default!r}")


@pytest.mark.parametrize("command,args", TEAM_REFUSING_COMMANDS,
                         ids=[command.name for command, _ in TEAM_REFUSING_COMMANDS])
def test_a_command_that_reads_team_config_refuses_a_run_that_named_none(command, args,
                                                                        resolvable_environment,
                                                                        monkeypatch):
    """No `--team`, no `TEAM`, and three commands that all say the same thing about it."""
    monkeypatch.delenv("TEAM", raising=False)

    result = invoke(command, list(args))

    assert result.exit_code == 1, (
        f"kmt {command.name} exited {result.exit_code} on a run that named no team; "
        f"a named refusal exits 1 with a sentence, and the run is only over if the exception "
        f"it raised is the tool's: {result.exception!r}")
    assert result.exception is None or isinstance(result.exception, SystemExit), \
        f"kmt {command.name} answered a missing team with {result.exception!r}, which is a "\
        "crash and not a refusal"
    refused = result.output
    assert "--team" in refused, refused
    assert "`dev/<team>` layer of the configuration directory" in refused, \
        f"the refusal did not name the layer it wanted: {refused}"
    assert "Traceback" not in refused, \
        f"the refusal reached the operator as a stack trace: {refused}"


@pytest.mark.parametrize("command,args", TEAM_LESS_COMMANDS,
                         ids=[command.name for command, _args in TEAM_LESS_COMMANDS])
def test_a_command_that_cannot_act_on_a_team_says_the_flag_is_not_its_option(command, args,
                                                                             recorder,
                                                                             resolvable_environment,
                                                                             monkeypatch):
    """The other half of the rule: a command with no team semantics does not accept `--team`."""
    monkeypatch.delenv("TEAM", raising=False)

    refused = invoke(command, list(args) + ["--team", "ops"])
    assert refused.exit_code != 0, f"kmt {command.name} still accepts a team it cannot act on"
    assert "No such option" in refused.output, refused.output
    assert "TEAM: None" not in refused.output, refused.output

    # The absence is structural, not just a parse error: the parameter is not there, so there is
    # nothing for a `TEAM` variable or a typed flag to reach. (The command itself is not run here —
    # without a cluster to read it would fail for a reason this test is not about.)
    params = {param.name for param in command.params}
    assert "team" not in params, f"kmt {command.name} still carries a team parameter"

    # And the absence is load-bearing for the one command group that reaches a team-scoped
    # function: run the tunnel with the flag it refuses off, and read back the pair it hands the
    # tunnels layer. A `--team` put back on the option, or `dev1` reintroduced anywhere in the
    # path, lands here as a team that is not None -- which is what the vacuous recorder this
    # replaced could not see.
    if command in TUNNEL_COMMANDS:
        before = len(recorder)
        opened = invoke(command, list(args))

        assert opened.exit_code == 0, opened.output
        assert len(recorder) == before + 1, (
            f"kmt {command.name} reached no tunnel function at all, so the recorder above is "
            "patching a name the command never looks up")
        assert recorder[-1][:2] == ("dev", None), (
            f"kmt {command.name} handed the tunnels layer {recorder[-1]}; a command with no team "
            "semantics hands it none")


# The four component tunnel verbs, identified by command so the test below can run them for real
# once the flag it refuses is off. `routing.status` and the seven below it never reach the tunnels
# layer.
TUNNEL_COMMANDS = {tunnels.tunnel_grafana, tunnels.tunnel_tekton, tunnels.tunnel_openbao_ui,
                   tunnels.tunnel_concourse}


@pytest.fixture(autouse=True)
def _an_environment_the_command_resolves(resolvable_environment):
    """Every command here resolves a cluster before it runs, so this module declares one."""
    return resolvable_environment
