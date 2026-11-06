"""The 1:1 check's two primitives: what cluster this process is standing on, and whether a claim is it.

Two names, and the line that reports their answer. They are the check once you take away the root it
reads, the environments it enumerates, and the object that records the comparison: a probe of the
material the kubelet mounted, the address equality both halves of the answer must agree on, and the
sentence that says *which* sibling was held back and why. Nothing here asks for a `Config`, an
environment name, or a path.

The order the file stands in is the whole of its legality. It reads `target.py` and `kubeconfig.py`
below it and no module above it — `kubementat.cluster` least of all, which is the point of installing
it before `registry.py` and `context.py` rather than beside them. `kubeconfig.py` is the leaf, the one
line here that touches a cluster at all is a call down into it, and every edge added by this commit
therefore points down.

Two shapes are load-bearing and do not follow from the rest of the file:

* **the running cluster is subtracted, never chosen.** `running_cluster_address` answers the in-cluster
  case only long enough to say that a target which resolves to that address is *here* rather than a
  second claim. No layer of resolution consults it as a candidate, a command that addresses no cluster
  still gets the error that names the file and the key, and a check that compared a target against
  itself would refuse kubementat's own install. That refusal is the only reason the probe exists.
* **the equality is spelled once, deliberately.** `_addresses_the_running_cluster` is the test the
  refusing check and the report line share, and the line may not say *verified* about a comparison
  that held itself back. One caller used to write the filter beside the check and probe the running
  cluster again for its own sentence — two spellings of one rule, which is how a refusal and the
  sentence about it start to disagree.

What this module deliberately does not hold is `ClusterComparison`, the record of what the check
compared. The plan places it in `registry.py`, beside the `collision_check` that builds it and the
`EnvironmentRegistry` that enumerates what it lists, and that is a real split of one idea across two
files: a reader who expects the comparison *object* beside the comparison *primitive* will look in the
wrong file. The confusion is the price of keeping one edge between them — the object needs the
registry's enumeration and the primitive needs nothing, so the direction is `registry -> comparison`
and never both.
"""

from __future__ import annotations

from typing import Optional

# `ClusterTarget` is bound as the module and not the name, for the reason every importer of it has:
# a name import here would answer `kubementat.core.identity.comparison.ClusterTarget` as well, and a
# second door to one frozen class is what a `monkeypatch.setattr` on the class object misses.
from kubementat.core.identity import kubeconfig as _kubeconfig
from kubementat.core.identity import target as _target
# The same shape, for a different reason: the call below is verbatim out of `cluster.py`, and the
# module owns both the function called and the `SERVICE_ACCOUNT_ROOT` four tests patch. Reaching it by
# name would be a second resolvable name for one function, and the patch would land on the copy.
from kubementat.errors import ServiceAccountNotMountedError


def running_cluster_address() -> Optional[str]:
    """This process's own cluster address, or `None` when it is not inside one.

    The question is asked for one reason: in-cluster is the only resolution path
    where the target is the cluster the code is running on, and a 1:1 check that compares against
    *itself* would refuse kubementat's own install. The answer is used only to *subtract*, never to
    choose — no resolution path here consults it as a candidate, and a command that addresses no
    cluster still gets the error that names the file and the key rather than this one.

    `None` is the normal answer everywhere except inside a pod, and the probe is the two file stats
    and two variable reads that keep the check cheap on reads.
    """
    try:
        return _kubeconfig._service_account_address()
    except ServiceAccountNotMountedError:
        return None


def _addresses_the_running_cluster(record, self_address: Optional[str]) -> bool:
    """Whether `record` is the cluster this command is already standing on.

    Spelled once because both halves of the answer have to agree: the comparison that may refuse,
    and the report line that has to say it was held back. An address equality is the only test —
    the identity being compared is the server string, and the running cluster is identified that
    way.
    """
    return bool(self_address) and record.target is not None and record.target.address == self_address


def _report_line(target: _target.ClusterTarget, label: str, others, *,
                 self_address: Optional[str] = None) -> str:
    """One of the resolution report's two shapes, and only ever one of them.

    `others` is every other environment in the root, the unresolved ones included — the line has
    to be able to say who was compared and who could not be.

    *verified* is said about the environments that were actually compared, so the number is in
    the line: "verified against 1 other environment" says what was checked and against how many.
    An environment that could not be read is named with the reason instead, and the word
    *verified* does not appear, because the comparison the line would be claiming did not happen.

    `self_address` is the running cluster's exclusion made visible. An environment that resolves to
    the cluster
    this command is standing on is not a second claim, so it is not compared — and the line may
    not say *verified* about a comparison that held itself back, which is why the exclusion is
    reported as *not verified: <reason>* and never as a silent subtraction. A command
    that is not in a cluster passes `None` here and gets exactly the line it got before.
    """
    header = f"cluster {target.environment}: {target.address} ({label})"
    unresolved = tuple(record for record in others if record.target is None)
    held_back = tuple(
        record for record in others
        if record.target is not None and _addresses_the_running_cluster(record, self_address)
    )
    if unresolved or held_back:
        notes = [
            f"{record.environment} could not be resolved ({record.reason})" for record in unresolved
        ]
        notes += [
            f"{record.environment} is the cluster this command is running on ({record.target.address})"
            for record in held_back
        ]
        return f"{header} — not verified: {'; '.join(notes)}"
    compared = tuple(record for record in others if record.target is not None)
    if not compared:
        return f"{header} — not verified: no other environment in this root declares a cluster"
    plural = "environment" if len(compared) == 1 else "environments"
    return f"{header} — verified against {len(compared)} other {plural}"
