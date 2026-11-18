"""Test names describe the behaviour they check, so they read without any other document."""

import ast
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Tracker ids as they look inside a snake_case name: `fin12`, `_fin-12`, `_d6`, `_d6x`.
TICKET_ID = re.compile(r"(?i)(?:^|_)(?:fin|vvt|fr)-?\d+|(?:^|_)d\d+(?:_|$)")


def unit_test_functions():
    """Every `def test_*` in the unit suite, paired with the file that declares it."""
    functions = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                functions.append((path, node.name))
    return functions


def test_unit_test_files_are_named_for_the_behaviour_they_check():
    offenders = sorted(path.name for path in TESTS_DIR.rglob("test_*.py")
                       if TICKET_ID.search(path.stem))

    assert offenders == [], "a file name has to say what it tests, not which tracker item asked for it"


def test_unit_test_cases_are_named_for_the_behaviour_they_check():
    offenders = sorted(f"{path.name}::{name}" for path, name in unit_test_functions()
                       if TICKET_ID.search(name))

    assert offenders == [], "name the assertion, not the tracker item"
