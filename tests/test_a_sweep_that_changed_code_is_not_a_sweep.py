"""The sweep verifier proves its claim by failing on code that moved, so this proves that it does.

A check that reports "clean" for a file whose condition was rewritten is worse than no check: 191 files
of comment sweep would pass it, and the first behaviour change in the pile would be the one thing nobody
looked at. So the guard is exercised in both directions, against strings rather than against the tree,
so it cannot drift into grading its own fixture.

The two directions are not symmetric in what they prove. Every prose-only edit returning clean says the
verifier is not over-eager, which is what makes it usable across 191 files without burying the one real
finding. Every possible move returning dirty says it is not blind -- and the moves here are deliberately
the quiet ones, because "a function was added" is caught by any tool and "the same function returns
something else" is what an actual sweep breaks.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import prose_only_sweep

TREE = '''"""The module docstring, which cites R-7.1 for a rule it does not need.

A paragraph that names the specification instead of stating it.
"""
CONST = 1


def function(x):
    """Docstring that cites §4.2's layer 3."""
    # a comment naming TD-1
    if x:
        print("layer 3 of §4.2 is where an operator edits it")
        return CONST
    return 0


class Thing:
    """Class docstring."""

    def method(self):
        return function(1)
'''


def moved_to(text):
    return prose_only_sweep._moved_units(TREE, text)


def edit(old, new):
    return TREE.replace(old, new, 1)


def strings_differ(text):
    """The file's string-value differences, or `None` when something other than a string moved."""
    return prose_only_sweep.strings_needing_a_read(TREE, text)


def test_the_untouched_tree_is_clean():
    assert moved_to(TREE) == []
    assert strings_differ(TREE) is None


@pytest.mark.parametrize("text", [
    # A citation that becomes a sentence is the entire point of the ticket, so it must be invisible.
    edit("# a comment naming TD-1", "# one directory is exactly one cluster"),
    edit('"""Docstring that cites §4.2\'s layer 3."""', '"""The environment\'s static.json, which is'
                                                        '\n     what an operator edits."""'),
    edit("R-7.1 for a rule it does not need", "the rule, stated without the citation"),
    # The same string, reflowed: a rewrapped paragraph is not a change of meaning to the verifier.
    edit("A paragraph that names the specification instead of stating it.",
         "A paragraph that names the specification\ninstead of stating it."),
])
def test_prose_moving_changes_nothing_the_verifier_reports(text):
    assert moved_to(text) == [], "a prose edit was reported as a code change: %r" % (text,)


@pytest.mark.parametrize("text,expect", [
    # A condition inside a function whose name and signature did not move. This is the case the first
    # version of the verifier called clean, and it is the reason this file exists.
    (edit("if x:", "if x and True:"), ["function"]),
    # A different return value, in an otherwise untouched function.
    (edit("return CONST", "return 2"), ["function"]),
    # A module-level constant, which has no containing function to be named.
    (edit("CONST = 1", "CONST = 2"), ["<module statements>"]),
    # A method renamed inside a class: the class is what differs, which is honest about the change.
    (TREE.replace("def method(self)", "def renamed(self)"), ["Thing"]),
])
def test_a_move_is_reported_by_name(text, expect):
    assert moved_to(text) == expect, "code moved and the verifier said %r" % (moved_to(text),)


def test_a_function_that_appears_is_a_move():
    assert moved_to(TREE + "\n\ndef added():\n    return 1\n") == ["added"]


def test_a_string_value_differing_is_reported_as_a_thing_to_read_and_not_as_a_pass():
    """The third answer, which exists because the first two were lies for this case.

    An assert's failure text lives in a string literal, so rewriting it *does* reach the parse tree,
    and the first version of the verifier called that a behaviour change -- which is true only in the
    sense that every edit is a change to bytes. So the tool separates logic moving from string values
    moving, and it does not pretend to know which of those a string is: a string an `==` compares and a
    string a `print` reports both look like a changed constant once you blank them, and the honest
    answer is a list of lines to read rather than a verdict.

    What this test protects is that the answer exists at all. A tool that filed a string edit under
    "prose only" would silently bless a changed comparison; one that filed it under "logic moved"
    would file ~40 lines of every sweep as an accusation until the report was ignored. Neither is the
    truth, so the tool says "read these" and names the lines.
    """
    reworded = edit("return CONST", 'return CONST  # cites nothing')
    assert moved_to(reworded) == []

    edited_message = edit('"""Docstring that cites \u00a74.2\'s layer 3."""',
                          '"""The layer an operator edits."""')
    assert moved_to(edited_message) == []
    assert strings_differ(edited_message) is None, (
        "a docstring is prose and must not land in the list to read")

    changed_string = edit('print("layer 3 of \u00a74.2 is where an operator edits it")',
                          'print("the environment file an operator edits")')
    assert moved_to(changed_string) == [], (
        "a reworded message is not logic, and filing it as logic is what would turn every sweep into"
        " an accusation")
    assert strings_differ(changed_string) is not None, (
        "a string value moved and the verifier had no answer for it")
    assert "function" not in (moved_to(changed_string) or []), (
        "the function that merely prints differently was reported as having moved")


def test_a_file_the_other_side_lacks_is_not_reported_as_moved(tmp_path):
    """A new file has no other side to have changed from.

    Without this branch a sweep that adds a test file would be reported as a behaviour change, which is
    noise, and noise is what makes people stop reading the report.
    """
    fresh = tmp_path / "added_by_the_sweep.py"
    fresh.write_text("def only_one_side_has_it():\n    return 1\n")
    assert prose_only_sweep.changed(fresh, revision="HEAD") == []
