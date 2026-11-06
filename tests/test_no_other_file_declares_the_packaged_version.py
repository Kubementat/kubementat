"""The version is stated once, and nothing else is allowed to state it or go looking for it.

The checkout used to have three. `pyproject.toml` said `0.4.0`, `VERSION` said `0.3.0`, and the
git tags said `0.1.0`…`0.3.0`; the only one a tool could read was the file, so the number a
reader got was the stale one. `VERSION` is deleted and `kubementat/version.py` reads the
distribution metadata instead, which leaves `pyproject.toml` as the single place the number is
written.

These two tests are what keeps it that way: the file that caused the disagreement is gone and no
longer referenced, and the declared number lives in one file rather than being echoed around the
source tree where the next copy is guaranteed to drift.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Who is asked. A *reader* of the deleted file can only be product code or a script; a test that
# names `VERSION` in its own assertion is not looking for the file, it is enforcing that nobody
# else is, and excluding it from the search is the only way the check does not eat itself. Same
# reason docs and the CHANGELOG are out: prose quotes the deleted number as history.
READ_TREES = ("src", "scripts")
# Where a second *declaration* of the version is not allowed. The tree that enforces the rule is
# searched too, because the shape is an assignment and not a mention -- a docstring that recites
# `0.4.0` while explaining why three copies existed is documentation, not a source of truth.
DECLARATION_TREES = ("src", "tests", "scripts")

# The shape that *is* a reader: a path segment or a bare string literal standing for a path.
VERSION_TOKEN = re.compile(r'''["'/]VERSION["'\s)\]]''')
# The shape that is not. `component_config_key(component, 'VERSION')` is how a `static.json` key is
# derived from a component name rather than spelled per call site, and its suffix argument matches
# the token above without ever addressing a file. Keying the exclusion to that one call -- and not
# to a file list, a directory, or "any line that mentions VERSION" -- is what keeps the check whole:
# a real reader never writes `component_config_key` on the line that opens the path.
KEY_SPELLING = re.compile(r'''component_config_key\(''')


def looks_for_a_version_file(text):
    """True when *text* addresses a file called `VERSION`, and not the config key that ends with it.

    Read line by line, because the difference between the two spellings lives on the line: the same
    token is a path in `f"{root}/VERSION"` and a key suffix in
    `component_config_key(component, 'VERSION')`, and no file-level grep can tell those apart.
    """
    return any(VERSION_TOKEN.search(line) and not KEY_SPELLING.search(line)
               for line in text.splitlines())


def tracked_files(*trees):
    """The tracked text files under *trees*, by path relative to the repo root.

    Missing files are dropped rather than read: the index knows about entries the working tree
    may not (a deletion staged elsewhere in the queue), and a file that is not there holds no
    literal.
    """
    listing = subprocess.run(["git", "ls-files", *trees], cwd=str(REPO_ROOT),
                             capture_output=True, text=True, check=True)
    return [name for name in listing.stdout.splitlines()
            if not name.endswith((".pyc", ".so")) and (REPO_ROOT / name).is_file()]


def declared_version():
    """The version `pyproject.toml` declares, read the way the file is written.

    The floor here is Python 3.9, and `tomllib` is 3.11+, so this is one line of the file's own
    shape rather than a parser.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml declares no version"
    return match.group(1)


def test_the_version_file_every_tool_used_to_read_is_gone():
    """`VERSION` is out of the checkout, and no product code looks for it.

    The check is that the file is not there, not that git has forgotten it: the deletion is in
    the working tree and git's index still has the entry until it is staged, and a test that
    asserted the index would fail for reasons that have nothing to do with the rule.

    The shape searched for is a path reference -- a bare `"VERSION"` string literal or a
    `/VERSION` segment -- and the search is over `src/` and `scripts/`, where a reader would live.
    This file is deliberately not in that set: it quotes the name to forbid it, and a grep that
    matches its own enforcement is a test that can only ever fail.

    A `static.json` key is the other false positive, and it is a real one: a component's release
    knobs are read from `<COMPONENT>_VERSION`, which `component_config_key` builds out of the
    component name, so the suffix argument is the same token. `looks_for_a_version_file` filters
    exactly that spelling and nothing else, and
    `test_a_release_version_key_is_not_mistaken_for_a_path` asserts the filter is narrow enough to
    still fail on the thing it forbids.
    """
    assert not (REPO_ROOT / "VERSION").exists(), "VERSION is still in the checkout"

    readers = [name for name in tracked_files(*READ_TREES)
               if looks_for_a_version_file((REPO_ROOT / name).read_text(errors="ignore"))]
    assert readers == [], "nothing should go looking for a file that no longer exists"
    assert readers == [], "nothing should go looking for a file that no longer exists"


def test_a_release_version_key_is_not_mistaken_for_a_path():
    """The exclusion is about `component_config_key`, and it does not generalise into blindness.

    Both directions are asserted here, because the guard is only worth what its filter is worth: a
    key spelled the way the product spells keys is not a reader, and a line that opens the path is.
    """
    assert not looks_for_a_version_file(
        "        key = component_config_key(component, 'VERSION')\n")
    assert looks_for_a_version_file('        path = f"{root}/VERSION"\n')
    assert looks_for_a_version_file('        open("VERSION")\n')


def test_the_declared_version_is_written_in_exactly_one_place():
    """The number `pyproject.toml` declares is *assigned* nowhere else.

    The rule is about declarations, not mentions, and the difference is the whole point: before
    the file was deleted, `VERSION` held the number as a value, and that is the shape that
    survives a rename and drifts. Prose that quotes `0.4.0` while explaining the three-copy
    history is not a second source and no grep should pretend otherwise -- an earlier version of
    this test searched for the bare substring, and the only files it found were the ones
    enforcing the rule, which made it fail for the wrong reason rather than for a real copy.
    """
    version = declared_version()
    declaration = re.compile(
        r'''^\s*(?:__version__|VERSION|version)\s*(?::\s*[^=]+)?=
        \s*["']{}["']'''.format(re.escape(version)), re.MULTILINE | re.VERBOSE)

    carriers = [name for name in tracked_files(*DECLARATION_TREES)
                if declaration.search((REPO_ROOT / name).read_text(errors="ignore"))]

    assert carriers == [], (
        f"{version!r} is declared in {len(carriers)} source file(s) besides pyproject.toml; it "
        f"belongs in the metadata that `kubementat.core.version.get_version()` reads: {carriers}")
