"""What `kmt --version` says, and where that number is allowed to come from."""

import importlib.metadata
import os

import pytest
from conftest import run_kmt

from kubementat.core import version as version_module


def test_the_version_kmt_prints_is_the_one_the_distribution_reports():
    """No drift is possible: what is printed is what the installed metadata says."""
    result = run_kmt(["--version"], dict(os.environ))

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip() == "kubementat " + importlib.metadata.version("kubementat")


def test_a_run_with_no_metadata_says_so_and_names_the_way_to_install_it(monkeypatch):
    """No metadata means no version, and the answer is a sentence with a fix in it."""
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
