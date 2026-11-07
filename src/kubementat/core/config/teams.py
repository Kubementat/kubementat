"""What a team directory says about the namespaces it owns.

One home for two readers with different appetites. `team_namespaces` is the strict one the
team-account commands use: a missing namespace is a Role that would bind in nowhere, so the file
has to name both. `declared_team_namespaces` is the tolerant one the uninstall sweep uses: a
directory that is not a team is a thing a directory listing can contain, and stopping a teardown
on the first of those is how a lane once deleted nothing at all.

They share the file they read, the exception they raise and the keys they look for, so they cannot
disagree about what `static.json` says. The rule over a team's name is what makes this worth
holding in one place: it yields an address and not a deed, and only a key the file actually set
says kubementat may delete what lives there.
"""


import logging

import click

from kubementat.core.config.config import Config


# The keys a team directory has to name for uninstall to be able to sweep it, in the order
# they are read. Two keys and no more, because those are the two namespaces a team's work
# lands in: the pipelines and the applications.
TEAM_NAMESPACE_KEYS = ("PIPELINE_NAMESPACE", "APP_DEPLOYMENT_NAMESPACE")


def declared_team_namespaces(ctx, team: str) -> dict:
    '''
    What one team directory actually says about the namespaces it owns.

    Reading the file is the whole job here; deciding what to do about a short answer is
    `namespace_sweep`'s, so that the strict readers -- the team-account commands, which
    cannot bind a Role in a namespace nobody named -- can keep demanding the full pair while
    the sweep does not.

    The missing-environment case had the same asymmetry as before: `kmt uninstall` named the
    file and stopped, and the team-account commands reached into `Config` and came back with a
    `FileNotFoundError` traceback -- one rule, two answers, which is the pattern this
    migration keeps having to remove. So the check is here, in the one function that reads a
    team's namespaces for both uninstall and team accounts.

    The `Config` for one team is built from the root this object was given rather than from the
    pair alone. A sweep is one command, and its root was decided once, upstream: re-deriving it
    per team is how a sweep over `platform_config/` in one tree reads a `static.json` out of
    another and calls both `namespaces`.
    '''

    config = ctx.config
    environment = ctx.environment

    if not config.env_static_config_path.is_file():
        raise click.ClickException(
            f"No environment configuration at {config.env_static_config_path}. The"
            f" namespaces team '{team}' is addressed through are read from that file, so"
            f" nothing can be created or deleted without guessing. Run kmt config init"
            f" <dir> --environment {environment}, or create the file for the environment."
        )
    team_config = Config(environment, team,
                         kubementat_main_dir=config.kubementat_main_dir,
                         platform_config_dir=config.platform_config_dir)
    # Two reads of one file, because two questions are being asked of it. `answered` is where
    # things go: the rule over a team's name makes `pandas` team's pipelines namespace
    # `pandas-pipelines` with no
    # file saying so, which is what installing and binding an account need. `deletable` is what may
    # be removed, and it is the file and nothing else: the rule yields an address and not a deed,
    # and this cluster may already hold a namespace called `pandas` that nobody here created.
    answered = team_config.get('team_static')
    files = team_config.declared('team_static')
    namespaces, missing, deletable = [], [], []
    for key in TEAM_NAMESPACE_KEYS:
        value = answered.get(key)
        if isinstance(value, str) and value.strip():
            namespaces.append(value.strip())
        else:
            missing.append(key)
        named = files.get(key)
        if isinstance(named, str) and named.strip():
            deletable.append(named.strip())
    return {
        "team": team,
        "path": str(team_config.team_static_config_path),
        "namespaces": namespaces,
        "missing": missing,
        "deletable": deletable,
    }


def unreadable_team_configuration(error: Exception, team: str, path) -> dict:
    '''
    Treat a `static.json` that will not parse as one that declares nothing.

    The decision is the one `namespace_sweep` already makes, for the same reason: a file a
    directory listing found and `json` cannot read is the second way a single foreign
    directory can stop a sweep that has a whole cluster in front of it, and deleting nothing is
    the worse answer. What is not the same is the sentence -- the file could not be read is
    said in those words, so the operator is not sent looking for a key that is there. One
    warning per directory, whichever way it fails, is `warn_about_team_directory`'s.

    The strict reader still refuses it, because a team the operator named by hand is a team
    whose config has to be right, and there "I could not read it" is an error and not a skip.

    Raises:
    - Exception: Anything that is not a bad document -- a missing file or a directory is not
      this case, and papering over it would hide the real failure.
    '''
    if not isinstance(error, ValueError):
        raise error
    return {"team": team, "path": str(path), "namespaces": [], "deletable": [],
            "missing": list(TEAM_NAMESPACE_KEYS), "unreadable": True}


def warn_about_team_directory(entry: dict) -> None:
    '''
    Say what a team directory that is not a team did, and what uninstall is doing about it.

    One warning per directory, and a sentence long enough to act on: the path, the key that is
    not set (or the fact that the file would not parse), the fact that the rest of the sweep is
    unaffected, and the two endings -- fix it, or delete it. A warning that only says "skipped"
    is a warning nobody can do anything about, and a directory that produces two warnings
    teaches the reader to stop counting them.
    '''
    missing = entry["missing"]
    # The rule over a team's name added a third shape a team directory can fail this sweep in. A
    # key can be set, unset
    # but answered by the rule over the team's name, or not answered at all, and the middle one is
    # the one that used to be invisible: the namespace is real, it is where the team's pipelines
    # live, and it is still not something kubementat may delete. Both halves are warned about here,
    # so a log line that says "swept" always means "a file named this".
    answered_but_not_named = [name for name in entry["namespaces"]
                              if name not in entry["deletable"]]
    if not missing and not answered_but_not_named:
        return
    verb = "is" if len(missing) == 1 else "are"
    if entry.get("unreadable"):
        reason = "is not readable as JSON"
        fix = "fix the file"
    elif missing:
        reason = f"{' and '.join(missing)} {verb} not set there"
        fix = "add the key that is missing"
    else:
        names = ", ".join(answered_but_not_named)
        said = "is" if len(answered_but_not_named) == 1 else "are"
        reason = f"{names} {said} answered by the team's name and not set there"
        fix = "write the namespace into the file"
    if entry["deletable"]:
        names = ", ".join(entry["deletable"])
        swept = f"only {names} {verb} swept"
    else:
        swept = "nothing under that directory is swept"
    logging.warning(
        f"{entry['path']} is a team directory uninstall cannot sweep in full: {reason}, so"
        f" {swept} and the rest of the sweep continues. If '{entry['team']}' is a team, {fix};"
        f" if it is not a team, remove the directory."
    )


def team_namespaces(ctx, team: str) -> list:
    '''
    The two namespaces `team`'s own configuration declares, both of them required.

    The team-account commands take this answer, and for them a missing namespace is not a
    foreign directory being skipped -- it is a Role that would be bound in nowhere, or an
    account that would be created in the wrong place. So this is the strict reader, and the
    sweep below is the tolerant one. What they share is the file both read and the exception
    both raise, so the two cannot disagree about what `static.json` says.

    Raises:
    - click.ClickException: When the environment config is missing, when `team`'s
      configuration names fewer than both namespaces, or when the file it names cannot be read
      as JSON. The last of these is a skip for the sweep and a refusal here, and the difference
      is which namespaces the operator took responsibility for naming.
    '''

    config = ctx.config
    environment = ctx.environment

    try:
        entry = declared_team_namespaces(ctx, team)
    except ValueError as error:
        # A file that will not parse is not a file that says nothing: a command asked to edit
        # this team's namespaces cannot answer that question, and letting a `JSONDecodeError`
        # through is how a config problem arrives as a traceback of the tool's own stack.
        raise click.ClickException(
            f"{config.platform_config_dir / environment / team / 'static.json'} is"
            f" not readable as JSON, so team '{team}' names no namespaces and nothing can be"
            f" created or bound for it: {error}"
        )
    if entry["missing"]:
        raise click.ClickException(
            f"{entry['missing'][0]} is not set in {entry['path']}. This command reads the"
            f" namespaces it binds a team account in from that file, so team '{team}' has to"
            f" name both - add the key, or pass the team you actually mean."
        )
    return entry["namespaces"]
