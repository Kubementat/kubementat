"""One command-execution policy for kubementat.

Everything that runs a program outside the interpreter goes through :func:`run`.
The rules it enforces are the reason the module exists:

* **argv lists, never command strings.** A string handed to ``subprocess`` with
  ``shell=True`` is a second parser waiting for a team name like ``ops;rm`` or a label
  filter with a comma. ``run`` refuses strings outright, so the mistake cannot be made
  again (see plan D8).
* **No mutating the whole process.** Working directory is an argument (``cwd=``), not
  ``os.chdir``.
* **Failures name the command.** A non-zero exit raises
  :class:`kubementat.errors.CommandExecutionError` carrying the full argv and the return code, so
  the operator sees what was tried.

The module is deliberately free of click, config and cluster knowledge; it is the bottom
layer that both ``cli/`` and ``kubementat`` may import.
"""

import logging
import os
import shlex
import subprocess
import time

from kubementat import errors

__all__ = ["CommandResult", "run"]


class CommandResult:
    """What a command did. ``returncode`` is the only verdict; everything else is evidence."""

    def __init__(self, argv, returncode, stdout="", stderr="", dry_run=False):
        self.argv = list(argv)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.dry_run = dry_run

    @property
    def ok(self):
        return self.returncode == 0

    @property
    def argv_display(self):
        """The argv as a human would type it (quoting the awkward ones)."""
        return shlex.join(self.argv)

    def __repr__(self):
        return f"CommandResult(argv={self.argv!r}, returncode={self.returncode})"


def run(argv, *, cwd=None, env=None, capture=False, check=True, retries=0,
        retry_backoff=0.5, dry_run=False, input=None):
    """Run *argv* and return a :class:`CommandResult`.

    Parameters
    ----------
    argv:
        The command and its arguments, one list entry each. A plain string is rejected —
        that is the whole point of the module.
    cwd:
        Working directory for the child process. Never call ``os.chdir`` for this.
    env:
        Child environment. ``None`` inherits this process's environment.
    capture:
        Capture stdout/stderr instead of letting them stream to the terminal. Streaming is
        what you want for anything interactive (``kubectl port-forward``); capturing is for
        output you are about to parse.
    check:
        Raise :class:`kubementat.errors.CommandExecutionError` on a non-zero exit. ``check=False``
        hands the verdict back to the caller.
    retries:
        Extra attempts after the first for commands that fail for transient reasons. The
        delay is constant, ``retry_backoff`` seconds between attempts — this matches the
        behaviour the install scripts had before this module existed; there is no
        exponential growth. A missing binary is not retried: it will not show up on the
        second attempt.
    dry_run:
        Log the command and report success without running it.
    input:
        Bytes or text fed to the child's stdin (for the ``kubectl auth can-i``
        confirmations that have always answered themselves "yes").

    Returns
    -------
    CommandResult

    Raises
    ------
    TypeError:
        If *argv* is a string, or holds a non-string item.
    ValueError:
        If *argv* is empty.
    kubementat.errors.CommandExecutionError:
        On a non-zero exit with ``check=True``, or on an unusable command line.
    """
    argv = _validated_argv(argv)
    attempts = max(1, int(retries) + 1)

    for attempt in range(1, attempts + 1):
        logging.info(f"Running command: {_display(argv)}")

        if dry_run:
            logging.info("dry-run: command not executed.")
            return CommandResult(argv, 0, dry_run=True)

        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                input=input,
                capture_output=capture,
                text=capture or isinstance(input, str),
                check=False,
            )
        except FileNotFoundError as error:
            # Two different problems look identical to `execve`: the program is missing, or
            # the directory we were asked to start in is. Only one of them means the
            # operator should go install something.
            if cwd is not None and not os.path.isdir(cwd):
                raise errors.CommandExecutionError(
                    CommandResult(argv, 127),
                    f"Working directory does not exist: {cwd}\n"
                    f"Command that would have run: {_display(argv)}",
                ) from error
            raise errors.CommandExecutionError(
                CommandResult(argv, 127),
                f"Command not found: {argv[0]} ({error})\n"
                f"Full command: {_display(argv)}",
            ) from error
        except OSError as error:
            raise errors.CommandExecutionError(
                CommandResult(argv, 126), f"Could not execute {argv[0]}: {error}"
            ) from error
        # KeyboardInterrupt deliberately propagates: Ctrl+C is the user's decision.

        result = CommandResult(
            argv,
            completed.returncode,
            stdout=(completed.stdout or "") if capture else "",
            stderr=(completed.stderr or "") if capture else "",
        )

        if result.ok:
            return result

        if attempt < attempts:
            logging.info(
                f"Command failed with exit code {result.returncode}, "
                f"retrying ({attempt}/{retries}) after {retry_backoff}s: {_display(argv)}"
            )
            time.sleep(retry_backoff)
            continue

        if check:
            raise errors.CommandExecutionError(result)
        logging.warning(f"Command exited {result.returncode} (check=False): {_display(argv)}")
        return result


def _validated_argv(argv):
    """Reject the two ways a command line can be wrong before it reaches the OS."""
    if isinstance(argv, (str, bytes)):
        raise TypeError(
            "shell.run takes an argv list, never a command string. "
            "A string here is what shell=True parsed for you, and that is exactly the "
            f"injection/breakage this policy removes. Split it: {argv!r}"
        )
    argv = list(argv)
    if not argv:
        raise ValueError("shell.run needs at least the program to run.")
    bad = [item for item in argv if not isinstance(item, str)]
    if bad:
        raise TypeError(f"Every argv entry must be a str, got {bad!r} in {argv!r}")
    return argv


def _display(argv):
    return shlex.join(argv)
