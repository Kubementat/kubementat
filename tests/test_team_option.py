"""`--team` carries no default: the team a command runs against is a choice.

`helmfile-apply` and the tunnels used to pass the literal `'dev1'`, and D9's answer was an option
*with that literal as its default* — so an invocation that typed nothing kept reading `dev1` and the
bug class survived the fix. Deleting the default takes it out, which leaves `--team`/`TEAM` as the only two
ways a command learns its team, and `Context.require_team` as the sentence that says what a command
with neither cannot do. The tests below are the two halves of that: the value a run does give
arrives, and the value a run does not give is refused rather than remembered.
"""

import re

import pytest
from click.testing import CliRunner

from cli import helpers
from cli.commands import (component, helm, kubernetes, kubementat, routing, tekton, tunnels,
                           users, utilities)
from kubementat.errors import TeamNotSuppliedError
# The run path's verbs are module functions now (P4-3), so this is the seam to intercept.
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
    # The four 11b made the finding for.
    (tunnels.tunnel_grafana, ("dev",)),
    (tunnels.tunnel_tekton, ("dev",)),
    (tunnels.tunnel_openbao_ui, ("dev",)),
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


# The three verbs `kmt tunnel` still opens, so the recorder below can be attached to the names the
# commands actually look up: the tunnels layer handed them functions rather than an object.
TUNNEL_FUNCTIONS = ("open_tunnel_grafana", "open_tunnel_tekton_dashboard", "open_tunnel_openbao")


class RecordingContext:
    """Stands in for the classes that take one `Context` -- `HelmUtils` does, and did.

    Records the `(environment, team)` pair the object carries, because that pair is what these
    tests assert; what changed is where it lives, not whether the command still decides it. The run
    path reads that pair plus the config and a client off the `Context` now (`P4-6` deleted
    `TektonUtils`, which is what used to read them off and hand them on), so the recorder that
    covered both services now covers only the one that is still a class.
    """

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, ctx):
        self.calls.append((ctx.environment, ctx.team))
        # What the run path reads off the object now (P4-3 moved the verb onto `runs`). The verb is
        # intercepted below, so nothing here is ever asked to touch a cluster.
        self.config = ctx.config
        self.environment = ctx.environment
        self.team = ctx.team
        self.kubernetes_utils = None
        return self

    def helmfile_apply(self, **kwargs):
        self.calls[-1] = self.calls[-1] + (kwargs,)
        return None


class RecordingVerb:
    """The run path's verb, recorded instead of run.

    `setup_cleanup_job` is a function on `infrastructure.tekton.runs` (P4-3), so a recorder
    attached to a class would let the command run the real thing against a cluster this test was
    never given. It records the `(environment, team)` pair off the `Context` the command handed
    over, which is the claim these tests made off the constructor until P4-6 deleted it — and since
    the class is what used to make that claim, the recorder now reads the pair from the one object
    that carries it, so a flag that never reaches the `Context` still fails here.
    """

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, ctx, kubernetes_utils, config, environment, *args, **kwargs):
        self.calls.append((environment, ctx.team))
        return None


class RecordingTunnels:
    """One of the three tunnel entry points, recorded instead of run.

    The tunnels used to be reached through an `Automation`, so a recorder that replaced the class
    covered all three. They are now names on the command module, called as
    `function(ctx, local_port, remote_port, host)`, so this records the pair the `Context` carries
    and returns nothing -- which is what `open_tunnel` reads as a tunnel that opened.
    """

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, ctx, *args):
        self.calls.append((ctx.environment, ctx.team))
        return None


@pytest.fixture
def recorder(monkeypatch):
    calls = []
    # The `TektonUtils` seam is gone with the class (P4-6), and the run-path verb carries the pair
    # the commands used to read off it, so intercepting the verb is both the recorder and the
    # assertion. A patch that only covered a name the command never looks up would keep this file
    # green while saying nothing, which is why the assertion below compares what the recorder saw.
    monkeypatch.setattr(runs, "setup_cleanup_job", RecordingVerb(calls))
    for name in TUNNEL_FUNCTIONS:
        monkeypatch.setattr(tunnels, name, RecordingTunnels(calls))
    return calls


def invoke(command, args, **runner_kwargs):
    """The command as an operator runs it, with no context object handed in.

    There is nothing to hand any more: `cli.kmt.Config` was a context object one field wide that
    every command ignored, and what a command receives now is built by `@kmt_context` out of
    the arguments it was given. So this passes arguments and nothing else, which is also the point
    the tests below are about.
    """
    return CliRunner().invoke(command, args, **runner_kwargs)


@pytest.mark.parametrize("command,base_args", TEAM_FLAG_COMMANDS,
                         ids=[command.name for command, _ in TEAM_FLAG_COMMANDS])
def test_the_team_flag_reaches_the_command(command, base_args, recorder):
    result = invoke(command, list(base_args) + ["--team", TEAM])

    assert result.exit_code == 0, result.output
    assert recorder[-1][:2] == ("dev", TEAM), "the team never left the flag it was typed on"


@pytest.mark.parametrize("command,base_args", TEAM_FLAG_COMMANDS,
                         ids=[command.name for command, _ in TEAM_FLAG_COMMANDS])
def test_the_team_environment_variable_is_honoured(command, base_args, recorder, monkeypatch):
    monkeypatch.delenv("TEAM", raising=False)
    result = invoke(command, list(base_args), env={"TEAM": "team-a"})

    assert result.exit_code == 0, result.output
    assert recorder[-1][1] == "team-a"


def test_the_option_offers_no_default_of_any_kind():
    """`--team` has no default, and the module that offered one no longer holds it.

    Asserted from both sides because the two failure modes are different: a `default=` put back on
    the option is a guess the operator cannot see, and a constant left behind in `helpers` is a
    guess the next command can import. `test_no_dead_code_ships_in_the_tree` covers the second one
    as dead code; this one covers it as a value that could still be handed to Click.
    """
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
    """No `--team`, no `TEAM`, and three commands that all say the same thing about it.

    The refusal is `require_team`'s, which is where it belongs: the sentence names the
    environment, the directory the value belongs in, and the flag that would have answered it. It
    arrives as the command's own answer and not as a stack trace — `TeamNotSuppliedError` is wrapped
    as a `ClickException` by `@kmt_context`, the same treatment `ClusterResolutionError` gets, so an
    operator who types nothing is told what is missing instead of being handed a bug report. The
    exit code is the named refusal's (1, not a crash's) and the traceback stays out of the output,
    which is the whole assertion of the ticket that made this true.
    """
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
    """The other half of the rule: a command with no team semantics does not accept `--team`.

    Deleting the option is what makes the flag's absence an answer rather than a question, so the
    assertion is what typing `--team` now costs: `No such option: --team`, exit 2, and no `TEAM:`
    line printed at nothing. An option that resolves to nothing is how the old tunnels read `dev1`,
    so this is the same rule with the opposite symptom checked.
    """
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


# The three tunnel verbs, identified by command so the test below can run them for real once the
# flag it refuses is off. `routing.status` and the seven below it never reach the tunnels layer.
TUNNEL_COMMANDS = {tunnels.tunnel_grafana, tunnels.tunnel_tekton, tunnels.tunnel_openbao_ui}


def test_no_team_is_hardcoded_in_the_command_modules():
    """The whole point of both rules together: no command gets to remember a team."""
    hardcoded_team = re.compile(r"['\"]dev1['\"]")
    offenders = []
    for module in (helm, tunnels, tekton, kubementat, utilities, kubernetes):
        with open(module.__file__) as source_file:
            for line in source_file:
                code = line.split("#")[0]
                if hardcoded_team.search(code):
                    offenders.append("%s:%s" % (module.__name__, line.strip()))

    assert not offenders, "hardcoded team back in %s" % offenders


def test_helmfile_apply_reports_the_environment_and_filter_it_used(monkeypatch):
    """What the apply says about itself, after the `--team` line came off it.

    The echo is what tells an operator why a scripted run came out the way it did, so the
    environment and the filter stay. The `team:` line went with the option: printing a value the
    command was not given, or was given and did not use, is noise wearing a label, and a
    run that types `TEAM=...` proves it is noise by changing nothing about the apply.
    """
    calls = []
    monkeypatch.setattr(helm, "HelmUtils", RecordingContext(calls))

    result = invoke(helm.helmfile_apply, ["dev", "--helmfile-label-filter", "group=routing"],
                    env={"TEAM": TEAM})

    assert result.exit_code == 0, result.output
    assert "environment: dev" in result.output
    assert "helmfile-label-filter: group=routing" in result.output
    assert TEAM not in result.output, "the apply still reports a team it never reads"
    # `TEAM` is in the process, and it changes nothing: the option is gone, so `_team_of` finds no
    # `team` parameter to read and the `Context` the service is built from carries `None`. That is
    # the whole point of deleting the flag rather than its echo -- a variable with nothing to reach
    # cannot become a default again. (The service is recorded and not run, so what is asserted here
    # is the pair it was handed, not a cluster read.)
    assert calls[-1][:2] == ("dev", None), (
        f"`TEAM` reached a command that has no parameter for it: {calls[-1]}")


@pytest.fixture(autouse=True)
def _an_environment_the_command_resolves(resolvable_environment):
    """Every command here resolves a cluster before it runs, so this module declares one.

    The services are recorded rather than called, so the fixture's job ends at resolution: it says
    which cluster the command did *not* reach, and nothing else about the run changes. The team is
    what these tests are about, and it comes from `--team`/`TEAM` exactly as the flag said it would.
    """
    return resolvable_environment
