"""A comment sweep that changed behaviour is not a comment sweep, and this says which one it is.

The rule for this ticket is that 1713 citations get rewritten into the sentences they stand for and no
byte of behaviour moves. Reviewing that by reading 191 files would not work: a rewritten comment is
indistinguishable from a rewritten line of code at a glance, and `git diff` on a sweep is exactly the
diff a reviewer stops reading. So the check is structural rather than visual.

Two facts make it close to airtight. Comments never reach a parse tree, so a change that only removes
them is invisible to the AST and needs no special handling. Docstrings *do* reach it, so they are
identified by position and removed from both sides before comparing. What is left is the code, and if
that is identical the file cannot behave differently.

The comparison is per-file and it is run against a revision, so a sweep that accidentally rewrites an
assertion in a test is caught by name and not by a green suite -- a test whose assertion moved is green
by construction and is precisely the failure mode this ticket is not allowed to have.
"""

import ast
import subprocess
import sys
from pathlib import Path


def _strip_docstrings(tree):
    """Drop the docstring nodes, which are the only part of a tree prose is allowed to change.

    Located by position rather than by trusting a convention, because a nested function's docstring is
    also the first statement of its body and the same rule has to find it there. A function whose body
    is nothing but a docstring gets an explicit `...`, which is what the interpreter does with it, so
    the comparison cannot mistake a removed statement for a removed string.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
                if not body:
                    body.append(ast.Expr(value=ast.Constant(value=..., kind=None)))
        for field, value in ast.iter_fields(node):
            if field == "body" and isinstance(value, list):
                for statement in value:
                    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) \
                            and isinstance(statement.value.value, str):
                        pass  # an expression-statement string in a function body is a no-op either way
    return tree


def code_without_prose(source):
    """The file's AST with docstrings removed, as text, for a comparison that ignores position.

    Column and line offsets are dropped because a comment removal above a statement moves every line
    number below it, and a comparison that reported those would call every one of this ticket's edits
    a change.
    """
    tree = _strip_docstrings(ast.parse(source))
    return ast.dump(ast.fix_missing_locations(tree), include_attributes=False)


def _named_units(source):
    """`(qualname -> subtree text)` for every function and class, plus the module's own statements.

    The per-file answer has to say *where* the code moved, and a comparison of two whole-file dumps
    cannot. A file whose one changed statement sits inside a function body differs in the dump of that
    function and nowhere else, so naming the functions whose dumps differ is what makes the report
    actionable -- and it is the difference between this checking something and this claiming to.

    The module's leftover statements are collected under their own name because a constant, a class
    body's assignment or a decorator at the top level is exactly as able to move as a function is.
    """
    tree = _strip_docstrings(ast.parse(source))
    units = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            units[node.name] = ast.dump(ast.fix_missing_locations(node), include_attributes=False)
        else:
            units.setdefault("<module>", []).append(ast.dump(ast.fix_missing_locations(node),
                                                            include_attributes=False))
    units["<module>"] = "\n".join(units.get("<module>", []))
    return units


def _blank_strings(tree):
    """Replace every string constant's value with one marker, so a value edit stops being visible.

    This is the second comparison and it exists because the first one cannot tell two things apart.
    A string that is *compared* is code, and a string that is *printed* is diagnostic text, and in an
    AST dump both look like a changed constant. Neither is prose in the sense a comment is prose --
    one is a value the program decides with and one is a value it reports with -- so neither is allowed
    to pass silently, and neither is allowed to be called a behaviour change on the same footing as a
    moved `if`.

    So the tool reports them as their own third answer, with the sites listed. A sweep that rewrote the
    wording of a failure message is a sweep that changed no control flow, and that is worth saying
    plainly; a sweep that changed a string an `==` reads is the same edit and is a real change, and the
    only honest way to tell those apart is to show the reviewer every site and let the ones that matter
    be read.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = "\x00string\x00"
            node.kind = None
    return tree


def _units_blanked(source):
    """The same per-unit dump as `_named_units`, with string values erased to one marker."""
    tree = _blank_strings(ast.parse(source))
    units = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            units[node.name] = ast.dump(ast.fix_missing_locations(node), include_attributes=False)
        else:
            units.setdefault("<module>", []).append(
                ast.dump(ast.fix_missing_locations(node), include_attributes=False))
    units["<module>"] = "\n".join(units.get("<module>", []))
    return units


def _moved_units(a, b):
    """The units whose code differs between two sources, as a sorted list of names.

    Only a change that survives the strings being blanked is a move here. A value that changed but
    whose surrounding logic did not is reported separately by `message_text_only`, because a failure
    message is not a decision the program makes, and calling it one would make this report say
    "behaviour moved" about the hundreds of edits that are exactly what this ticket is for.
    """
    ua, ub = _named_units(a), _named_units(b)
    ba, bb = _units_blanked(a), _units_blanked(b)
    moved = []
    for name in sorted(set(ua) | set(ub)):
        if ua.get(name) == ub.get(name):
            continue
        if ba.get(name) != bb.get(name):
            moved.append(name if name != "<module>" else "<module statements>")
    return moved


def strings_needing_a_read(a, b):
    """The units that differ *only* in string values, or `None` when the file moved in other ways.

    This is the answer that cannot be made self-proving, and the honest version of it says so. Blanking
    every string value makes "the failure message was reworded" and "the string an `==` compares
    against was edited" look identical, and they are not the same thing: one is a report and one is a
    decision the program makes. Nothing in a syntax tree distinguishes a string that is printed from a
    string that is tested without knowing what the call does, and a tool that guessed would be the thing
    that silently passes a real change.

    So it names the units to read and lets a human call them. It does not return line numbers, because
    there is no diff being computed against the string positions here: an earlier version listed every
    string constant in the file, which for a file like the harness's stages produced a list of ~500
    numbers that were neither the changed lines nor usable as a to-read list, and it looked correct
    because it was long and specific. A unit name is a smaller claim and a true one.
    """
    ua, ub = _named_units(a), _named_units(b)
    if ua == ub:
        return None
    ba, bb = _units_blanked(a), _units_blanked(b)
    if any(ua.get(name) != ub.get(name) and ba.get(name) != bb.get(name)
           for name in set(ua) | set(ub)):
        return None  # logic moved somewhere, so this is not the string-only case
    return [name if name != "<module>" else "<module statements>"
            for name in sorted(set(ua) | set(ub)) if ua.get(name) != ub.get(name)]


def sources_of(path, revision=None):
    """`(current, other)` text for one file, reading the other side from git when asked."""
    current = Path(path).read_text()
    if revision is None:
        return current, current
    other = subprocess.run(["git", "show", "%s:%s" % (revision, path)], capture_output=True, text=True)
    if other.returncode != 0:
        return current, None  # the file is new on this side; nothing to compare against
    return current, other.stdout


def changed(path, revision=None):
    """The names whose *logic* differs, or `[]` when only prose or message text moved.

    Every logic difference is reported, not only the ones that changed a name: a rewritten condition
    inside an unchanged function is the whole point of this check and the first version of it reported
    that case as clean, which is the one answer a checker of this kind must never give.
    """
    current, other = sources_of(path, revision)
    if other is None:
        return []
    try:
        moved = _moved_units(other, current)
    except SyntaxError as error:
        return ["!SyntaxError: %s" % error]
    return moved


def strings_moved(path, revision=None):
    """The lines of a file whose only difference is string values, or `None` when that is not the case."""
    current, other = sources_of(path, revision)
    if other is None:
        return None
    try:
        return strings_needing_a_read(other, current)
    except SyntaxError:
        return None


def sweep_is_prose_only(paths, revision="HEAD"):
    """Files whose behaviour moved, and it is empty or the ticket is broken.

    `paths` is the diff, not the tree: a sweep that claims to have rewritten 191 files but changed a
    single `if` is a behaviour change wearing a docstring's clothes, and the only honest answer is to
    name the file.
    """
    bad = {}
    for path in paths:
        moved = changed(path, revision)
        if moved:
            bad[path] = moved
    return bad


def main(argv):
    revision = argv[0] if argv else "HEAD"
    paths = subprocess.run(["git", "diff", "--name-only", revision, "--", ".", "*.py"],
                           capture_output=True, text=True).stdout.split()
    if not paths:
        print("nothing changed; nothing to verify")
        return 0
    bad = sweep_is_prose_only(paths, revision)
    diagnostics = {}
    for path in paths:
        if path not in bad:
            lines = strings_moved(path, revision)
            if lines:
                diagnostics[path] = lines
    print("checked %d file(s) against %s" % (len(paths), revision))
    if bad:
        print("LOGIC MOVED in %d file(s) -- this is not a comment sweep any more:" % len(bad))
        for path, names in sorted(bad.items()):
            print("  %s: %s" % (path, ", ".join(names)))
    if diagnostics:
        total = sum(len(lines) for lines in diagnostics.values())
        print("STRING VALUES MOVED in %d file(s), %d unit(s) -- the tool cannot tell a reworded message"
              " from an edited comparison, so read these:" % (len(diagnostics), total))
        for path, units in sorted(diagnostics.items()):
            print("  %s: %s" % (path, ", ".join(units) if units else "(whole file)"))
    if not bad and not diagnostics:
        print("prose only: no code changed in any file")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
