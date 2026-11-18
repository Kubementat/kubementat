"""`kmt`'s help output, read from the real entry point.

The commands are attached inside `main()`, so the surface that ships is only visible after it runs.
`--help` ends the invocation before any command body executes, which makes this safe in a suite that
has no cluster.
"""

import contextlib
import io
import re
import sys

import pytest

from cli import kmt as kmt_module


def run_main(monkeypatch, argv):
    """Run `main()` with *argv* and return every `(name, callback, command)` it registered."""
    registrations = []
    original = kmt_module.cli.add_command

    def record(command, name=None):
        registrations.append((command.name, command.callback, command))
        return original(command, name)

    monkeypatch.setattr(kmt_module.cli, "add_command", record)
    # `main()` configures logging for the process; a test that only reads the surface must not
    # leave a root handler behind for every other test in the run.
    monkeypatch.setattr("logging.basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(sys, "argv", argv)
    try:
        kmt_module.main()
    except SystemExit as exit_request:
        assert exit_request.code == 0, "the help invocation must not fail"
    return registrations


def run_help(*arguments):
    """The help text one `kmt` invocation prints. Click answers `--help` before a command body runs."""
    out = io.StringIO()
    monkeypatch = pytest.MonkeyPatch()
    try:
        with contextlib.redirect_stdout(out):
            run_main(monkeypatch, list(arguments))
    finally:
        monkeypatch.undo()
    return out.getvalue()


def help_output():
    """`kmt --help`."""
    return run_help("kmt", "--help")


def commands_from_help(text):
    """The command names a `--help` listing prints.

    Click writes each name in a two-space column; a name longer than the column gets a line of its
    own and its help continues indented further, so only the two-space column holds names.
    """
    listing = text.split("Commands:\n", 1)[1]
    return {line.split()[0] for line in listing.splitlines() if re.match(r"^ {2}\S", line)}
