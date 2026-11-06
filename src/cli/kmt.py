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
    cli.add_command(kubementat.setup_secrets)
    cli.add_command(kubementat.setup_docker_registry_access)


    # tunnels: the four tunnels the parity record counts are the `tunnel` group (`kmt tunnel pod`), so the
    # group name carries the kind and the verb carries the target. `tunnel-polaris` and
    # `tunnel-mysql` were the two flat names left outside the group; both are retired, and
    # nothing else here moved, so the group is the whole tunnel surface now.
    cli.add_command(tunnels.tunnel)

    # tekton: six of the ten commands here are on the `kmt tekton` group. The cleanup job and the
    # three generators keep their flat names for the same reason the parity record leaves them out.
    cli.add_command(tekton.tekton)
    cli.add_command(tekton.setup_cleanup_job)
    cli.add_command(tekton.generate_deploy_pipeline)
    cli.add_command(tekton.generate_trigger_configs)
    # The last generator on the tekton path (`generate_pipeline_run.sh`, deleted in the same commit).
    # It had no caller in `src/`, so the only way to reach it was to know its name and stand in the
    # right directory; a command is how that stops being true.
    cli.add_command(tekton.generate_pipeline_run)

    # helm: `kmt helm apply` is this group's row. `helm-version` stays flat for the same
    # reason the unmeasured commands above do -- no marker measures it -- and the fact that a `helm`
    # group now answers to an apply that used to be its only helm command is exactly what the grouping
    # was for.
    cli.add_command(helm.helm)
    # The flat one-component uninstall is not registered here either: that command is now
    # `kmt component uninstall`, wired in with the install half below. Renamed outright -- no alias,
    # no second name that answers to the same deletion.
    # The newest version a chart has, printed. The script it replaces is deleted in the same
    # commit; it is flat here because no marker measures it.
    cli.add_command(helm.helm_version)

    # utilities
    cli.add_command(utilities.run_ci_container)
    # There is no command here that takes a script path from the operator and execs it.
    # Nothing replaces it: the CLI's own work goes through `kubementat.infrastructure.shell`, and what an operator
    # gets is a command and not a path to run. The guard that keeps it out is
    # `tests/test_no_cli_command_starts_a_script_by_path.py`.
    # The Ingress manifest generator that replaces `create_ingress_yml.sh`,
    # the last script under `scripts/utilities/kubernetes/`, which is deleted in the same
    # commit.
    # It is a command and not a path handed to a runner, because the script had no caller
    # in `src/` or `tekton_ci/` - reaching it meant knowing the filename and standing at the right
    # depth, because it resolved `../../../platform_config` against itself. No alias: the same
    # rule the other migrated scripts kept, and the class it writes is now answered through
    # `Config` (`ingress.py`) rather than `jq`'ing that same file.
    cli.add_command(utilities.create_ingress)

    # kubernetes reads: the diagnostics that were scripts under
    # scripts/utilities/kubernetes/, now through the client. No aliases, no shims.
    cli.add_command(kubernetes.analyze_deployment)
    cli.add_command(kubernetes.pod_images)
    cli.add_command(kubernetes.cluster_capacity)
    # Disk usage of the claims, the fourth of those reads and the last one nobody could
    # reach without standing in a directory and typing a filename. No alias, for the reason the
    # others give: `list_pvc_usage.sh` is deleted in this same commit, and it had no caller in
    # `src/` or `tekton_ci/` either -- `git grep -n list_pvc_usage` says the only things that ever
    # named it were plan files. It also started a `kubectl proxy`, slept two seconds, curled the
    # local port, then `pkill -9`ed it, so there is no shell-shaped way to keep it running beside
    # the client call it duplicates.
    cli.add_command(kubernetes.pvc_usage)

    # The helm-deployment sessions: the two scripts under scripts/utilities/helm/ that
    # were a pod lookup plus one kubectl call. Both stream to the terminal and neither captures.
    cli.add_command(kubernetes.deploy_ssh)
    cli.add_command(kubernetes.deploy_logs)

    # A token read: the one script under scripts/secret_management/ that an operator
    # actually needed, now through the client so a typo is a sentence and not an empty `token:`.
    cli.add_command(kubernetes.service_account_token)

    # The write beside those reads: what `create_secret_from_json.sh` did, now through
    # the client. That script's only caller was `tekton_ci/tasks/create-secret-from-json.yml`,
    # so a command is what makes that Task able to stop typing a path into a shell, and the script is
    # deleted in the same commit. No alias, and no `--team`/`--environment` flag either: the
    # environment is the first positional the Task passes and a flag for it would be a second
    # way to say which cluster the Secret lands in, while a flag that resolved the namespace out of
    # `platform_config` would be a second way to say which namespace to write to.
    cli.add_command(kubernetes.create_secret_from_json)

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
