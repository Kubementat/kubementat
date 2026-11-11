"""CLI-wide helpers shared by the command modules.

`helmfile-apply` and the four `tunnel-*` commands used to pass the literal `'dev1'` as the
team, so they silently read another team's `platform_config/{environment}/<team>/...` and
every path derived from it. The fix was one option with one default, and the default
was the same guess wearing a different name: every invocation that did not type `--team` kept
reading `dev1`, which is what a default team means by *guessing*. It is deleted here, and so
is the other half of the same habit: `default='dev'` on the environment arguments of `src/cli/commands/`
(kubernetes, tunnels, users) and on `kmt users`' `--environment` option. Neither a team nor an
environment is a value kmt is allowed to supply for a command, and the reason the defaults existed is
the reason they went: an operator who typed nothing did not get the configuration they meant, they got
the one this file remembered.

There used to be a `run_kubementat_script` next to it, the shared way the CLI started a
kubementat script. It died with its last caller (`kmt run-ci-container`), which is the
honest place to write that down rather than keep a helper nobody calls. What outlived it by two
tickets was `kmt run-script`, the command that took the path from the operator rather than from
the CLI's own source, and that difference is why it was the escape hatch and not the CLI's
business. It is gone now, so no route from a `kmt` command to a script chosen by path is left
on either side of this file.
"""

import click


def team_option(**kwargs):
    """Add a `--team` option (envvar `TEAM`) to a click command.

    Apply it below the arguments and above `@kmt_context`, and accept the extra
    `team` keyword in the command function. `@click.pass_obj` is gone from this tree: the
    object it carried was `cli.kmt.Config`, a class no command read.

    There is no default and there was never a reason there should have been one. The
    operator names the team, or the `TEAM` variable names it, or the command says what it was
    missing: `Context.require_team` is the sentence, and it is printed by whatever needed the team --
    a service about to read `platform_config/{environment}/<team>/`, or one that puts the team in an
    argv namespace (`kubectl run -n`) or into a name (`<environment>-cluster-<team>-<account>`).
    A `Config` built without a team no longer interpolates `None` into the paths it derives; it
    refuses at the read (`_team_scoped_path`). So the guard belongs in front of the two uses above,
    and in front of nothing else: ticket 3 put it in nine constructors and six of those nine were
    refusing work that had no team read anywhere behind it.

    A command that has no team semantics takes no `--team` at all, and the `Context` it
    runs with carries `team=None` — that is `src/cli/context.py`, and it is why the
    service classes above are the only things that ask. A flag a command cannot act on is how the
    old tunnels came to read `dev1`, so the absence is the rule and not an omission.
    """
    option_kwargs = {
        "envvar": "TEAM",
        "help": "Team whose platform_config/{environment}/<team> config this command uses. "
                "Also reads the TEAM environment variable. kubementat does not name a team "
                "for a command that did not name one.",
    }
    option_kwargs.update(kwargs)
    return click.option("--team", **option_kwargs)

