"""`kmt environments list` — what environments this `platform_config` has, and which cluster each addresses.

One command, and no cluster contacted. The name follows what
exists: kubementat owns no object called a cluster, so the inventory *is*
this directory tree, and a command called `kmt clusters list` would promise a registry nobody
maintains.

**Why it sits on `EnvironmentRegistry` and not on a `Context`.** The same primitive backs the 1:1
check, which is the whole point: a listing that worked its answer out for itself would be a
second read of one key, and two reads of one key are how the command and the check start to
disagree about what is configured. So this command enumerates, resolves, and formats — and knows
nothing about a team, a namespace, or what any of this hardware is running.

**What a row says when an environment cannot be resolved** is the part worth reading. The rule is
report-and-skip rather than block, and the reason carries over here: an environment whose kubeconfig
moved is *named with the reason*, not left off the list. A listing that dropped it would be a
command that lies about its own coverage, and the operator would find that out from the one command
that was supposed to be safe to run in that state.
"""

import click
from pathlib import Path

from cli.context import CONFIG_DIR_OPTION, CONFIG_DIR_PARAMETER
from kubementat.cluster import (
    STATIC_SOURCE_PREFIX,
    EnvironmentRegistry,
)
from kubementat.config import resolve_platform_config_dir

TABLE_HEADERS = ("ENVIRONMENT", "CLUSTER_ADDRESS", "CONTEXT", "SOURCE", "CHECK")

# What a row that resolved nothing says in the two columns the answer was supposed to fill. Both are
# statements about the file, not about a cluster: the address is what could not be read, and the
# context is what was not declared, and neither one is a cluster's absence.
UNRESOLVED_ADDRESS = "(unresolved)"
CONTEXT_NOT_DECLARED = "(not declared)"


@click.group(name='environments',
             help='List the environments this platform_config has, and the cluster each addresses.')
def environments():
    pass


@environments.command(name='list',
                      help='Print one row per environment: its address, the context that reaches '
                           'it, the file that declared it, and what was checked against it. Reads '
                           'the configuration only — no cluster is contacted, so it answers on a '
                           'machine that has nothing reachable configured.')
@click.option(CONFIG_DIR_OPTION, CONFIG_DIR_PARAMETER, default=None, metavar="PATH",
              type=click.Path(exists=False, file_okay=False),
              help="The platform_config directory to list. It is the same root the 1:1 check "
                   "resolves against, so the listing and a command's refusal cannot disagree. "
                   "With neither this flag nor $KUBEMENTAT_PLATFORM_CONFIG_DIR the run refuses: "
                   "a configuration root is named, never located.")
def environments_list(config_dir=None):
    # The flag is threaded to the registry rather than consulted beside it, for the reason the
    # module docstring gives: a listing that resolved its own root while the check read another is
    # the second answer this command exists not to have. `None` is "the operator named no root",
    # and the rule for that case is `config.resolve_platform_config_dir`'s — the same function a
    # `Config` answers with, so the listing and the 1:1 check cannot spell the precedence two
    # ways. It used to be spelled here: same rule, same outcome, but a second owner of it
    # in `src/`, and agreement is not the same thing as one rule.
    registry = EnvironmentRegistry(resolve_platform_config_dir(config_dir))
    resolutions = registry.all()
    if not resolutions:
        click.echo(f"no environment in {registry.platform_config_dir} declares a cluster. "
                   f"An environment is a directory that carries a static.json of its own; run "
                   f"kmt config init <dir> --environment <environment> to write one.")
        return

    rows = [_row(resolution, registry.platform_config_dir) for resolution in resolutions]
    widths = [max(len(header), max(len(row[column]) for row in rows))
              for column, header in enumerate(TABLE_HEADERS)]
    click.echo("  ".join(header.ljust(widths[column]) for column, header in enumerate(TABLE_HEADERS)))
    for row in rows:
        # The last column is a sentence, so it goes last and is never padded: a reason that has to
        # be truncated is a reason nobody can act on.
        cells = row[:len(TABLE_HEADERS) - 1]
        click.echo("  ".join(cell.ljust(widths[column]) for column, cell in enumerate(cells))
                   + "  " + row[-1])


def _row(resolution, platform_config_dir):
    """One environment as the five columns, with the unresolved half saying why it is unresolved.

    A resolved row's `SOURCE` is the `ClusterTarget`'s own provenance (kept on the object so
    a debugging session can tell "from `--context`" from "from the file"), prefix stripped —
    `EnvironmentRegistry` resolves only from an environment's own `static.json`, so in a listing the
    prefix repeats on every line and says nothing. An unresolved row names the same file, because
    that is the file the claim should have come out of, and the reason in `CHECK`.
    """
    if resolution.target is not None:
        return [resolution.environment,
                resolution.target.address,
                resolution.target.context,
                _source_label(resolution.target.source),
                "ok"]
    return [resolution.environment,
            UNRESOLVED_ADDRESS,
            resolution.declared_context or CONTEXT_NOT_DECLARED,
            str(Path(platform_config_dir) / resolution.environment / "static.json"),
            resolution.reason]


def _source_label(source):
    """`static.json:/path/to/file` as the path itself.

    The prefix belongs on the object so a target says which configuration layer it came from; in a
    table where every row is that layer it only widens a column.
    """
    if isinstance(source, str) and source.startswith(STATIC_SOURCE_PREFIX):
        return source[len(STATIC_SOURCE_PREFIX):]
    return str(source)
