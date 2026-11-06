"""What kubementat refuses with.

A refusal is an object and not a stack trace, so the shape of what a command raises is product: the
message that names what is missing and says what to do about it is what an operator reads, and a bare
traceback is what they do not. This module is the one place that answers
"kubementat can refuse me with what" without opening the thirty-odd modules that raise things.

An error's own prose — the reason the refusal exists and the question it answers — belongs to the
error and travels with it here. A class that only restates the name is a comment nobody reads twice,
and the modules that raise these objects still say, at the raise, what that particular call site
found wrong.

`KubementatError` is what every domain error descends from, so that one catch can mean "a kubementat
refusal" instead of meaning one class at a time. It carries no behaviour on purpose: no message
convention, no exit code, no payload. A base class that carried behaviour would let a subclass that
forgot to carry it work by accident, and an accident with two owners is a bug nobody can find.
"""

from typing import Optional


class KubementatError(Exception):
    """A refusal kubementat means, as opposed to a Python error that escaped.

    Nothing more than the name, which is the whole job: a caller that wants to turn any domain
    refusal into a single clean answer needs one type to catch and not a list of them.
    """


class TeamNotSuppliedError(KubementatError):
    '''
    This work needs a team's configuration, and the run did not name one.

    Not a resolution failure: the cluster resolved, and a `Context` built without a team still
    carries it. What is missing is the other half of the pair — `platform_config/{environment}/<team>/`
    — and the rule is that kmt does not guess it, so the object says so instead of letting a
    service read a path built out of a name nobody gave it. Two calls raise it, for the same
    reason: `Config` when a team-scoped value is read from a `Config` that has no team, and
    `kubementat.cluster.Context.require_team` when a service asks a Context for the half it was
    not given. One question, one sentence, and it lives here rather than in the module that builds
    the path the refusal is about, because that refusal outlives where the path happens to be
    assembled: the cluster layer asks it too, and neither module owns an answer that belongs to the
    pair.
    '''


# ---------------------------------------------------------------------------
# The cluster refusals.
#
# These seven answered "which cluster does this command mean" from inside
# `kubementat.cluster`. They travel here because that question is asked from the CLI, the check,
# and every service that touches a kubeconfig, and because the two the operator can feel —
# `ClusterResolutionError` and `ClusterIdentityCollisionError` — are converted into one `Error:`
# line and exit 1 by `cli/context.py`, which is the only place that decides how they are printed.
# The rest of the group is what those two wrap or refuse before a socket opens.
#
# The shape is deliberate: `ClusterResolutionError` is the middle base the five resolution failures
# descend from, so `except ClusterResolutionError` still means "no cluster was named" and nothing
# wider; `ClusterIdentityCollisionError` hangs off `KubementatError` beside it, because a caller
# that carries on past a resolution failure must not carry on past a collision.
# ---------------------------------------------------------------------------


class ClusterResolutionError(KubementatError):
    """The cluster a command asked for could not be named.

    Carries a `reason` as well as the message: the message is the text an operator acts on, which
    names the environment, and the reason is the same fact without the environment's name,
    which is what a report line that already named it needs.
    """

    def __init__(self, message: str, reason: Optional[str] = None):
        super().__init__(message)
        self.reason = reason or message


class ClusterNotConfiguredError(ClusterResolutionError):
    """Nothing in the resolution order declared this environment's cluster, and no fallback named one."""


class ClusterContextNotFoundError(ClusterResolutionError):
    """The declared context is not in the kubeconfig that was resolved.

    The exception is where the available contexts are listed, because that list is the answer to
    the mistake that raised it — `prod-eu` typed for `prod-europe`.
    """

    def __init__(self, message, reason=None, available=()):
        super().__init__(message, reason)
        self.available = tuple(available)


class ClusterFileUnreadableError(ClusterResolutionError):
    """A file the resolution was told to read is missing, unparseable, or says nothing.

    This is the failure class the 1:1 check reports as *not verified*: an environment refused
    because of a file that cannot be read is an environment that is reported, not dropped.
    """


class ServiceAccountNotMountedError(ClusterResolutionError):
    """In-cluster resolution was asked for where no service account is mounted.

    It is a `ClusterResolutionError`, not an `OSError`, for a reason worth stating: the mounted
    material is part of resolving which cluster a command means, and the fact that failed is a fact about
    *configuration* — this process is not running inside a cluster — not about a path somebody
    typed wrong. `FileNotFoundError` from the kubelet's own directory is the same sentence with no
    verb in it, and it names a file the operator cannot create.

    Its `reason` is what the last layer folds into the not-configured error when nothing above it
    declared a context either, so a refusal never claims a cluster exists and then dies on the read.
    """


class StatedClusterMismatchError(ClusterResolutionError):
    """What a run states about its cluster and what the environment declares are two claims.

    A Tekton pod answers "which cluster" twice. The cloned configuration declares one answer in
    `static.json`, and the TaskRun states another through the flags and variables above the file,
    and a precedence rule alone would take the statement and never look at the declaration. The two
    claims answer different questions: the file's claim is about **which environment** the work is
    for, the run's claim is about **where the work runs**, and when the two disagree kubementat is
    not the thing that gets to decide.

    It is a `ClusterResolutionError` and not a sibling of `ClusterIdentityCollisionError` for one
    reason: the collision is found *after* a cluster resolved, while this refusal is raised instead
    of resolving. Nothing above the file gets to be a hint, so the run that is refused resolved no
    cluster and touched no kubeconfig.

    The message carries both claims and the source that wrote each one, because the mistake is
    always in one of them and the reader has to be able to tell which. There is no override flag:
    a dial that lets a pipeline walk past this is a dial that lets it install one environment's
    platform into another environment's cluster.
    """

    def __init__(self, message: str, reason: Optional[str] = None, *, stated: str = "",
                 declared: str = ""):
        super().__init__(message, reason)
        self.stated = stated
        self.declared = declared


class ClusterIdentityCollisionError(KubementatError):
    """Two environments address the same cluster, which v2 refuses rather than accepts.

    It is not a `ClusterResolutionError`: both environments resolved perfectly well, and a
    caller that catches resolution failures and carries on would carry on into a cluster that
    belongs to someone else. The refusal is given no override flag, so there is nothing here to
    pass.
    """
