"""Tests for the packaging layout: one importable package, no path hacks.

The defects these lock down:

* **D1** — the library was reachable twice under two unrelated module trees
  (`kubementat.*` via `sys.path.append(".../lib")` in every CLI module, and
  `lib.kubementat.*` via the packaging `include = ["lib*"]`). Module-level state,
  constants and the `logging.basicConfig` side effect were duplicated per process.
* **D2** — `import kubementat` failed in the venv, so `kmt` worked by import-order
  accident rather than because the package was installed correctly.

Both are closed by moving `lib/kubementat` and `cli` under `src/` and pointing
`[tool.setuptools.packages.find]` at `src`. These tests are what stop the `sys.path`
hack from creeping back in.
"""

import subprocess
import re
import sys
from pathlib import Path

from kubementat.core.config.config import default_kubementat_main_dir

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_python(code: str) -> subprocess.CompletedProcess:
    """Run `code` with a clean interpreter, from outside the checkout.

    cwd=/tmp matters: inside the checkout, a stray `sys.path.append` or a leftover
    `lib/` directory would still resolve, which is exactly what we want to catch.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd="/tmp",
    )


def test_import_kubementat_works_from_a_clean_dir():
    """The package has to import on its own, without a path hack helping it."""
    result = run_python("import kubementat.core.config.config")
    assert result.returncode == 0, result.stderr


def test_lib_kubementat_is_no_longer_importable():
    """The second module tree has to be gone, not just unused."""
    result = run_python("import lib.kubementat.core.config.config")
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


def test_kubementat_resolves_to_src():
    """The only tree that can load is the one under `src/`.

    This catches the other way a second tree comes back: an editable install or a
    `PYTHONPATH` that points at a copy nobody is editing.
    """
    result = run_python("import kubementat.core.config.config as m; print(m.__file__)")
    assert result.returncode == 0, result.stderr
    loaded = Path(result.stdout.strip())
    assert loaded.is_absolute()
    assert loaded.relative_to(REPO_ROOT / "src")


def test_cli_entrypoint_imports_from_a_clean_dir():
    result = run_python("from cli.kmt import main; assert callable(main)")
    assert result.returncode == 0, result.stderr


def test_no_sys_path_hacks_in_shipped_code():
    offenders = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if "sys.path" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_the_old_lib_directory_is_not_handed_back():
    assert not (REPO_ROOT / "lib").exists()


def test_the_root_the_cli_drives_is_the_checkout(monkeypatch):
    """The CLI resolves no root of its own, so the only answer is the library's.

    `cli.kmt.Config` held one and derived it from `__file__`, which is the D11 bug for every
    install that is not a checkout, and 32 commands accepted it through `@click.pass_obj` without
    ever reading it. The class is gone, so the invariant it stood for is asserted against
    the one function that answers the question — same invariant, same failure the day the two
    drift: `kmt` installs into what `KUBEMENTAT_HOME` names, or into this checkout when it does
    not, and never into whichever directory `kmt` happened to be installed under.

    `KUBEMENTAT_HOME` has to be unset for the checkout-relative half to be the one under test;
    the override is pinned by `tests/test_config_path_resolution.py`.
    """
    monkeypatch.delenv("KUBEMENTAT_HOME", raising=False)
    monkeypatch.delenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", raising=False)

    assert default_kubementat_main_dir().resolve() == REPO_ROOT


def test_no_module_under_the_cli_resolves_a_root_of_its_own():
    """The drift this replaced, caught the other way round.

    One root, resolved once, is the rule, and what the deleted code added was a second owner of it.
    A `kubementat_main_dir` assigned in `src/cli/` would be that second owner reappearing, and it
    would be invisible to
    the assertion above for the only reason that matters: the two would disagree in an installed
    `kmt`, and never in a checkout.
    """
    assignments = re.compile(r"^\s*(?:self\.)?kubementat_main_dir\s*=")
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src" / "cli").rglob("*.py")
        if any(assignments.match(line) for line in path.read_text().splitlines())
    )

    assert offenders == [], "a second resolution of the project root is back: " + ", ".join(offenders)
