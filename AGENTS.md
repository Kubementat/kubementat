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

- python + uv — the product is a Python distribution, and the `kmt` console script is its only entry point
- tekton CI/CD — Tasks and Pipelines are generated and applied from Python; the CI images are built from
  `docker_files/`
- helm and helmfile — every release kubementat applies arrives through helmfile
- Docker

What is **not** in the stack is a layer of shell scripts. The verbs that used to be bash are Python in
`src/kubementat/` now, and the `.sh` files still in the tree build CI images and drive the e2e lanes —
nothing a `kmt` command execs.

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

## Branching and Releases

### Branching

- `main` is the release line. It points at what is releasable, and nothing lands on it except finished work.
- `develop` is the integration branch. A branch is merged there when it is done.
- Work happens in your own branch, in its own `git worktree` — never on `develop`, and never on `main`.
- `develop` to `main` is a release, not a habit. It happens once, by whoever is cutting the release, and it
  carries a `--no-ff` merge commit so that the integration boundary survives in the history.

Before releasing, fetch and confirm `develop` contains what both remotes call `main`:

```bash
git fetch --all
git merge-base --is-ancestor forgejo/main develop && git merge-base --is-ancestor origin/main develop
```

If that fails, `main` has commits `develop` does not: merge those in first. A stale local `main` is the usual
reason a release push gets rejected, and `--force` is not the answer to it.

### Releases (tag-based)

A release is an **annotated tag `vX.Y.Z` on the tip of `main`**, pushed to both remotes (`origin` is GitHub,
`forgejo` is the self-hosted one). Two things record it and they must move together:

- the tag, which is what the release *means*, and
- `pyproject.toml`'s `version` under `[project]` — the same number the tag carries, written without the `v`. That
  line is the whole version machinery: `src/kubementat/core/version.py` is its only reader, asking
  `importlib.metadata` when it is asked for, so `kmt --version` answers from it and nothing else does. There is no
  `VERSION` file and there must not be one: a second source of truth that `kmt --version` does not read is the
  bug, not the convenience.

If the two ever disagree, the tag wins and the `version` line is the bug. Because the number a running `kmt`
reports is the one baked into the installed distribution, a bumped `version` only shows up in a checkout after
`uv sync`.

Semver is measured by what an operator has to change, not by how much churn a release contains:

- **MAJOR** — a `kmt` verb or flag that is gone or renamed, a configuration directory that a tree no longer
  resolves the same way, an environment variable that stopped meaning what it meant. Anything that makes an
  existing command line, or an existing configuration tree, wrong.
- **MINOR** — a new verb, a new configuration key, a new component. Backwards compatible by construction.
- **PATCH** — fixes to existing behaviour.

The changelog is `CHANGELOG.md`; there is no `docs/releases/` directory, and its `# Unreleased` block is the
working copy. At release time that heading becomes `# X.Y.Z` with the date, a fresh `# Unreleased` opens above
it, and nothing already written is rewritten: the row that describes a change *is* the record of that change, and
rewording it to fit a tag is how a changelog becomes fiction. The raw list comes from the log between the previous
tag and `develop`:

```bash
git log --no-merges --pretty='- %s (%ad)' --date=short <previous-tag>..develop
```

Use it to prove no change went unrecorded, not to write the entries.

Release checklist:

1. **Green tree.** `uv run pytest -q`, and the e2e lane for what the release touches (`uv run python
tests/e2e/suite.py`; the full lane if the release changes the install or the uninstall). A release is not the
place to discover a lane was broken.
2. **Notes and version, in one commit on `develop`.** The `# Unreleased` heading moves to `# X.Y.Z`, the
   cross-check against `git log` says every change is accounted for, and `pyproject.toml` carries `X.Y.Z`.
3. **Move `main`.** `git switch main && git merge --no-ff develop`, then push both remotes
   (`git push origin main && git push forgejo main`). `--no-ff` is not ceremony: a fast-forward would leave no
   trace of where the release came from.
4. **Tag and push it.** `git tag -a vX.Y.Z -m "release vX.Y.Z"`, then `git push origin vX.Y.Z && git push
forgejo vX.Y.Z`. Annotated only, and never move a tag that is already on a remote — cut a new tag instead.

The tags before **2.0.0** (`0.1.0`, `0.2.0`, `0.3.0`) are lightweight and carry no `v`, and they stay as they
are; nothing reads a tag, so renaming them would cost history for no behaviour. From 2.0.0 the convention above
holds, and `git log --oneline 0.3.0..develop` is the raw list for anything cut after it.

## Architecture

We separate the cli implementation ( `src/cli` ) strictly from the core kubementat logic ( `src/kubementat` )

## Configuration Management

### The configuration directory

kubementat is configured by an **external configuration directory**: a directory the operator chooses that
is a git repository in its own right, prepared by `kmt config init`. It is not in this checkout, nothing is
written into it from this checkout, and no code path looks for a `platform_config/` beside the installed
package. That name survives only inside help text and old prose.

- It is **named, never located**: `--config-dir PATH` on the command, which outranks
  `$KUBEMENTAT_PLATFORM_CONFIG_DIR`. With neither, a command refuses — `core/config/paths.py` owns that
  precedence and refuses in one place, so no verb gets to have its own idea of what the default is.
- The layers, highest first: `<root>/<environment>/<team>/static{,.encrypted}.json`, then
  `<root>/<environment>/static{,.encrypted}.json`, then kubementat's own shipped defaults. `Config` in
  `src/kubementat/core/config/config.py` resolves them and `kmt config show` is the only command that
  prints which of the three answered a key.
- `<root>/kubementat.yaml` stamps the tree — the product constraint it was prepared for and its
  `schema_version` — and `core/config/stamp.py` is the only reader. A tree that predates the stamp is
  refused, not assumed.
- An `export` of a configuration key's name answers nothing. The variables that mean anything all point,
  none of them configures: `KUBEMENTAT_HOME` and `KUBEMENTAT_PLATFORM_CONFIG_DIR` for the two roots,
  `KUBECONFIG` and `ENVIRONMENT` for the machine and the environment, and `CLUSTER_KUBE_CONTEXT` /
  `CLUSTER_KUBECONFIG_PATH` as the layer above what an environment declares — a way to state a cluster from
  outside, not a way to configure one.
- An environment is exactly one cluster. The two `CLUSTER_*` keys are what make that true, and they are
  checked against each other across the whole tree before anything touches a cluster.

### Git-Crypt

**This repository ships no encrypted files, so nothing here is ever unlocked.** git-crypt belongs to the
configuration directory, and `config init` sets it up in the order that makes the first encrypted file land
encrypted. kubementat runs git-crypt for `init` and never again: a read that finds ciphertext is a named
refusal, and unlocking is the operator's act or the pipeline's, not the library's. The one exception is not
ours to make: the helmfile template asks only whether a `values.encrypted/<component>.yaml` exists, and
helm opens the bytes itself.

The verbs, the order and the reason each step sits where it does are in
[docs/README_GIT_CRYPT.md](docs/README_GIT_CRYPT.md).