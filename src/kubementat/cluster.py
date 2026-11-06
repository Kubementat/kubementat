"""An environment addresses one cluster, and the address is the claim the 1:1 check compares.

Two objects live here, and each of them exists to stop "which cluster" being answered six
times by six different classes. The third name of that trio — `ClusterTarget`, the frozen value
object that says *which* cluster and *where* that was declared — is one level down in
`core/identity/target.py`, imported rather than duplicated: one class object is what makes
`isinstance(ctx_or_target, ClusterTarget)` answer the same way for every caller, and this module
has no copy to be wrong about. Nothing in this module runs a command, opens a
socket, or asks a cluster anything: every address is read out of a file before a service exists
— or, last of the layers that can answer, out of the material the kubelet mounted in the pod that
is asking.

* `ClusterTarget` — read from `core/identity/target.py` by every comparison, resolution and
  refusal below; it carries its own server address, so a comparison cannot forget to happen.
* `EnvironmentRegistry` — enumerates environments (directories at depth 1 of `platform_config`
  that contain a `static.json`) and resolves each one's own claim.
* `Context` — the pair of `(config, target)` plus the team and environment they belong to,
  built in one place so a command either has a cluster it can name or does not run.
* The 1:1 check's primitives — the running cluster's own address, the address equality the check and
  the report line must agree on, and the report line — are read from `core/identity/comparison.py`.
  Only the check that combines them stays here, and `ClusterComparison` goes wherever the registry
  that enumerates what it lists goes, because it is that registry's answer and not this file's.

Two decisions that are load-bearing and not obvious from the shape of the code:

* **The address is resolved by reading the YAML, not by loading the client.**
  `kubernetes.config.load_kube_config` would run any exec plugin the kubeconfig names and would
  mutate the process-wide default `Configuration` — the side effect `ClusterTarget.client()` now
  avoids by loading into its own. Reading the file directly means the address is available for every
  environment in the root, cheaply enough for the check to run on reads, and it means an
  `environments list` cannot trigger a credential refresh.
* **`$KUBECONFIG` is read when we are asked, not when the module was imported.**
  `kubernetes.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION` is a module-level constant the
  library captured at import (the fixtures in `tests/conftest.py:547` exist because of this), so
  a default resolved through it would silently disagree with the environment the caller is in.
  `kubeconfig.default_kubeconfig_path` applies the same documented rule — `$KUBECONFIG`, then
  `~/.kube/config` — at call time, and the target's `client()` hands the concrete path it answers
  to the library so the address and the client cannot disagree.

  What reads a kubeconfig lives one level down, in `core/identity/kubeconfig.py`: the files a
  `$KUBECONFIG` list names, the merge rule that decides which context wins, the server string read
  out of the YAML, and the material a pod is mounted with. That module is below this one and reads
  nothing in it, which is the direction the whole identity split runs in. `target.py` stands on the
  same leaf, and `comparison.py` — the 1:1 check's primitives, which this module held until now —
  stands on `target.py`, so what this module imports reaches
  `cluster → comparison → target → kubeconfig` with every edge pointing down. `kubeconfig` is imported
  as `_kubeconfig` because `Context.build` already names a parameter `kubeconfig`: bound under its own
  name, the module would be shadowed by that parameter — usually by `None` — on every path that was
  handed no flag, and the failure would be an `AttributeError` about a `NoneType` inside a resolution
  that had nothing wrong with it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.reads import LockedConfigFileError, read_decrypted
# The 1:1 check primitives are bound as the module and not by their three names, for the same
# reason `target` is bound that way below: all three are reached from inside this file today, and a
# name import would leave `kubementat.cluster.running_cluster_address` standing beside the function
# itself — a second binding for one function object, which is what a patch aimed at the module nobody
# runs then misses. The same rule kept them out of this file's namespace.
from kubementat.core.identity import comparison as _comparison
from kubementat.core.identity import kubeconfig as _kubeconfig
# `ClusterTarget` is bound here as the module and not the name on purpose: a name import
# would answer `kubementat.cluster.ClusterTarget`, and a second door to one class object is
# what a `monkeypatch.setattr` on the class then misses. The ten readers left below reach the
# class through the module that owns it; the eleventh went with `_report_line`.
from kubementat.core.identity import target as _target
from kubementat.errors import (
    ClusterContextNotFoundError,
    ClusterFileUnreadableError,
    ClusterIdentityCollisionError,
    ClusterNotConfiguredError,
    ClusterResolutionError,
    StatedClusterMismatchError,
    TeamNotSuppliedError,
)


# `TeamNotSuppliedError` is imported above rather than defined here, and it comes from
# `kubementat.errors` rather than from this module's neighbourhood: the same refusal is raised by
# `Context.require_team` and by `Config`'s team-scoped reads, and defining a second one here would
# make the guard raise one type and the services catch another. Not a resolution failure: the cluster
# resolved and `Context` carries it. What is missing is the other half of the pair —
# `platform_config/{environment}/<team>/` — and kmt does not guess what a command means, so the object
# says so rather than letting a service read a path built out of a name nobody gave it. That both
# call sites raise it is the reason the refusal has one home instead of one per service.


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
        f"fallback — run kmt config init <dir> --environment {environment}, or name the context "
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


@dataclass(frozen=True)
class Context:
    """The environment, the team, the configuration, and the cluster — one object, one read.

    This exists rather than an extra argument on six constructors for a reason that is not obvious
    from the shape of the code: every
    service class used to resolve "which cluster" for itself, so the identity check ran as many
    times as there were services in a command, and one of those resolutions was apologising for
    being a second read in a docstring.

    `team` is nullable and `config` is not: a command with no team semantics resolves its
    cluster from the environment layer and carries the environment's configuration without a
    directory named after a team. That is the uniform type — one `Context`, either shape — and
    `require_team` is what a service that does need the other half says when the uniformity is not
    an answer. It is not a downgrade of what one object promises: a team-less command has no
    team-valued config
    to keep in step with the cluster, so there is nothing to drift. `config` exists either way
    because *what to deploy* is half of what a command is asked, and `uninstall` — a command with no
    team that has to know which namespaces to delete — is the proof that the other half is not
    optional.

    `target` is nullable only for `for_configuration` below, and `require_target` is what stops a
    command from treating that state as "no cluster" rather than "no cluster has been addressed
    yet".
    """

    environment: str
    team: Optional[str]
    config: Optional[Config]
    target: Optional[_target.ClusterTarget]
    resolution_report: Tuple[str, ...] = field(default=(), compare=False)

    # `target` is optional for exactly one shape: the `for_configuration` Context below, which is
    # the same "this environment has not addressed a cluster yet" state
    # `EnvironmentRegistry.resolve` reports for an environment that declares nothing. Every
    # Context a command resolves carries a target; an empty one is a fact about the command, not an
    # unset field, and `require_target` is what stops a call site reading it as "no cluster".

    def require_team(self, what_is_missing: str) -> str:
        """This team's name, or a refusal that says what it was needed for.

        One `Context` type means a service cannot be told about the cluster a command resolved
        and left in the dark about the team it did not, so the service asks instead of guessing.
        `what_is_missing` is the caller's own noun — the thing it was about to read, write or
        *name* — because "no team" is a fact about the run and what the reader needs is the sentence
        saying which decision that fact stops.

        The sentence says what a team is *for*, in both of the two ways this tree uses one, and does
        not claim that the caller is about to open a file: some sites call this because they are
        about to read `platform_config/{environment}/<team>/`, and two call it because the team is
        an argv namespace (`kubectl run -n`) or a substring of a kubeconfig's context name. A
        message that only described the first kind told an operator running `kmt run-ci-container`
        that a file was missing when the code that refused it opens no file at all.

        There is no default to fall back to, and that is not an oversight: `DEFAULT_TEAM` was taken
        out for the reason `kmt users` already refuses a team it was not given, which is that
        guessing a team is guessing what a command means.
        """
        if self.team is None:
            raise TeamNotSuppliedError(
                f"{what_is_missing} belongs to a team, and this command was not given one. A team "
                f"is what platform_config/{self.environment}/<team> is keyed by, and what the "
                f"namespaces and account names it governs are built from; kubementat will not name "
                f"one for a command that did not name one. Pass --team (or set TEAM)."
            )
        return self.team

    def require_target(self, what_is_missing: str) -> _target.ClusterTarget:
        """The cluster this command is about, or a refusal that says what it was needed for.

        The sibling of `require_team`, and for the same reason: this field is nullable in exactly
        one state — a `Context` that writes configuration and never addresses a cluster, which is
        `for_configuration`'s reason to exist and the reason not to make the target eager — and a
        call site that reads it as though it were always there turns "no cluster yet" into a
        command that addresses nothing and says nothing about it. So the state has a name, and
        anything that has to *use* a target asks for it.

        `ClusterNotConfiguredError` is the refusal, because that is what the state is: the key that
        names a cluster was never named for this environment, and the refusal's sentence is the one
        an operator acts on.
        It is not a new failure class, and there is no override for it.
        """
        if self.target is None:
            raise ClusterNotConfiguredError(
                f"{what_is_missing} needs the cluster environment '{self.environment}' addresses, "
                f"and this Context was built to write configuration without ever addressing one. "
                f"{constants.CLUSTER_KUBE_CONTEXT_KEY} is what names it: set it in "
                f"platform_config/{self.environment}/static.json, or run the command that resolves "
                f"one instead of the one that configures.",
                reason=f"no ClusterTarget was resolved for environment '{self.environment}'",
            )
        return self.target

    @classmethod
    def for_configuration(cls, environment: str, team: str,
                          platform_config_dir=None) -> "Context":
        """A `Context` with its configuration and no cluster claim — laziness applied to the object
        a constructor is handed.

        `kmt config init` is the command this exists for. It writes the `static.json` a resolution
        reads, so asking it to resolve one first would make it the strictest command in the product
        for the least benefit: a tree that does not exist yet cannot say which cluster it is for,
        and the promise is that the first run works. The same argument against an eager
        `Automation` — a constructor that resolves a kubeconfig fails a command that
        touches no cluster — applies to the object a constructor is handed, so a verb that writes
        a configuration root gets
        this Context and nothing else.

        What is deliberately absent, and what each costs:

        * `target` is None: the environment has declared nothing, which is the state an unresolved
          environment is in. Nothing may address a cluster through this object, and
          `require_target` is what says so at the one point a caller might try.
        * the 1:1 check does not run. There is no claim to compare, which is the condition for
          skipping it rather than an exception to it.
        * `resolution_report` is empty. The report is a claim about a resolution that happened;
          a line printed for one that did not is the same line with a different meaning.

        A command that *does* address a cluster should build this by accident and never be told:
        `Context.build` is that command's constructor, and it cannot produce an object with no
        target. This one is for the command that configures a tree and stops.

        `platform_config_dir` is the root the operator named — `config init`'s positional directory,
        or the `--config-dir` every reading command carries.
        It reaches the `Config` this Context carries, and with it every directory and file
        the configuration half of a `Config` carries. Without it and without the variable there is
        no root to
        scaffold, and `Config` refuses rather than inventing a `platform_config` beside wherever
        the wheel was installed.
        """
        return cls(
            environment=environment,
            team=team,
            config=Config(environment, team, platform_config_dir=platform_config_dir,
                          preload_configs=False),
            target=None,
        )

    @classmethod
    def build(cls, environment: str, team: Optional[str] = None, context=None,
              kubeconfig=None, platform_config_dir=None) -> "Context":
        """Resolve in the order the layers are declared, check 1:1, and say what was compared.

        `platform_config_dir` is the root one command was given — the `--config-dir` a `kmt_context`
        command carries. It is threaded to the `Config`, and so to both halves of what this class
        reads: the environment's own `static.json` (through `_environment_declaration`) and the
        sibling sweep the 1:1 check compares (through `config.platform_config_dir` below). Handing
        one of the two and re-deriving the other is the bug this threading exists to close, which is
        why neither is spelled from the environment here.

        This is the only place the resolution order lives, and the only place a command runs the
        1:1 check — the check itself is `EnvironmentRegistry.collision_check`, which this method
        asks rather than writes out beside it. That is the point of one enumeration and one filter:
        one rule, one spelling, and the report line reading the same comparison that may refuse.
        It runs on reads too rather than only on writes, and the reason is stated rather than
        assumed: a check that skips reads needs a notion of purpose on the object, and the
        difference between that and running everywhere is a handful of local file reads.

        `team=None` is the shape the team-less commands hand up — `uninstall`, `cluster-capacity`,
        `pvc-usage`, the diagnostics reads — and they still resolve, because "which cluster" is not
        a question about teams. What they carry is the environment's own configuration and no
        team-scoped path, which `_environment_declaration` and `Config`'s own refusal of that state
        are the pair that makes true.

        Raises
        ------
        ClusterNotConfiguredError
            If no layer declared a context, or a layer declared one with an empty value. The
            message names the environment, the file that should have held the key, and the value
            that was found there.
        ClusterContextNotFoundError
            If the declared context is not in the kubeconfig that was resolved.
        StatedClusterMismatchError
            If a layer above `static.json` stated a cluster and the environment's declaration
            contradicts it. This is asked *before* a kubeconfig is opened or a target
            is built, so the refusal is the comparison and not the file the declaration pointed at.
        ClusterIdentityCollisionError
            If another environment in this root already addresses this cluster. There is no
            override, so nothing here can be argued with — except by the rule that removes the
            cluster this process is standing on from the comparison rather than adding a flag.

        The last layer sits behind all three of the declared ones: when nothing above it named
        a context, a process that is running inside a cluster resolves itself from the material the
        kubelet mounted. That is the only ambient resolution this code permits, it is last, and it
        says so in the report line; a process that is not inside a cluster gets the error below
        rather than an in-cluster guess.
        """
        config, static_path, declared = _environment_declaration(environment, team,
                                                                 platform_config_dir)

        layers = cls._declaration_layers(context, kubeconfig, static_path, declared)
        # The stated-versus-declared comparison, and it happens before anything resolves. Both
        # sides of it are already
        # in `layers`: the two above `static.json` are the run's own statement about where the work
        # runs, and `static.json` is the clone's claim about which environment it is for. Reading a
        # file is not what is being decided here, which is why a dead `CLUSTER_KUBECONFIG_PATH` in
        # the declaration cannot turn this refusal into a file-not-found error on its way past.
        cls._check_stated_cluster(environment, layers, static_path)
        resolved_context = None
        resolved_path = None
        resolved_source = None
        resolved_label = None
        for layer_context, layer_path, layer_source, layer_labels in layers:
            if resolved_context is None and layer_context is not None:
                resolved_context = layer_context
                resolved_source = layer_source
                resolved_label = layer_labels["context"]
            if resolved_path is None and layer_path is not None:
                resolved_path = layer_path
        # Nothing above this named a file, so the tree gets its own chance before the machine's does:
        # an environment that carries a kubeconfig is read from that file, and `$KUBECONFIG` is not
        # consulted. A path somebody declared is a different matter and has already won above.
        if resolved_path is None:
            resolved_path = _kubeconfig.in_tree_kubeconfig(config.platform_config_dir, environment)

        if resolved_context is None:
            # Layer 5, and the only reason it is last: an environment that declared a context
            # above this already answered, and the pod's own credentials must not outvote a
            # declaration. Nothing else about this object changes when it is used.
            target, label, refused = cls._service_account_layer(environment)
            if target is None:
                raise cls._nothing_declared(environment, static_path, layers, refused)
            resolved_source = target.source
            resolved_label = label
        else:
            target = _target.ClusterTarget.from_kubeconfig(
                resolved_path, resolved_context, environment, source=resolved_source
            )
        # The root is read off the `Config` rather than re-derived, so a root this method was handed
        # and a root the environment variable says are the same one object here — which is the whole
        # point of threading it through the `Config` instead of around it.
        registry = EnvironmentRegistry(config.platform_config_dir)
        # The one 1:1 check, asked of the one primitive that owns it. What used to be here is the
        # filter this method wrote beside the method — same file, same rule, computed twice — and
        # the half it computed twice was the half the report line read, which is how a
        # docstring could promise agreement and the code deliver two answers.
        comparison = registry.collision_check(target)
        if comparison.collisions:
            raise cls._collision(target, comparison.collisions)

        return Context(
            environment=environment,
            team=team,
            config=config,
            target=target,
            resolution_report=(
                _comparison._report_line(target, resolved_label, comparison.others,
                                         self_address=comparison.self_address),
            ),
        )

    @classmethod
    def _service_account_layer(cls, environment) -> Tuple[Optional[_target.ClusterTarget], Optional[str], Optional[str]]:
        """The last layer as `(target, report label, reason it is not available)`.

        A refusal here is not an error: it is the fact that this process is not running inside a
        cluster, which the caller folds into the refusal's own sentence rather than letting it stand
        alone as
        the fifth way to fail. That ordering matters — the operator who typed `kmt install dev` on a
        laptop needs the file-and-key answer, not a lecture about service accounts that were never
        asked to exist.
        """
        try:
            target = _target.ClusterTarget.from_service_account(environment)
        except ClusterResolutionError as exc:
            return None, None, exc.reason
        return (
            target,
            f"{constants.SOURCE_SERVICE_ACCOUNT} {_kubeconfig.SERVICE_ACCOUNT_ROOT}",
            None,
        )

    @staticmethod
    def _declaration_layers(context, kubeconfig, static_path, declared):
        """The three *declared* layers, in order, as `(context, path, source, labels)`.

        The last layer is not in here, and it cannot be: it is a construction rather than a
        value somebody declared, so `Context.build` asks for it itself after these three come back
        silent. Everything else about the order is unchanged — a layer that reads the environment it
        is running in belongs behind the ones that read an operator's configuration, not among them.

        The source and the label come from the layer that declared the **context**, because the
        context is the claim the 1:1 check compares; a path declared lower down does not change what the
        environment declared about where it lives.

        `declared` is what the environment's own file says, read once by
        `_environment_declaration` — the same read `EnvironmentRegistry` makes, which is why a
        Context and a listing cannot report two different things about one `static.json`.
        """
        return (
            (
                Context._flag_value(context),
                Context._flag_value(kubeconfig),
                constants.SOURCE_FLAG,
                {"context": "flag --context", "kubeconfig": "flag --kubeconfig"},
            ),
            (
                Context._process_value(constants.CLUSTER_KUBE_CONTEXT_KEY),
                Context._process_value(constants.CLUSTER_KUBECONFIG_PATH_KEY),
                constants.SOURCE_ENV,
                {
                    "context": f"env {constants.CLUSTER_KUBE_CONTEXT_KEY}",
                    "kubeconfig": f"env {constants.CLUSTER_KUBECONFIG_PATH_KEY}",
                },
            ),
            (
                declared["context_value"],
                declared["path_value"],
                f"{constants.STATIC_SOURCE_PREFIX}{static_path}",
                {"context": str(static_path), "kubeconfig": str(static_path)},
            ),
        )

    @classmethod
    def _statement_layers(cls, layers) -> Dict[str, Optional[str]]:
        """The run's own claim on its cluster, read out of the layers above `static.json`.

        A statement is either half: the context the run names, or the kubeconfig file it names. The
        file half is a claim too, and the reason it is compared rather than ignored is the CI pod:
        the file says which contexts exist *where the work runs*, so an environment whose context is
        not among them is asking to be installed somewhere the run is not.

        Each half keeps the label of the layer that wrote it, because the refusal is unusable
        without it — "the statement and the declaration" is two sentences, and each needs the flag,
        the variable, or the file that is worth opening.
        """
        statement = {"context": None, "context_source": None,
                     "kubeconfig": None, "kubeconfig_source": None}
        for layer_context, layer_path, _source, labels in layers[:-1]:
            if statement["context"] is None and layer_context is not None:
                statement["context"] = layer_context
                statement["context_source"] = labels["context"]
            if statement["kubeconfig"] is None and layer_path is not None:
                statement["kubeconfig"] = layer_path
                statement["kubeconfig_source"] = labels["kubeconfig"]
        return statement

    @classmethod
    def _check_stated_cluster(cls, environment: str, layers, static_path: Path) -> None:
        """A statement above the file may answer for an environment, not contradict it.

        The ordering is untouched here, and that is the point of putting a comparison in
        front of it instead of editing it. Layer 1 still beats layer 2, layer 2 still beats layer 3,
        and a stated `--kubeconfig` is still the file that gets opened; what is no longer allowed is
        for a stated *name* to disagree with a declared one, or for a stated *file* to not contain
        the declared context. The two claims answer different questions, so neither of them gets to
        be discarded by precedence — kubementat's own precedence would silently install `dev`'s
        platform into the cluster the TaskRun happens to be standing on.

        Both halves run *before* `from_kubeconfig`, which is what makes the CI case legible: when
        the clone declares a machine-local `CLUSTER_KUBECONFIG_PATH` that no pod has, the operator
        gets the comparison — both claims, both sources — and not a stack of paths that were tried.

        A declaration that answers nothing makes the comparison moot: there is no claim to disagree
        with, which is the state "the environment does not yet address a cluster" is about, and the
        flags' whole
        reason to exist. And a run that states nothing states nothing: the file answers, as always.
        """
        stated = cls._statement_layers(layers)
        declared_context = layers[-1][0]
        declared_source = layers[-1][2]
        if declared_context is None or (stated["context"] is None and stated["kubeconfig"] is None):
            return

        if stated["context"] is not None:
            if stated["context"] == declared_context:
                return
            raise cls._stated_cluster_mismatch(
                environment,
                stated=f"{stated['context']} (stated by {stated['context_source']})",
                declared=f"{declared_context} (declared in {static_path} as {constants.CLUSTER_KUBE_CONTEXT_KEY})",
                declared_source=declared_source,
                fix=(f"state {declared_context}, which is what the environment says, or write "
                     f"{stated['context']} into the environment: kmt config set <configuration-root> "
                     f"{environment} {constants.CLUSTER_KUBE_CONTEXT_KEY}={stated['context']}"),
            )

        # Only the file was stated. Its claim about the cluster is the set of contexts it declares,
        # so the declared context has to be one of them — the same test reading the file would make,
        # made here so the two claims are named instead of one file being blamed for the other.
        declared_files = _kubeconfig._kubeconfig_files(stated["kubeconfig"])
        available = _kubeconfig._read_kubeconfig(declared_files)["contexts"]
        if declared_context in available:
            return
        listing = ", ".join(sorted(available)) if available else "it declares none"
        raise cls._stated_cluster_mismatch(
            environment,
            stated=(f"the kubeconfig {stated['kubeconfig']} (stated by "
                    f"{stated['kubeconfig_source']}), whose contexts are: {listing}"),
            declared=f"{declared_context} (declared in {static_path} as {constants.CLUSTER_KUBE_CONTEXT_KEY})",
            declared_source=declared_source,
            fix=(f"state a context this file has, mount the kubeconfig that has {declared_context}, "
                 f"or write the run's context into the environment: kmt config set "
                 f"<configuration-root> {environment} {constants.CLUSTER_KUBE_CONTEXT_KEY}=<the-context>"),
        )

    @staticmethod
    def _stated_cluster_mismatch(environment: str, *, stated: str, declared: str,
                                 declared_source: str, fix: str) -> StatedClusterMismatchError:
        """The sentence a stated-versus-declared disagreement deserves, spelled once for both
        shapes of it.

        Four facts and no more: the two claims, the two sources that wrote them, and the fact that
        they mean different things. The reason a run that states one cluster while the environment
        declares another is not simply resolved to the stated one is written into the sentence
        rather than left to the ordering, because the ordering is what a reader would otherwise
        quote back as an argument.
        """
        return StatedClusterMismatchError(
            f"environment '{environment}' states its cluster two ways and they disagree: the run "
            f"says {stated}; the environment says {declared}. What the run states is where the work "
            f"runs; what the configuration declares is which environment that work is for. "
            f"kubementat will not choose between them. Until {declared_source} and the statement "
            f"agree: {fix}.",
            reason=f"stated and declared cluster disagree for environment '{environment}'",
            stated=stated,
            declared=declared,
        )

    @staticmethod
    def _process_value(key: str) -> Optional[str]:
        """A layer-2 variable, treated the way every other variable in the tree is treated.

        Set-but-empty is not "unset": an empty `CLUSTER_KUBE_CONTEXT` would otherwise fall back
        to a file and then be blamed for the file. `paths.py:_directory_from_environment` refuses
        the same thing, and this is the same refusal.
        """
        if key not in os.environ:
            return None
        value = os.environ[key]
        if not value.strip():
            raise ClusterNotConfiguredError(
                f"{key} is set but empty. Give it the context it names, or unset it to read "
                f"{constants.CLUSTER_KUBE_CONTEXT_KEY} from the environment's static.json.",
                reason=f"{key} is set but empty in the process environment",
            )
        return value.strip()

    @staticmethod
    def _flag_value(value) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _nothing_declared(environment: str, static_path: Path, layers,
                          in_cluster: Optional[str] = None) -> ClusterResolutionError:
        """The refusal an operator acts on now that the current-context fallback is gone.

        The environment, the file with a path, the key, and the value that was found in it. The
        sentence also says what to do, because an operator who got here by typing `kmt install
        dev` is not reading a stack trace by choice.

        `in_cluster` is the reason the last layer could not answer, when there was a last layer to
        ask. It is a clause and not a headline: the failure the operator can act on is still the
        unset key, and a sentence that made "am I in a pod?" the main verb would be a second answer
        to a question one file already answers.
        """
        _, _, _, labels = layers[-1]
        state = "is not set"
        try:
            state = _static_declaration(static_path)["context_state"]
        except ClusterResolutionError:
            pass
        sentence = (
            f"environment '{environment}' does not address a cluster: "
            f"{constants.CLUSTER_KUBE_CONTEXT_KEY} {state} in {static_path}. There is no current-context "
            f"fallback — kubementat installs into what the environment declares, not into what "
            f"kubectl is pointed at. Name the context in {static_path}, pass --context, or set "
            f"{constants.CLUSTER_KUBE_CONTEXT_KEY}."
        )
        if in_cluster:
            sentence += (
                f" In-cluster resolution was considered last, and this process could not use it: "
                f"{in_cluster}."
            )
        return ClusterNotConfiguredError(
            sentence,
            reason=f"{constants.CLUSTER_KUBE_CONTEXT_KEY} {state} in {static_path}",
        )

    @staticmethod
    def _collision(target: _target.ClusterTarget, collisions) -> ClusterIdentityCollisionError:
        """The collision refusal: both environments, both addresses, and where to see the whole
        picture.

        The addresses are equal — that is what the check found — but they are printed per
        environment rather than once, because "equal" is what the operator needs to see for
        themselves when the two declarations differ only in how they spell one host.
        """
        claimed = "; ".join(
            [f"'{target.environment}' is {target.address}"]
            + [f"'{record.environment}' is {record.target.address}" for record in collisions]
        )
        names = ", ".join(f"'{record.environment}'" for record in collisions)
        return ClusterIdentityCollisionError(
            f"environment '{target.environment}' and {names} address the same cluster "
            f"({claimed}). Two environments must not address one cluster: kubementat will not "
            f"let a command decide which of them it meant. Run kmt environments list to see "
            f"which environment claims which cluster, then point one of them somewhere else."
        )
