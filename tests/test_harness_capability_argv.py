"""Guards the E2E capability table's argv against the scripts and commands it drives.

These are the two mistakes that made capabilities red without any product change:
a `{placeholder}` that no stage supplies (it survives into argv verbatim), and a
script invoked with fewer arguments than its `Usage:` line requires. The account
scripts need `<ENVIRONMENT> <TEAM> <SERVICE_ACCOUNT_NAME>`, and for a long time the
table handed them two.
"""

import contextlib
import importlib.util
import io
import re
import sys
from pathlib import Path

import click
import pytest

HARNESS_CONFIG = Path(__file__).resolve().parent / "e2e" / "harness" / "config.py"
REPO_ROOT = Path(__file__).resolve().parent.parent

PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

ACCOUNT_CAPABILITIES = (
    "task.create-team-account",
    "task.create-team-account-read-only",
    "task.delete-team-account",
)

# The grafana accounts, which came over from `run-script` later and take the environment as an
# argument rather than as an option. Listed apart so the check above stays about service accounts,
# which is what its `--environment` rule was written for.
GRAFANA_ACCOUNT_CAPABILITIES = (
    "task.create-grafana-accounts",
)


def load_harness_config():
    spec = importlib.util.spec_from_file_location("kmt_e2e_harness_config", HARNESS_CONFIG)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return load_harness_config()


def script_invocation(argv):
    """The script path followed by the arguments it gets, from a run-script argv.

    Handles both `--args VALUE` and the unambiguous `--args=VALUE` form.
    """
    tokens = []
    for token in argv[argv.index("run-script") + 1:]:
        if token == "--args":
            continue
        if token.startswith("--args="):
            tokens.append(token[len("--args="):])
            continue
        tokens.append(token)
    return tokens


def positionals(invocation):
    """The arguments a script reads as `$1..$N`; everything else is a flag like `--yes`."""
    return [token for token in invocation[1:] if not token.startswith("-")]


def test_every_placeholder_is_somewhere_the_stage_can_supply_it(harness):
    resolvable = set(harness.LANE_PLACEHOLDERS) | set(harness.STAGE_PLACEHOLDERS)
    surface = build_kmt_surface()
    unresolved = {}
    for name, cap in sorted(harness.CAPABILITIES.items()):
        for token in cap.argv:
            for placeholder in PLACEHOLDER.findall(token):
                if placeholder not in resolvable:
                    unresolved.setdefault(name, set()).add(placeholder)

    assert not unresolved, "unknown placeholders: %s" % unresolved


@pytest.mark.parametrize("capability", ACCOUNT_CAPABILITIES)
def test_account_capabilities_are_commands_and_not_scripts(harness, capability):
    """A team account is reached by command, so the table says `kmt users ...`.

    These rows drove `kmt run-script --args .../create_team_account.sh` while that script
    existed, which is how the lane was the only thing that could exercise a product feature.
    A row that still says `run-script` here is pointing at a file this checkout deleted.
    """
    argv = harness.argv_for(capability, **harness.LANE_PLACEHOLDERS)

    assert argv[0] == "kmt" and argv[1] == "users", argv
    assert "run-script" not in argv, "%s runs a script, and scripts/user_management/ is gone" % capability
    assert argv[2] == capability[len("task."):], "%s does not name the command it drives" % capability


@pytest.mark.parametrize("capability", ACCOUNT_CAPABILITIES)
def test_account_capabilities_name_environment_team_account_and_confirmation(harness, capability):
    """All four things the command needs, or the command does something else.

    The account scripts took `<ENVIRONMENT> <TEAM> <SERVICE_ACCOUNT_NAME>` and a `--yes`, and
    for a long time the table handed them two of the four. The shape changed when the scripts
    became commands; what has not changed is that an environment or a team left out of argv is
    a run that edits the wrong cluster.
    """
    argv = harness.argv_for(capability, **harness.LANE_PLACEHOLDERS)

    assert "--environment" in argv, "%s silently uses the CLI's default environment" % capability
    assert argv[argv.index("--environment") + 1] == harness.LANE_PLACEHOLDERS["env"]
    assert "--team" in argv, "%s silently uses the CLI's default team" % capability
    assert argv[argv.index("--team") + 1] == harness.TEAM
    assert "--yes" in argv, "%s would block on a confirmation there is no terminal to answer" % capability

    bare = positional_tokens(argv)
    assert len(bare) == 1, (
        "the account name is the command's only positional argument, got %s" % bare)
    assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", bare[0]), \
        "%s passes %r, which is not a service account name" % (capability, bare[0])


def positional_tokens(argv, start=3):
    """The bare arguments after the command name, with every `--flag VALUE` pair removed.

    `{env}` and `{team}` resolve to ordinary words by the time argv is real, so telling them
    apart from a positional has to be done structurally rather than by their shape. `start=3`
    skips `kmt users <command>`, which are names of the command rather than arguments to it.
    """
    tokens, index = [], start
    while index < len(argv):
        token = argv[index]
        if token.startswith("-"):
            index += 2 if "=" not in token and index + 1 < len(argv) and not argv[index + 1].startswith("-") else 1
            continue
        tokens.append(token)
        index += 1
    return tokens


def test_the_account_commands_exist_on_the_cli(harness):
    """The table must drive something that answers.

    A capability row that points at a command nobody registered exits 2 with "no such
    command", and in a lane that reads as a product failure.
    """
    from cli.commands import users

    registered = set(users.users.commands)
    driven = {harness.argv_for(name, **harness.LANE_PLACEHOLDERS)[2]
              for name in harness.CAPABILITIES
              if harness.argv_for(name, **harness.LANE_PLACEHOLDERS)[:2] == ["kmt", "users"]}

    assert driven <= registered, ("the table drives commands nobody registered: "
                                  f"{sorted(driven - registered)}")


@pytest.mark.parametrize("capability", GRAFANA_ACCOUNT_CAPABILITIES)
def test_grafana_account_capabilities_are_commands_and_resolve_their_own_address(
        harness, capability):
    """The row drives a command, and hands it nothing about where grafana is.

    Passing `--grafana-url` here would be the same bug with a different spelling: the address
    would come from the harness instead of from the cluster, which is precisely the part that was
    defaulted to a socket nobody had opened. So the interesting assert is that the flag is absent,
    next to the usual "this is a command and it can run unattended" checks.
    """
    argv = harness.argv_for(capability, **harness.LANE_PLACEHOLDERS)

    assert argv[0] == "kmt" and argv[1] == "users", argv
    assert "run-script" not in argv, "%s runs a script, and scripts/user_management/ is gone" % capability
    assert argv[2] == capability[len("task.") :], "%s does not name the command it drives" % capability
    assert harness.LANE_PLACEHOLDERS["env"] in argv, "%s does not say which environment" % capability
    # No `--team`: the rows, the credential and the address this command reads are all
    # environment-level, so the option was deleted with the guard behind it, and a `--team`
    # in this argv would now be a flag the command the row drives does not define.
    assert "--team" not in argv, "%s names a team it cannot act on" % capability
    assert "--yes" in argv, "%s would block on a confirmation there is no terminal to answer" % capability
    assert "--grafana-url" not in argv, (
        "%s hands the command an address, which is the vacuous row in a new place: resolving it "
        "from the cluster is what the row measures" % capability)


def test_the_read_only_account_is_not_the_admin_account(harness):
    """Both account names have to survive into argv, and they cannot be the same string."""
    def account_in(capability):
        return positional_tokens(harness.argv_for(capability, **harness.LANE_PLACEHOLDERS))[-1]

    admin = account_in("task.create-team-account")
    read_only = account_in("task.create-team-account-read-only")
    deleted = account_in("task.delete-team-account")

    assert admin != read_only, "both accounts would be created in the same namespace under the same name"
    assert deleted == admin, "the stage checks that the account it created is gone"
    assert harness.TEAM_ACCOUNT == admin
    assert re.fullmatch(r"[a-z0-9-]+", admin) and re.fullmatch(r"[a-z0-9-]+", read_only), "not valid SA names"


def declared_positional_parameters(script_text):
    """How many positionals a script reads: the highest `$N` it assigns to itself."""
    indices = [int(n) for n in re.findall(r'="\$(\d+)"', script_text)]
    return max(indices) if indices else 0


def run_script_capabilities(harness):
    return sorted(name for name, cap in harness.CAPABILITIES.items() if "run-script" in cap.argv)


@pytest.mark.parametrize("capability", run_script_capabilities(load_harness_config()))
def test_no_run_script_capability_passes_more_arguments_than_its_script_reads(harness, capability):
    invocation = script_invocation(harness.argv_for(capability, **harness.LANE_PLACEHOLDERS, group="standard"))
    script = REPO_ROOT / invocation[0]
    declared = declared_positional_parameters(script.read_text())

    assert script.exists(), "capability %s points at a script that is not in the checkout" % capability
    assert len(positionals(invocation)) <= declared, (
        "%s reads %d positionals but %s supplies %d - one of them is being read as the wrong parameter"
        % (script.name, declared, capability, len(positionals(invocation))))


def team_flagged_capabilities(harness):
    """Every row whose argv still carries `--team`, read out of the table rather than remembered.

    A list written here would go stale the day a command's option went away -- which is exactly the
    list's own failure mode, so it is computed from the argv the harness would actually run.
    """
    return sorted(name for name, cap in harness.CAPABILITIES.items() if "--team" in cap.argv)


@pytest.mark.parametrize("capability", team_flagged_capabilities(load_harness_config()),
                         ids=lambda capability: capability)
def test_team_is_named_in_the_argv_for_the_team_scoped_commands(harness, capability):
    """Where a command *does* resolve a team, the row has to name one for it.

    The three component tunnels used to be on this list, and they earned it for the wrong reason:
    the argv carried `--team` because the command accepted one, not because anything it read came
    from `platform_config/{env}/<team>/`. The option was deleted on commands that cannot act on a
    team, so a `--team` in their argv would now be a flag their command does not define -- which is
    exactly what `test_no_capability_argv_carries_an_option_its_command_does_not_define` catches.
    Only the rows whose commands still resolve a team are asserted here, and `helm.helmfile-apply`
    moved off the list for the same reason the tunnels went: `helm apply` derives
    every path it uses from the environment.
    """
    argv = harness.argv_for(capability, **harness.LANE_PLACEHOLDERS, group="standard")

    assert "--team" in argv, "%s silently uses the CLI's default team" % capability
    assert argv[argv.index("--team") + 1] == harness.TEAM


def test_account_names_are_valid_kubernetes_service_account_names(harness):
    assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", harness.TEAM_ACCOUNT)
    assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", harness.TEAM_ACCOUNT_READ_ONLY)
    assert harness.LANE_PLACEHOLDERS["account"] == harness.TEAM_ACCOUNT


# --------------------------------------------------------------------- routing
# The routing group is applied by its own command rather than by a helmfile selector, which makes it
# the first capability whose argv is also the answer to "how is this group applied". Both halves are
# checked in this file because they are the same class of mistake: a row that does not spell the
# command right, and a mapping that points at a row nobody can run.

ROUTING_CAPABILITIES = ("routing.install", "routing.status")


def test_the_routing_rows_are_the_commands_the_plan_named(harness):
    """`kmt routing install <env> --yes`, and the same command without the --yes for the read.

    Which half of the confirmation contract each row is on is the whole assertion. A mutating
    command that cannot prompt fails closed, so an install row without --yes is a lane that stalls
    on a question nobody is asked to answer; and --yes on a read-only row is the tell that somebody
    made it write, because a command with nothing to confirm has no reason to be given one.
    """
    install = harness.argv_for("routing.install", **harness.LANE_PLACEHOLDERS)
    status = harness.argv_for("routing.status", **harness.LANE_PLACEHOLDERS)

    assert install == ["kmt", "routing", "install", harness.ENVIRONMENT, "--yes"]
    assert status == ["kmt", "routing", "status", harness.ENVIRONMENT]
    assert "--yes" not in status


@pytest.mark.parametrize("capability", ROUTING_CAPABILITIES)
def test_the_routing_rows_say_whether_they_write_to_the_cluster(harness, capability):
    """`mutating` is where a row says it changes the cluster, and only the install row may.

    Stage 04 re-runs a later group by re-running the capability that applies it, so the mark is
    what tells a reader which rows are expected to run twice. Inert here would mean the rerun loop
    is re-running a command it has been told cannot change anything, and the group's second pass is
    a command nobody marked as repeatable.
    """
    expected = capability == "routing.install"

    assert harness.CAPABILITIES[capability].mutating is expected


def test_the_mapping_says_the_routing_group_is_applied_by_the_install_command(harness):
    """One answer to "how is this group applied", and it is the command that can decline.

    A `--helmfile-label-filter group=routing` applies; it cannot decide not to. The mapping exists
    because that difference has to be stated once, for every stage, rather than inferred from which
    stage happens to be walking the group list.
    """
    assert harness.GROUP_APPLY_CAPABILITIES["routing"] == "routing.install"
    assert harness.apply_capability_for_group("routing") == "routing.install"


def test_a_group_with_no_mapping_entry_keeps_the_plain_helmfile_apply(harness):
    """The fallback is what makes the mapping an addition rather than a tax on new groups.

    A group nobody has named is still applied the way groups have always been applied, so forgetting
    to add a row is what a new group does by default and not a lane that silently stops applying it.
    """
    assert harness.apply_capability_for_group("standard") == harness.HELMFILE_APPLY
    assert harness.apply_capability_for_group("something-new-here") == harness.HELMFILE_APPLY
    assert harness.HELMFILE_APPLY in harness.CAPABILITIES


def test_every_capability_the_mapping_names_is_a_row_that_can_be_run(harness):
    """A mapping entry that points at a row nobody can resolve is a stage that dies on a KeyError.

    The resolution goes through `argv_for`, so a typo in either half surfaces here first: either the
    key is not in the table, or the row it selects cannot be spelled out from the lane's own
    placeholders plus the group the stage is holding.
    """
    ctx = dict(harness.LANE_PLACEHOLDERS, group="standard")
    for group, capability in sorted(harness.GROUP_APPLY_CAPABILITIES.items()):
        assert capability in harness.CAPABILITIES, (
            "group %s maps to %s, which is not a capability row" % (group, capability))
        argv = harness.argv_for(capability, **ctx)
        assert all("{" not in token for token in argv), (
            "group %s resolves to argv that still carries an unresolved placeholder: %s" % (group, argv))

# ------------------------------------------------------------------ renamed-command guard
# 02cd932 renamed the 11 flat commands the lane measures into the `tunnel`/`tekton`/`helm`
# groups with no aliases, and the capability table kept spelling the old names: eleven rows
# that exit 2 with "No such command", i.e. a lane that cannot execute the capability at all
# rather than a red one. A stage driving a dead command measures nothing, so this resolves
# what the table names against the real CLI surface -- argv[1], plus argv[2] when argv[1] is
# a group -- rather than against anyone's memory of what the command is called.

def build_kmt_surface():
    """The surface `main()` registers: walked out of the entry point rather than out of a
    list of names someone typed, so the real `cli.commands` modules are what get read.
    Cheap and single: `--help` ends the invocation before a command body runs, nothing
    reaches a cluster, and the run is cached for the whole module.
    """
    from pytest import MonkeyPatch
    from cli import kmt as kmt_module

    with MonkeyPatch.context() as patch:
        attached = []
        original = kmt_module.cli.add_command

        def record(command, name=None):
            attached.append(command)
            return original(command, name)

        patch.setattr(kmt_module.cli, "add_command", record)
        patch.setattr("logging.basicConfig", lambda **kwargs: None)
        patch.setattr(sys, "argv", ["kmt", "--help"])
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                kmt_module.main()
            except SystemExit as exit_request:
                assert exit_request.code == 0, "registering the surface must not fail"
    return {command.name: command for command in attached}


def resolved_command(surface, argv):
    """The command an argv drives: argv[1] on the root, plus argv[2] when argv[1] is a group.

    Returns (command, the command spelled out) or (None, what it spelled) when the surface
    does not answer -- which is the whole defect this guard exists to catch.
    """
    command = surface.get(argv[1])
    if command is None:
        return None, "kmt %s" % argv[1]
    if isinstance(command, click.Group):
        sub = command.get_command(None, argv[2])
        if sub is None:
            return None, "kmt %s %s" % (argv[1], argv[2])
        return sub, "kmt %s %s" % (argv[1], argv[2])
    return command, "kmt %s" % argv[1]


def option_tokens(argv):
    """The flags an argv carries: record `--flag`, then step over its value if the next
    token is not itself a flag. A bare flag followed by another bare flag (`--yes --update`)
    therefore contributes both, which is what the mismatch guard below reads correctly.
    """
    tokens, index = [], 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("-"):
            tokens.append(token.split("=")[0])
            index += 1
            if index < len(argv) and not argv[index].startswith("-"):
                index += 1  # the value of `--flag value`
            continue
        index += 1
    return tokens


def lane_ctx(harness):
    """The table's own placeholders, per-call ones filled with values no row asserts on.

    Command names and options are the only tokens this guard reads, and a placeholder that
    survives into argv[1] or argv[2] would simply resolve to nothing there.
    """
    ctx = dict(harness.LANE_PLACEHOLDERS)
    ctx.update(group="standard", port="30001", ns="kubementat", name="probe", pod="probe")
    return ctx


def test_every_capability_argv_names_a_command_the_cli_still_has(harness):
    """Nothing in the table may name a command the product removed.

    Parametrised across the whole table rather than across the 11 renamed rows, because the
    next rename is not the row this test is for -- it is any row, and a guard with an
    exemption list is a guard for last rename's table.
    """
    surface = build_kmt_surface()
    unresolved = {}
    for name, cap in sorted(harness.CAPABILITIES.items()):
        argv = harness.argv_for(name, **lane_ctx(harness))
        command, spelled = resolved_command(surface, argv)
        if command is None:
            unresolved[name] = (spelled, argv)
    assert not unresolved, "capability rows name commands nobody registered: %s" % unresolved


def test_no_capability_argv_carries_an_option_its_command_does_not_define(harness):
    """A flag is part of the contract, so a move must carry it -- and a flag that is not
    defined is evidence the row and the command disagree about the interface.

    The 11 renames are exactly where a quietly dropped option hides: an argv can resolve
    and still run a different command if an option it needs is gone.
    """
    surface = build_kmt_surface()
    mismatched = {}
    for name, cap in sorted(harness.CAPABILITIES.items()):
        argv = harness.argv_for(name, **lane_ctx(harness))
        command, spelled = resolved_command(surface, argv)
        if command is None:
            continue  # the row above is the one that names the dead command
        accepted = {opt for param in command.params for opt in getattr(param, "opts", [])}
        carried = option_tokens(argv)
        missing = sorted(set(carried) - accepted)
        if missing:
            mismatched[name] = (spelled, missing)
    assert not mismatched, ("options the resolved command does not accept, i.e. an argv that "
                            "resolves but does not run what the row means: %s" % mismatched)


def test_every_capability_argv_parses_for_the_command_it_names(harness):
    """The row's argv has to be the command's argv: right flags, and the right number of arguments.

    The two guards above read command names and option names out of argv, and both of them can say
    "fine" to an argv Click would refuse. `kmt config set` stopped taking the directory as a
    positional and started taking it as `--config-dir`; a row that moved the option and left the
    directory in front of it would resolve, would carry only options it defines, and would exit 2 on
    the guest, which is a lane failing rather than a lane reporting. So this hands every row's argv
    to Click's own parser and reports what the parser says.

    Parsing is what runs, never the command body: Click decides arity while it resolves parameters,
    so an extra positional is caught here and not on the first cluster write.
    """
    surface = build_kmt_surface()
    refused = {}
    for name, cap in sorted(harness.CAPABILITIES.items()):
        argv = harness.argv_for(name, **lane_ctx(harness))
        command, spelled = resolved_command(surface, argv)
        if command is None:
            continue  # the guard above already names the row that drives a dead command
        context = click.Context(command, info_name=spelled)
        try:
            command.parse_args(context, argv[len(spelled.split()):])
        except click.UsageError as error:
            refused[name] = (spelled, str(error), argv)
    assert not refused, ("capability rows whose argv the command would refuse -- right command, "
                         "wrong shape: %s" % refused)


def test_the_configuration_rows_hand_the_root_to_the_flag_and_not_to_the_positional(harness):
    """Every configuration row names the root it writes, because the lane's tree is not the guest's.

    This is the row that keeps the lane honest about which tree its assertions are about. A
    configuration command that is told no root has two ways to learn one, and the second is
    `$KUBEMENTAT_PLATFORM_CONFIG_DIR` -- a value the guest could set to something else, which would
    leave a stage answering from a tree this lane never prepared and still reporting green. So every
    `kmt config` row spells `--config-dir`, spells it as the lane's own placeholder, and spells it
    after the arguments rather than before them, the way the rest of the surface spells it.
    """
    rows = sorted(name for name, cap in harness.CAPABILITIES.items()
                  if cap.argv[:2] == ["kmt", "config"])
    assert rows, "the lane configures through something other than kmt config, so this guard is vacuous"

    for name in rows:
        argv = harness.argv_for(name, **lane_ctx(harness))
        assert "--config-dir" in argv, \
            f"{name}: the row reads a configuration root without naming it, so the guest's own " \
            "environment decides which tree the stage's assertions are about"
        assert argv[argv.index("--config-dir") + 1] == harness.LANE_PLACEHOLDERS["config_dir"], \
            f"{name}: --config-dir is not the lane's root, so the stage is writing somewhere else"
        first_positional = next((i for i, token in enumerate(argv) if not token.startswith("-")
                                 and i > 2), None)
        assert first_positional is not None, f"{name}: the row passes nothing to the verb"


# --------------------------------------------------------------------- the configuration rows
# The lane configures through `kmt config` and never through jq, which makes each of these rows part
# of the product surface rather than part of the test scaffolding: a row that spells the verb wrong
# fails a stage, and a row that spells it right but points somewhere else makes a stage green about
# the wrong tree. Both halves are guarded here -- the parse, and the root.


def test_the_lane_configures_only_through_the_verbs_that_write_configuration(harness):
    """No stage writes configuration by editing a file, so every edit is one of these rows.

    The rule is stated in `docs/plans/config-code-separation-remaining-work.md` and enforced by the
    shape of this table: `init` lays a tree down, the two layer verbs grow it, `set` and `unset`
    answer a key and take one away, and `show` and `validate` read the verdict back. A capability
    that is missing here but is being driven by a stage is a second writer of one tree with no
    owner, which is the defect the rule exists to prevent.
    """
    verbs = sorted(harness.CAPABILITIES[name].argv[2]
                   for name, cap in sorted(harness.CAPABILITIES.items())
                   if cap.argv[:2] == ["kmt", "config"])

    assert set(verbs) <= {"init", "add-environment", "add-team", "set", "unset", "show", "validate"}, \
        f"the lane drives a configuration verb that is not on the surface: {verbs}"
    assert set(harness.CAPABILITIES[n].argv[2] for n in
               harness.CAPABILITIES if harness.CAPABILITIES[n].argv[:2] == ["kmt", "config"]) == \
        {"init", "add-environment", "add-team", "set", "unset", "show", "validate"}, \
        "the lane no longer exercises every verb that writes configuration, so a regression in the " \
        "missing verb's argv would reach a release unmeasured"


def test_every_capability_argv_parses_for_the_command_it_names(harness):
    """The row's argv has to be the command's argv: right flags, and the right number of arguments.

    The guards above read command names and option names out of argv, and both can say "fine" to an
    argv Click would refuse. `kmt config` stopped taking the directory as a positional and started
    taking it as `--config-dir`; a row that moved the option and left the old directory in front of
    it would still resolve, and would still carry only options the command defines, but it would
    exit 2 on the guest. A lane that fails is louder than a lane that reports, so the parse is
    checked here rather than on the first stage that runs the row.

    What runs is Click's parser and never the command body: arity is decided while parameters are
    resolved, so an extra positional is caught here and not on a live cluster.
    """
    surface = build_kmt_surface()
    refused = {}
    for name, cap in sorted(harness.CAPABILITIES.items()):
        argv = harness.argv_for(name, **lane_ctx(harness))
        command, spelled = resolved_command(surface, argv)
        if command is None:
            continue  # the guard above names the row that drives a dead command
        context = click.Context(command, info_name=spelled)
        try:
            command.parse_args(context, argv[len(spelled.split()):])
        except click.UsageError as error:
            refused[name] = (spelled, str(error))
    assert not refused, ("rows whose argv the command they name would refuse -- right command, wrong "
                         "shape: %s" % refused)


def test_the_configuration_rows_hand_the_root_to_the_flag_and_not_to_a_positional(harness):
    """Every `kmt config` row names the root, because the lane's tree is not the guest's tree.

    This is the row that keeps the lane honest about which tree its assertions are about. A
    configuration command given no root has two ways to learn one and the second is
    `$KUBEMENTAT_PLATFORM_CONFIG_DIR`, a value the guest could set to something else, which would
    leave a stage answering from a tree this lane never prepared and still reporting green. So every
    row spells `--config-dir`, spells it with the lane's own placeholder, and spells it after the
    arguments the way the rest of the surface spells it.
    """
    rows = sorted(name for name, cap in sorted(harness.CAPABILITIES.items())
                  if cap.argv[:2] == ["kmt", "config"])
    assert rows, "the lane configures through something other than kmt config, so this guard is vacuous"

    for name in rows:
        argv = harness.argv_for(name, **lane_ctx(harness))
        assert "--config-dir" in argv, \
            f"{name}: reads a configuration root without naming it, so the guest's own environment " \
            "decides which tree this stage's assertions are about"
        assert argv[argv.index("--config-dir") + 1] == harness.LANE_PLACEHOLDERS["config_dir"], \
            f"{name}: --config-dir is not the lane's root, so the stage writes somewhere else"
        assert argv.index("--config-dir") > 2, \
            f"{name}: the flag precedes the arguments, which is the spelling no other verb uses"


def test_the_lane_configures_through_every_verb_that_writes_a_tree_and_no_other(harness):
    """The lane's coverage of the configuration surface, counted rather than remembered.

    Every stage writes configuration through `kmt config` rather than editing a file, which is what
    makes the surface observable from a lane. A verb that is on the product and not in this table is
    a verb whose argv regression reaches a release unmeasured; a verb in this table that is not on
    the product is a row pointing at something nobody registered.
    """
    driven = {cap.argv[2] for cap in harness.CAPABILITIES.values() if cap.argv[:2] == ["kmt", "config"]}
    writers = {"init", "add-environment", "add-team", "set", "unset", "show", "validate"}

    assert driven == writers, ("the lane drives " + " / ".join(sorted(driven)) +
                               " and the surface is " + " / ".join(sorted(writers)))


def test_the_lane_reports_no_needs_grouped_cli_form(harness):
    """The report the next lane prints for `needs_grouped_cli_form` is empty, and this says why.

    A `fin62=` marker only ever remembered argv that a rename had not moved yet; a row that
    carries one now carries a dead command, which the guard above also catches. The reporting
    machinery (`NEEDS_FIN62`, suite.py's `needs_grouped_cli_form`) is untouched, so if the
    surface ever goes flat again this row fails on purpose instead of the lane failing slowly.
    """
    assert harness.NEEDS_FIN62 == []
