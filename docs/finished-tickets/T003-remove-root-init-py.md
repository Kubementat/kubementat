# T003: Remove Root `__init__.py`

> **Phase:** 1 — Quick Wins | **Effort:** 5 min | **Complexity:** Low

## Description

The root `__init__.py` file is not needed — the project uses `lib/` and `cli/` as separate packages.

## File to Remove

- `__init__.py` (root directory)

## Acceptance Criteria

- [ ] Root `__init__.py` removed
- [ ] `uv run kmt --help` still works

## Notes

- The project's entry point is `cli/kmt.py` (registered via `pyproject.toml` → `[project.scripts] kmt = "cli.kmt:main"`).
- The root `__init__.py` has no content and serves no purpose.
