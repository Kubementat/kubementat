"""Nothing a user runs may take a script path and execute it, on the CLI or behind it.

`kmt run-script <path> --args ...` was the last command of that shape. It took a path from the
command line, resolved it against the checkout, ran it with `kubementat.shell.run` and passed
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

What is *not* asserted here is that the two scripts under `scripts/utilities/kubernetes/` have a
route. They have a Python equivalent and a parity test; their own tickets decide whether the
files stay. What they must not get back is a command that runs them by path.
"""

import ast
import contextlib
import io
import sys
from pathlib import Path

import click

from cli import kmt as kmt_module

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"


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


def _module_tree(dotted):
    """The syntax tree of one module inside `src/`, parsed once; None for anything else.

    Needed because a call site may read its argv from another module: `automation.py` reads
    `helm_utils.HELM_LIST_RELEASES_ARGV`, and a guard that refuses to cross the import would ask for
    the same argv to be spelled again where it is used. That is the second answer row 24 removes.
    """
    path = SRC_ROOT.joinpath(*dotted.split("."))
    if path.is_dir():
        path = path / "__init__.py"
    else:
        path = path.with_suffix(".py")
    if not path.is_file():
        return None
    if path not in _MODULE_TREES:
        _MODULE_TREES[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _MODULE_TREES[path]


def _imports_by_name(tree):
    """Names this tree did not assign and where they came from: `from <module> import <name>`."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = node.module
    return found


def _definitions_returning(tree, module=None):
    """Names defined by a `def` in *tree*, answered by what they return and where they were written.

    An argv is a program plus arguments, and the program is increasingly something a
    function assembles rather than something a call site spells: the cluster is an argument, four
    call sites ask the same question of it, and the answer they share is a builder. A guard that read
    only assignments would call `shell.run(` `helm_list_releases_argv(target))` unanswerable and
    refuse it — the wrong verdict, and one that teaches the next author to inline the argv, which is
    precisely how one question acquires a second answer.

    Each entry carries the assignments of the module that wrote the function, because that is where
    the function's own list is spelled: `automation.py` calls a builder whose argv lives in
    `helm_utils.py`, and following the call without following the names would resolve nothing.
    """
    written = {}
    local = _assignments_by_name(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            returned = [item.value for item in ast.walk(node)
                        if isinstance(item, ast.Return) and item.value is not None]
            if returned:
                written.setdefault(node.name, []).extend((item, local) for item in returned)
    if module is None:
        return written
    for name, imported in _imports_by_name(tree).items():
        nested = _module_tree(imported)
        if name not in written and nested is not None:
            found = _definitions_returning(nested)
            if name in found:
                written[name] = found[name]
    return written


def _assignments_by_name(tree, module=None):
    """Every module- or function-level `name = ...` in *tree*, keyed by name.

    Scopes are not resolved: an assignment is a candidate value wherever it sits, so the check is
    conservative. A program named in one place and appended to in another (`argv += [...]`) still
    answers, because `+=` never decides what `argv[0]` is.

    An imported name is answered from the module that declares it -- but only when this tree does not
    assign it, so a local value always wins and the imported candidate can never stand in for one.
    A name imported from outside `src/` is not followed: nothing outside this tree can answer what a
    program name is.
    """
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
    if module is None:
        return found
    for name, imported in _imports_by_name(tree).items():
        if name in found:
            continue
        values = _assignments_by_name(_module_tree(imported), None) if _module_tree(imported) else {}
        if name in values:
            found[name] = values[name]
    return found


def _is_path_shaped(text):
    """Whether a string constant names a place rather than a program that is already on `PATH`.

    A program is one bare token. A separator inside it says a file is being pointed at, and `.`
    (which includes `..`) or `~` at the front says it is being pointed at relatively, or rooted in
    a home directory — the shape `kmt run-script` resolved against the checkout and exec'd. Both
    separator spellings are checked rather than `os.sep`, because this reads source constants: a
    Windows-rooted path spelled into this tree is read on Linux and refused there too. A token that
    is only whitespace names nothing and is refused on the same grounds.
    """
    return (not text.strip()
            or "/" in text
            or "\\" in text
            or text.startswith((".", "~")))


def _concatenated_text(node):
    """The text of a string constant, or of two or more of them joined with `+`; None for anything else.

    Needed because `"/srv/scripts/" + "thing.sh"` is a path in every part but its first, and the
    decision about `argv[0]` is made by reading what it spells as a whole.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _concatenated_text(node.left)
        right = _concatenated_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _slice_starts_at_front(list_node):
    """Whether a subscript on *list_node* keeps its first element, so its head is still the program.

    A guard that read every slice as "the same list" would accept `NAMED[1:]` and call a list of
    arguments a program name, which is the wrong direction for a check whose whole job is to refuse.
    """
    if not isinstance(list_node, ast.Subscript):
        return True
    return _slice_starts_at_front(list_node.value) if isinstance(list_node.value, ast.Subscript) \
        else _bounds_start_at_zero(list_node.slice)


def _bounds_start_at_zero(slice_node):
    """`[:2]`, `[0]`, `[0:2]` start at the front; `[1:]`, `[i:]` and anything runtime-shaped do not."""
    if isinstance(slice_node, ast.Slice):
        return slice_node.lower is None or (isinstance(slice_node.lower, ast.Constant)
                                            and slice_node.lower.value == 0)
    if isinstance(slice_node, ast.Tuple):  # py<3.9 spelled a slice as a tuple of bounds
        return _bounds_start_at_zero(slice_node.elts[0]) if slice_node.elts else False
    if isinstance(slice_node, ast.Index):  # py<3.9
        return _bounds_start_at_zero(slice_node.value)
    return isinstance(slice_node, ast.Constant) and slice_node.value == 0


def _starts_with_a_named_program(node, assignments, seen, defined=None):
    """Whether the first element of an argv is a program named in the source and not a path.

    A list literal is read at `elts[0]`, a `list + [...]` at its left, a name through the values
    assigned to it, and a `[key]` subscript through every entry of the literal dict assigned to the
    name it is taken from. A string — or a run of strings joined with `+` — is read through
    `_is_path_shaped`, which is what decides the answer: a bare `"helm"` is a program, and a
    separator, a leading `.` or a leading `~` makes it a path. Anything else — `os.path.join`,
    `Path(...)`, a dict built at runtime, a parameter no assignment names — answers *no*, which is
    the point: the shape that used to live in `utilities.py` was
    `[resolved_script_path] + list(args[1:])`, and it is refused rather than pattern-matched.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        return _starts_with_a_named_program(node.elts[0], assignments, seen, defined) if node.elts else True
    if isinstance(node, ast.Call) and (isinstance(node.func, ast.Name)
                                       or isinstance(node.func, ast.Attribute)):
        # An argv assembled by a function rather than spelled out, which is how a cluster's address
        # gets onto a cluster command: `helm_list_releases_argv(target)` builds what is run. The program is
        # whatever that builder returns, read across the import the way constants are read, because
        # `automation.py` and `helm_utils.py` ask one question and a guard that could not follow the
        # call would ask each of them to spell it again. Anything the builder does not answer about is
        # refused, so the shape stays checked and not merely assumed.
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        if name in seen:
            return False
        candidates = (defined or {}).get(name)
        if candidates is not None:
            return all(_starts_with_a_named_program(value, where, seen | {name}, defined)
                       for value, where in candidates)
        if not assignments.get(name):
            return False
        return all(_starts_with_a_named_program(value, assignments, seen | {name}, defined)
                   for value in assignments[name])
    if isinstance(node, ast.Starred):
        # `[*NAMED[:2], ...]`: the arguments are what is splatted, but the program is still at the
        # front of it, and a guard that stopped here would call the read unanswerable.
        return _starts_with_a_named_program(node.value, assignments, seen, defined)
    text = _concatenated_text(node)
    if text is not None:
        return not _is_path_shaped(text)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _starts_with_a_named_program(node.left, assignments, seen, defined)
    if isinstance(node, ast.Name):
        if node.id in seen:
            return False
        values = assignments.get(node.id)
        if not values:
            return False
        return all(_starts_with_a_named_program(value, assignments, seen | {node.id}, defined)
                   for value in values)
    if isinstance(node, ast.Subscript):
        # An argv picked out of a table, which is how the version probes are handed over. Every entry is
        # read, not only the branch this call would take, so a table that stores a path in some entry
        # nothing currently selects is still refused. It used to be merely *examined*, and the
        # difference was the whole gap: a string constant answered "is a named program" for any
        # string, so `/usr/local/bin/evil` sitting in a table entry was walked and then believed.
        # Row 17 closed that in the one place it could be closed — the string case above, which is
        # what decides the answer for every branch that reaches this one.
        if not isinstance(node.value, ast.Name):
            return False
        values = assignments.get(node.value.id)
        if not values:
            return False
        if any(isinstance(value, ast.List) for value in values):
            # A slice of a literal argv, which is how a cluster's address gets spliced into a read:
            # `helm_list_releases_argv` runs `HELM_LIST_RELEASES_ARGV[:2]` first so the flags it adds
            # cannot land before the program. Only a slice that starts at the front is read, because
            # `argv[1:]` is the arguments without the program and is no evidence of one.
            if not all(_slice_starts_at_front(value) for value in values if isinstance(value, ast.List)):
                return False
            return all(_starts_with_a_named_program(value, assignments, seen | {node.value.id}, defined)
                       for value in values)
        return all(isinstance(value, ast.Dict) and value.values
                   and all(_starts_with_a_named_program(entry, assignments, seen | {node.value.id}, defined)
                           for entry in value.values)
                   for value in values)
    return False


def _is_shell_run(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "run"
            and isinstance(func.value, ast.Name) and func.value.id == "shell")


def _path_shaped_offenders(root):
    """`<file>:<line>` for every `shell.run` under *root* whose argv[0] is not a named program.

    Split out of the test below so the same walk can be pointed at a planted fixture instead of at
    `src/` — the plant is only evidence if the guard runs over it exactly as it runs over the
    product. Files outside *root* are labelled relative to *root*; nothing else changes.
    """
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = ".".join(path.relative_to(root).parts).rsplit(".", 1)[0]
        assignments = _assignments_by_name(tree, module)
        defined = _definitions_returning(tree, module)
        for node in ast.walk(tree):
            if _is_shell_run(node) and (not node.args
                                        or not _starts_with_a_named_program(node.args[0], assignments,
                                                                           frozenset(), defined)):
                try:
                    label = str(path.relative_to(REPO_ROOT))
                except ValueError:
                    label = str(path.relative_to(root))
                offenders.append(f"{label}:{node.lineno}")
    return offenders


def test_no_src_module_starts_a_program_from_a_path_a_caller_supplied():
    """Every `shell.run` in `src/` names its program, wherever its argv was assembled.

    This is the rule `test_command_execution_policy.py` already states for `shell=True`,
    `subprocess` and `os.chdir`, aimed at one call shape: what `kubementat.shell.run` is handed is
    a program to exec and not a file to open. The call sites it walks answer with `"kubectl"`,
    `"helm"`, `"helmfile"`, `"git-crypt"` — the names of programs the CLI needs — and the day one
    of them answers with a path, this fails.
    """
    offenders = _path_shaped_offenders(SRC_ROOT)

    assert offenders == [], "shell.run argv[0] must name a program, not a resolved path: " + ", ".join(offenders)


def _verdict_for_source(source):
    """What the guard says about the argv of the one `shell.run` spelled in *source*.

    The answer is read out of the same function the walk above uses, with the assignments in the
    source handed to it, so a shape that only exists as text still gets the real verdict.
    """
    tree = ast.parse(source)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    return _starts_with_a_named_program(call.args[0], _assignments_by_name(tree), frozenset(),
                                       _definitions_returning(tree))


def _verdict_for(argv):
    """The guard's answer for `shell.run(argv)` written out as source text."""
    return _verdict_for_source(f"shell.run({argv})")


def test_an_absolute_path_as_argv_zero_is_not_a_named_program():
    """A path typed as text is refused, which is the hole row 17 closes.

    The retired shape was `[resolved_script_path] + list(args[1:])`, and a string constant used to
    answer "this is a program" for any string at all — including `"/srv/scripts/thing.sh"`. A
    separator inside the token says *path*, so it says no.
    """
    assert not _verdict_for('["/srv/scripts/thing.sh", *args]')
    assert not _verdict_for('["/usr/bin/kubectl", "get", "pods"]')


def test_a_relative_or_home_rooted_path_as_argv_zero_is_not_a_named_program():
    """A path does not become a program because it was written relatively, or with a `~`.

    `.`/`..` and `~` are where a path starts when it carries no separator in front of it, and a
    relative script path is exactly what `kmt run-script` resolved against the checkout.
    """
    assert not _verdict_for('["./scripts/thing.sh", *args]')
    assert not _verdict_for('["../scripts/thing.sh", *args]')
    assert not _verdict_for('["~/scripts/thing.sh", *args]')


def test_a_bare_program_name_as_argv_zero_is_still_a_named_program():
    """What the rest of the tree relies on is untouched: one token, no separator, answers yes.

    Every call site in `src/` hands over a name and not a location, and refusing the names is not
    how this hole gets closed.
    """
    assert _verdict_for('["helm", "list", "--all-namespaces"]')
    assert _verdict_for('["kubectl", "config", "current-context"]')
    assert _verdict_for('["uv", "sync", "--all-extras"]')
    assert _verdict_for('["git-crypt", "init"]')


def test_a_path_stitched_from_literal_halves_as_argv_zero_is_not_a_named_program():
    """A path built out of string literals is a path, whichever half the separator is in.

    Reading only the left operand of a `+` would accept `"/usr/bin/" + "kubectl"` because its left
    half is a directory, so a concatenation of literals is read whole before it is judged.
    """
    assert not _verdict_for('["/srv/scripts/" + "thing.sh", *args]')
    assert not _verdict_for('["usr/bin" + "/kubectl", "get", "pods"]')


def test_a_path_held_in_an_argv_table_is_not_a_named_program():
    """A table entry the call would not even select is refused, not merely looked at.

    The version probes read `argv` out of a dict, and that branch already examined every entry.
    Examining was the whole gap: an entry that is a path was examined and then believed.
    """
    table = ('BINARY_VERSION_ARGV = {"helm": ["helm", "version", "--short"],\n'
             '                    "evil": ["/srv/scripts/thing.sh", "--version"]}\n'
             'shell.run(BINARY_VERSION_ARGV["helm"])')
    assert not _verdict_for_source(table)


def test_an_f_string_as_argv_zero_is_not_a_named_program():
    """An argv spelled with an f-string answers no today and after this fix, and says nothing new.

    `f"{root}/thing.sh"` is not resolvable, so the guard already refused it; the shape is recorded
    so the answer is stated rather than assumed — a conservative *no*, never a false *yes*.
    """
    assert not _verdict_for('[f"{root}/thing.sh", *args]')


def test_the_guard_refuses_a_planted_module_that_starts_a_script_by_path(tmp_path):
    """The fix bites: a planted module, outside `src/`, is reported by the walk that guards `src/`.

    The plant is written under the test's own `tmp_path` and removed with it — the only place a
    deliberate `argv[0]` of `"/srv/scripts/thing.sh"` can live without shipping it to a cluster.
    Running the walk over it rather than over a syntax tree built in memory is what proves the
    guard catches the shape end to end and not just in one branch of its recursion.
    """
    (tmp_path / "planted.py").write_text(
        "import kubementat.shell as shell\n"
        "\n"
        "def run(*args):\n"
        "    shell.run([\"/srv/scripts/thing.sh\", *args])\n",
        encoding="utf-8")

    assert _path_shaped_offenders(tmp_path) == ["planted.py:4"]
