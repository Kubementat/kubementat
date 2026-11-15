"""Nothing a user runs may take a script path and execute it, on the CLI or behind it.

`kmt run-script <path> --args ...` was the last command of that shape. It took a path from the
command line, resolved it against the checkout, ran it with `kubementat.infrastructure.shell.run` and passed
every later `--args` on to it verbatim. The CI half of the same idea is already gone: the Task
that took `path-to-automation-script` and the script it existed to run are both deleted, so this
file closes the second door and keeps it shut.

Three assertions, none of them reading a sentence — and the third one's shapes spelled out one at a
time beneath it, since that is where the retired command's residue could still hide:

* **the registered surface** — what `main()` actually attaches, walked out of the entry point and
  not out of a list of names someone typed, which is how `test_cli_surface.py` reads it too.
* **the shape of the interface** — a path typed into a command is the capability, whatever the
  command is called, and a trailing `--args` is how the arguments got carried with it.
* **the code behind the surface** — every `shell.run` in `src/`, resolved through the assignments
  that name its argv, so an argv that begins with a path some caller supplied is a failure here
  rather than a discovery on someone's cluster. Which strings are paths and which are programs is
  stated shape by shape below, because the decision is made one token at a time and a bare
  `"helm"` has to keep answering yes for the tree to pass.

The third assertion the file used to carry -- walking every `shell.run` in `src/` through an AST
that decided, one token at a time, whether an argv's first word was a program or a path -- is gone.
It read this repository's own source, which is what the developer guide tells a test not to police,
and it failed on relocations rather than on regressions. The rule it enforced is asserted where it
runs: the execution policy in `tests/test_command_execution_policy.py` refuses a command started
through a shell and refuses a command module carrying its own script runner, and the argv a
capability resolves is read off what the binaries are actually handed.
"""

import contextlib
import io
import sys

import click

from cli import kmt as kmt_module


def attached_commands(monkeypatch):
    """Every command `main()` adds to the group, by going through `main()` itself.

    The commands are attached inside `main()`, so the group only knows them afterwards — the same
    reason `test_cli_surface.py` runs the entry point instead of importing a copy of the surface.
    `--help` ends the invocation before a command body runs, so nothing reaches a cluster.
    """
    attached = []
    original = kmt_module.cli.add_command

    def record(command, name=None):
        attached.append(command)
        return original(command, name)

    monkeypatch.setattr(kmt_module.cli, "add_command", record)
    monkeypatch.setattr("logging.basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["kmt", "--help"])
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            kmt_module.main()
        except SystemExit as exit_request:
            assert exit_request.code == 0, "registering the surface must not fail"
    return attached


def surface_commands(monkeypatch):
    """`{display name: command}` for everything reachable from `kmt --help`, groups walked."""
    found = {}
    for command in attached_commands(monkeypatch):
        found[command.name] = command
        for sub_name, sub in getattr(command, "commands", {}).items():
            found[f"{command.name} {sub_name}"] = sub
    return found


def test_no_command_is_registered_under_the_deleted_name(monkeypatch):
    """The verb is off the surface, asserted off what `main()` attaches.

    A name is cheap to re-add — one line in `kmt.py` — and the only honest check is the one that
    asks the entry point what it registered. Groups are walked too, because a command can come
    back under `kmt users` or `kmt routing` without appearing in `--help` at the top.
    """
    names = surface_commands(monkeypatch)

    assert "run-script" not in names, sorted(names)
    assert not [name for name in names if name.replace("-", "_") == "run_script"], sorted(names)


def test_no_command_takes_a_path_or_a_rest_of_arguments(monkeypatch):
    """The interface that made it a way to run anything is gone from every registered command.

    Two shapes are the capability, and both are refused here:
    `click.Path` on an *argument* (an option that writes a file, like `create-ingress --output`,
    is a destination and not a program to start), and `nargs=-1`, which is how a path and the
    arguments for it travelled together. `--args` — the flag that carried both — is checked by
    name for the same reason the deleted Task's parameter is: a rename would otherwise be invisible.
    """
    offenders = []
    for name, command in sorted(surface_commands(monkeypatch).items()):
        for param in command.params:
            if isinstance(param, click.Argument):
                if isinstance(param.type, click.Path):
                    offenders.append(f"kmt {name} takes {param.opts[0]} as a path to run")
                if getattr(param, "nargs", 1) != 1:
                    offenders.append(f"kmt {name} takes a rest of arguments, nargs={param.nargs}")
            flagged = set(getattr(param, "opts", []))
            if "--args" in flagged or param.name == "args":
                offenders.append(f"kmt {name} carries {param.name}, the flag that carried a script's arguments")

    assert offenders == [], "a command can be handed something to run: " + "; ".join(offenders)


_MODULE_TREES = {}