# Kubementat tests

Two suites, for two questions:

```bash
uv run pytest -q                                  # unit suite: no cluster, ~20 s
uv run python tests/e2e/suite.py --lane smoke     # a throwaway VM: install, verify, uninstall (~9 min)
uv run python tests/e2e/suite.py --lane full      # every stage, including the reinstall (~29 min)
```

The unit suite is the CI gate. The e2e suite runs when a person decides to run it; its
`result.json` names the git SHA it tested. What belongs in which is in
[docs/README_E2E_TESTS.md](../docs/README_E2E_TESTS.md), and the lanes themselves in
[`e2e/`](e2e/).

## Layout

One directory per product area. Each file is named for the behaviour its cases prove.

| Directory | What it covers |
|---|---|
| `config_root_and_layers/` | naming the configuration root (`--config-dir` over the variable), the layers and which answers a key, locked files, `config show` |
| `config_init/` | `config init`, `config set/unset`, adding layers, `config validate`, the stamp, the deployer keypair |
| `cluster_identity/` | resolving the one cluster an environment declares, kubeconfigs, the one-cluster-per-environment check |
| `cli/` | the command surface, consent, refusals for a missing environment or team, `shell.run`, the version |
| `install/` | helmfile apply, the manifest and values layers, release knobs, component install, version warnings |
| `uninstall/` | the uninstall sweep, what it reports as residue, component uninstall |
| `routing/` | routing install and status, and how kubementat tells its Traefik from someone else's |
| `tekton/` | the Tekton install and preflight, generated pipelines and triggers, pipeline runs, the cleanup job |
| `accounts/` | team accounts, their tokens, kubeconfigs and roles, Grafana accounts |
| `kubernetes/` | secrets, applying a directory, diagnostics, PVC usage |
| `shipped_manifests/` | facts about the shipped Tekton tasks, helmfile manifest and values files |

## How a test reaches no cluster

`conftest.py` makes every test start from a machine that names no configuration root, no
environment and no cluster, and refuses any connection the Kubernetes client tries to make. Two
seams stand in for the cluster:

- **`recorded_api`** replaces every client kubementat builds with a recorder bound to the cluster
  it was built for. `cluster_state` and `cluster_deletes` say what the cluster holds and how it
  answers; `recorded_operations` reduces the log to operations a test can compare.
- **`fake_binaries`** puts recorder programs named `helm`, `helmfile`, `kubectl`, `git-crypt` and
  `tkn` first on `PATH` (`shims/`), so a test reads the argv a command ran and decides what it
  printed.

`fixture_repo` writes a small configuration tree that answers every read, and `dry_run_env`
points a `kmt` run at it. `run_kmt` runs `kmt` as its own process, with `apiserver_fake` as its
cluster.

## Rules

- **A case asserts kubementat's behaviour, and nothing else.** No case reads this repository's
  source, checks the docs against the code, guards that something deleted stays deleted, or tests
  the e2e harness. Reading a shipped artefact (a Tekton task, the helmfile manifest, a values file)
  is a fact about the product and belongs in `shipped_manifests/`.
- **One case per behaviour.** A new case earns its place by asserting something no other case
  asserts: a refusal, an order, a precedence, what is left untouched. A case that reaches the same
  lines to assert the same thing is a duplicate.
- **Names describe behaviour, never a ticket.** `test_relative_script_path_is_resolved_against_the_checkout`,
  not `test_run_script_fin46_relative_path`. Parametrized ids are the values' names, not
  `command0`…`command4`. `test_test_name_conventions.py` enforces the tracker-id part.
- **Docstrings are one line**, saying what the case proves.
