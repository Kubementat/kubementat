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
