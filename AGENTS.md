# Kubementat Developer Guide

## Main instructions

Use the `mcp__codegraph__codegraph_explore` tool for searching the codebase.

## Implementation rules

- **A comment is written for a reader who has never seen the specification.** No `§x.y`, no `R-x.y`, no
`FR-nn`, no `TD-n`/`N-n`, no ticket id, no plan or spec filename inside a comment or a docstring. State
the rule in prose, and say what the code does — the file it lives in is not an argument, and a pointer
to a document that will be superseded is how a comment goes stale while the code stays correct. Where
a decision genuinely needs its reason, the reason is written out; where it needs its source, that
belongs in `docs/`, not in the source.
- **The test suite covers kubementat's behaviour and nothing else.** No self-audits of the harness or of
the suite's own hygiene: not "the lane must not write configuration with jq", not "this module must
not declare that constant", not "the docs agree with the code". Those are checked once, by reading,
when the change lands. A test may assert what a stage proved about the product, and may read the argv
a capability row resolves to; it may not police this repository's own source.
- **No backwards compatibility, no shims, no aliases, no deprecation branches.** Behaviour changes when it
changes, and `CHANGELOG.md` records it.
- **Test names describe behaviour, never ticket ids.**
`tests/test_test_name_conventions.py` enforces that, and `tests/README.md` explains the convention.

## Software Stack

- python + uv
- bash scripts
- tekton CI/CD
- helm &amp; helmfile
- Docker

### Python Environment Setup

```bash
uv sync --all-extras  # Install dependencies with all extras
```

## Test

```bash
uv run pytest -q                          # unit suite, no cluster, ~2 s
uv run python tests/e2e/suite.py               # real VM: prepare, install, verify, uninstall
uv run python tests/e2e/suite.py --lane full --scope full   # every stage incl. reinstall, run by hand
```

The e2e suite is not a CI gate; attach its `result.json` (which names the git SHA) to the work you
report. Test and case names describe behaviour, never ticket ids — `tests/test_test_name_conventions.py`
enforces that, and `tests/README.md` explains the convention

## Architecture

We separate the cli implementation ( `src/cli` ) strictly from the core kubementat logic ( `src/kubementat` )

## Configuration Management

### Configuration Files

- Stored in an external config directory:`<external_config_dir>/{environment}/{team}/` directory
- Two JSON files: `static.json` (public) and `static.encrypted.json` (git-crypt protected)
- Loaded via `Config` class in `src/kubementat/config.py`

### Git-Crypt

Repository uses git-crypt for encrypting sensitive configuration:

```bash
git-crypt unlock  # Unlock encrypted files before working with platform_config/
```

More info on git-crypt can be found in [docs/README_GIT_CRYPT.md](docs/README_GIT_CRYPT.md).