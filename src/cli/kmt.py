#!/usr/bin/env python3

import logging

import click

from cli.commands import tunnels
from cli.commands import tekton
from cli.commands import kubementat
from cli.commands import routing
from cli.commands import component
from cli.commands import utilities
from cli.commands import helm
from cli.commands import environments
from cli.commands import users
from cli.commands import kubernetes
from cli.commands import config
from kubementat.core.version import get_version
from kubementat.errors import (
    DeployerKeyMissingError,
    LockedConfigFileError,
    ObsoleteTriggerDirectoryError,
)


# The library configures nothing when it is imported, so the CLI configures logging for
# itself (plan D10). Doing it in the library meant every consumer of `import kubementat`
# got a root handler and INFO level whether it wanted them or not.
def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )


# The group carries no context object any more. It used to carry a `Config` holding one field,
# `kubementat_main_dir`, and 32 commands accepted it through `@click.pass_obj` without reading it:
# the project root is resolved where a path is actually derived, by
# `kubementat.core.config.paths.default_kubementat_main_dir`, so a `kmt` installed into site-packages still
# drives the checkout `KUBEMENTAT_HOME` names (D11) without a second copy of the rule in the CLI.
# What a command receives now is a `kubementat.core.identity.context.Context`, built per command by
# `@kmt_context` — because "which cluster" is decided by the command's own arguments, and a
# group callback runs before those are parsed.
# `--version` answers from the distribution metadata and not from a string written here, so the
# flag and the wheel cannot drift apart (the reason the stale `VERSION` file could be deleted).
# It is eager, so it is answered before any command is resolved, and it exits rather than
# continuing into a command that would contact a cluster for no reason.
def print_version(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    click.echo(f'kubementat {get_version()}')
    ctx.exit()


class KmtGroup(click.Group):
    """The root group, which renders the refusals no decorated command met on its way.

    `@kmt_context` renders the refusals the decorated commands can raise; the `config` and
    `environments` verbs deliberately build no `Context` (they touch no cluster), so a tree refusal
    raised on their way — a locked configuration file, the obsolete trigger directory, a missing
    deployer keypair — would reach the terminal as a traceback. The CLI still renders them here,
    one `Error:` line and exit 1, because "only the CLI decides how a refusal is printed" has to
    hold for the whole surface and not only for the half of it that opens a socket.
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (DeployerKeyMissingError, LockedConfigFileError,
                ObsoleteTriggerDirectoryError) as error:
            raise click.ClickException(str(error))


@click.group(cls=KmtGroup, help='Kubementat CLI')
@click.option('--version', 'version_requested', is_flag=True, is_eager=True, expose_value=False,
              callback=print_version, help="Print kubementat's version and exit.")
def cli():
    pass


def main():
    configure_logging()
    cli.add_command(kubementat.install)
    cli.add_command(kubementat.uninstall)
    # Two cluster writes that belong to laying a cluster down rather than to reading one that already
    # exists. They stay flat, and the `kubernetes` group comment below says why.
    cli.add_command(kubementat.setup_secrets)
    cli.add_command(kubementat.setup_docker_registry_access)


    # tunnels: the four tunnels the parity record counts are the `tunnel` group (`kmt tunnel pod`), so the
    # group name carries the kind and the verb carries the target. `tunnel-polaris` and
    # `tunnel-mysql` were the two flat names left outside the group; both are retired, and
    # nothing else here moved, so the group is the whole tunnel surface now.
    cli.add_command(tunnels.tunnel)

    # tekton: the whole tekton surface is the `tekton` group. The cleanup job and the three
    # generators kept flat names beside it for as long as the rest of them were flat, but a command
    # whose first word is the group's name and that answers to a hyphen instead of a space is a
    # surface that says one thing and does another, so they moved in and their flat names went.
    cli.add_command(tekton.tekton)

    # helm: the whole helm surface is this group. `helm apply` is the write (the verb the lane's own
    # capability row pins), `helm latest-version` is the read that kept a flat `helm-version` line for
    # as long as the rest of the surface was flat; once the group was going to take more than its one
    # verb, leaving a command whose first word is the group's name to answer to a hyphen was the
    # inconsistency the tekton grouping had already resolved. Both flat names are gone rather than
    # aliased.
    cli.add_command(helm.helm)
    # Nothing here registers the one-component uninstall: that command is `kmt component uninstall`,
    # wired in with its install half below. Renamed outright -- no alias, no second name that answers
    # to the same deletion.

    # utilities
    cli.add_command(utilities.run_ci_container)
    # There is no command here that takes a script path from the operator and execs it.
    # Nothing replaces it: the CLI's own work goes through `kubementat.infrastructure.shell`, and what
    # an operator gets is a command and not a path to run. The guard that keeps it out is
    # `tests/test_no_cli_command_starts_a_script_by_path.py`.

    # The kubernetes group: everything you read or change in a cluster that already exists. The six
    # reads were scripts under scripts/utilities/kubernetes/, scripts/utilities/helm/ and
    # scripts/secret_management/; the Ingress manifest generator and the Secret write joined them, so
    # a reader of `kmt --help` gets one word for "the cluster" instead of nine scattered verbs. The
    # nine flat names they had are off `--help` and answer to nothing: no alias, no shim, and no
    # second spelling of one read.
    #
    # `setup-secrets` and `setup-docker-registry-access` are the two commands that address a cluster
    # and did not move. They arrive during the install that creates what this group reads, and sorting
    # verbs by what they edit rather than by when they run is how a command ends up in a group its own
    # name does not put it in. The line is stated here so the next reader does not have to rediscover
    # it from the help text.
    cli.add_command(kubernetes.kubernetes)

    # team accounts (the bash scripts under scripts/user_management/ are gone,
    # so the account is created by a command and not by running a file out of that directory)
    cli.add_command(users.users)

    # The routing group: what Traefik the cluster is already running (`status`), and the install
    # that only installs when the answer is "none" (`install`). It is a group rather than a flat
    # command because it is a decision with two questions in it, and it is deliberately not part
    # of `kmt install`, which gets no routing and no `--update` -- and whose help test asserts both
    # absences. The script it replaces, `install_routing.sh`, is deleted rather than shimmed: no
    # caller in `src/` or `scripts/` ever reached it, and this repo keeps no aliases for commands
    # that a Python path replaced.
    cli.add_command(routing.routing)

    # The component group: one component on, one component off. The uninstall half arrived first as a
    # flat command; the install half is added here and both sit on one group,
    # because the two verbs are the two answers to one question about one component -- the same shape
    # `kmt routing` and `kmt users` already have. The old flat name is gone rather than aliased: a
    # command with two names is a command with two answers.
    cli.add_command(component.component)

    # Which environments this `platform_config` has, and which cluster each one addresses.
    # It sits directly on the `EnvironmentRegistry` primitive the 1:1 check resolves against, for
    # the reason: a listing that worked out its own answers would be a second opinion
    # about what is configured, and the two would drift. No cluster is contacted to answer it.
    cli.add_command(environments.environments)

    # The configuration group. It is a group rather than a flat `kmt config-init` because this is
    # where `config set` / `unset` / `show` / `validate` land: the four verbs that read or edit a
    # tree that already exists belong together, and this puts the group and its first command on it
    # so the fourth verb does not arrive as a fifth flat name. The absences are stated on the group
    # itself -- no `config unlock`, no `config secrets`, no `config generate` -- because a
    # surface is defined by what it refuses as much as by what it lists.
    cli.add_command(config.config)
    cli()

if __name__ == '__main__':
    main()
