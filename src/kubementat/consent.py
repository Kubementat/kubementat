"""Consent, as a value the caller hands down instead of a question the library asks.

Four library functions used to read stdin: `install/kubementat.py` asked before it touched a
cluster, `uninstall/kubementat.py` asked after it had printed the deletion plan,
`routing/routing.py` asked before it put a second Traefik beside a running one, and
`infrastructure/user_management.py` asked before it edited a team's accounts. Each pair was a check
of the terminal and then a read of a line, so the library owned a terminal it could not see: a run
that had no terminal blocked inside a read it could not answer, and a run that had one was asked by
whichever code happened to be standing there rather than by the command that offered the flag.

Both halves of that move here, and they move to different places. The *decision* is a parameter, so
the caller says what it decided. The *asking* is the caller's job too, and it travels as a callable
the CLI builds. It has to travel that way: the routing question only arises when the cluster turns
out to already run someone else's Traefik, so a command that must not read the cluster twice cannot
bring the answer forward, and it hands the library the way to ask instead.

The rule that survives the move is the fail-closed one, unchanged:

* `--yes` answers the question, and says so.
* A terminal is asked, and the question is only settled when it says yes or no.
* Nobody decided, so nothing gets written. That is the case a library caller is in when it passes
  no consent, and it is the only case whose sentence is new: the caller of a library is not an
  operator standing at a terminal, so it is told what it did not supply instead of being told to
  re-run a command it never ran.

Nothing here opens stdin, asks whether there is a terminal, or imports Click — which is the point of
the file, and the claim the item's Done-when greps for.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from kubementat import errors

# The three states a consent can be in. `undecided` is not a soft no and never was: it is the state
# of a run nobody asked about.
CONSENT_ASSUMED = "assumed"
CONSENT_ANSWERED = "answered"
CONSENT_UNDECIDED = "undecided"


@dataclass(frozen=True)
class Consent:
    """What the caller decided about a write, and how to ask if it has not decided.

    `answer` is one of the three constants above. `ask` is the caller's way of putting the question:
    it takes the action as a sentence, returns when the answer is yes, and raises when it is not —
    `ConfirmationDeclinedError` for a human who said no,
    `ConfirmationUnavailableError` for a run with no terminal to ask.
    """

    answer: str = CONSENT_UNDECIDED
    ask: Optional[Callable[[str], None]] = None

    @classmethod
    def from_flag(cls, yes: bool, ask: Optional[Callable[[str], None]] = None) -> "Consent":
        """What a command carrying a `--yes` flag hands down.

        The flag answers the question; anything else leaves it open and takes the way to ask it with
        the call. This is the whole mapping from the operator's flag to the library's parameter, and
        it is the only place that mapping is written.
        """
        if yes:
            return cls(CONSENT_ASSUMED)
        return cls(CONSENT_UNDECIDED, ask)

    @classmethod
    def assumed(cls) -> "Consent":
        """Decided without a flag: for a caller that is not a command line."""
        return cls(CONSENT_ASSUMED)

    @classmethod
    def answered(cls) -> "Consent":
        """Asked on the caller's own terminal and told yes, before the library was entered."""
        return cls(CONSENT_ANSWERED)

    @classmethod
    def undecided(cls, ask=None) -> "Consent":
        """Nobody has decided. `ask` is the way the caller can ask, when it has one.

        Passing it without an `ask` is what a library caller that wants to be refused should pass,
        and it is the default of every library function this item moved: a run that neither decided
        nor carried a terminal is a run that was never told to write.
        """
        return cls(CONSENT_UNDECIDED, ask)

    def is_assumed(self) -> bool:
        """True when `--yes` answered this, which is the case that gets a sentence about it."""
        return self.answer == CONSENT_ASSUMED

    def decide(self, action: str) -> None:
        """Return when this run may carry out `action`; raise when it may not.

        `action` is a sentence the caller can act on, not a key: it is what a refusal quotes back,
        and it is what the CLI interpolates into the question when the question is one of the
        "do you really wish to …" shape. A caller that builds the question itself — Traefik's
        second-controller warning is not a yes/no about an action — ignores it.

        Raises:
        - kubementat.errors.ConfirmationDeclinedError: the caller's ask got a no.
        - kubementat.errors.ConfirmationUnavailableError: no answer, and no way to get one.
        """
        if self.answer != CONSENT_UNDECIDED:
            return
        if self.ask is not None:
            self.ask(action)
            return
        raise errors.ConfirmationUnavailableError(
            f"{action} needs confirmation before anything is written, and the caller supplied none:"
            " no decision, and no way to ask. Run it through kmt with --yes (or -y), or pass a"
            " consent the caller has already taken.")
