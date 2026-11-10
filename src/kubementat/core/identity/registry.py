"""The environments under one root, and the cluster each one claims.

Nine names, and one subject: enumerate what exists, read each thing's own claim, and write the
answer down in a shape the 1:1 check and `kmt environments list` both read without re-deriving it.
Everything below the class answers a question about a file; nothing in this module asks a cluster
anything.

* enumeration — `EnvironmentRegistry` itself, whose only rule is "directories at depth 1 that hold a
  `static.json`", read off the disk rather than out of a list someone maintains.
* resolution — `EnvironmentResolution`, `resolve`, and the three helpers that read one
  `static.json` (`_static_declaration` and the two functions it calls). A resolution that came back
  bare would have to be a `None` in a list, and the reason an environment could not be read is the
  half of the answer a check needs.
* the comparison — `ClusterComparison`, the record of what `collision_check` compared, and the
  sentence built for the case where an environment has no declaration file at all
  (`environment_file_missing`).
* the plumbing — `_environment_declaration` and `_load_environment_config`, which is one `Config`
  built lazily and the one file a cluster claim depends on.

The order the file stands in is the whole of its legality, and it is the order the census chose for
the split: `kubeconfig <- target <- comparison <- registry <- context`. It reads the three modules
below it and nothing above it — `context.py` least of all, since that is the module that
stands on this one and is the only way a split that looks acyclic can turn out to have a back edge.
`context.py` exists now and nothing here needs it: every name it owns is a reader of this module and
never a dependency of it, which is the edge the last move had to keep pointing the same way.

**`ClusterComparison` lives here and not in `comparison.py`, and that is a divergence the census
flags rather than one it hides.** The plan puts the comparison *object* beside the object that
enumerates what it lists, while `comparison.py` got the comparison *primitives*, so a reader who
expects the record of a check beside the primitives of that check will look in the wrong file. The
reason is the edge, not the name: the object has exactly one reader, `collision_check` two lines
above it, and putting it anywhere else makes one class's home depend on which of its two fields a
stranger greps for. Keeping it here leaves `comparison.py` needing nothing from above it, which is
why the split has one arrow between those two files instead of two.

Two shapes are load-bearing and do not follow from the rest of the file:

* **the root is threaded and never re-derived.** Every `Config` and every path below comes from the
  root a command was given, so the listing, the check, and the report line are answers about one
  directory rather than about three that agree by luck.
* **a refusal names the file and the key.** Nothing here falls back to a `current-context`, a
  `KUBECONFIG`, or a guess about what a partially-configured environment meant; the sentence an
  operator acts on says which file was opened and which key was not in it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.reads import read_decrypted
from kubementat.errors import LockedConfigFileError
# The three modules below this one are bound and not unpacked, for the one reason every importer of
# them has had: a name import would leave `kubementat.core.identity.registry.ClusterTarget` and
# friends standing beside the objects themselves, and a second resolvable name for one object is
# what a `monkeypatch.setattr` aimed at the module nobody is running then misses. The aliases match
# the ones in `context.py` so that a line read here is the line that was read there.
from kubementat.core.identity import comparison as _comparison
from kubementat.core.identity import kubeconfig as _kubeconfig
from kubementat.core.identity import target as _target
from kubementat.errors import (
    ClusterFileUnreadableError,
    ClusterNotConfiguredError,
    ClusterResolutionError,
)


@dataclass(frozen=True)
class EnvironmentResolution:
    """One environment's own claim on a cluster, or the reason it has none.

    The unresolved half is not a `None` in a list: the 1:1 check wants the environment named with
    the reason it could not be read, because a check that silently omits environments is a check
    that lies about its coverage.

    `declared_context` is what the environment's `static.json` said, whether or not it reached a
    cluster. `kmt environments list` prints that column, and it has to come out of the same
    read the check ran: an answer the command derived for itself is a second read of one key, and
    two reads of one key are how a listing and a check start to disagree about what is configured.
    """

    environment: str
    target: Optional[_target.ClusterTarget]
    reason: Optional[str] = None
    declared_context: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.target is not None


@dataclass(frozen=True)
class ClusterComparison:
    """One 1:1 check, with everything the answer was built out of still attached to it.

    It is a comparison and not a boolean because the same question feeds two sentences: the refusal,
    which only needs the environments that overlapped, and the report line, which needs the whole
    set that was compared and the half of it held back because it is the cluster the command is
    running on. Returning only
    the overlaps would leave the second sentence to go and work the answer out again — and that
    second working is exactly the second implementation this object exists to delete.

    * `others` is every *other* environment in the root, unresolved ones included, because a line
      that says what was verified has to be able to name what could not be.
    * `self_address` is the running cluster's exclusion made visible: the same value the check
      subtracted with, so
      the report can say *which* environments were held back and why.
    * `collisions` is the only field that may refuse a command, and it is empty exactly when the
      command may run.

    `target` is what was asked about, kept so a reader of a log or a debugger can see the question
    beside the answer instead of inferring it from the address.
    """

    target: _target.ClusterTarget
    others: Tuple[EnvironmentResolution, ...]
    collisions: Tuple[EnvironmentResolution, ...]
    self_address: Optional[str] = None


class EnvironmentRegistry:
    """Every environment under one `platform_config` root, and the cluster each one declares.

    Enumeration is directories at depth 1 of the root that contain a `static.json`, and nothing
    more: that falls out of the existing shape — a team's `static.json` is depth 2, and
    `kubementat_components/` carries no environment-level one — so there is no registry file and
    no rule to keep in sync with the tree.
    """

    def __init__(self, platform_config_dir):
        self.platform_config_dir = Path(platform_config_dir)

    def environments(self) -> Tuple[str, ...]:
        """Environment names, sorted, by what is on disk.

        Reading a list out of the directory rather than out of code is the same decision that put
        `Config.teams()` on a directory listing: a name someone remembered is a name someone
        forgot.
        """
        if not self.platform_config_dir.is_dir():
            return ()
        return tuple(
            sorted(
                entry.name
                for entry in self.platform_config_dir.iterdir()
                if entry.is_dir() and (entry / "static.json").is_file()
            )
        )

    def all(self) -> Tuple[EnvironmentResolution, ...]:
        """Every environment's own claim, resolved or refused with a reason.

        Nothing here consults the process environment or anyone's command line: a sibling is
        asked what *it* declares, which is the only question that can be answered about a directory
        the current command is not running.
        """
        return tuple(self.resolve(name) for name in self.environments())

    def resolve(self, environment: str) -> EnvironmentResolution:
        """One environment's target, read out of its own `static.json`."""
        static_path = self.platform_config_dir / environment / "static.json"
        if not static_path.is_file():
            return EnvironmentResolution(
                environment,
                None,
                reason=f"{static_path} does not exist",
            )
        try:
            declared = _static_declaration(static_path)
        except ClusterResolutionError as exc:
            return EnvironmentResolution(environment, None, reason=exc.reason)
        context = declared["context_value"]
        if context is None:
            return EnvironmentResolution(
                environment,
                None,
                reason=f"{constants.CLUSTER_KUBE_CONTEXT_KEY} {declared['context_state']} in {static_path}",
                declared_context=context,
            )
        try:
            target = _target.ClusterTarget.from_kubeconfig(
                declared["path_value"] or _kubeconfig.in_tree_kubeconfig(self.platform_config_dir, environment),
                context,
                environment,
                source=f"{constants.STATIC_SOURCE_PREFIX}{static_path}",
            )
        except ClusterResolutionError as exc:
            return EnvironmentResolution(
                environment, None, reason=exc.reason, declared_context=context)
        return EnvironmentResolution(environment, target, declared_context=context)

    def collision_check(self, target: _target.ClusterTarget) -> ClusterComparison:
        """The *other* environments that address the same cluster `target` does, and the comparison
        that answer came out of.

        An empty `collisions` is the only answer that lets a command run. The target's own
        environment is excluded by name, not by address: it is allowed to be the environment that
        declares this cluster, which is the whole point of 1:1.

        So is the running cluster, by a rule that has to be stated rather than inferred: when this
        process is inside one, an environment
        that resolves to that same address is *here*, not a second cluster, and reporting it as a
        collision would refuse kubementat's own install. The exclusion is applied once, here, and
        returned inside the comparison instead of being recomputed by whoever reports the answer —
        which is the reason this method exists rather than an inline `any()` somewhere else. Once
        `Context.build` spelled this filter out beside the method and then probed the running
        cluster a second time for its own report line: two spellings of one rule are how a refusal
        and the sentence about it start to disagree, and one probe is what makes them share.
        """
        # One pass over the root, because the cost of local reads is acceptable but the cost of
        # doing it twice for the sake of a report line is not.
        self_address = _comparison.running_cluster_address()
        others = tuple(record for record in self.all() if record.environment != target.environment)
        collisions = tuple(
            record
            for record in others
            if record.target is not None
            and record.target.address == target.address
            and not _comparison._addresses_the_running_cluster(record, self_address)
        )
        return ClusterComparison(target=target, others=others, collisions=collisions,
                                 self_address=self_address)


def _static_declaration(static_path: Path) -> Dict[str, Any]:
    """The two cluster keys as they were declared in `static_path`.

    Returns the value of each key (or `None` when it is absent, null, or blank — all three are
    treated as not declared) plus a `context_state` phrase that says *which* of the three it was,
    because the error has to name the value found and not just the key missing.
    """
    try:
        document = json.loads(read_decrypted(static_path, "environment's static.json"))
    except OSError as exc:
        raise ClusterFileUnreadableError(
            f"{static_path} could not be read: {exc}", reason=f"{static_path} could not be read"
        ) from exc
    except LockedConfigFileError as exc:
        # The seam's sentence, this module's type. It used to be a `UnicodeDecodeError`
        # that walked past the two handlers below and printed a codec traceback about a file whose
        # only crime was being locked.
        raise ClusterFileUnreadableError(
            f"{static_path} could not be read: {exc}", reason=f"{static_path} could not be read"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ClusterFileUnreadableError(
            f"{static_path} is not valid JSON: {exc}", reason=f"{static_path} is not valid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise ClusterFileUnreadableError(
            f"{static_path} is not an environment static.json: it is not an object.",
            reason=f"{static_path} is not an object",
        )

    raw_context = document.get(constants.CLUSTER_KUBE_CONTEXT_KEY)
    raw_path = document.get(constants.CLUSTER_KUBECONFIG_PATH_KEY)
    return {
        "context_value": _declared_value(raw_context),
        "context_state": _declaration_state(raw_context, constants.CLUSTER_KUBE_CONTEXT_KEY in document),
        "path_value": _declared_value(raw_path),
    }


def _declared_value(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _declaration_state(value, present: bool) -> str:
    """How the key reads in the file, for an error that has to name the value found.

    Absent, null, and empty are the three ways a key reads as not declared, and
    an operator reading the file sees three different files, so the sentence says which one it
    found — `null` is what a freshly prepared environment ships, and "not set" is what a
    hand-edited one that never had the key looks like.
    """
    if not present:
        return "is not set"
    if value is None:
        return "is null"
    if not str(value).strip():
        return "is empty"
    if str(value).strip().lower() == "null":
        return "is the string 'null'"
    return f"is set to {value!r}"


def environment_file_missing(environment: str, static_path: Path) -> str:
    """The refusal an operator acts on when an environment has no `static.json` to read a key out of.

    Spelled once because both halves of `Context.build` need it: the team-bearing one fails inside
    `Config`'s constructor, the team-less one in front of the file it was about to read. Same
    file, same missing key, same instruction — which is the point of not writing it twice.
    """
    return (
        f"environment '{environment}' does not address a cluster: {static_path} does not exist, "
        f"so {constants.CLUSTER_KUBE_CONTEXT_KEY} cannot be read from it. There is no current-context "
        f"fallback — run kmt config init --environment {environment} "
        f"--config-dir <configuration-root>, or name "
        f"the context "
        f"in that file."
    )


def _environment_declaration(environment: str,
                             team: Optional[str],
                             platform_config_dir=None) -> Tuple[Config, Path, Dict[str, Any]]:
    """`(config, static_path, declared)` for the environment one command means.

    `platform_config_dir` is the root one command was given, and it is threaded into the one
    `Config` this function builds rather than passed beside it. A root that stopped at this
    signature would reach the registry below and not the file read here, and that is how one flag
    ends up answering two questions.

    The `Config` is built either way, and that is the whole of what a `Context` answers: `config`
    says *what to deploy* and `target` says *where*, and a command that sweeps namespaces has to be
    able to answer the first question even when it was never given a team. **With a team**,
    this is the `Config` every service class used to build for itself. **Without one**, the
    same two arguments reach `Config` with `team=None`, which is now a state that object supports
    rather than a lie it is told: the environment-level files resolve, the team-scoped paths are not
    built, and reading a team-scoped value raises `TeamNotSuppliedError` instead of interpolating
    `None` — or, from an empty team, reading the environment's own file and calling it the team's.

    What both paths do is read **one** file, and name it when it is not there. The environment's own
    declaration is the only configuration a cluster claim depends on, so it is the only one read
    here; every other file stays lazy, exactly as it was before the decorator, so that a broken
    team-level file keeps arriving as the sentence `Config.get` writes about that file instead of as
    a JSON error from a place that cannot say which of four files it opened.
    """
    config = _load_environment_config(environment, team, platform_config_dir)
    static_path = config.env_static_config_path
    if not static_path.is_file():
        raise ClusterNotConfiguredError(
            environment_file_missing(environment, static_path),
            reason=f"{constants.CLUSTER_KUBE_CONTEXT_KEY} cannot be read, {static_path} does not exist",
        )
    return config, static_path, _static_declaration(static_path)


def _load_environment_config(environment: str, team: Optional[str],
                             platform_config_dir=None) -> Config:
    """The one `Config` a Context carries, built the lazy way.

    A root is passed through and never re-derived: `None` means "this command named none", and the
    precedence rule for that case is `Config`'s, not a guess made here. The
    flag that reaches this argument is the same spelling a `Config` keyword already has, so
    plumbing it changes no rule.

    `preload_configs=False` is not an optimisation. It is what keeps `Context.build` reading the
    single file a cluster claim lives in: `Config`'s eager read opens four and fails on whichever is
    wrong first, so a malformed *team* file would refuse a command whose cluster resolved perfectly,
    with a message that cannot say which of the four it opened. A `Context` that loaded eagerly would
    be a second, louder answer to a question one file already answers.

    Nothing else about the object changes. `get()` loads what it is asked for on first use, so a read
    of `team_static` still fails naming the file that was missing — and, for a Context given no team
    at all, fails naming the team it was given no name for.
    """
    return Config(environment, team, platform_config_dir=platform_config_dir,
                  preload_configs=False)
