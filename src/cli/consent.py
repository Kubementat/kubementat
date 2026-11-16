"""The CLI half of consent: whoever owns the terminal owns the question.

The library asks nothing now (see `kubementat/consent.py`). Every question, every accepted answer
and every refusal a consent path can print is written here, because this is the layer that can see
a terminal and that knows which flag would have answered the question. Two things follow, and both
are deliberate rather than convenient:

* **The words do not change.** The sentences below are the ones the library printed. Moving a
  question is not rewriting one: the accepted answers in particular (`yes`/`y`, `no`/`n`, and the
  repeated "Please answer yes or no." in between) are part of what the operator agreed to years
  ago, and an answer that used to be accepted and now is not would be a silent behaviour change
  wearing a refactoring's face.
* **The refusal belongs to the command, not to the library.** `kmt install`, `kmt uninstall` and
  `kmt users create-team-account` each look at stdin *at their own door*, so a run that cannot be
  asked is refused before it does any work — before the dependency probes, before the plan is read,
  before the first cluster request. `kmt routing install --update` is the exception written down
  below, and it is an exception for a reason that cannot be argued away.

The three shapes of "no terminal" are three shapes and not one, because that is what the surface
already had. `uninstall` and `users` raise Click's `UsageError`, which is how they have always
exited. `install` says it in three `logging.error` lines, which is how it said it from inside the
library. A refactor that unified them would have changed the exit code of commands that were only
being moved.
"""

import logging
import sys

import click

from kubementat import errors
from kubementat.consent import Consent


def ask(prompt: str, decline, *, strip: bool = True) -> None:
    """Ask until the answer is a yes or a no.

    `decline` is what an answer of no does, and it is an argument because the four commands that
    ask do not agree about it: three raise the same `ConfirmationDeclinedError` with their own
    sentence, and `kmt install` still prints the line its bash printed and exits 1. Reaching the
    end means yes.

    `strip` keeps each command's own reading of an answer. The uninstall, routing and users prompts
    trimmed whitespace; `kmt install` never did, so `" yes "` was not an answer there. Preserving
    that is not nostalgia — it is the difference between "the same question, asked elsewhere" and
    "a question that silently started accepting new things".
    """
    while True:
        answer = input(prompt)
        if strip:
            answer = answer.strip()
        answer = answer.lower()
        if answer in ("y", "yes"):
            return
        if answer in ("n", "no"):
            decline()
        logging.info("Please answer yes or no.")


def declined(text: str):
    """A refusal that is a decision and not a bug: one line, and nothing was written."""
    def decline():
        raise errors.ConfirmationDeclinedError(text)
    return decline


def install_consent(environment: str, team: str, yes: bool) -> Consent:
    """The consent for `kmt install <environment> <team>`.

    `--yes` answers it. Without it, a run with no terminal to ask in is refused here — the same
    three lines the library printed, from the same place in the output order, one dependency probe
    earlier. That earliness is the item's only observable change and it is written up in the
    CHANGELOG: a run that cannot be asked should find that out before it spends ten subprocesses
    finding out.
    """
    if yes:
        return Consent.assumed()
    if not sys.stdin.isatty():
        logging.error("install requires confirmation, but stdin is not a terminal.")
        logging.error("Re-run with --yes (or -y) to install kubementat without prompting:")
        logging.error(f"  kmt install {environment} {team} --yes")
        sys.exit(1)
    return Consent.from_flag(False, lambda _action: ask(
        "Do you really wish to install kubementat on this cluster? (yes/no): ",
        decline=lambda: (logging.info("Cancelled install script."), sys.exit(1)),
        strip=False))


def uninstall_consent(environment: str, yes: bool) -> Consent:
    """The consent for `kmt uninstall <environment>`.

    The no-terminal guard is the command's own, unchanged: it is the reason a lane or a cron entry
    could never hold this command open on a script that read stdin and answered itself EOF. What
    was in the library was the question, and the question is here now.
    """
    if yes:
        return Consent.assumed()
    if not sys.stdin.isatty():
        raise click.UsageError(
            "uninstall requires confirmation, but stdin is not a terminal. "
            "Re-run with --yes (-y) to uninstall kubementat without prompting.")
    return Consent.from_flag(False, lambda _action: ask(
        "Do you really wish to uninstall all kubementat resources? ",
        decline=declined("Cancelled uninstall script. Nothing was deleted and the cluster is"
                         " untouched; re-run with --yes (or -y), or answer yes, to go ahead.")))


def routing_consent(environment: str, yes: bool) -> Consent:
    """The consent for `kmt routing install <environment> --update`.

    This one cannot look at stdin at the door. The question only arises when the cluster turns out
    to already run a Traefik kubementat did not install, and the only way to know that is to read
    the cluster — which the command is about to do once, and only once, for the status report the
    same run prints. So the answer cannot be brought forward; the *way to ask* is handed down
    instead, and the library decides at the moment the branch is reached whether it needs it.

    `environment` is what the message names twice: the command that shows what is there without
    writing anything, and `kmt routing status <environment>`, which is the read that answers the
    question before the operator has to.
    """
    if yes:
        return Consent.assumed()

    def ask_the_operator(_action: str) -> None:
        if not sys.stdin.isatty():
            raise errors.ConfirmationUnavailableError(
                f"Installing a second Traefik next to the one already running needs confirmation,"
                f" but stdin is not a terminal. Re-run with --yes (or -y) to accept the"
                f" second-controller warning, or run kmt routing install {environment} --update"
                f" alone to see what is there: kmt routing status {environment}.")
        ask(f"A Traefik is already running here. Install a second controller, class and"
            f" status writer alongside it? (yes/no): ",
            decline=declined("Cancelled. Nothing was applied and the controller that is running"
                             " was left alone; re-run with --yes (or -y), or answer yes, to go"
                             " ahead."))

    return Consent.from_flag(False, ask_the_operator)


def team_account_consent(yes: bool) -> Consent:
    """The consent for the three team-account commands, which differ only in the sentence.

    The action is a parameter and not text fixed here, because three commands call one gate and the
    sentence is the one thing that must say which of the three it is. The bash copy this replaced
    could not say, and that is why the same vague question used to be asked of three different
    writes.
    """
    if yes:
        return Consent.assumed()

    def ask_the_operator(action: str) -> None:
        if not sys.stdin.isatty():
            raise errors.ConfirmationUnavailableError(
                f"Confirmation required, but stdin is not a terminal. Re-run with --yes for: {action}"
            )
        ask(f"Do you really wish to {action}? ", decline=declined("Cancelled."))

    return Consent.from_flag(False, ask_the_operator)
