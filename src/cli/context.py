"""`@kmt_context` — the one place a command finds out which cluster it is about to touch.

Three things live here and nowhere else: the resolution of the configuration layers, the 1:1
check, and the resolution report, printed before the command body runs. They live together because the
alternative — `cli.kmt.Config` on `ctx.obj`, which 32 commands accepted and none read — was a
context object that answered no questions, so "which cluster" stayed outside kubementat's own
configuration and every service resolved it again for itself.

**Why a decorator and not the group's callback.** A Click group runs before the command's own
arguments are parsed, so `--context` — layer 1 of the resolution order — would be invisible to it,
and the precedence the resolution order chose would be silently dropped. The same argument decides where
it sits in
the stack: it goes *under* the command's options, exactly where `helpers.team_option` goes, so that
by the time it runs the environment is a parsed value and not a guess.

**Why the report goes to stderr.** Every command prints what it resolved before it writes
anything, and where it prints is not free: `kmt create-ingress` puts a manifest on stdout so it can
be piped and sends its warnings to stderr for exactly that reason, and `kmt service-account-token`
prints a credential and nothing else. A resolution line in front of either is a command lying about
the shape of its own output. So the report is informational in Click's sense — `err=True`, always
there on a terminal, never inside a pipe.

**Two rules this file's shape encodes.**

* **A command that cannot act on a team is given no team.** `uninstall`, `cluster-capacity`,
  `pvc-usage`, the diagnostics reads and the three component tunnels carry no `--team`, so the
  `Context` they build carries none. That is the deleted `DEFAULT_TEAM` read from the other side —
  kmt does not guess what a command means, and inventing a team for a command that took none would be
  inventing the namespace it acts in. `kubementat.cluster.Context` says what a Context without a team
  is good *for* (it resolves a cluster and names it) and what it is not (`require_team`).
* **A named refusal is rendered, not rethrown.** Both families of refusal this decorator can meet —
  `Context.build`'s cluster resolution and a service's `require_team` are wrapped
  as `ClickException` so the operator gets one sentence and exit 1, not the tool's own traceback. The
  library stays free of Click to raise them; only the CLI decides how they are printed, because a
  refusal that arrives as a bug report teaches nothing the message did not.
* **Nothing here asks `kubectl`.** The answer comes from `Context.build`, which reads
  `platform_config` and a kubeconfig file and opens no socket. Both
  `kubectl config current-context` call sites are deleted for the same reason: a command whose cluster came
  from whatever `kubectl` was pointed at is the defect this change closes.
"""

import functools
import inspect
import os

import click

from kubementat.cluster import CLUSTER_KUBE_CONTEXT_KEY, Context
from kubementat.config import check_configuration_stamp, resolve_platform_config_dir
from kubementat.errors import (
    ClusterIdentityCollisionError,
    ClusterResolutionError,
    TeamNotSuppliedError,
)

# The variable Click's `envvar='ENVIRONMENT'` reads, spelled once because two halves of the CLI need
# it: the commands that declare the argument, and this decorator for the commands that do not. Those
# commands carry the argument as a positional; before they did, the same variable was the only honest
# answer.
ENVIRONMENT_VARIABLE = "ENVIRONMENT"

# The first layer of the resolution order: the two flags that say *which cluster*, and the flag that
# says *which root the
# configuration lives in* — all three land in parameters named after the flag, so the string an
# operator types and the string a resolution records are the same string and not two of them. The
# third is not part of that order: it changes what is read, not what is addressed, and that is the
# reason it is plumbed to `Config` and never to a `ClusterTarget`.
CONTEXT_OPTION = "--context"
KUBECONFIG_OPTION = "--kubeconfig"
CONFIG_DIR_OPTION = "--config-dir"
CONTEXT_PARAMETER = "context"
KUBECONFIG_PARAMETER = "kubeconfig"
CONFIG_DIR_PARAMETER = "config_dir"


def kmt_context(func):
    """Resolve this command's cluster, print what it found, and hand the command a `Context`.

    Apply it under the command's arguments and options, the way `helpers.team_option` is applied,
    and take one extra positional in the command function:

        @click.command(name='install', ...)
        @click.argument('environment', envvar='ENVIRONMENT')
        @team_option()
        @kmt_context
        def install(ctx, environment, team, ...):

    The `Context` it hands over is resolved, checked against every other environment in the root,
    and reported — in that order — before the command body has written, read, or decided anything.
    """
    command_parameters = inspect.signature(func).parameters

    @click.option(CONTEXT_OPTION, CONTEXT_PARAMETER, default=None, metavar="NAME",
                  help="Kubeconfig context to use instead of the one the environment's "
                       f"static.json declares ({CLUSTER_KUBE_CONTEXT_KEY}). Layer 1 of the "
                       "resolution order: it beats the variable and the file.")
    @click.option(KUBECONFIG_OPTION, KUBECONFIG_PARAMETER, default=None, metavar="PATH",
                  type=click.Path(exists=False, dir_okay=False),
                  help="Kubeconfig file to read the context from. With none of --kubeconfig, the "
                       "CLUSTER_KUBECONFIG_PATH variable or the environment's own key, kubementat "
                       "reads platform_config/<environment>/kubeconfig.encrypted.yaml when that file "
                       "exists -- it is the environment's own kubeconfig, encrypted by the git-crypt "
                       "filter the tree already installs, and it answers ahead of $KUBECONFIG so a "
                       "stray shell export cannot re-point a command that named an environment. Only "
                       "when no such file is there does Kubernetes' own rule apply: $KUBECONFIG, then "
                       "~/.kube/config.")
    @click.option(CONFIG_DIR_OPTION, CONFIG_DIR_PARAMETER, default=None, metavar="PATH",
                  type=click.Path(exists=False, file_okay=False),
                  help="The platform_config directory to read this command's configuration from. "
                       "Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with neither, the run refuses, "
                       "because a configuration root is named and not located.")
    @functools.wraps(func)
    def with_context(*args, **params):
        context_arg = params.pop(CONTEXT_PARAMETER, None)
        kubeconfig_arg = params.pop(KUBECONFIG_PARAMETER, None)
        config_dir_arg = params.pop(CONFIG_DIR_PARAMETER, None)
        resolved = _resolve(_environment_of(command_parameters, params),
                            _team_of(command_parameters, params),
                            context_arg, kubeconfig_arg,
                            config_dir_arg=config_dir_arg)
        for line in resolved.resolution_report:
            click.echo(line, err=True)
        # The report above is the answer to "which cluster"; the body below is where "which team"
        # is asked, and a `Context` that was handed no team answers it with a named error. Which
        # half of the run raised is not the operator's problem, so both are turned into the same
        # `Error: …` line the resolution failures already produce -- see `_resolve`'s docstring.
        try:
            return func(*args, resolved, **params)
        except TeamNotSuppliedError as error:
            raise click.ClickException(str(error))

    return with_context


def _environment_of(parameters, params):
    """The environment this command means, or a refusal that says where it should have come from.

    A command that declares the argument gets what it declares — Click parsed it, and `envvar`
    already read the variable. That is every command that reaches a cluster, and not one of them
    declares a default, so the value this function receives is never one it
    supplied itself. A command that declared *no* environment at all would be read from the same
    variable rather than left to pick one out of the air — which no command does today (`kmt
    environments list` is the only command with no environment parameter, and it contacts nothing,
    so it never comes through here). The branch stays because the day one appears the honest answer
    is still the variable and not a guess.

    The rest is an error rather than a default: `UsageError` because what is missing is on the
    command line, and no edit to `platform_config` would have fixed it. It answers the blank case —
    `ENVIRONMENT=` on the way in, or a declared argument the operator left empty — because a missing
    *positional* is refused by Click itself, `Error: Missing argument 'ENVIRONMENT'.`, before this
    function is reached. Either way the run stops on the sentence and not on a default.
    """
    if "environment" in parameters:
        value = params.get("environment")
    else:
        value = os.environ.get(ENVIRONMENT_VARIABLE)

    if value is None or not str(value).strip():
        raise click.UsageError(
            "no environment was named, and every command that reaches a cluster says which one it "
            f"means. Pass it as an argument or set {ENVIRONMENT_VARIABLE}; kubementat has no "
            "default environment and no current cluster to fall back to."
        )
    return str(value).strip()


def _team_of(parameters, params):
    """The team this command means, or `None` when the command has no team semantics.

    Deliberately no default, because `DEFAULT_TEAM` is gone and the reason it existed — the
    four tunnels silently reading `dev1` — is the bug class v2 refuses. So a command without
    `--team` gets a `Context` without a team, and `Context.require_team` is what says what such a
    Context cannot do.
    """
    if "team" not in parameters:
        return None
    value = params.get("team")
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _resolve(environment, team, context_arg, kubeconfig_arg, config_dir_arg=None):
    """`Context.build`, with the two families of refusal turned into the sentence an operator acts on.

    Both are wrapped as `ClickException` because both are the command's answer and not a bug in it:
    the failure text of a cluster resolution is worth as much here as it is in the library, and a
    traceback teaches nothing the message did not. The collision is included for the same reason — a
    stack trace is a
    poor way to tell someone that two of their environments are one cluster, and the check that
    refused has no override flag to point at.
    """
    # The stamp is asked before `Context.build` is called, and the ordering is the whole point. A
    # check that runs after resolution can be reached by a verb that has already resolved a cluster,
    # which is what a version gate that gates nothing is. Nothing in `Context.build` is needed to know
    # the
    # root, so the root is resolved the same way the builder will resolve it and the question is
    # asked of it first.
    for line in check_configuration_stamp(resolve_platform_config_dir(config_dir_arg)):
        click.echo(line, err=True)
    try:
        return Context.build(environment, team, context=context_arg, kubeconfig=kubeconfig_arg,
                             platform_config_dir=config_dir_arg)
    except (ClusterResolutionError, ClusterIdentityCollisionError) as error:
        raise click.ClickException(str(error))
