---
name: kmt
description: >-
  Operate kubementat through the kmt CLI: prepare and edit its configuration directory
  (config init / add-environment / add-team / set / unset / show / validate), install and
  uninstall the platform on a Kubernetes cluster, list the environments a tree has, install or
  remove single components (grafana, prometheus, loki, alloy, polaris, openbao, cert-manager,
  traefik), apply helmfile releases, read and install routing, set up and run Tekton pipelines
  plus generate their pipeline/run/trigger manifests, run cluster reads (analyze-deployment,
  deployment-logs, deployment-ssh, pod-images, pvc-usage, cluster-capacity, create-ingress,
  create-secret-from-json, service-account-token, all under `kmt kubernetes`), open tunnels to
  Grafana, the Tekton dashboard or OpenBao, and create team and Grafana accounts. Also answers
  questions about kubementat configuration, components, namespaces and repository layout.
  Triggers: kubementat, kmt, config init, config set, config show, environments list, helmfile
  apply, component install, routing status, tekton run-pipeline, tunnel grafana, tunnel
  openbao-ui, kubernetes analyze-deployment, deployment-logs, cluster-capacity,
  create-team-account, static.json, config-dir.
license: MIT
metadata:
  author: kubementat
  version: 1.1.0
  created: 2026-11-15
  last_reviewed: 2026-11-15
  review_interval_days: 90
activation: /kmt
provenance:
  maintainer: kubementat
  version: 1.1.0
  created: 2026-11-15
  source_references:
    - README.md of the kubementat repository
    - src/cli/kmt.py and src/cli/commands/*.py of the kubementat repository
    - tests/test_cli_surface.py, which pins the surface as a test
---

# /kmt — kubementat CLI

You drive kubementat, the opinionated Kubernetes platform automation, through `kmt`, its
click-based CLI. One binary, five kinds of work: configuration, lifecycle, components, Tekton,
cluster reads.

## Trigger

```
/kmt install kubementat on dev for team dev1
/kmt which ingress controller is on the cluster, and what would routing install do?
/kmt run the hello-world pipeline on dev and wait for it
/kmt why is the grafana pod crash-looping?
/kmt what does the dev tree actually answer for BASE_DOMAIN?
```

## Four rules

1. **Run it from a kubementat checkout**: `uv run kmt <command>`. From elsewhere, set
   `KUBEMENTAT_HOME=/path/to/checkout` (an empty `KUBEMENTAT_HOME` is an error, never a silent
   default — it names the checkout, never the configuration).
2. **Every command that reads or writes kubementat's configuration names its root**:
   `--config-dir PATH`, which outranks `$KUBEMENTAT_PLATFORM_CONFIG_DIR`. With neither, the run
   refuses — a configuration root is named and not located, and there is no `platform_config/`
   beside the checkout to fall back on. `KUBEMENTAT_HOME` answers which *code* checkout a command
   reads; `--config-dir` answers which *tree*. They are not interchangeable.
3. **Read the contract before running anything unfamiliar**: `uv run kmt <command> --help`, and
   `uv run kmt <group> --help` for the verbs on `component`, `config`, `environments`, `helm`,
   `kubernetes`, `routing`, `tekton`, `tunnel`, `users`.
   [references/command-reference.md](references/command-reference.md) is a snapshot of that
   output stamped with the commit it was read from — live `--help` outranks it.
4. **Check the cluster you are about to touch**: `uv run kmt environments list --config-dir D`
   prints every environment, the context that reaches it and the file that declared it, without
   contacting a cluster. `kubectl config current-context` is the fallback. kubementat operates on
   whatever the resolved context points at.

## Environment, team, and the tree they select

| Slot | How it is passed | Default |
|---|---|---|
| environment | first positional `ENVIRONMENT` on every command that reaches a cluster, else `--environment` where there is no slot for it (`config init`, `users *`), or `$ENVIRONMENT` | **none** — a command that touches a cluster refuses rather than guessing |
| team | positional `TEAM` where the command has one (`install`, `setup-secrets`, `setup-docker-registry-access`, `run-ci-container`, and nine of the ten `tekton` verbs), else `--team` (`tekton setup-cleanup-job`, `users *`), or `$TEAM` | **none** — kubementat does not name a team for a command that did not |

The layers, highest first: `<root>/<environment>/<team>/static{,.encrypted}.json`, then
`<root>/<environment>/static{,.encrypted}.json`, then kubementat's shipped defaults. `kmt config
show <env> [team]` is the only command that prints which of the three answered a key; a key you
never answered is not missing, it is a tagged default. `<root>/kubementat.yaml` stamps the tree
with the product constraint and `schema_version` it was prepared for, and a tree that predates the
stamp is refused, not assumed. `<root>/<environment>/tekton/` and
`<root>/<environment>/<team>/tekton/{tasks,pipelines,pipeline-runs,triggers}` are where Tekton
resources live; the team's replace the environment's, which replace the shipped ones.

What the shell exports is what the tree *points* at, never what it answers: `KUBEMENTAT_HOME` and
`KUBEMENTAT_PLATFORM_CONFIG_DIR` for the two roots, `KUBECONFIG` and `ENVIRONMENT` for the machine
and the environment, `CLUSTER_KUBE_CONTEXT` / `CLUSTER_KUBECONFIG_PATH` as the layer above what an
environment declares. Exporting a key's name answers nothing. `<environment>/kubeconfig.encrypted.yaml`
inside the tree outranks `$KUBECONFIG`, so a stray export cannot re-point a command that named an
environment.

An environment is exactly one cluster, and the two `CLUSTER_*` keys are checked against each other
across the tree before anything touches one.

## Command map

| Want | Run |
|---|---|
| A configuration tree, for the first time | `config init --environment dev --team dev1 --gpg-email you@example.com --config-dir ~/kmt-config` — a re-run on a tree it prepared writes no byte and names the step it skipped; one that carries `--set` answers is refused. Answer more keys with `config set` |
| Grow an existing tree | `config add-environment <env> --config-dir D`, `config add-team <env> <team> --config-dir D` |
| Answer or remove one key | `config set <env> KEY=VALUE [--team T] [--encrypted] [--generate KEY] [--from-file F]`, `config unset <env> KEY [--team T]` |
| What the tree answers / is it usable | `config show <env> [team]`, `config validate <env> [team]`, `environments list` |
| First install of the platform | `install <env> <team>` → `setup-docker-registry-access <env> <team>` → `setup-secrets <env> <team>` |
| One component on/off, no reinstall | `component install <env> <component>` / `component uninstall <env> <component>` |
| Apply a whole helmfile group | `helm apply <env> [--helmfile-label-filter 'group=standard'] [-y]` |
| Newest version of a chart this machine can see | `helm latest-version <env> <chart-path>` |
| Routing (traefik + cert-manager), never part of install | `routing status <env>` → `routing install <env> [--update] [-y]` |
| Pipelines and tasks | `tekton setup-pipelines <env> <team>`, `tekton run-pipeline <env> <team> [identifier]`, `tekton list <env> <team>`, `tekton setup-triggers <env> <team>`, `tekton setup-cleanup-job <env> [--team T]`, `tekton cleanup-pipeline-runs <env> <team> [--filter succeeded\|all]`, `tekton uninstall-pipelines <env> <team>` |
| New app's manifests | `tekton generate-deploy-pipeline <env> <team> <app>`, `tekton generate-pipeline-run <env> <team> <pipeline.yml>`, `tekton generate-triggers <env> <team> <app> [--pipeline-name N --trigger-type github\|gitlab]` — then review: app-specific parameters come out as `TODO` |
| Diagnose workloads | `kubernetes analyze-deployment <env> <ns> <deploy>`, `kubernetes deployment-logs <env> <ns> <deploy> [container] [--tail N]`, `kubernetes deployment-ssh <env> <ns> <deploy>`, `kubernetes pod-images <env> <ns>` |
| Capacity and storage | `kubernetes cluster-capacity <env>`, `kubernetes pvc-usage <env> [-h]` |
| Generate one object | `kubernetes create-ingress <env> <domain> <svc> <port> [-o file]`, `kubernetes create-secret-from-json <env> <ns> <name> <json-file>`, `kubernetes service-account-token <env> <ns> <sa>` |
| Look at a UI | `tunnel grafana\|tekton\|openbao-ui <env>`, `tunnel pod <env> <ns> <pod> --local-port P --remote-port P` |
| People | `users create-team-account <sa> --environment E --team T`, `create-team-account-read-only`, `delete-team-account`, `create-grafana-accounts <env>`, `create-grafana-read-only-account <env> <email> [name]` |
| A debug shell in the CI image | `run-ci-container <env> <team>` |
| Cluster teardown | `uninstall <env>` |

Read-only, nothing in that table writes: `config show`, `config validate`, `environments list`,
`routing status`, `helm latest-version`, `tekton list`, `kubernetes analyze-deployment`,
`kubernetes deployment-logs`, `kubernetes pod-images`, `kubernetes pvc-usage`,
`kubernetes cluster-capacity`, `kubernetes service-account-token`, and `kubernetes create-ingress`
while you leave its manifest on stdout. Everything else changes the cluster or the tree.
`-y/--yes` is for scripts; if a command asks, ask on.

Component names are the helmfile `component_name` labels: `prometheus`,
`prometheus-blackbox-exporter`, `prometheus-helm-exporter`, `grafana`, `loki`, `alloy`,
`polaris` (group `standard`, applied by `kmt install`), `cert-manager` and `traefik`
(group `routing`, applied by `kmt routing install`), `openbao` (group `extra`).
`tekton`, `goldilocks`, `vpa`, `cluster-cronjobs` are namespaces, not releases — `component
install` refuses them.

## Recipes

**From nothing to a running platform.** The tree is the configuration; nothing is exported first.

```bash
uv run kmt config init --environment dev --team dev1 \
    --set BASE_DOMAIN=example.com \
    --set DOCKER_REGISTRY_BASE_URL=registry.example.com \
    --set AUTOMATION_GIT_URL=git@github.com:you/kubementat_dev.git \
    --encrypted-set CLUSTER_MANAGER_EMAIL=you@example.com \
    --gpg-email you@example.com --config-dir ~/kmt-config
# The deploy keypair lands in the tree; the public half goes to your git host.
uv run kmt config validate dev --config-dir ~/kmt-config   # every fault named in one run
uv run kmt environments list --config-dir ~/kmt-config     # which cluster each name reaches
uv run kmt install dev dev1 --config-dir ~/kmt-config      # tekton, then group=standard, then pipelines
uv run kmt setup-docker-registry-access dev dev1 --config-dir ~/kmt-config
uv run kmt setup-secrets dev dev1 --config-dir ~/kmt-config   # from <root>/dev/dev1/static.encrypted.json
uv run kmt routing install dev --config-dir ~/kmt-config   # traefik only when the cluster has none
```

`kmt install` checks, before it touches anything, that `kubectl`, `helm`, `helmfile`, `helm diff`,
`tkn`, `git`, `gpg`, `python3` and `pip` answer. `git-crypt` is wanted by one verb, `config init`,
and is not on that list. A check that fails names the binary.

**Run a pipeline and wait**:
`uv run kmt tekton run-pipeline dev dev1 <name-or-path> --wait --config-dir D` — an absolute path,
or any argument with a separator that exists there, is used as given; a bare name resolves under
the team's `tekton/pipeline-runs/`; `global/<name>.yml` resolves under `tekton_ci/pipeline-runs/`;
without it the startable runs are listed and nothing is applied. `--wait` exits non-zero when the
run failed or never started; `--poll-interval` and `--timeout` (default 3 s, 1800 s) sit beside it.

**A new app**: `tekton generate-deploy-pipeline` → `tekton generate-pipeline-run` →
`tekton generate-triggers`, then `tekton setup-pipelines` and `tekton setup-triggers`. The
generators write into the team's own layer and cannot know the app's database or seeding, so
review what they emit.

**Something is broken**: `kubernetes analyze-deployment <env> <ns> <name>` →
`kubernetes deployment-logs <env> <ns> <name> --tail 200` → `kubernetes pvc-usage <env> -h` /
`kubernetes cluster-capacity <env>` for the node underneath.

## Gathering information about kubementat

- `uv run kmt --help` then `--help` on any command is the authoritative surface.
- `README.md` holds the workflow prose; `docs/README_PLATFORM_CONFIG.md`, `README_TEKTON.md`,
  `README_MONITORING_AND_LOGGING.md`, `README_GIT_CRYPT.md` hold the detail.
- `CHANGELOG.md` and `TODOs.md` record what changed and what is owed.
- `tests/test_cli_surface.py` lists the surface as a test and asserts that every group holds its
  commands and the old flat names are dead, so a command that is not there was not added
  deliberately.
- Regenerate [references/command-reference.md](references/command-reference.md) after the surface
  moves, from a kubementat checkout, and it stamps the commit it was read from:
  `KMT_BIN=... python3 scripts/kmt_reference.py > references/command-reference.md`
  (`kmt` on PATH, else `uv run kmt`, is tried for you).

## Failure modes that are not bugs

- `... is set but empty` → an empty `KUBEMENTAT_HOME`/`KUBEMENTAT_PLATFORM_CONFIG_DIR`; give it a
  path or unset it.
- `Missing argument 'ENVIRONMENT'` / `kubementat has no default for the environment` → name it;
  nothing guesses `dev` any more.
- `No such command 'analyze-deployment'` → the flat names are dead. Ten cluster verbs answer to
  `kmt kubernetes <verb>`, and the three generators plus `setup-cleanup-job` to
  `kmt tekton <verb>`; the cluster verbs also grew an environment in the first positional, so
  `kubernetes pod-images <env> <ns>` and not `pod-images <ns>`. `initialize` is gone,
  `helm-version` is `helm latest-version`, `deploy-logs` is `kubernetes deployment-logs`.
- `Got unexpected extra argument` on a `config` verb → the configuration root is not a positional
  any more; it is `--config-dir`.
- `config init` refuses with neither `--config-dir` nor the variable → that is the rule, not a
  bug: point it at the directory you want it to prepare.
- `config init` warns and writes nothing → the tree is already prepared. That is correct; it names
  the step it skipped.
- `config init` on a second run then says `static.json ... is not what kubementat would write` →
  the first run carried `--set` answers, which makes the file differ from the template this verb
  would write, and that refusal does not distinguish the two cases. A re-run of the same recipe is
  therefore not safe: answer the rest with `config set`, not by running `config init --set` again.
- A dependency check failure names the binary. `helm diff` is a helm *plugin*:
  `helm plugin install https://github.com/databus23/helm-diff --verify=false`.
- `routing status` says a foreign controller → leave it alone; `--update` installs kubementat's
  release beside it, so a second controller and class.
- Encrypted config reads as ciphertext → kubementat never unlocks; `git-crypt unlock` is the
  operator's act, not the library's.
