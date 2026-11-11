"""Tests are read before the plan that motivated them, so their names have to stand alone.

A test called `test_run_script_fin46_relative_path` tells a reader nothing unless they open
the plan and look up the ticket that fragment names. Once they do, they have also trusted a name
over the assertion.
Both problems go away when the name describes the behaviour.

These two checks keep the convention from rotting: ticket ids belong in a docstring body,
where they point at evidence, never in a file name, a function name, or a parametrized id.
"""

import ast
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# `fin12`, `_fin-12`, `_d6`, `_d6x` at the start or after an underscore: the shapes ticket
# ids take when they leak into a snake_case identifier.
TICKET_ID = re.compile(r"(?i)(?:^|_)(?:fin|vvt|fr)-?\d+|(?:^|_)d\d+(?:_|$)")


def unit_test_functions():
    """Every `def test_*` in the unit suite, paired with the file that declares it."""
    functions = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                functions.append((path, node.name))
    return functions


def test_unit_test_files_are_named_for_the_behaviour_they_check():
    offenders = sorted(path.name for path in TESTS_DIR.glob("test_*.py")
                       if TICKET_ID.search(path.stem))

    assert offenders == [], "a file name has to say what it tests, not which ticket asked for it"


def test_unit_test_cases_are_named_for_the_behaviour_they_check():
    offenders = sorted(f"{path.name}::{name}" for path, name in unit_test_functions()
                       if TICKET_ID.search(name))

    assert offenders == [], "name the assertion; keep the ticket id in the docstring body"
