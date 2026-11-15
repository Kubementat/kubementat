---
name: kmt
description: >-
  Operate kubementat through the kmt CLI: install, initialize and uninstall the platform on a
  Kubernetes cluster, install or remove single components (grafana, prometheus, loki, alloy,
  polaris, openbao, cert-manager, traefik), apply helmfile releases, read and install routing,
  set up and run Tekton pipelines plus generate their pipeline/run/trigger manifests, run
  cluster diagnostics (analyze-deployment, pod-images, pvc-usage, cluster-capacity, deploy-logs,
  deploy-ssh, service-account-token), open tunnels to Grafana, the Tekton dashboard or OpenBao,
  create team and Grafana accounts, and generate Ingress or Secret objects. Also answers
  questions about kubementat configuration, components, namespaces and repository layout.
  Triggers: kubementat, kmt, helmfile apply, component install, routing status, tekton
  run-pipeline, tunnel grafana, tunnel openbao, create-team-account, platform_config,
  static.json, cluster-capacity, deploy-logs.
license: MIT
metadata:
  author: kubementat
  version: 1.0.0
  created: 2026-11-15
  last_reviewed: 2026-11-15
  review_interval_days: 90
activation: /kmt
provenance:
  maintainer: kubementat
  version: 1.0.0
  created: 2026-11-15
  source_references:
    - README.md of the kubementat repository
    - src/cli/kmt.py and src/cli/commands/*.py of the kubementat repository
---

# /kmt — kubementat CLI

You drive kubementat, the opinionated Kubernetes platform automation, through `kmt`, its
click-based CLI. One binary, four kinds of work: lifecycle, components, Tekton, cluster reads.

## Trigger

```
/kmt install kubementat on dev for team dev1
/kmt which ingress controller is on the cluster, and what would routing install do?
/kmt run the hello-world pipeline on dev and wait for it
/kmt why is the grafana pod crash-looping?
/kmt how do I add a component without reinstalling the platform?
```

## Four rules

1. **Run it from a kubementat checkout**: `uv run kmt <command>`. From elsewhere, set
   `KUBEMENTAT_HOME=/path/to/checkout` (an empty `KUBEMENTAT_HOME` is an error, never a
   silent default). `KUBEMENTAT_PLATFORM_CONFIG_DIR` overrides `platform_config/`.
2. **Read the contract before running anything unfamiliar**: `uv run kmt <command> --help`,
   and `uv run kmt <group> --help` for the verbs on `component`, `helm`, `routing`, `tekton`,
   `tunnel`, `users`. [references/command-reference.md](references/command-reference.md) is a
   snapshot of that output — live `--help` outranks it.
3. **Read before you write, and write only what was asked.** Read-only: `routing status`,
   `analyze-deployment`, `pod-images`, `pvc-usage`, `cluster-capacity`, `deploy-logs`,
   `helm-version`, `tekton list`, `service-account-token`, `create-ingress` (stdout only).
   Everything else changes the cluster or the checkout. `-y/--yes` is for scripts; if a
   command asks, ask on.
4. **Check the cluster you are about to touch**: `kubectl config current-context` first.
   kubementat operates on whatever kubeconfig the current context points at.

## Environment and team

| Slot | How it is passed | Default |
|---|---|---|
| environment | positional `ENVIRONMENT`, or `$ENVIRONMENT` | required on lifecycle/component/tekton commands, else `dev` |
| team | positional `TEAM`, `--team`, or `$TEAM` | `dev1` |

Configuration they select: `platform_config/<environment>/static.json` (public) and
`static.encrypted.json` (git-crypt; run `git-crypt unlock` to read or write it), plus the team's
own `platform_config/<environment>/<team>/` — including its `tekton/pipeline-runs/` and
`triggers/`. `src/kubementat/constants.py` holds the fallbacks (namespaces, service selectors,
pinned versions); config is the source of truth, code is the fallback.

## Command map

| Want | Run |
|---|---|
| First install of the platform | `initialize <env> <team>` → `install <env> <team>` → `setup-docker-registry-access` / `setup-secrets` |
| One component on/off, no reinstall | `component install <env> <component>` / `component uninstall <env> <component>` |
| Apply a whole helmfile group | `helm apply <env> [--helmfile-label-filter 'group=standard']` |
| Routing (traefik + cert-manager), never part of install | `routing status <env>` → `routing install <env> [--update]` |
| Pipelines and tasks | `tekton setup-pipelines <env> <team>`, `tekton run-pipeline <env> <team> [run]`, `tekton list`, `tekton cleanup-pipeline-runs`, `tekton uninstall-pipelines` |
| New app's manifests | `tekton-generate-deploy-pipeline`, `tekton-generate-pipeline-run <env> <team> <pipeline.yml>`, `tekton-generate-triggers` — then review: app-specific parameters come out as `TODO` |
| Diagnose workloads | `analyze-deployment <ns> <deploy>`, `deploy-logs <ns> <deploy>`, `deploy-ssh <ns> <deploy>`, `pod-images <ns>` |
| Capacity and storage | `cluster-capacity`, `pvc-usage [-h]` |
| Look at a UI | `tunnel grafana|tekton|openbao-ui [env]`, `tunnel pod <ns> <pod> --local-port P --remote-port P` |
| People | `users create-team-account <sa>`, `create-team-account-read-only`, `delete-team-account`, `create-grafana-accounts` |
| Cluster teardown | `uninstall <env>` |

Component names are the helmfile `component_name` labels: `prometheus`,
`prometheus-blackbox-exporter`, `prometheus-helm-exporter`, `grafana`, `loki`, `alloy`,
`polaris` (group `standard`, applied by `kmt install`), `cert-manager` and `traefik`
(group `routing`, applied by `kmt routing install`), `openbao` (group `extra`).
`tekton`, `goldilocks`, `vpa`, `cluster-cronjobs` are namespaces, not releases — `component
install` refuses them.

## Recipes

**Install from a clean checkout** — needs 8 env vars exported first (`BASE_DOMAIN`,
`AUTOMATION_GIT_URL`, `AUTOMATION_GIT_SERVER_HOST`, `AUTOMATION_GIT_SERVER_PORT`,
`AUTOMATION_GIT_SERVER_SSH_USER`, `KUBERNETES_DEFAULT_STORAGE_CLASS`,
`DOCKER_REGISTRY_BASE_URL`, `CLUSTER_MANAGER_EMAIL`), and `kubectl`, `helm`, `helmfile`,
`helm diff`, `tkn`, `git`, `gpg`, `python3` on PATH.

```bash
uv run kmt initialize dev dev1     # idempotent: an initialized env prints what it spared and writes nothing
uv run kmt install dev dev1        # tekton, then helmfile group=standard, then pipelines + triggers
uv run kmt setup-secrets dev dev1  # SSH deploy keys from static.encrypted.json
uv run kmt routing install dev     # traefik only when the cluster has none
```

**Run a pipeline and wait**: `uv run kmt tekton run-pipeline dev dev1 <name-or-path> --wait`
(a bare name resolves under the team's `tekton/pipeline-runs/`, `global/<name>.yml` under
`tekton_ci/pipeline-runs/`; without it the startable runs are listed).

**Something is broken**: `analyze-deployment <ns> <name>` → `deploy-logs <ns> <name> --tail
200` → `pvc-usage -h` / `cluster-capacity` for the node underneath.

## Gathering information about kubementat

- `uv run kmt --help` then `--help` on any command is the authoritative surface.
- `README.md` holds the workflow prose; `docs/README_PLATFORM_CONFIG.md`,
  `README_TEKTON.md`, `README_MONITORING_AND_LOGGING.md`, `README_GIT_CRYPT.md` hold the detail.
- `CHANGELOG.md` and `TODOs.md` record what changed and what is owed.
- `tests/test_cli_surface.py` lists the surface as a test, so a command that is not there was
  not added deliberately.
- Regenerate [references/command-reference.md](references/command-reference.md) after the
  surface moves, from a kubementat checkout: `KMT_BIN=... python3 scripts/kmt_reference.py >
  references/command-reference.md` (`kmt` on PATH, else `uv run kmt`, is tried for you).

## Failure modes that are not bugs

- `... is set but empty` → an empty `KUBEMENTAT_HOME`/`KUBEMENTAT_PLATFORM_CONFIG_DIR`; give it
  a path or unset it.
- `initialize` warns and writes nothing → the environment already has configuration. That is
  correct; delete what you want regenerated and run it again.
- A dependency check failure names the binary. `helm diff` is a helm *plugin*:
  `helm plugin install https://github.com/databus23/helm-diff --verify=false`.
- `routing status` says a foreign controller → leave it alone; `--update` installs
  kubementat's release beside it, so a second controller and class.
- Encrypted config reads as garbage → `git-crypt unlock` first.
