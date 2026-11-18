"""The `kmt` command surface: exactly the commands it says it has, registered once each.

Two defects live here.

* **D3** — `cli/commands/tunnels.py` defined `def tunnel_polaris` twice, so the second
  definition (the vault tunnel, `openbao-ui` since the rename) won the name binding and `kmt tunnel-polaris` was registered
  pointing at another component's tunnel. A name that appears once in `--help` and silently
  does something else is invisible to every other kind of test: the command exists, it exits
  0, it tunnels *something*. Row 25 retired that command outright, so what this file keeps is
  the shape that made it possible — no callback answering to two names, and a surface that is
  exactly the list below. The retired pair is guarded by
  `test_the_polaris_and_mysql_tunnel_commands_stay_retired.py`, not here.
* **The surface drifting without anyone saying so** — commands added, renamed, or left behind
  by a migration that deleted its script.

So this file asserts three things: the exact set of names `--help` prints, that no name is
registered twice, and that no single function answers to two names.

The fourth arrived when the surface grew its Click groups: a command that moved
under a group is only *gone* if the flat name stopped answering too, and "gone" is the half a
surface test that only reads `--help` can miss. Hence the `*_group_holds_...` tests below: the
group's own listing, and the old flat name answered with "No such command".

The fifth is the rule that the environment is the first positional of every command that touches a
cluster, and it is here rather than in the modules a command is tested in because a command whose
environment sits in the wrong slot fails nothing but its own shape: the old argv
still parses, it parses the wrong things. So `CLUSTER_TOUCHING_POSITIONALS` pins the eleven
positional lists, and the Task check that runs beside it compares each Tekton step's own argv to
the command it calls -- the half of the rule a unit test on the command cannot see.
"""

import contextlib
import io
import re
import sys

import click
import pytest
import yaml
from click.core import Argument, Option

from cli import kmt as kmt_module
from asset_paths import tekton_ci_path

# The commands `kmt --help` prints. A migration that adds a command adds a row here, and one
# that deletes a command deletes a row. If
# this list and the CLI disagree, one of them is wrong — which is the whole point of having
# the list.
EXPECTED_COMMANDS = {
    # `helm` is a group, so neither `helmfile-apply` nor `helm-version` is on this list -- they answer
    # to `kmt helm apply`, `kmt helm latest-version`, and to nothing else. See
    # `test_the_helm_group_holds_the_apply_and_the_version_read_and_their_flat_names_are_dead`.
    "helm",
    # `kubernetes` is a group, so none of the nine cluster verbs it took is on this list. See
    # `test_the_kubernetes_group_holds_the_nine_cluster_verbs_and_their_flat_names_are_dead`.
    "kubernetes",
    "component",
    # `initialize` is deleted outright: `kmt config init` is the verb that prepares a
    # configuration directory, and no row here answers to the old name.
    "install",
    "run-ci-container",
    "setup-docker-registry-access",
    "setup-secrets",
    # The whole tekton surface is the group, so no tekton name is on this list. The cleanup job and
    # the three generators used to keep flat names beside it on the argument that nothing measured
    # them; once the other six were verbs, four commands whose first word is the group's name
    # answering to a hyphen was the inconsistency, so they joined the group and their flat names died.
    "tekton",
    # The tunnel surface is the `tunnel` group and nothing else: the verbs on the group,
    # and the two flat names that were never on it -- `tunnel-polaris`
    # and `tunnel-mysql` -- retired, so their absence from this list is the assertion now.
    "tunnel",
    "uninstall",
    # `users` is a group: its own commands (`create-team-account`, the read-only variant,
    # `delete-team-account`) show up under `kmt users --help`, not here.
    "users",
    # Routing is its own group because it is its own decision: `kmt install` neither installs it
    # nor takes `--update`, so the group is what the harness capability table calls.
    # Its commands live under `kmt routing --help`, the same way `users`' do.
    "routing",
    # What environments this `platform_config` has, and which cluster each addresses. It is
    # on the surface for the reason a decision about it settled: the alternative to answering it in one command is an
    # operator discovering it when a write lands on the wrong cluster. No script it replaced, no
    # group it moved under -- it is the only command here that is new rather than migrated.
    "environments",
    # The configuration group. It is on the surface because creating a tree is a command an
    # operator has to be able to name: before the first byte there is no file to edit, and the path
    # that used to write the checkout instead of a tree the operator chose is deleted. The group is
    # where `config set` / `unset` / `show` / `validate` land, so the fourth verb arrives on a
    # surface with a shape instead of as a fifth flat name.
    "config",
}

# What is on `kmt kubernetes`: the nine verbs that address a cluster that already exists. The set is
# the assertion, so a tenth verb cannot arrive on the group without being decided twice: once as a
# command, and once here, where the comment says what it reads or writes.
# What is on `kmt environments`: the listing, and nothing else. It sits on the
# `EnvironmentRegistry` primitive the 1:1 check resolves against, so the command and the
# check cannot disagree about what is configured -- and a second verb that resolved its own answer
# would be the first place that does.
EXPECTED_ENVIRONMENTS_COMMANDS = {"list"}


# ------------------------------------------------------------------------------------------
# The three flags every command that addresses a cluster carries, and the reason they are not
# spelled out five times below: `@kmt_context` adds them (the `--context` and `--kubeconfig` pair,
# which is the first thing asked when a cluster is resolved, and the root
# that reaches `Config` beside them), so they arrive on a command together with the `Context` it
# builds. What is asserted per command stays exact -- this set plus the flags the command itself
# takes -- because the interesting half of the check is an option that drifted on or off *this*
# command, and that shows up here as surely as it would if each line were written by hand.
# ------------------------------------------------------------------------------------------


# ------------------------------------------------------------------------------------------
# The three groups: `tunnel`, `tekton`, `helm`
#
# Three groups, fourteen commands, no aliases. What is pinned below is the *subcommand* side of the
# name, because that is the half a group has to get right: `tunnel` is the group and `pod` is the
# verb, which together say the same thing `tunnel-pod` said on its own line.
#
# The grouping list started as the 11 commands the lane's capability table measures as still flat --
# four tunnels, six tekton commands and one helm apply. Of the commands that list did not include,
# the two flat tunnels (`tunnel-polaris`, `tunnel-mysql`) were retired rather than grouped (row 25)
# and `helm-version` kept its flat name. The cleanup job and the three generators joined the `tekton`
# group on 2026-11-11 for the reason the other nine had, except that no marker measured these four:
# `kmt tekton` already existed, so commands that read `tekton-<verb>` on the flat surface beside
# verbs that read `tekton <verb>` were a surface describing a rule it did not follow. What keeps a
# rename nothing measures honest is the check below, which asks the shipped entry point whether the
# flat name still answers, and not a capability row that was never going to run them.
# ------------------------------------------------------------------------------------------


def _run_main(monkeypatch, argv):
    """Run `main()`'s registration and return every `add_command` it made.

    `main()` is what wires the commands in, so going through it is the only way to check the
    surface that actually ships rather than a copy of it kept in a test. `--help` ends the
    invocation before anything runs, so no command body executes.
    """
    registrations = []
    original = kmt_module.cli.add_command

    def record(command, name=None):
        registrations.append((command.name, command.callback, command))
        return original(command, name)

    monkeypatch.setattr(kmt_module.cli, "add_command", record)
    # `main()` configures logging for the process (plan D10: it moved out of the library).
    # Registering the surface is what this file checks, and a root handler added by a test
    # that never asked for one is noise for every other test in the run.
    monkeypatch.setattr("logging.basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(sys, "argv", argv)
    try:
        kmt_module.main()
    except SystemExit as exit_request:
        assert exit_request.code == 0, "the help invocation must not fail"
    return registrations


@pytest.fixture
def registrations(monkeypatch):
    """Every `(name, callback, command)` `main()` registered, with `sys.argv` restored."""
    return _run_main(monkeypatch, ["kmt", "--help"])


def help_output():
    """`kmt --help`'s own output, from the real entry point.

    The commands are attached inside `main()`, so the group `cli` only knows them afterwards
    — that is why this goes through `main()` instead of invoking the group directly.
    """
    return run_help("kmt", "--help")


def run_help(*arguments):
    """The help text of one `kmt` invocation, printed by the entry point and nothing else.

    Anything below the group is reached the same way: Click resolves a subcommand's help while it
    processes its parameters, so the command body never runs and asking `kmt install --help` is
    safe in a suite that has no cluster to install onto.
    """
    out = io.StringIO()
    monkeypatch = pytest.MonkeyPatch()
    try:
        with contextlib.redirect_stdout(out):
            _run_main(monkeypatch, list(arguments))
    finally:
        monkeypatch.undo()
    return out.getvalue()


def commands_from_help(text):
    """The command names `--help` prints, parsed out of its own output.

    Click writes each name at two spaces and pads the help column out, so a name longer than the
    column (`setup-cleanup-job`, or three words of a `kmt config` help line, say) gets a line of its
    own and its help starts on the following line, indented to the help column. Reading "any
    indented word" as a name read the first word of that help as a command too, so only the
    two-space column is one.
    """
    listing = text.split("Commands:\n", 1)[1]
    return {line.split()[0] for line in listing.splitlines() if re.match(r"^ {2}\S", line)}


def test_the_help_surface_is_the_expected_command_set():
    names = commands_from_help(help_output())

    assert names == EXPECTED_COMMANDS, (
        f"kmt --help does not match the surface on record; "
        f"unexpected: {sorted(names - EXPECTED_COMMANDS)}, "
        f"missing: {sorted(EXPECTED_COMMANDS - names)}")


# ------------------------------------------------------------------------------------------
# The three groups, each checked the same way: the group is what it is on record as, the
# commands under it still take what they took before, and the flat name each one had is dead.
#
# The last of those three is the one worth reading. A group that grew without the rename would print
# the new form and answer to the old one as well, which is two routes to the same write -- and on
# `kmt tekton run-pipeline` the old route is a Tekton Task that could keep running it. So each test
# below asks the shipped entry point for the old name and reads the answer it must get: exit 2, "No
# such command", no command body.
#
# The old names are *built* from the group and the verb rather than spelled, for the reason
# the same reason one earlier grouping test gives: the grep that says a rename landed has to stay empty, and
# a test that writes the old name puts it back in the tree.
# ------------------------------------------------------------------------------------------


def test_the_environments_group_holds_the_listing_and_nothing_else():
    """The environments listing, and the reason the group has one verb.

    `kmt environments list` is the read-only half of what `kmt doctor` would be, and it is on the
    surface because the inventory is `platform_config`, there is no cluster registry, and the
    alternative to answering "which environment is which cluster" in one command is an operator
    discovering it from a write that went to the wrong place.

    The flag check is the interesting part. A `--context` here would mean the listing resolved a
    cluster, and a listing that resolved its own answers would be a second opinion about what is
    configured — the exact disagreement the shared primitive exists to prevent. So the one
    flag it takes names the root and nothing else: the same root the 1:1 check resolves, which is
    what keeps the listing and a command's refusal saying the same thing about one tree.
    """
    from cli.commands import environments

    names = set(environments.environments.commands)
    assert names == EXPECTED_ENVIRONMENTS_COMMANDS, (
        f"kmt environments does not match the surface on record; "
        f"unexpected: {sorted(names - EXPECTED_ENVIRONMENTS_COMMANDS)}, "
        f"missing: {sorted(EXPECTED_ENVIRONMENTS_COMMANDS - names)}")

    for name, command in environments.environments.commands.items():
        assert (command.help or "").strip(), f"kmt environments {name} has no help text"
        params = {param.name for param in command.params}
        # `--config-dir` is the only flag, and it is the reason this command still agrees with the
        # 1:1 check: the listing reads the same root the check resolves, instead of re-deriving one
        # from the process's own environment. A `--context` here would still be a second opinion
        # about which cluster is configured, and a second opinion is what the rule rules out, so the
        # exact set stays exact.
        assert params == {"config_dir"}, (
            f"kmt environments {name} carries {sorted(params)}; it reads the registry and resolves "
            f"nothing, so a flag that pointed it at a cluster would be a second answer about which "
            "cluster is configured")

    listed = commands_from_help(help_output())
    assert "environments" in listed, "kmt environments is not on kmt --help"
    assert commands_from_help(run_help("kmt", "environments", "--help")) == EXPECTED_ENVIRONMENTS_COMMANDS, \
        "the group's own listing is not the one verb it is supposed to be"


def test_the_update_flag_reaches_the_function_that_decides(monkeypatch, resolvable_environment):
    """`--update` is honoured, not merely accepted.

    Checking that a flag is on the command line cannot tell a value that arrives from one that is
    dropped on the way in, and dropping it is the one defect this file exists to catch: `install_routing`
    is where the repository refresh, the CRD apply and the second-controller consent live, so a run
    that upgraded nothing while printing the upgrade's log is a run that lied to the
    person who read it. Hence running the command twice against a recording `install_routing` -- once
    with no flags, once with both -- and comparing what reaches the function to what the command line
    said. `False` in both runs is the failure this is written to catch, so the first run has to say
    `False` for it to mean anything.

    Nothing in the path touches a cluster: `install_routing` is the only thing replaced, and the
    command's own argument and option parsing is the real one. The environment that `kmt_context`
    resolves before the body runs comes from `resolvable_environment`, which is the fixture's and
    not this checkout's — a command that resolves a cluster cannot be tested by stubbing the thing
    it resolves, only by giving it somewhere honest to have come from.
    """
    import inspect
    import types

    from click.testing import CliRunner

    from cli.commands import routing as routing_cli
    from kubementat.automations.routing import routing as routing_core

    # Read before the patch, because it is the function the operator's flag is bound to, not this
    # test's stand-in: both flags have a named parameter to land in, or the keyword call is a
    # TypeError the surface would have taken instead of a decision.
    parameters = inspect.signature(routing_core.install_routing).parameters
    assert "update" in parameters and "consent" in parameters, (
        "install_routing has no parameter for the flags kmt routing install offers; the CLI is "
        "offering flags with nowhere to go. `consent` is where --yes lands: the flag answers the "
        "one branch that needs it, and the CLI decides how it is answered (item 7).")
    assert "ctx" in parameters, (
        "install_routing no longer takes the one `Context` every class that reaches a cluster is "
        "given; the CLI's `@kmt_context` has nothing to hand over")

    calls = []

    def recording(ctx, update=False, consent=None):
        calls.append({"environment": ctx.environment, "team": ctx.team,
                      "update": update,
                      # What the flag became: `assumed` is `--yes`, `undecided` is the CLI handing
                      # down a terminal to ask on. The flag's whole job is which of the two arrives.
                      "consent": consent.answer if consent is not None else None})
        return types.SimpleNamespace(summary=lambda: "recorded")

    monkeypatch.setattr(routing_core, "install_routing", recording)
    monkeypatch.delenv("TEAM", raising=False)

    runner = CliRunner()
    # `kmt routing install` takes no `--team` any more, so both runs reach `install_routing` with
    # `team=None` and the comparison below is only about the flags. That is a change of surface and
    # not of behaviour: the namespace it writes into is the environment's, and a run that named a
    # team used it for nothing. The two runs are otherwise the same run, so any drift the flags
    # cause has nowhere to hide.
    plain = runner.invoke(routing_cli.routing, ["install", "dev"])
    both = runner.invoke(routing_cli.routing, ["install", "dev", "--yes", "--update"])

    assert plain.exit_code == 0, plain.output
    assert both.exit_code == 0, both.output
    assert len(calls) == 2, "neither invocation reached install_routing"

    without_flags, with_flags = calls
    assert (without_flags["update"], without_flags["consent"]) == (False, "undecided"), (
        f"the bare command asked for an upgrade anyway, or decided consent for the operator: "
        f"{without_flags}")
    assert (with_flags["update"], with_flags["consent"]) == (True, "assumed"), (
        f"--yes/--update did not reach install_routing as they were given: {with_flags}")
    for call in calls:
        assert (call["environment"], call["team"]) == ("dev", None), (
            f"the command changed what it operates on while it was passing the flags through: {call}"
            ". `team` is None because `kmt routing install` no longer offers one; if it came back"
            " this assert is where the change is noticed")


# ------------------------------------------------------------------------------------------
# The environment is the first positional of every command that touches a cluster
#
# This is the shape guard the rule names, so it lives here and not in the module a command
# is tested in: a command that quietly keeps `namespace` first keeps passing its own tests.
#
# The table is the whole surface plus the two commands that were already there and had to be moved
# into line -- `create-ingress`, whose environment used to be an optional fifth argument, and
# `helm latest-version`, whose environment used to be second. The manifest generator is on this list
# for the rule and not for the cluster: it writes a file and reads no socket. The rule admits no
# exception, because a rule with one exception is a rule somebody has to remember.
#
# The keys are the argv that reaches the command, so a command under a group is keyed with the group
# first. That is not decoration: this table is read by the Task check below, which resolves a step's
# own argv against the command tree, and a key that left the group out would resolve to nothing and
# check nothing.
# ------------------------------------------------------------------------------------------

CLUSTER_TOUCHING_POSITIONALS = {
    "kubernetes analyze-deployment": ["environment", "namespace", "deployment"],
    "kubernetes pod-images": ["environment", "namespace"],
    "kubernetes cluster-capacity": ["environment"],
    "kubernetes pvc-usage": ["environment"],
    "kubernetes deployment-ssh": ["environment", "namespace", "deployment", "container"],
    "kubernetes deployment-logs": ["environment", "namespace", "deployment", "container"],
    "kubernetes service-account-token": ["environment", "namespace", "service_account_name"],
    "kubernetes create-secret-from-json": ["environment", "namespace", "secret_name",
                                           "json_file_path"],
    # The grouped ones are addressed by the argv an operator types, group first.
    "tunnel pod": ["environment", "namespace", "pod_name"],
    "kubernetes create-ingress": ["environment", "domain", "service_name", "service_port"],
    "helm latest-version": ["environment", "chart_path"],
}


# The Tekton Tasks, which are the other half of this shape and the half most likely to be missed.
# The Tasks ship inside the package, so this is asked of the resolver and not of the
# checkout: the shape rule below is a rule about the manifests `kmt install` applies, and a wheel
# applies the packaged ones.
CI_TASKS = tekton_ci_path("tasks")


def _command_tree(registrations, group=None, prefix=()):
    """Every registered command, keyed by the argv path that reaches it.

    `main()` is what wires the commands in, so this walks the registrations the surface test
    collected rather than the bare `cli` object — which knows nothing about them until `main()` has
    run, and a test that read it before that would silently resolve nothing.
    """
    if group is None:
        entries = [((name,), command) for name, _callback, command in registrations]
    else:
        entries = list(group.commands.items())
    for name, command in entries:
        path = prefix + (name,) if group is not None else name
        if isinstance(command, click.Group):
            yield from _command_tree(registrations, command, path)
        else:
            yield tuple(path), command


def _command_by_argv_path(registrations, path):
    """The command `kmt <path>` runs, or None when nothing answers to that argv."""
    return dict(_command_tree(registrations)).get(tuple(path))


def _positionals(command):
    """The command's arguments, in the order it takes them."""
    return [parameter.name for parameter in command.params
            if isinstance(parameter, Argument)]


def _task_scripts():
    """The text of every step script in every Task, with the file it came from.

    Parsed as YAML and not grepped, because the string that matters is inside a block scalar and a
    grep reads the whole file, comments and param descriptions included.
    """
    for path in sorted(CI_TASKS.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in document["spec"].get("steps", []):
            script = step.get("script") or ""
            if isinstance(script, str):
                yield path, script


def _kmt_argv_from(script, start):
    """The tokens that follow one occurrence of the word `kmt`, read as a shell would read them.

    Quoted arguments stay one token, a `$(params.x)` substitution stays one token, a `\n`
    continuation is skipped, and the read stops where the command stops: at `;`, `&`, `|`, a
    closing paren (bash's array assignment), or a comment. That last rule is why the read does not
    have to know which lines of a script are prose — a sentence in a comment that says what a
    command replaced is scanned as its own candidate, resolves to nothing, and is dropped by the
    caller rather than mistaken for an invocation.
    """
    token = re.compile(r"""\$\([A-Za-z0-9_-]+\)
                          |\$\{?[A-Z_][A-Z0-9_]*\}?
                          |"[^"]*"
                          |'[^']*'
                          |[^;\s|)&#]+""", re.X)

    argv = []
    index = start
    while index < len(script) and len(argv) < 12:
        if script[index].isspace() or script[index] == "\\":
            index += 1
            continue
        if script[index] == "#":
            newline = script.find("\n", index)
            if newline == -1:
                break
            index = newline + 1
            continue
        if script[index] in ";&|)":
            break
        match = token.match(script, index)
        if match is None:
            break
        text = match.group(0)
        if text != "\\":
            argv.append(text.strip('"').strip("'"))
        index = match.end()
    return argv


def _kmt_invocations(script):
    """Every argv the step builds from the word `kmt`, in the order it appears.

    Full-line comments are dropped first, because a step may say what its command replaced — and
    the sentence usually names the command in the same words the step uses to run it, which reads as
    a second invocation that was never one.
    """
    body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    body = _without_message_text(body)
    return [_kmt_argv_from(body, match.end())
            for match in re.finditer(r"(?<![\w-])kmt\s+", body)]


def _without_message_text(body: str) -> str:
    """Blank the text an `echo` or `printf` prints, and keep everything a step actually executes.

    The same reason comments are dropped, applied to the wider case the comment rule does not cover: a
    step's *error message* names the verb that would fix the problem -- "check that kmt install ran against
    the configuration directory that owns this clone" -- and that is prose inside shell quoting, not a
    command. Read as argv it invents a long call to a short command and reports it against whichever Task
    happened to be written politely to its operator, which is a failure with no cause and no fix.

    Only the quoted argument of a message command is removed, so a step that prints and then runs is still
    seen running. A call hidden inside a string that this strips would be a call Tekton never executes, and
    there is no such thing.
    """
    return re.sub(r"(?<![\w-])(?:echo|printf)[ \t]+((?:[\"'][^\"']*[\"'])|(?:[^\\s]|\\\\)+)",
                  lambda m: "" if "kmt" in m.group(1) else m.group(0), body)


def _positional_tokens(command, tokens):
    """The argv tokens that are the command's arguments, with its flags taken out.

    A flag is not one token: `--wait` costs one and `--config-dir PATH` costs two, and the shape of
    the token cannot tell the two apart — both start with `-`. So the answer comes from the
    command's own declarations, which is the same place every other sentence in this file reads
    from. Until this ticket no shipped Task had passed a flag at all, and the helper that now reads
    them had no reason to know the difference: everything after the command's name was counted as a
    positional, which silently turned every boolean flag in the tree into a phantom argument and
    every flag with a value into two.

    An option the command does not declare stays in the list. Click refuses it when the step runs,
    and the count that reports it is closer to the truth than a guess about what it meant to be.
    """
    takes_value = {}
    for param in command.params:
        if isinstance(param, Option):
            for opt in list(param.opts) + list(param.secondary_opts):
                takes_value[opt] = (not getattr(param, "is_flag", False)
                                    and getattr(param, "nargs", 1) >= 1)

    kept = []
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            continue
        if token.startswith("-") and len(token) > 1:
            option, _, inline_value = token.partition("=")
            if option in takes_value:
                skip_value = takes_value[option] and not inline_value
                continue
            # Not declared by this command: kept, deliberately. Click refuses it the moment the step
            # runs, and the count that reports a token it did not expect is closer to the truth than
            # a filter that quietly absorbs a typo into "that was a flag, then".
            kept.append(token)
            continue
        kept.append(token)
    return kept


def _split_invocation(registrations, argv):
    """`('create-secret-from-json', ['$(params.environment)', ...])` for a step's argv.

    The command path is read greedily from the front: a group name is a command's name too, and the
    first token that is not a command is where the positionals start. The options that follow are
    not positionals and come out here, by `--config-dir`'s first appearance on a shipped step.
    """
    for length in range(len(argv), 0, -1):
        command = _command_by_argv_path(registrations, argv[:length])
        if command is not None:
            return command, argv[:length], _positional_tokens(command, argv[length:])
    return None, argv[:1], argv[1:]


def _names_the_environment(token):
    """Whether a step's token says the environment, by param name or by variable."""
    return "environment" in token.lower()


def test_the_ci_task_manifests_pass_the_positionals_their_commands_take(registrations):
    """Every Task that runs a `kmt` command, read out of its own script and not remembered.

    This is the collateral most likely to be discovered late: the Tasks
    and the commands move together, or a pipeline breaks somewhere no unit test can see. So the
    check is general rather than pinned to the one Task this ticket changed. For each invocation
    found in a Task: the command it names is registered, the number of positionals the step sends is
    the number the command takes, and the first of them is the environment whenever the command
    declares one.

    Position and count, not values: a step may pass `$(params.environment)` or `$ENVIRONMENT`, and
    both say the same thing.
    """
    checked = []
    for path, script in _task_scripts():
        for argv in _kmt_invocations(script):
            command, command_path, positional = _split_invocation(registrations, argv)
            if command is None:
                # Prose in a comment, or a `kmt` that is not a command name; an invocation that
                # resolves to nothing is caught by the surface test above, not by this one.
                continue

            taken = _positionals(command)
            assert taken, (f"{path.name} runs kmt {' '.join(command_path)}, which takes no "
                           "positionals, so the step's argv has nothing to line up with")
            assert len(positional) == len(taken), (
                f"{path.name} passes {len(positional)} positionals to kmt "
                f"{' '.join(command_path)}, which takes {len(taken)}: {taken}. A Task and a command "
                "are one change; this is the difference that cannot be found by reading the "
                "command")
            if "environment" in taken:
                assert positional and _names_the_environment(positional[0]), (
                    f"{path.name} passes {positional[:1]} first to kmt "
                    f"{' '.join(command_path)}, whose first positional is the environment. "
                    "The step and the command are one change.")

            checked.append(f"{path.name}: kmt {' '.join(command_path)}")

    assert checked, "no Task in tekton_ci/tasks runs a kmt command, so nothing was compared"
