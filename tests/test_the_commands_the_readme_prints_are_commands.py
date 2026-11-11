"""The commands a README tells a reader to run are commands, and this runs them.

The quickstart is the only part of this repository that a new reader reads once, and it is also the part
that goes stale by accretion rather than by decision: `kmt initialize` was deleted by this epic, and the
README's quickstart still ended with it, because the ticket that fixed the prose around it fixed the prose
around it and not the command. The sentence that survived was true; the line under it was a usage error.
A reader who typed it got `Error: No such command 'initialize'.`, learned nothing about their own setup,
and went to look for a different document.

So the check is the same one `test_an_instruction_that_names_a_command_is_one_that_parses.py` already
applies to what a refusal prints: run the command and look for Click's exit code 2. A usage error is a
command that does not exist, an option that was renamed, or the wrong number of arguments, and all three
are exactly the things that happen to prose when code moves. A command that gets as far as refusing about
the directory, the cluster, or the file is a pass: the reader's command parsed, and what it objected to is
their tree and not this document.

Two decisions make this honest rather than decorative:

* Placeholders are substituted before running, and an unrecognised one fails the test. A guard that ran
  `--config-dir <dir>` literally would be proving that Click can parse a shell redirection. The table
  below is the whole set, and a new placeholder has to be decided about rather than added quietly.
* The commands that address a cluster are checked for parsing and *not* executed. `kmt install` is not
  run because installing it is what the reader is doing, not what the test should do to a real machine;
  the read-only verbs run for real, because that is a difference that makes the guard stronger where it
  can be.
"""

import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

# What the README writes where it means "yours". Everything here is a fact about the reader's machine, so
# a test that invents one is guessing; these are answered with something that parses and then refused
# appropriately, which is the outcome the check wants.
SUBSTITUTIONS = {
    "<dir>": "/tmp/kmt-readme-trial-config",
    "~/kmt-config": "/tmp/kmt-readme-trial-config",
    "<environment>": "dev",
    "<team>": "dev1",
    "<environment-name>": "dev",
    "<you@example.com>": "someone@example.invalid",
    "<address>": "someone@example.invalid",
    "<name>": "readme-trial",
    "dev1": "dev1",
    "./ssh-deploy-keys.json": "/tmp/kmt-readme-trial-keys.json",
}

# A command line inside a fence that starts with `uv run kmt`, with the fence's indentation stripped.
INVOCATION = re.compile(r"^uv run (kmt\s+[^\n#]+)$", re.M)

USAGE_ERROR = 2


def quickstart_commands():
    """Every `uv run kmt …` line the README prints, with continuations joined.

    The README breaks long `config init` calls across lines the way a shell would, so a fence holding
    four lines holds one command. Reading them separately would turn one verified invocation into four
    that were never written, and the last of them would end in a backslash. A backslash at the end of a
    line is a continuation and nothing else, so it is removed here rather than answered.
    """
    lines = [line.strip() for line in README.read_text().splitlines()]
    joined, pending = [], ""
    for line in lines:
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        line = pending + line
        pending = ""
        joined.append(line)
    if pending:
        joined.append(pending.strip())
    found = []
    for line in joined:
        match = INVOCATION.search(line)
        if match:
            found.append(match.group(1).strip())
    return found


def substitute(argv):
    """Swap the README's stand-ins for things that parse, and refuse the ones that stand for nothing.

    An unrecognised placeholder is the interesting failure: it means the README invented a blank the
    reader is expected to fill, and this test cannot tell whether that blank would have parsed.
    """
    resolved = []
    for token in argv:
        if token in SUBSTITUTIONS:
            resolved.append(SUBSTITUTIONS[token])
        elif "<" in token and ">" in token:
            raise AssertionError("README placeholder %r has no answer in SUBSTITUTIONS, so a reader "
                                 "would be running a command none of us has checked" % token)
        else:
            resolved.append(token)
    return resolved


@pytest.mark.parametrize("command", sorted(set(quickstart_commands())))
def test_the_readme_prints_a_command_that_parses(command):
    argv = shlex.split(command)
    assert argv and argv[0] == "kmt", "the README's commands are expected to start with `kmt`: %s" % command
    argv = substitute(argv[1:])
    # The guard needs a `kmt` that is the real CLI and not a shell alias, so it goes through the same
    # entry point `uv run kmt` resolves to.
    result = subprocess.run(["uv", "run", "kmt"] + argv, cwd=ROOT, capture_output=True, text=True)
    combined = (result.stdout or "") + (result.stderr or "")
    assert result.returncode != USAGE_ERROR, (
        "README says `%s`, and that is not a command kubementat has. The output was:\n%s"
        % (command, combined.strip()[:600]))
    assert "No such command" not in combined, "README says `%s`: %s" % (command, combined.strip()[:400])
    assert "No such option" not in combined, "README says `%s`, and an option it names is gone" % command
    assert "unexpected extra argument" not in combined, (
        "README says `%s`, which the CLI reads as the wrong number of arguments" % command)


def test_no_quickstart_command_names_a_deleted_verb():
    """The commands that were deleted, caught by name rather than by exit code.

    Worth the second check because an old quickstart line can parse for the wrong reason: `kmt config
    diff` is gone, but `kmt config` with an unrecognised subcommand still *prints* help, and a test that
    only looked for exit codes would call that a pass.
    """
    deleted = ("initialize", "explain", "diff", "migrate", "setup-tekton", "login-kubernetes-dashboard")
    offenders = [command for command in quickstart_commands()
                 if any(token in command.split() for token in deleted)]
    assert not offenders, "the README tells a reader to run verbs kubementat deleted: %s" % offenders
