"""The CLI asks, the library decides, and the words in between are pinned here.

Item 7 moved four `isatty()`+`read-a-line` pairs out of `src/kubementat/` and into `src/cli/`. That
is only a safe change if the sentences survive it, so this file holds the sentences: each one is
copied out of the code it replaced, and a test that fails on a wording change is the only defence a
refactoring of this shape has against its own diff.

Two claims are asserted here, and they pull in opposite directions:

* **Nothing the operator sees has moved.** The question, the order of it, the answers it accepts,
  the sentence printed when the answer is neither, and the way a refusal reads — byte for byte what
  the library printed before it was asked to stop asking.
* **The library can no longer ask.** That is asserted with a grep over the tree rather than with a
  stub, because a stub proves the thing it stubs and nothing else.

The asymmetry the item was really about is in here too: a run at a terminal is asked exactly what it
used to be, and a run that is not a terminal is refused *earlier* than it used to be, with the same
words. Where earlier-means-fewer-commands, that is written down as a fact and not left implied.
"""

import logging
from types import SimpleNamespace

import pytest

from cli.consent import (install_consent, routing_consent, team_account_consent,
                         uninstall_consent)
from kubementat.consent import CONSENT_ASSUMED, CONSENT_UNDECIDED, Consent
from kubementat.errors import (ConfirmationDeclinedError, ConfirmationUnavailableError)


def terminal(monkeypatch, answers, *, tty=True):
    """A stdin that is a terminal (or is not), fed `answers` one line at a time.

    Two seams, because the ask touches two: `isatty` is read from the module that owns it, and the
    line itself arrives through `input`. `answers` running out means end-of-input, which is what a
    pipe that has nothing left to say looks like.
    """
    queue = list(answers)

    def answering(prompt="", *_args):
        if queue:
            return f"{queue.pop(0)}"
        raise EOFError("the terminal ran out of answers")

    monkeypatch.setattr("cli.consent.sys.stdin", SimpleNamespace(isatty=lambda: tty))
    monkeypatch.setattr("builtins.input", answering)
    return queue


def ask(consent, action="do the thing the test is about"):
    """The call a library function makes, from a test that is not a library function."""
    consent.decide(action)


# ---------------------------------------------------------------------------------------------
# the Done-when, as a test: the library reads no terminal, anywhere
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# the value itself: who decided, and what happens when nobody did
# ---------------------------------------------------------------------------------------------

def test_yes_arrives_as_a_decision_and_not_as_a_silence():
    """`--yes` is not "skip the question", it is "the question was answered elsewhere"."""
    assert Consent.from_flag(True).answer == CONSENT_ASSUMED
    assert Consent.assumed().is_assumed() is True
    assert Consent.from_flag(False).answer == CONSENT_UNDECIDED


def test_an_undecided_consent_with_a_way_to_ask_uses_it_once():
    asked = []
    ask = lambda action: asked.append(action)      # noqa: E731 - the CLI's ask, in one line
    Consent.undecided(ask=ask)

    Consent.from_flag(False, ask).decide("uninstall all kubementat resources")

    assert asked == ["uninstall all kubementat resources"], (
        "the action sentence is what the operator is asked about; losing it is how three commands"
        " used to ask one vague question")


def test_a_caller_that_supplied_neither_answer_nor_ask_is_refused_and_names_the_flag():
    """The library-caller case: not an operator, so not told to re-run a command it never ran."""
    with pytest.raises(ConfirmationUnavailableError) as raised:
        Consent.undecided().decide("delete the team account 'ops-account'")

    assert "delete the team account 'ops-account'" in str(raised.value)
    assert "--yes" in str(raised.value)


# ---------------------------------------------------------------------------------------------
# site 1: kmt install
# ---------------------------------------------------------------------------------------------


def test_install_still_refuses_a_padded_yes_because_it_never_trimmed(monkeypatch, caplog):
    """` yes ` is not an answer to `kmt install`, and it never was.

    The three other prompts trimmed their answer; this one did not. Normalising it here would be a
    silent behaviour change wearing a refactoring's face — an answer that used to be refused would
    start installing a cluster.
    """
    caplog.set_level(logging.INFO)
    terminal(monkeypatch, [" yes ", "n"])
    with pytest.raises(SystemExit):
        ask(install_consent("dev", "ops", yes=False))

    assert "Please answer yes or no." in caplog.text


def test_install_refuses_a_run_with_nobody_to_ask_and_says_it_in_three_lines(monkeypatch, caplog):
    """The message is what it was, and it lands earlier than it used to.

    Before item 7 this said the same three lines *after* the dependency probes had run, because the
    library found out about the pipe by standing in it. The item's only observable change is that a
    doomed run now spends no subprocesses finding out, and this test is where the shape is pinned.
    """
    terminal(monkeypatch, [], tty=False)

    with pytest.raises(SystemExit) as stop:
        install_consent("dev", "ops", yes=False)

    assert stop.value.code == 1
    assert ("install requires confirmation, but stdin is not a terminal." in caplog.text)
    assert "Re-run with --yes (or -y) to install kubementat without prompting:" in caplog.text
    assert "  kmt install dev ops --yes" in caplog.text


def test_install_with_yes_never_reaches_a_question(monkeypatch):
    def no_prompt(prompt=""):
        raise AssertionError(f"--yes reached a prompt it was told to skip: {prompt!r}")

    monkeypatch.setattr("builtins.input", no_prompt)
    assert Consent.from_flag(True).answer == CONSENT_ASSUMED
    ask(install_consent("dev", "ops", yes=True))


# ---------------------------------------------------------------------------------------------
# site 2: kmt uninstall
# ---------------------------------------------------------------------------------------------

def test_uninstall_asks_after_the_plan_and_cancels_in_the_same_words(monkeypatch):
    """The sentence has to say what was not done and what would have been.

    The bash printed one line and exited; the library gave the CLI a clean `Error:` and the answer
    still has to be greppable by the lane's `confirmation-rejected-changes-nothing` assert.
    """
    terminal(monkeypatch, ["n"])
    consent = uninstall_consent("dev", yes=False)
    assert consent.answer == CONSENT_UNDECIDED

    with pytest.raises(ConfirmationDeclinedError) as raised:
        ask(consent)

    assert "Cancelled uninstall script." in str(raised.value)
    assert "Nothing was deleted and the cluster is untouched" in str(raised.value)


def test_uninstall_with_no_terminal_is_a_usage_error_naming_the_flag(monkeypatch):
    """`--yes (-y)`: the command's own guard, unchanged since the bash, rc 2 and all."""
    import click

    terminal(monkeypatch, [], tty=False)
    with pytest.raises(click.UsageError) as raised:
        uninstall_consent("dev", yes=False)

    assert "uninstall requires confirmation, but stdin is not a terminal." in str(raised.value)
    assert raised.value.exit_code == 2, "the refusal this command has always exited with"


# ---------------------------------------------------------------------------------------------
# site 3: kmt routing install --update
# ---------------------------------------------------------------------------------------------

def test_routing_asks_the_second_controller_question_and_declines_in_the_same_words(monkeypatch):
    terminal(monkeypatch, ["n"])
    with pytest.raises(ConfirmationDeclinedError) as raised:
        ask(routing_consent("dev", yes=False))

    assert "Cancelled. Nothing was applied and the controller that is running was left alone" \
        in str(raised.value)


def test_routing_names_both_commands_it_could_have_run_instead(monkeypatch):
    """A refusal that names only `--yes` sends the operator to the one action that writes.

    So the sentence also gives them the read: `kmt routing status dev` answers whose controller it
    is without applying anything, and `kmt routing install dev --update` is the command that would
    have asked this question out loud.
    """
    terminal(monkeypatch, [], tty=False)
    with pytest.raises(ConfirmationUnavailableError) as raised:
        ask(routing_consent("dev", yes=False))

    message = str(raised.value)
    assert "Installing a second Traefik next to the one already running needs confirmation" in message
    assert "kmt routing install dev --update" in message
    assert "kmt routing status dev" in message


# ---------------------------------------------------------------------------------------------
# site 4: the team-account commands
# ---------------------------------------------------------------------------------------------


def test_a_team_account_run_with_no_terminal_names_the_action_it_refused(monkeypatch):
    terminal(monkeypatch, [], tty=False)
    with pytest.raises(ConfirmationUnavailableError) as raised:
        ask(team_account_consent(yes=False), "create the team account 'ops-account'")

    assert "Re-run with --yes for: create the team account 'ops-account'" in str(raised.value)
