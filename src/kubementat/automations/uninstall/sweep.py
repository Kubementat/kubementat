"""The namespaces `kmt uninstall` deletes, and what each team directory had to say.

Two sources, both of them configuration, and one failure policy that belongs to the sweep and not
to the reader: `sweep` is where a directory that is not a team is decided. The strict reader of the
same file lives in `core.config.teams`, so the two commands cannot disagree about what
`static.json` declares; what they decide differently is only what to do about a short answer.
"""


import logging

from kubementat.core import constants
from kubementat.core.config.teams import (
    declared_team_namespaces,
    unreadable_team_configuration,
    warn_about_team_directory,
)


def namespaces_kubementat_owns(ctx) -> list:
    '''
    Every namespace kubementat would create or has created, in deletion order.

    This is the same answer `namespace_sweep` gives, reduced to its namespace list, and it
    exists so a caller that only wants the names does not have to know what the report looks
    like. The failure policy -- what happens to a directory that is not a team -- is
    documented there.

    Returns:
        list[str]: Namespace names, deduplicated, teams before components.

    Raises:
    - click.ClickException: When the environment has no configuration directory at all.
    '''
    return namespace_sweep(ctx)["namespaces"]


def namespace_sweep(ctx) -> dict:
    '''
    The namespaces uninstall deletes, and what every team directory had to say about itself.

    Two sources, both of them configuration:

    * the pipeline and application namespace of every team the environment configures.
      `dev1` and `smoke` were two names somebody wrote down, not the set of teams, and a
      third team's namespaces were left on the cluster by a script that carried its own
      TODO about exactly that.
    * one namespace per component `kubementat.core.constants` declares, each read through
      `Config.component_namespace` so a renamed namespace is honoured. `uninstall_kubementat.sh`
      read fourteen of them and missed `cert-manager`, whose release then outlived it, and
      `nginx-ingress`, which is not a component at all any more and is therefore swept by
      `uninstall_nginx_ingress` rather than by anything reading this set.

    The team half is where the failure policy lives, and it is deliberately not the strict
    one. Discovery is a directory listing and a directory listing is allowed to contain
    things that are not teams -- a migration or a hand-made probe can leave a directory
    behind, and the retired `initialize` command used to write `kubementat_components` itself.
    Stopping the teardown
    on the first of those was the bug a real lane measured: one directory under a
    config-isolation probe aborted `kmt uninstall` twice and *nothing* was deleted, which is
    the worst possible outcome for the highest-risk command in the product. So each directory
    is swept for the namespaces it declares and for nothing else, and the ones it does not
    declare are named and warned about rather than guessed. A directory that declares nothing
    contributes nothing and is said to have contributed nothing.

    Since the rule over a team's name that sentence needs **declares** to mean what it says. A
    team's namespaces are
    answered by a rule even when no file wrote them down, and that answer is about placement: it
    says where `pandas` team's pipelines live. It is not permission to delete, because a namespace
    of that name may already exist on this cluster, made by something that was never kubementat.
    So the sweep deletes `entry["deletable"]` — the names a file actually set — and reads
    `entry["namespaces"]` only to say what it left alone and why.

    This is not a licence to be blind: the warning is the product's answer, it is printed
    every time, and the final summary repeats it so a `DONE` cannot be read as "everything was
    swept" when part of the tree was skipped.

    Returns:
        dict: {"namespaces": the deduplicated list, teams before components, and
               "teams": {team: {"path", "namespaces", "missing"}} for every directory the
               environment configured, including the ones that are not teams. "missing" holds
               the namespace keys that file does not set, so an empty "namespaces" together
               with two missing keys is the "directory that is not a team" case and one
               missing key is a half-written team.

    Raises:
    - click.ClickException: When there is no configuration directory for the environment, or
      no environment `static.json` to read the namespaces out of. A cluster is never deleted
      on the strength of a name that was never configured.
    '''

    config = ctx.config
    environment = ctx.environment

    teams = {}
    namespaces = []
    for team in config.teams():
        try:
            entry = declared_team_namespaces(ctx, team)
        except ValueError as error:
            # A directory that cannot be read is decided the same way as one that says
            # nothing, and said out loud the same way -- see unreadable_team_configuration.
            entry = unreadable_team_configuration(error, team, config.platform_config_dir / environment / team / "static.json")
        teams[team] = entry
        namespaces += entry["deletable"]
        warn_about_team_directory(entry)
    for component in sorted(constants.DEFAULT_NAMESPACES):
        namespaces.append(config.component_namespace(component))
    # `dict.fromkeys` rather than a set: two teams that share a namespace still have to be
    # swept once, and the order is what the log has to be able to show.
    return {"namespaces": list(dict.fromkeys(namespaces)), "teams": teams}
