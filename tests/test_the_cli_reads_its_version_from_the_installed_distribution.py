"""What `kmt --version` says, and where that number is allowed to come from.

There is one place that states the kubementat version — `pyproject.toml`'s `project.version` —
and the CLI reads it out of the installed distribution rather than reciting it. A test that
compared the flag's output against a literal would have put a second copy of the number in the
suite, which is the drift this is meant to make impossible, so the comparison below is against
`importlib.metadata` and not against a string.

Two failure modes are in scope: the stale file that used to be read (`VERSION`, deleted), and
the run that has no metadata behind it, which is either a checkout that was never installed or an
interpreter that was never handed the distribution. The answer there is the way to install it, and
the checkout is one branch of that sentence and not the whole of it.
"""

import importlib.metadata
import os
import re

import pytest

from conftest import run_kmt

from kubementat.core import version as version_module


def test_kmt_reports_a_version_and_exits_cleanly():
    """`kmt --version` is a sentence and not a traceback: `kubementat <version>`, exit 0."""
    result = run_kmt(["--version"], dict(os.environ))

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    printed = result.stdout.strip()
    assert re.fullmatch(r"kubementat \d+(?:\.\d+)*(?:[-.+]?\w*)?", printed), printed


def test_the_version_kmt_prints_is_the_one_the_distribution_reports():
    """No drift is possible: what is printed is what the installed metadata says.

    The assertion is against `importlib.metadata` for the same reason the code reads from it —
    a literal here would be a third source of truth and would rot exactly the way `VERSION`
    did (`pyproject.toml` said `0.4.0` while the file said `0.3.0`).
    """
    result = run_kmt(["--version"], dict(os.environ))

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip() == "kubementat " + importlib.metadata.version("kubementat")


def test_a_run_with_no_metadata_says_so_and_names_the_way_to_install_it(monkeypatch):
    """No metadata means no version, and the answer is a sentence with a fix in it.

    Not a fallback constant: a hard-coded string in a checkout that has never been installed is
    how `VERSION` came to disagree with `pyproject.toml` in the first place.

    The sentence has to cover both ways of getting here, and the common one is not a checkout: an
    installed `kmt` run by an interpreter that was never handed the distribution answers the same
    refusal. A message that only said "run `uv sync --all-extras`" was a checkout-only diagnosis,
    and it told somebody who had installed a wheel to go and find a source tree.
    """
    def no_distribution(_name):
        raise importlib.metadata.PackageNotFoundError("kubementat")

    monkeypatch.setattr(version_module.metadata, "version", no_distribution)

    with pytest.raises(RuntimeError) as raised:
        version_module.get_version()

    message = str(raised.value)
    assert "uv sync --all-extras" in message, message
    assert "not installed" in message, message
    assert "pipx install kubementat" in message, message
    assert "uv tool install kubementat" in message, message
