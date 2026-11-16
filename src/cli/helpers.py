"""CLI-wide helpers shared by the command modules.

`helmfile-apply` and the four `tunnel-*` commands must not pass a team they were not given: a
literal team makes them read another team's layer of the configuration directory and
every path derived from it. One option with one default is not the fix, either, because the
default is the same guess wearing a different name: an invocation that does not type `--team` and
silently gets `dev1` is guessing. The same applies to the `default='dev'` on the environment
arguments of `src/cli/commands/`
(kubernetes, tunnels, users) and on `kmt users`' `--environment` option. Neither a team nor an
environment is a value kmt may supply for a command: an operator who typed nothing must not get the
configuration this file remembered.
"""

import click


def team_option(**kwargs):
    """Add a `--team` option (envvar `TEAM`) to a click command.

    Apply it below the arguments and above `@kmt_context`, and accept the extra
    `team` keyword in the command function. `@click.pass_obj` is gone from this tree: the
    object it carried was `cli.kmt.Config`, a class no command read.

    There is no default, because there is no value that could be guessed here. The
    operator names the team, or the `TEAM` variable names it, or the command says what it was
    missing: `Context.require_team` is the sentence, and it is printed by whatever needed the team --
    a service about to read the team layer of the configuration directory, or one that puts the team in an
    argv namespace (`kubectl run -n`) or into a name (`<environment>-cluster-<team>-<account>`).
    A `Config` built without a team refuses at the read (`_team_scoped_path`) rather than
    interpolating `None` into the paths it derives. So the guard belongs in front of the two uses above,
    and in front of nothing else: a guard in a constructor that has no team read behind it is refusing
    work it has no business refusing.

    A command that has no team semantics takes no `--team` at all, and the `Context` it
    runs with carries `team=None` — that is `src/cli/context.py`, and it is why the
    service classes above are the only things that ask. A flag a command cannot act on is how the
    old tunnels came to read `dev1`, so the absence is the rule and not an omission.
    """
    option_kwargs = {
        "envvar": "TEAM",
        "help": "Team whose `environment/team` layer of the configuration directory this command "
                "uses. Also reads the TEAM environment variable. kubementat does not name a team "
                "for a command that did not name one.",
    }
    option_kwargs.update(kwargs)
    return click.option("--team", **option_kwargs)

