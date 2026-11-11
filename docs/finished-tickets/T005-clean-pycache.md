# T005: Clean Up pycache Directories

> **Phase:** 1 — Quick Wins | **Effort:** 10 min | **Complexity:** Low

## Description

Remove `__pycache__` directories from disk and remove tracked `.pyc` files from git.

## Current State

**Tracked in git (must be removed):**
- `cli/__pycache__/__init__.cpython-39.pyc`
- `cli/__pycache__/kmt.cpython-39.pyc`

**On disk (not tracked, but should be cleaned):**
- `cli/__pycache__/`
- `cli/commands/__pycache__/`
- `lib/kubementat/__pycache__/`

## Acceptance Criteria

- [ ] `.pyc` files removed from git tracking: `git rm cli/__pycache__/*.pyc`
- [ ] All `__pycache__` directories removed from disk
- [ ] `.gitignore` already covers this via T002 (`**/__pycache__/` pattern)

## Notes

- T002 adds the `.gitignore` pattern; this ticket removes the actual files.
