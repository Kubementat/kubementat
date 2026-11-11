# T002: Clean Up .gitignore

> **Phase:** 1 — Quick Wins | **Effort:** 30 min | **Complexity:** Low

## Description

Update `.gitignore` with proper wildcard patterns and add missing entries for Python artifacts and git-crypt keys.

## Current .gitignore Issues

- No `**/__pycache__/` wildcard pattern (only specific paths)
- No `**/*.pyc` or `**/*.pyo` patterns
- No `.pytest_cache/` or `.mypy_cache/` patterns
- `.git-crypt/keys/` directory (contains GPG key material) is NOT excluded

## Changes Required

Add the following to `.gitignore`:

```
# Python artifacts
**/__pycache__/
**/*.pyc
**/*.pyo
.pytest_cache/
.mypy_cache/

# Git-crypt keys (sensitive)
.git-crypt/keys/
```

## Acceptance Criteria

- [ ] `**/__pycache__/` pattern added
- [ ] `**/*.pyc` and `**/*.pyo` patterns added
- [ ] `.pytest_cache/` and `.mypy_cache/` patterns added
- [ ] `.git-crypt/keys/` added
- [ ] Existing entries preserved

## Notes

- This ticket does NOT remove tracked files — that's handled by T006 (pycache) and T012 (.git-crypt keys).
