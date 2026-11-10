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
from pathlib import Path

import click
import pytest
import yaml
from click.core import Argument, Option

from cli import kmt as kmt_module
from cli.kmt import cli
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
    # The tunnel surface is the `tunnel` group and nothing else: the four
    # measured verbs on the group, and the two flat names that were never on it -- `tunnel-polaris`
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


EXPECTED_USERS_COMMANDS = {
    "create-team-account",
    "create-team-account-read-only",
    "delete-team-account",
    # The grafana accounts, migrated off three scripts in the same commit: the bulk row of
    # `GRAFANA_USERS`, and the single account the lane calls read-only. Both live on this group,
    # because they are the same act as the team accounts -- handing a person a credential.
    "create-grafana-accounts",
    "create-grafana-read-only-account",
}

# What is on `kmt routing`: both halves of the decision. `status` asks the cluster what it is
# running, `install` changes it -- the command landed and this row went in with it, exactly as
# the comment that used to sit here promised. What kept that comment from being a placeholder was
# never the absence of the name; it was the rule below that a flag the help offers and the command
# ignores is the lie this file exists to catch. `install` is now checked carrying both flags its own
# help promises, so a surface that offers `--update` and hands the decider something else fails here
# rather than in a cluster.
EXPECTED_ROUTING_COMMANDS = {
    "install",
    "status",
}

# ------------------------------------------------------------------------------------------
# The three flags every command that addresses a cluster carries, and the reason they are not
# spelled out five times below: `@kmt_context` adds them (the `--context` and `--kubeconfig` pair,
# which is the first thing asked when a cluster is resolved, and the root
# that reaches `Config` beside them), so they arrive on a command together with the `Context` it
# builds. What is asserted per command stays exact -- this set plus the flags the command itself
# takes -- because the interesting half of the check is an option that drifted on or off *this*
# command, and that shows up here as surely as it would if each line were written by hand.
# ------------------------------------------------------------------------------------------

RESOLUTION_FLAGS = {"context", "kubeconfig", "config_dir"}


EXPECTED_KUBERNETES_COMMANDS = {
    "analyze-deployment",
    "cluster-capacity",
    "create-ingress",
    "create-secret-from-json",
    "deployment-logs",
    "deployment-ssh",
    "pod-images",
    "pvc-usage",
    "service-account-token",
}

# The exact parameters of each verb, by set equality. Grouping a command may not change what it
# takes, and that is the sentence this table exists to check: an option that drifted off
# `create-ingress` during the move is an option the operator now has to pass some other way, and a
# `--team` that drifted onto any of them is a flag that resolves nothing.
#
# None of the nine takes `--team`. Each decides what it edits out of the *environment's* own files
# plus the positionals the operator typed: the namespace is a positional, the cluster is what the
# environment's `static.json` names, and the one team-scoped key anywhere on this group's path
# (`DOCKER_REGISTRY_CREDENTIALS`) belongs to the flat `setup-docker-registry-access`, which did not
# move.
EXPECTED_KUBERNETES_COMMAND_PARAMS = {
    # The reads that were scripts under `scripts/utilities/kubernetes/`.
    "analyze-deployment": {"environment", "namespace", "deployment"} | RESOLUTION_FLAGS,
    "cluster-capacity": {"environment"} | RESOLUTION_FLAGS,
    "pod-images": {"environment", "namespace"} | RESOLUTION_FLAGS,
    # `--human` is the script's `-h`, which meant human-readable and not help.
    "pvc-usage": {"environment", "human"} | RESOLUTION_FLAGS,
    # The two sessions: a shell in a pod, and the log stream. Both take the container as an optional
    # positional, and `--tail` is the one option the stream needs.
    "deployment-ssh": {"environment", "namespace", "deployment",
                       "container"} | RESOLUTION_FLAGS,
    "deployment-logs": {"environment", "namespace", "deployment", "container",
                        "tail_lines"} | RESOLUTION_FLAGS,
    # A credential printed on stdout and nothing else, so no flag redirects it into a file.
    "service-account-token": {"environment", "namespace",
                              "service_account_name"} | RESOLUTION_FLAGS,
    # Four positionals and no options: the shape the Tekton Task that runs this command passes.
    "create-secret-from-json": {"environment", "namespace", "secret_name",
                                "json_file_path"} | RESOLUTION_FLAGS,
    # `--output` is the only option, and it is the one the flat command carried before the move.
    "create-ingress": {"environment", "domain", "service_name", "service_port",
                       "output_path"} | RESOLUTION_FLAGS,
}



# The flags each command on the group carries, and by exact set equality, the ones it must not.
# The rule is about what the command does, not about the group it sits under, which is why it is
# spelled per command: a group name is the one thing someone new to this file cannot trust.
#
#   * `install` writes, and one of its branches writes beside a controller it did not install, so it
#     takes the confirmation escape hatch every writing command established (`kmt uninstall`,
#     `kmt users create-team-account`) and the flag that changes what the cluster read decided.
#   * `status` runs three reads and has nothing to confirm, so `--yes` on it would be a
#     mutating-command flag on a command that cannot mutate -- and `--update` on it would be a
#     second way to change Traefik, reached by someone looking for the write in the read-only half.
#
# Neither half takes `--team` any more, and they are not two answers. `install`'s namespace comes
# from `Config.component_namespace("traefik")`, which is the environment's `static.json`, as does its
# ingress class; `status` reads the same accessor plus three reads of the cluster. Nothing reachable
# from either resolves a team's directory, and a command that cannot act on a team does not accept a
# `--team`, so both of them get the same surface -- ticket 11b
# made that finding for `status`, and ticket 12b for `install`, whose `require_team` was refusing a
# write that had no team read behind it.
EXPECTED_ROUTING_COMMAND_PARAMS = {
    "install": {"environment", "yes", "update"} | RESOLUTION_FLAGS,
    "status": {"environment"} | RESOLUTION_FLAGS,
}

# What is on `kmt component`: the two verbs for one component. `uninstall` is the command that was
# already there, put
# on the group rather than kept flat alongside its new counterpart; `install` is the half the surface
# never had, and it is what makes the group worth having instead of two flat names.
EXPECTED_COMPONENT_COMMANDS = {
    "install",
    "uninstall",
}

# The flags, per command, by exact set equality. Each takes the environment and the component name
# as arguments and nothing else -- the argv the flat uninstall command took once `--team` is off it,
# and the shape the install half keeps so the pair reads the same way. `install` takes neither
# `--yes` nor a `--helmfile-*` flag: it applies through helmfile the way `kmt install` applies a
# group, without asking, so a confirmation flag would be help text for a question nobody asks, and a
# filter flag would turn "this component" back into "whichever releases a string selects".
#
# No `--team` on either verb, because both are one `HelmUtils` deep, and the namespace a
# release is addressed at, the manifest the releases come from and the grafana credentials helmfile
# is handed are all read out of the environment's files. `uninstall` used to have one, and used to
# refuse a run that did not type it.
EXPECTED_COMPONENT_COMMAND_PARAMS = {
    "install": {"environment", "component"} | RESOLUTION_FLAGS,
    "uninstall": {"environment", "component"} | RESOLUTION_FLAGS,
}

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

EXPECTED_TUNNEL_COMMANDS = {"pod", "grafana", "tekton", "openbao-ui"}

EXPECTED_TUNNEL_COMMAND_PARAMS = {
    # `pod` takes the environment first, like every command that opens a socket to a cluster.
    # Which cluster the forward is opened against used to be the one thing this verb could not say.
    "pod": {"environment", "namespace", "pod_name", "local_port", "remote_port",
            "host"} | RESOLUTION_FLAGS,
    # The three component tunnels reach their target by namespace and selector, both read from the
    # *environment's* `static.json`, so a command that cannot act on a team leaves them without a
    # `--team`. Their absence from
    # these exact sets is the assertion: a flag that resolves to nothing an operator can decline is
    # the same shape that let the old tunnels read `dev1`, and the check is exact equality in both
    # directions, so one drifting back on fails as surely as one dropping off `pod` would.
    "grafana": {"environment", "local_port", "remote_port", "host"} | RESOLUTION_FLAGS,
    "tekton": {"environment", "local_port", "remote_port", "host"} | RESOLUTION_FLAGS,
    "openbao-ui": {"environment", "local_port", "remote_port", "host"} | RESOLUTION_FLAGS,
}

# The whole tekton surface, which is the assertion now: the four verbs that joined late
# (`setup-cleanup-job`, the three `generate-*`) replace flat names of exactly the form `_flat_forms`
# builds, so the check below asks whether the old names still answer, and they must not.
EXPECTED_TEKTON_COMMANDS = {"run-pipeline", "list", "cleanup-pipeline-runs", "setup-pipelines",
                            "setup-triggers", "uninstall-pipelines", "setup-cleanup-job",
                            "generate-deploy-pipeline", "generate-triggers",
                            "generate-pipeline-run"}

EXPECTED_TEKTON_COMMAND_PARAMS = {
    "run-pipeline": {"environment", "team", "pipeline_run_identifier", "parallel", "wait",
                     "poll_interval_seconds", "timeout_seconds"} | RESOLUTION_FLAGS,
    "list": {"environment", "team"} | RESOLUTION_FLAGS,
    "cleanup-pipeline-runs": {"environment", "team", "filter"} | RESOLUTION_FLAGS,
    "setup-pipelines": {"environment", "team"} | RESOLUTION_FLAGS,
    "setup-triggers": {"environment", "team"} | RESOLUTION_FLAGS,
    "uninstall-pipelines": {"environment", "team", "yes"} | RESOLUTION_FLAGS,
    # The team is an option here and a positional on every other verb of this group, and that is a
    # decision and not an accident: the cleanup job writes one CronJob into the cluster-scoped
    # namespace the environment config names, so the team it wants is the credential its pod pulls
    # with rather than a directory it selects. Grouping a command may not change what it takes, so
    # the move left this shape exactly where it was.
    "setup-cleanup-job": {"environment", "team"} | RESOLUTION_FLAGS,
    # The generators write into the team's own `platform_config` tree and open no socket of their
    # own, which is why they take the pair as positionals like their neighbours do.
    "generate-deploy-pipeline": {"environment", "team", "app_name", "git_url", "git_project_name",
                                 "git_server_host", "git_server_port",
                                 "git_server_ssh_user"} | RESOLUTION_FLAGS,
    "generate-triggers": {"environment", "team", "app_name", "pipeline_name",
                          "trigger_type"} | RESOLUTION_FLAGS,
    "generate-pipeline-run": {"environment", "team", "pipeline_file"} | RESOLUTION_FLAGS,
}

# What is on `kmt helm`: the apply, and the one read that had a flat name of its own until the group
# took more than one verb.
EXPECTED_HELM_COMMANDS = {"apply", "latest-version"}

# The exact set each verb must still carry. `helmfile-apply` took environment, the label
# filter, the confirmation escape hatch and the two paths that decide where helmfile runs, and
# grouping it changed none of that: an option that drifted off the command during the move is an
# option the operator has to pass in a different way, which is the whole thing a rename must not do.
#
# `--team` is the exception, and it drifted off on purpose: everything helmfile is handed here comes
# from the environment's `static.json` and `static.encrypted.json`, so the option resolved nothing
# and the `require_team` behind it refused an apply that was about to read one file, so it went off on
# purpose. The version read never had one: `get_latest_chart_version` runs `helm repo list` and `helm
# search` and reads no configuration category at all.
EXPECTED_HELM_COMMAND_PARAMS = {
    "apply": {"environment", "helmfile_label_filter", "yes",
              "helmfile_working_directory", "helmfile_path"} | RESOLUTION_FLAGS,
    "latest-version": {"environment", "chart_path"} | RESOLUTION_FLAGS,
}

# What is on `kmt config`: the verb that lays a tree down, the two verbs that add one layer to a tree,
# the two that put one answer on one layer of it, and the two that read the result back. `config unlock`
# kubementat never unlocks a configuration), `config secrets` (`validate` answers that) and `config
# generate` (`kmt tekton generate-*` is that verb) are refused by the surface itself -- the exact set
# below is what keeps a well-intentioned fifth verb from arriving without anyone deciding it should.
# `config diff` and `config migrate` were on the group and are deleted, on owner instruction of
# 2026-11-05: kubementat rewrites no operator tree, so there is no rename table to apply and nothing
# that reports what a version bump would cost. Every verb on it also takes the root it works on, which
# is the one argument the group shares with the rest of kmt and no positional carries.
EXPECTED_CONFIG_COMMANDS = {"init", "add-environment", "add-team", "set", "unset", "show",
                            "validate"}

# The exact parameter set of each verb on the group, and that is the assertion rather than an opinion
# about flags. Every verb here carries `config_dir`, because a configuration root is named and not
# located and this group has no positional left to carry it: the flag and
# `$KUBEMENTAT_PLATFORM_CONFIG_DIR` are the two spellings, and a verb that took its root as a
# positional would answer to only one of them while the rest of kmt answered to both. What none of
# them carries is `--context` or `--kubeconfig`, and that is the claim rather than a style preference:
# the verbs on this list address no cluster, so a resolution flag on them would be a dial that changes
# nothing and promises something. A verb that does resolve a cluster belongs on this table with its own
# exact set when it arrives, and `test_every_configuration_verb_names_its_root_the_same_way.py` is what
# checks that all seven mean the flag.
EXPECTED_CONFIG_COMMAND_PARAMS = {
    # The verb that lays a tree down: the layer names, the answers, the five flags the preparation
    # needs, and the root it is laying the tree down under. `--gpg-key` and `--gpg-email` are the two
    # spellings of a deployer identity, and they are the names `config_keys` refuses with; `--git-url`
    # names a remote without adding one; `--no-commit` is the operator's half of the first commit.
    # `--force` means material kubementat did not write and nothing else, which is why it is still here
    # and still one flag.
    "init": {"environment", "team", "set_answers", "encrypted_answers", "gpg_key", "gpg_email",
             "git_url", "no_commit", "force", "config_dir"},
    # One positional: the layer's own name, the one thing this verb cannot be told. The root it grows
    # the layer in is named by the flag, the same way every other verb on the group names one.
    "add-environment": {"environment", "config_dir"},
    # Two, in the order the tree holds them below its root: the environment the team sits under, then
    # the team. Nothing to answer, so nothing to answer with.
    "add-team": {"environment", "team", "config_dir"},
    # The environment, the answer, and the flags that say where the answer lands. `--team` is a flag
    # and not a positional so a team answer cannot arrive without the layer it sits on being spelled,
    # and `--encrypted` picks which half of the pair takes the answer. `set_answers` is the parameter
    # name `init` already uses for the same `KEY=VALUE` shape, so one flag and one positional that
    # accept the same thing do not become two names for one idea. `--generate` is that second spelling
    # and not a second verb: generation is an option on one answer rather than a class of key, so the
    # verb that writes one answer to one key is the only place a value may be invented, and it takes a
    # key name rather than a value. There is no `--yes`, no prompt, and no key-class flag — nothing
    # that could send some names to the encrypted file and not others. `--from-file` is the document
    # spelling of the same answer, which is why it belongs to this verb: it answers every key of a JSON
    # object in one run, and it is how a value that must not sit in a process table gets answered,
    # because only the path travels. It is not a second verb either, and it cannot choose a layer:
    # `--team` and `--encrypted` still decide where the keys land.
    "set": {"environment", "set_answers", "team", "encrypted", "generate", "from_file", "config_dir"},
    # `key` rather than `KEY=VALUE` because this verb removes an answer and has no opinion about what
    # it was. `--encrypted` is deliberately absent: an unset clears both files of the layer, so a flag
    # that chose one of them would undo the pair rule.
    "unset": {"environment", "key", "team", "config_dir"},
    # The read-only verbs. The environment, the layer to ask, and the root. `team` here is a positional
    # and not a `--team` flag like `set` and `unset` carry, which is what the surface's own spelling of
    # these rows gives them -- a write verb needs the flag to say *where* an answer lands, and a read
    # verb only needs to know which layer to ask. Neither carries `--context` or `--kubeconfig`: both
    # resolve no cluster, so a resolution flag on them would be a dial that changes nothing and promises
    # something. The exact sets below are what keep a later `--generate`-shaped addition from arriving
    # as a harmless convenience.
    "show": {"environment", "team", "config_dir"},
    # The verdict verb, in the same shape `show` has: environment, team, root. Like `show` it is a
    # read, so the team is a positional and not a flag, and like every verb here it carries no
    # `--context` or `--kubeconfig`: validate resolves no cluster, reads no kubeconfig, and execs
    # nothing — its collision half comes from the registry's own file reads, so a resolution flag on it
    # would promise an answer this verb never asks for.
    "validate": {"environment", "team", "config_dir"},
}


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


def test_the_users_group_holds_the_team_account_commands():
    """The team-account commands exist, and only the ones on record do.

    The group is how a team account is reached now that `create_team_account.sh` is gone, so the
    surface under it is a surface the lane runs. A command there that is not on this list would
    be a second way to do the same thing - the exact shape the duplicated bash script was.
    """
    from cli.commands import users

    names = set(users.users.commands)
    assert names == EXPECTED_USERS_COMMANDS, (
        f"kmt users does not match the surface on record; "
        f"unexpected: {sorted(names - EXPECTED_USERS_COMMANDS)}, "
        f"missing: {sorted(EXPECTED_USERS_COMMANDS - names)}")
    for name, command in users.users.commands.items():
        assert (command.help or "").strip(), f"kmt users {name} has no help text"
    # Every one of them takes --yes: a lane run has no terminal, and a command without the
    # escape hatch is a command the lane cannot run.
    for name, command in users.users.commands.items():
        params = {param.name for param in command.params}
        assert "yes" in params, f"kmt users {name} cannot run non-interactively"
        assert "environment" in params, (
            f"kmt users {name} does not say which environment it edits")
        # The two halves of this group take different halves of the pair, and the difference is
        # which file the decision lives in. A team account is created in a namespace a team's
        # `static.json` names, so `--team` is one of its arguments; a grafana account is declared
        # and authenticated out of the environment's `static.encrypted.json`, so a `--team` on it
        # resolved nothing and the guard behind it refused a run with no team read in front of it.
        if name in {"create-team-account", "create-team-account-read-only",
                    "delete-team-account"}:
            assert "team" in params, (
                f"kmt users {name} does not take the team whose namespace it edits")
        else:
            assert "team" not in params, (
                f"kmt users {name} offers a team its one client never reads")


def test_the_routing_group_holds_both_halves_of_the_routing_decision():
    """The routing surface is the whole decision: read the cluster, and change it. Nothing else.

    `kmt routing` is a group rather than a flat command because routing is a decision with two
    questions in it: what is on the cluster, and whether to change it. Both commands are
    here now, so the surface is checked for both: a command cannot arrive on the group without being
    on record, and no command can carry a flag that belongs to the other half of the decision.

    Which flags belong to which command is the load-bearing part of this check, and it is checked per
    command rather than across the group (`EXPECTED_ROUTING_COMMAND_PARAMS`). It used to be one rule
    for one command -- "no `--yes` anywhere on this group" -- and that rule was true of read-only
    commands, not of groups: `install` writes, so it owes the confirmation flag the writing commands
    established, and `status` does not, so it owes neither. Making the rule per command keeps both
    directions honest: a `--yes` that drifted onto `status` fails, and an `--update` that was dropped
    from `install`'s signature fails, and the second one is what would otherwise be a command line
    that offers a flag it does not have.

    The absence checks are the point as much as the presence ones are. Plan decision 1 says routing
    is not part of `kmt install`, with no pass-through and no `--update` there; a `--update` that
    drifted onto `install` would be a second command that changes Traefik, reached by someone
    reading `kmt install --help` and looking for the flag the routing docs named.
    """
    from cli.commands import routing

    names = set(routing.routing.commands)
    assert names == EXPECTED_ROUTING_COMMANDS, (
        f"kmt routing does not match the surface on record; "
        f"unexpected: {sorted(names - EXPECTED_ROUTING_COMMANDS)}, "
        f"missing: {sorted(EXPECTED_ROUTING_COMMANDS - names)}")

    # Self-documenting, like every other command: this is a surface the operator reads.
    for name, command in routing.routing.commands.items():
        expected = EXPECTED_ROUTING_COMMAND_PARAMS[name]
        assert (command.help or "").strip(), f"kmt routing {name} has no help text"
        params = {param.name for param in command.params}
        assert "environment" in params, (
            f"kmt routing {name} does not take the environment it reads as an argument")
        # Neither half takes `--team`. `install` writes, but what it writes into is the namespace
        # the *environment* names (`Config.component_namespace`), not one a team's files name, so
        # the flag it used to carry resolved nothing and the guard behind it refused a write that
        # had no team read anywhere in front of it. `status` never had one. Both halves now agree,
        # and `EXPECTED_ROUTING_COMMAND_PARAMS` is exact for both, so a `--team` drifting back onto
        # either fails here rather than in the lane.
        assert "team" not in params, f"kmt routing {name} offers a team it cannot act on"
        # Exact equality, both directions: the extra flag is the mutating-command signal sitting on a
        # command that cannot mutate, and the missing one is help text describing a flag the command
        # does not accept. Which of the two it is has to be readable from the failure.
        assert params == expected, (
            f"kmt routing {name} carries {sorted(params - expected)} "
            f"and is missing {sorted(expected - params)}; the surface on record is "
            f"{sorted(expected)}, because {'it writes' if name == 'install' else 'it only reads'}")

    # The group is reachable from the top, and its commands are not reachable from `install`.
    assert "routing" in commands_from_help(help_output()), \
        "kmt routing is not on kmt --help"
    install_help = run_help("kmt", "install", "--help").lower()
    assert "routing" not in install_help, (
        "kmt install advertises routing; plan decision 1 keeps routing out of install")
    assert "--update" not in install_help, (
        "kmt install advertises --update, which belongs to kmt routing install alone")


def test_the_component_group_holds_both_halves_of_one_component():
    """One component is one question, asked two ways: put it on, take it off. Both are on the group.

    The shape this file has to keep honest is the one the ticket chose, not the one that was easiest
    to type. `kmt component` is a group for the same reason `kmt users` and `kmt routing` are: the
    two commands are the two answers to one question about one component, and a flat pair
    (`component-install`, `component-uninstall`) would have given the surface a third spelling of an
    existing pair for a later grouping to redo by guesswork.

    The rename is checked here rather than in the ticket text. The flat uninstall command is renamed
    onto this group outright, so the failure this guard catches is the one this repo keeps making: a
    command that grows a second name and answers to both. Hence both directions -- the old name is off
    `--help`, and the function that implemented it is no longer defined at all. A name that is only
    shadowed by a deprecation branch is still two routes to one deletion, and this project accepts
    no deprecation branch.
    """
    from cli.commands import component as component_cli
    from cli.commands import helm as helm_cli

    names = set(component_cli.component.commands)
    assert names == EXPECTED_COMPONENT_COMMANDS, (
        f"kmt component does not match the surface on record; "
        f"unexpected: {sorted(names - EXPECTED_COMPONENT_COMMANDS)}, "
        f"missing: {sorted(EXPECTED_COMPONENT_COMMANDS - names)}")

    for name, command in component_cli.component.commands.items():
        expected = EXPECTED_COMPONENT_COMMAND_PARAMS[name]
        assert (command.help or "").strip(), f"kmt component {name} has no help text"
        params = {param.name for param in command.params}
        assert "environment" in params and "component" in params, (
            f"kmt component {name} does not take the environment and component it addresses"
            f" as arguments")
        assert "team" not in params, (
            f"kmt component {name} offers a team its one `HelmUtils` never reads")
        assert params == expected, (
            f"kmt component {name} carries {sorted(params - expected)} "
            f"and is missing {sorted(expected - params)}; the surface on record is "
            f"{sorted(expected)}, because applying and deleting one release asks for no flag at all")

    # The old flat name is gone from the printed surface, and from the module that had it. The check
    # is written over the `-uninstall` half of that name rather than the whole string, because the
    # rename's green light is a grep for the old name being empty -- and this file has to stay that
    # way while still failing if the name comes back.
    listed = commands_from_help(help_output())
    assert [name for name in sorted(listed) if "component-uninstall" in name] == [], sorted(listed)
    assert not hasattr(helm_cli, "helm_component_uninstall"), (
        "the module that put this command on still defines the old implementation, so something "
        "can still reach it")
    assert "helm" not in _names_belonging_to_a_component(listed), sorted(listed)
    # One route to uninstalling one component: the group, and nothing shaped like it.
    assert [name for name in sorted(listed) if "component" in name] == ["component"], sorted(listed)
    assert "component" in commands_from_help(help_output()), "kmt component is not on kmt --help"
    assert commands_from_help(run_help("kmt", "component", "--help")) == EXPECTED_COMPONENT_COMMANDS, \
        "the group's own listing is not the two verbs it is supposed to be"


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


def _names_belonging_to_a_component(names):
    """The printed names that say `component`, plus `helm` only while `helm` is a component subgroup.

    `helm` was given a group of its own (`kmt helm apply`), so a bare `helm` on the surface is no
    longer evidence of a subgroup nothing else had. The hiding place that claim was guarding -- a
    component verb living inside helm -- is guarded where it would actually be, by
    `test_the_helm_group_holds_the_apply_the_surface_had`.
    """
    return {name for name in names if "component" in name}


def _flat_forms(group_name, expected, mechanism=None):
    """The flat name each grouped verb replaces: `<group>-<verb>`, built and not written out.

    `mechanism` is for the one command whose flat name did not read that way: `helmfile-apply` was
    mechanism-then-verb, and it became the verb alone (`helm apply`, the form the lane's own marker
    pins) rather than `helm-apply`.
    """
    prefix = mechanism if mechanism else group_name
    return {verb: f"{prefix}-{verb}" for verb in expected}


def _command_answers_to(name):
    """Whether the shipped entry point resolves a name to a command, or refuses it.

    Registers the surface through `main()` first, because a command that exists only on a module and
    was never wired in would otherwise answer too. No arguments are given, so nothing beyond command
    resolution happens: a name that resolves is asked for its parameters and a name that does not is
    refused, and either way no command body runs.
    """
    from click.testing import CliRunner

    monkeypatch = pytest.MonkeyPatch()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _run_main(monkeypatch, ["kmt", "--help"])
            result = CliRunner().invoke(cli, [name])
    finally:
        monkeypatch.undo()
    return result.exit_code, result.output


def check_group(group_name, commands, expected_commands, expected_params, mechanism=None,
                flat_names=None):
    """The three checks every group that replaced flat names gets, in the order a reader would want
    them.

    Same shape as `users`, `routing` and `component` above: what is on the group, what each command
    takes, and what the flat names it replaces now do when asked.

    `flat_names` is for a group whose old names did not read `<group>-<verb>`. `kmt kubernetes
    analyze-deployment` replaced a verb that was already called `analyze-deployment`, so building the
    dead name from the group would assert only that `kmt kubernetes-analyze-deployment` is gone --
    true, and not the thing anyone would want to know. A group that passes `flat_names` states the
    whole list of dead names itself, so the built forms are not consulted for it.
    """
    names = set(commands)
    assert names == expected_commands, (
        f"kmt {group_name} does not match the surface on record; "
        f"unexpected: {sorted(names - expected_commands)}, "
        f"missing: {sorted(expected_commands - names)}")

    for name, command in commands.items():
        expected = expected_params[name]
        assert (command.help or "").strip(), f"kmt {group_name} {name} has no help text"
        params = {param.name for param in command.params}
        assert params == expected, (
            f"kmt {group_name} {name} carries {sorted(params - expected)} and is missing "
            f"{sorted(expected - params)}; grouping a command may not change what it takes")

    listed = commands_from_help(help_output())
    assert group_name in listed, f"kmt {group_name} is not on kmt --help"
    assert commands_from_help(run_help("kmt", group_name, "--help")) == expected_commands, (
        f"the group's own listing is not the verbs it is supposed to be")

    forms = flat_names if flat_names is not None else _flat_forms(
        group_name, expected_commands, mechanism)
    for verb, flat_name in sorted(forms.items()):
        assert flat_name not in listed, f"kmt {flat_name} is still printed on kmt --help"
        exit_code, output = _command_answers_to(flat_name)
        assert exit_code == 2 and "No such command" in output, (
            f"kmt {flat_name} is still reachable after the move to kmt {group_name} {verb}: "
            f"exit {exit_code}: {output.strip()}")


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


def test_the_config_group_holds_the_init_that_lays_a_tree_down():
    """The ways to change or judge a configuration directory live on one group.

    `kmt config init --config-dir D --environment E` creates a tree, the two layer verbs grow it by
    one layer, and `set` / `unset` put one answer on one layer of it. This is the check every other
    group gets and not a smaller one: what is on the group, what each verb takes, and that no flat
    `config-init`-shaped name answers beside it. The last of those reads as a formality -- there is no
    flat name to kill -- and it is not: `config-set` or `config-add-environment` appearing later is a
    second route to the same write that nothing on the group's listing reports.

    The flag check asserts two things about the verbs on this list, and neither is about the group
    forever. What they all carry is the root flag, because a root is named and not located and the
    variable a container sets is the second spelling of it -- a verb that took its root as a positional
    would be the one verb in kmt that a scheduled run could not address. What none of them carries is a
    *cluster* resolution flag: `init` addresses nothing, the two layer verbs add state to a directory
    they were named, and the two answer verbs edit files inside one. A verb that does resolve a cluster
    belongs on this table with its own exact set when it arrives, which is why the table and not this
    sentence is what would notice.

    The help text says which verbs are deliberately off the group, so the surface states its own
    absences instead of leaving a reader to notice them.
    """
    from cli.commands import config as config_module

    check_group("config", config_module.config.commands, EXPECTED_CONFIG_COMMANDS,
                EXPECTED_CONFIG_COMMAND_PARAMS)

    group_help = run_help("kmt", "config", "--help")
    for absent in ("unlock", "secrets", "generate"):
        assert absent in group_help, (
            f"kmt config --help does not say that config {absent} is deliberately absent, so the "
            f"refusal is a decision nobody reading the surface can find")


def test_the_tunnel_group_holds_the_four_tunnels_and_their_flat_names_are_dead():
    """`kmt tunnel <target>` replaced four `kmt tunnel-<target>` lines.

    The four are the tunnels the lane measures, and since row 25 retired the two flat ones they are
    the whole tunnel surface, which is why a bare equality here is the interesting assertion: a
    fifth tunnel that drifted onto the group without a marker would be a second way to reach a
    component that no row of the capability table reports.
    """
    from cli.commands import tunnels

    check_group("tunnel", tunnels.tunnel.commands, EXPECTED_TUNNEL_COMMANDS,
                EXPECTED_TUNNEL_COMMAND_PARAMS)


def test_the_tekton_group_holds_the_ten_commands_and_their_flat_names_are_dead():
    """The whole tekton surface lives under `kmt tekton`.

    `run-pipeline` is the command that makes this check matter, because it is the one a Tekton Task
    runs (`tekton_ci/tasks/run-tekton-pipeline.yml`): a flat `tekton-run-pipeline` left reachable
    next to the group would be a Task that keeps running a name no report mentions.

    The four that joined the group late matter for the mirror-image reason, and it is worth writing
    down because their absence from the capability table is exactly what a reader here will ask
    about. Nothing on the lane runs the generators or the cleanup job, so no row would notice a flat
    name still answering beside the group -- and two routes to one generator is how a manifest ends
    up written where a different command already decided it belongs. What makes that rename verifiable
    is this test's third check, not a marker.
    """
    from cli.commands import tekton

    check_group("tekton", tekton.tekton.commands, EXPECTED_TEKTON_COMMANDS,
                EXPECTED_TEKTON_COMMAND_PARAMS)


def test_the_helm_group_holds_the_apply_and_the_version_read_and_their_flat_names_are_dead():
    """The two helm verbs sit on the group, and both flat names they had answer to nothing.

    Why `apply` and not `helmfile-apply`: the lane pins the future argv for this row in its own
    capability table, and it pins the verb. `helmfile` is the mechanism the `--helmfile-*` options
    still name; `apply` is the answer.

    `helm-version` joined for the reason the four late tekton verbs joined: a command whose first word
    is a group's name, answering to a hyphen beside verbs that answer to a space, is a surface
    describing a rule it does not follow. The lane measures only the apply, so nothing pins this
    rename from outside -- which is exactly why the dead name is checked by asking the shipped entry
    point what it answers to, and not by a capability row that was never going to run it.
    """
    from cli.commands import helm

    check_group("helm", helm.helm.commands, EXPECTED_HELM_COMMANDS,
                EXPECTED_HELM_COMMAND_PARAMS,
                flat_names={"apply": "helmfile-apply",
                            "latest-version": "helm-version"})


def test_the_kubernetes_group_holds_the_nine_cluster_verbs_and_their_flat_names_are_dead():
    """Every verb that addresses a cluster that already exists is `kmt kubernetes <verb>`.

    The nine came from three different flat blocks on the surface: four diagnostics, the two pod
    sessions, and three that were scattered as a token read, a manifest generator and a Secret write.
    What they share is a cluster behind the first positional, and one word for that on `kmt --help` is
    what the group buys.

    The dead names are not built from the group, and that is the half of this check a reader will want
    stated. Eight of the nine kept their verb through the move, so the name to prove dead is the verb
    alone; the two sessions are the exception, because `deploy-ssh` and `deploy-logs` said what the new
    verb says less precisely and so were renamed rather than prefixed.

    `setup-secrets` and `setup-docker-registry-access` are deliberately absent, and what is asserted is
    that they are *not* on the group: they write the cluster this group reads, during the install that
    creates it, so a verb that runs before there is anything to read does not belong to a group named
    after the reading.
    """
    from cli.commands import kubernetes

    dead = {verb: verb for verb in EXPECTED_KUBERNETES_COMMANDS}
    dead["deployment-ssh"] = "deploy-ssh"
    dead["deployment-logs"] = "deploy-logs"

    check_group("kubernetes", kubernetes.kubernetes.commands, EXPECTED_KUBERNETES_COMMANDS,
                EXPECTED_KUBERNETES_COMMAND_PARAMS, flat_names=dead)

    # The two flat writes that did not move are still on the surface, so they cannot have drifted in.
    listed = commands_from_help(help_output())
    assert "setup-secrets" in listed and "setup-docker-registry-access" in listed
    assert "setup-secrets" not in kubernetes.kubernetes.commands
    assert "setup-docker-registry-access" not in kubernetes.kubernetes.commands


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
    assert "update" in parameters and "assume_yes" in parameters, (
        "install_routing has no parameter for the flags kmt routing install offers; the CLI is "
        "offering flags with nowhere to go")
    assert "ctx" in parameters, (
        "install_routing no longer takes the one `Context` every class that reaches a cluster is "
        "given; the CLI's `@kmt_context` has nothing to hand over")

    calls = []

    def recording(ctx, update=False, assume_yes=False):
        calls.append({"environment": ctx.environment, "team": ctx.team,
                      "update": update, "assume_yes": assume_yes})
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
    assert (without_flags["update"], without_flags["assume_yes"]) == (False, False), (
        f"the bare command asked for an upgrade anyway: {without_flags}")
    assert (with_flags["update"], with_flags["assume_yes"]) == (True, True), (
        f"--yes/--update did not reach install_routing as they were given: {with_flags}")
    for call in calls:
        assert (call["environment"], call["team"]) == ("dev", None), (
            f"the command changed what it operates on while it was passing the flags through: {call}"
            ". `team` is None because `kmt routing install` no longer offers one; if it came back"
            " this assert is where the change is noticed")


def test_no_command_name_is_registered_twice(registrations):
    """The collision that leaves one command unreachable but still in `--help`.

    `dict` keeps the last write, so a second registration under the same name deletes the
    first one's command — the help output keeps showing the name, which is why nobody had
    noticed.
    """
    names = [name for name, _callback, _command in registrations]

    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], f"registered more than once: {duplicates}"


def test_no_callback_answers_for_two_commands(registrations):
    """A function that ends up under two names is the shape of D3.

    The tunnel module's `tunnel_polaris` was defined twice; the binding that survived pointed
    at the vault tunnel, so `kmt tunnel-polaris` ran vault's code. Reading the name is not
    enough to catch that — the name looked right — but the callback it resolves to is shared
    with the other tunnel, and two names on one function is exactly the bug.
    """
    by_function = {}
    for name, callback, _command in registrations:
        by_function.setdefault(callback, []).append(name)

    shared = {callback: names for callback, names in by_function.items() if len(names) > 1}
    offenders = sorted(f"{name} -> {getattr(callback, '__name__', callback)}"
                       for callback, names in shared.items() for name in names)
    assert offenders == [], f"one function is registered under more than one name: {offenders}"


def test_every_command_documented_itself(registrations):
    """A command nobody can guess the arguments for is a command nobody uses.

    Same rule as `--help`'s content: this is a surface the operator reads.
    """
    for name, _callback, command in registrations:
        assert (command.help or "").strip(), f"{name} has no help text"


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

# What an argument carries that makes "the first positional" mean the same thing everywhere: the
# variable Click reads when the operator did not type one.
ENVIRONMENT_VARIABLE = "ENVIRONMENT"

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


def _environment_names(parameter):
    """The variables behind a parameter -- Click takes one or many, and CLI code uses both."""
    envvar = getattr(parameter, "envvar", None)
    if envvar is None:
        return ()
    return tuple(envvar) if isinstance(envvar, (list, tuple)) else (envvar,)


@pytest.mark.parametrize("name", sorted(CLUSTER_TOUCHING_POSITIONALS))
def test_the_environment_is_the_first_positional_of_every_cluster_touching_command(name,
                                                                                    registrations):
    """The exact positional list, and not merely the first slot of it.

    Checking only "environment is among the arguments" would pass a command that moved the
    environment there and left everything else where it was, and that is the one shape with no
    discoverable later symptom: the old argv still parses, it just parses the wrong things. A
    namespace sitting in the environment's slot resolves a `static.json` that does not exist, which
    arrives as a confusing error instead of a missing key.
    """
    command = _command_by_argv_path(registrations, name.split())
    assert command is not None, f"kmt {name} is not registered, so there is nothing to check"

    assert _positionals(command) == CLUSTER_TOUCHING_POSITIONALS[name]
    first = command.params[0]
    assert isinstance(first, Argument) and first.name == "environment", \
        f"kmt {name} takes {first.name} first"
    assert ENVIRONMENT_VARIABLE in _environment_names(first), \
        f"kmt {name}'s environment is a positional with no variable behind it"


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
