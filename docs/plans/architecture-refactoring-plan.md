# Kubementat Architecture Refactoring Plan

A complete, self-contained brief for an implementing agent. All facts below were verified against
the tree at commit `5cb48c5`; re-verify with the commands given before acting on any number.

---

## 1. Goal

Reorganize `src/kubementat/` from a flat package with two god objects, three dependency cycles,
and three layering inversions into a layered architecture where the dependency rule

> `cli` → `automations` → `infrastructure` → `core`, never back

holds by construction. No behaviour changes. This is a pure reorganization: the unit suite is the
gate, and it is already a behavioural spec.

## 2. Project rules that bind this work (from `AGENTS.md` — obey these)

- **No backwards compatibility, no shims, no aliases, no deprecation branches.** Every move is a
  move: old module paths vanish, all importers (including tests) update in the same commit.
  `CHANGELOG.md` records the change.
- **Comments are written for a reader who has never seen the specification.** No ticket ids, no
  plan/filename pointers in code comments. When you move code, its existing comments (which are
  unusually good and explain *why*) travel with it unchanged unless the statement became false.
- **Never add tests that police this repository's own source** — no architecture tests, no
  "the module must not import X" tests, no self-audits of the harness. Dependency rules are
  verified by reading and by an optional `lint-imports` run outside pytest.
- **Test names describe behaviour, never ticket ids.** You are renaming imports in tests, not
  adding/removing test cases. If a test case must move, its name still describes behaviour.
- `uv run pytest -q` is the unit gate (~2 s, no cluster). `uv run python tests/e2e/suite.py` is
  not a CI gate; run it once at the end of the whole plan and attach its `result.json`.
- Use `mcp__codegraph__codegraph_explore` before edits to get context instead of grep loops.
- Do not run more than 3 subagents / parallel sessions from one orchestrator; one writer per
  worktree.

## 3. Verified current-state facts

### 3.1 Size hot spots

| File | Lines | Public surface | Problem |
|---|---:|---|---|
| `src/kubementat/automation.py` | 2 824 | `Automation`: 21 public methods / 55 total | god object: install, uninstall, secrets, accounts, tunnels, ingress/linkerd removal, sweep, resource apply |
| `src/kubementat/kubernetes_utils.py` | 2 098 | `KubernetesUtils`: 77 public methods / 81 total | god facade; 11 importing modules |
| `src/kubementat/tekton_utils.py` | 2 443 | `TektonUtils`: 18 public | install + triggers + generation + run lifecycle in one class |
| `src/kubementat/cluster.py` | 1 554 | `Context`, `ClusterTarget`, `EnvironmentRegistry`, kubeconfig parsing, comparison, errors | identity + I/O + taxonomy mixed |
| `src/kubementat/helm_utils.py` | 1 811 | `HelmUtils`: 8 public | mostly fine |
| `src/kubementat/config.py` | 1 455 | `Config`: 16 public | Config mixed with paths/versions/stamp |

### 3.2 Dependency cycles (verified with an AST import walk)

1. `automation ⇄ tekton_utils` — `automation.py:20` imports `TektonUtils`;
   `tekton_utils.py:1561` does `from kubementat.automation import Automation` *inside a function
   body* (the comment says it is only deferred to avoid the import-time deadlock).
2. `config ⇄ config_derived` — `config.py` imports `config_derived`; `config_derived.py:80` imports
   `TeamNotSuppliedError` from `config` inside a function.
3. `cluster ⇄ config` — none, but `config_init`/`config_validate` import `cluster`, and the
   cycle-free-but-messy chain `config_validate → config_init → cluster → config` makes every
   config-introspection module depend on cluster-resolution code.

### 3.3 Layering inversions

- `config_answers.py:188`: `class _ManifestReader(HelmUtils)` — subclass that replaces only the
  constructor to inherit `helmfile_paths`, `helmfile_state_path`, `component_release_index`
  without a `Context`. The config layer depends on the helm/cluster layer to read the release
  index. The read itself (`helmfile` manifests on disk) is pure filesystem — the dependency is an
  accident, not a necessity.
- `ingress_generator.py` imports `COMPONENT`, `DEFAULT_INGRESS_CLASSES` from
  `automations/routing/routing.py` — a leaf tool depends on a deep automation for two constants.
- `config_init.py:1158-1167` prints warnings with `click.echo` from the domain layer. (This is the
  *only* `click.*` output in `kubementat/`; everywhere else uses `logging` — 437 call sites.)
- Three exceptions subclass `click.ClickException` directly: `DeployerKeyMissingError`,
  `LockedConfigFileError`, `ObsoleteTriggerDirectoryError` — domain errors hardwired to the CLI.

### 3.4 Error taxonomy location

36 exception classes live across 15 modules. Full inventory (module → classes):
`automation.py`: UninstallIncompleteError, MysqlNotConfiguredError, ResourceWithoutNameError;
`ci_utils.py`: CiImageNotConfiguredError; `cluster.py`: ClusterResolutionError (base),
ClusterNotConfiguredError, ClusterContextNotFoundError, ClusterFileUnreadableError,
ServiceAccountNotMountedError, StatedClusterMismatchError (all subclass ClusterResolutionError),
ClusterIdentityCollisionError; `config.py`: TeamNotSuppliedError; `config_keys.py`:
DeployerKeyMissingError(click.ClickException); `config_reads.py`: LockedConfigFileError(click.ClickException);
`grafana_client.py`: GrafanaError (base), GrafanaConfigurationError, GrafanaUnauthorizedError;
`ingress_generator.py`: IngressTargetExistsError, IngressArgumentNotSuppliedError,
IngressPortNotUsableError; `kubernetes_utils.py`: PodNotFoundError,
ServiceAccountTokenNotFoundError, NamespaceNotFoundError; `shell.py`: CommandExecutionError;
`tekton_utils.py`: TektonControllerWithoutConfiguredApiError, PipelineNamespaceNotConfiguredError,
TektonReleaseVersionNotConfiguredError, TektonWebhookNotReachableError,
ObsoleteTriggerDirectoryError(click.ClickException), CleanupJobImageNotConfiguredError,
GenerationTemplateNotFoundError, GenerationPlaceholderNotConfiguredError,
GenerationArgumentNotSuppliedError, GenerationInputNotFoundError, GenerationInputNotUsableError,
GenerationTargetExistsError.

### 3.5 What is already good — keep and imitate

- `src/cli/` → `kubementat/` is strictly one-directional; `kubementat/` imports `cli` nowhere.
- `automations/routing/` (`routing.py`, `traefik_presence.py`) already demonstrates the
  vertical-slice pattern with small result dataclasses (`TraefikFinding`, `ProbeOutcome`,
  `HelmRelease`, `RoutingInstallResult`). This is the template for all new slices.
- `Context` (in `cluster.py`) is the single-resolution object every service takes. The `X(ctx)`
  constructor convention stays.
- Module-level pure helpers (`kubectl_target_args`, `helm_target_args`, `parse_cpu`,
  `parse_memory`, `plural_for_kind`) — these are fine, they just live in the wrong file.

### 3.6 Test-coupling facts (drive the migration mechanics)

181 files under `tests/`. They reference implementation by dotted string paths (monkeypatch
targets and imports), roughly: `config` ×51, `cluster` ×43, `tekton_utils` ×27,
`kubernetes_utils` ×27, `automation` ×26, `helm_utils` ×23, `config_init` ×17,
`automations.routing` ×12, `shell` ×7. **Every move therefore ends with a repo-wide
`grep -rn "<old.dotted.path>" tests src | ...` sweep, updating both imports and monkeypatch
target strings.** A string that is not updated silently stops patching — the test keeps passing
and testing the wrong thing, or patches a nonexistent attribute and errors. Verify each move:
for every renamed module path, `grep -r "<old>" tests src` must return nothing, and the full
unit suite must be green.

## 4. Target architecture

```
src/kubementat/
│
├── errors.py                    # all 36 exception classes + KubementatError base
│
├── core/                        # pure: filesystem + data structures, no subprocess, no cluster, no click
│   ├── __init__.py
│   ├── constants.py             # current constants.py (add COMPONENT, DEFAULT_INGRESS_CLASSES here)
│   ├── version.py, assets/      # unchanged, moved under core/
│   ├── config/
│   │   ├── config.py            # Config, layer precedence
│   │   ├── derived.py           # current config_derived.py (cycle broken; see P0-1)
│   │   ├── keys.py              # key naming / component_config_key
│   │   ├── reads.py             # current config_reads.py, read_decrypted (encrypted half)
│   │   ├── paths.py             # default_kubementat_main_dir, default_platform_config_dir,
│   │   │                        #   resolve_platform_config_dir, Config path properties
│   │   ├── stamp.py             # ConfigurationStamp, read_configuration_stamp, check_configuration_stamp
│   │   ├── versions.py          # product_version, satisfies_version_constraint, version gap sentences
│   │   └── release_index.py     # pure helmfile-manifest index reader (see P0-2) — HelmUtils will call it
│   ├── identity/
│   │   ├── context.py           # Context (from cluster.py)
│   │   ├── target.py            # ClusterTarget (`_normalise_address` lives in kubeconfig.py — see below)
│   │   ├── kubeconfig.py        # kubeconfig reading/parsing, service-account material
│   │   ├── registry.py          # EnvironmentRegistry, EnvironmentResolution, ClusterComparison
│   │   └── comparison.py        # running_cluster_address + the 1:1 check primitives
│   └── quantities.py            # parse_cpu, parse_memory, plural_for_kind (pure math/naming)
│
├── infrastructure/
│   ├── shell.py                 # run/CommandResult/CommandExecutionError, unchanged
│   ├── kubernetes/
│   │   ├── clients.py           # api factories, dynamic_client, kubectl_args, target,
│   │   │                        #   cluster_is_reachable, self_can, api_version_is_served
│   │   ├── apply.py             # apply_resources — THE fix for the automation⇄tekton cycle
│   │   ├── namespaces.py        # create/delete/list/wait_namespaces_gone/namespace_phases
│   │   ├── rbac.py              # all roles/bindings/cluster-roles + list/delete
│   │   ├── secrets.py           # secrets, docker secrets, SA-token secrets
│   │   ├── serviceaccounts.py   # SA create/replace/patch/list + token read
│   │   ├── customresources.py   # CR get/list/create_or_replace/delete + CRDs
│   │   ├── workloads.py         # pods, deployments, services, PVCs, node/pod summaries,
│   │   │                        #   cluster_capacity, analyze_deployment, list_pod_images
│   │   ├── exec.py              # exec_in_pod, get_pod_logs, stream_logs, stream_deployment_logs,
│   │   │                        #   open_deployment_shell
│   │   └── tunnel.py            # open_tunnel (the kubectl port-forward primitive)
│   ├── helm/                    # current helm_utils.py split into helmfile.py (apply/uninstall/
│   │   │                        #   paths/state) and cli.py (helm binary: latest version, list)
│   │   └── ...
│   ├── tekton/
│   │   ├── install.py           # install, ClusterRole, webhook wait, api-version preflight
│   │   ├── triggers.py          # setup_triggers, RBAC setup, webhook secrets
│   │   ├── generation.py        # generate_deploy_pipeline, generate_trigger_configs,
│   │   │                        #   generate_pipeline_run, generate_random_postfix, templates
│   │   └── runs.py              # run/wait/list/check_running/cleanup_*
│   └── grafana/
│       ├── client.py            # current grafana_client.py
│       └── accounts.py          # current grafana_accounts.py
│
├── automations/                 # vertical feature slices, the routing/ pattern
│   ├── install/                 # from Automation: install_kubementat, check_dependencies_are_installed,
│   │   │                        #   validate_cluster_prompt, check_cluster_permissions, setup_pipelines,
│   │   │                        #   setup_secrets, setup_docker_registry_access
│   │   └── ...
│   ├── uninstall/               # uninstall_kubementat, uninstall_pipelines, namespaces_kubementat_owns,
│   │   │                        #   namespace_sweep, uninstall_linkerd, uninstall_nginx_ingress
│   │   └── ...
│   ├── tunnels/                 # open_tunnel_grafana, open_tunnel_tekton_dashboard, open_tunnel_openbao
│   ├── accounts/                # create_team_account, create_team_account_read_only, delete_team_account
│   ├── ci/                      # ci_utils.py: CiUtils
│   ├── ingress/                 # ingress_generator.py (constants now come from core.constants)
│   └── routing/                 # unchanged (reference pattern)
│
src/cli/                         # unchanged in shape; imports shift to new module paths
```

Dependency rule (the only thing a reviewer needs to check per PR):
- `core/*` imports only stdlib, `errors`, and other `core/*`.
- `infrastructure/*` imports `core/*`, `errors`, itself — never `automations` or `cli`.
- `automations/*` imports `infrastructure/*`, `core/*`, `errors` — and must not import
  another `automations/*` slice, except `automations.routing` primitives that were designed to
  be shared (prefer duplicating a constant into `core.constants`).
- `cli/*` imports anything below; nothing below imports `cli`.

## 5. Phase plan

Each phase is one reviewable, green-on-`pytest -q` unit of work. Phase numbers are strictly
sequential (later phases assume earlier ones landed). Within a phase, tasks are independent.

### Phase 0 — break the cycles and inversions (no structural moves)

**P0-1: `config ⇄ config_derived`.** Create `src/kubementat/errors.py` (only this file, no other
moves yet): `class KubementatError(Exception)` plus `TeamNotSuppliedError`. `config.py` and
`config_derived.py` both import from `errors`; delete the function-level import in
`config_derived.require_team`. Verify: `grep -rn "from kubementat.config import" src/kubementat/config_derived.py`
returns nothing.

**P0-2: `config_answers → helm_utils`.** Extract the pure index reader. Move the manifest-parsing
half of `HelmUtils.component_release_index` / `helmfile_paths` / `helmfile_state_path` (the code
that reads and parses helmfile YAML, no subprocess) into
`src/kubementat/config_reads_index.py` as plain functions taking a `Config`
(`read_release_index(config) -> dict`, `helmfile_paths_for(config)`, `helmfile_state_path_for(...)`).
`HelmUtils` delegates to it; `config_answers` imports it directly and deletes
`class _ManifestReader(HelmUtils)` entirely. Verify: `config_answers.py` has no `helm_utils`
import; `grep -rn "_ManifestReader" src tests` returns nothing.

**P0-3: `ingress_generator → automations.routing`.** Move `COMPONENT` and
`DEFAULT_INGRESS_CLASSES` into `constants.py`; both modules import from `constants`. Verify:
`ingress_generator.py` imports nothing from `automations`.

**P0-4: lift `apply_resources`.** Move `Automation.apply_resources` (and its private helpers
`_resource_files`, `_metadata_name`, `_document_target`) verbatim into
`src/kubementat/k8s_apply.py` as module functions `apply_resources(ctx_or_k8s, resource_dir,
target_namespace, kind, group, version)`. `automation.setup_pipelines` and
`tekton_utils.setup_triggers` call it; the `from kubementat.automation import Automation` inside
`tekton_utils.py` (around line 1561) is deleted. **This is the actual cycle fix.** Verify:
neither module imports the other (`grep -n "import" src/kubementat/tekton_utils.py | grep
automation` and the reverse both empty).

Phase-0 gate: unit suite green; the AST import walk in §3.2 shows no `automation ⇄ tekton_utils`
edge and no `config ⇄ config_derived` edge.

### Phase 1 — `errors.py`, complete

Move all 36 classes from §3.4 into `errors.py`, grouped by domain with per-domain comments kept.
Hierarchy: `KubementatError` base; cluster errors keep `ClusterResolutionError` as intermediate
base; grafana keeps `GrafanaError`; tekton generation errors may get a
`GenerationError` intermediate if it reads well. The three `click.ClickException` subclasses
(`DeployerKeyMissingError`, `LockedConfigFileError`, `ObsoleteTriggerDirectoryError`) become plain
subclasses of `KubementatError`; the CLI renders them — check `src/cli/context.py` (`kmt_context`
already wraps refusals as `ClickException`) and where `config.py` / `tekton.py` commands catch
them, adding the rendering there. Update every `raise`/`except`/import site in `src` and `tests`.
Verify: `grep -rn "class .*Error\(.*Exception\|class .*Error(Exception)" src/kubementat/`
returns only `errors.py`.

### Phase 2 — package layout with pure moves (`core/`, `infrastructure/`)

Mechanical relocation. Order matters: create packages with `__init__.py` files, then rewrite all
dotted paths. Suggested task split (each is independently green-able):

- **P2-1** `constants.py`, `version.py`, `assets/` → `core/`.
- **P2-2** config modules: `config.py` → `core/config/config.py`, `config_derived.py` →
  `core/config/derived.py`, `config_reads.py` → `core/config/reads.py`, `config_answers`,
  `config_init`, `config_keys`, `config_bootstrap`, `config_validate` stay flat modules under
  `src/kubementat/` **for now** (they are verb-orchestration; moving them later is cheap). Split
  `config.py` into `config.py` / `paths.py` / `stamp.py` / `versions.py` / `keys.py` — split by
  the existing sections, do not redesign `Config`.
- **P2-3** `cluster.py` split → `core/identity/` per §4 table. `Context` goes to `context.py`;
  `ClusterTarget` to `target.py`. Module-level helpers (`default_kubeconfig_path`,
  `in_tree_kubeconfig`, `running_cluster_address`, `environment_file_missing`) land in the file
  whose subject they name.

  Two assignments in this table are wrong and the census of `cluster.py` shows why; they were corrected
  before the split was dispatched rather than discovered mid-move. `_normalise_address` goes to
  `kubeconfig.py`: two of its three callers are there, and putting it in `target.py` adds
  `kubeconfig → target` beside the existing `target → kubeconfig`, which is the split's only two-file
  cycle. And the seven key and source-label constants go to `core/constants.py` below all five files —
  `CLUSTER_KUBE_CONTEXT_KEY` in `context.py` because it answers "which kube context" makes
  `target.py` and `registry.py` read upward and produces fourteen cycles, so a key that three files
  name cannot live in one of them. With both corrected the graph is acyclic and `kubeconfig.py` is the
  leaf, which decides the order: leaf first, bottom-up, so no commit needs a back-edge into the module
  that still holds the rest.
- **P2-4** pure helpers: `parse_cpu`, `parse_memory`, `plural_for_kind` →
  `core/quantities.py`; `kubectl_target_args`, `helm_target_args` → `core/identity/target.py`
  (they build argv from a `ClusterTarget`; that is target behaviour).
- **P2-5** `kubernetes_utils.py` → `infrastructure/kubernetes/` per the §4 file table. Use the
  exact method-to-file assignment below (§7) — do not invent another split. Each new module keeps
  `class KubernetesUtils` **out** of existence: expose functions/classes per area, and provide
  `infrastructure/kubernetes/__init__.py` exposing the union so importers need only one import
  line. The `X(ctx)` construction becomes `clients.client_set(ctx)` (a small dataclass holding
  the api factories) passed to the resource modules; delete the giant class and its
  `__init__(ctx_or_target, api_client=None)` — callers already only pass `ctx`.
- **P2-6** `helm_utils.py` → `infrastructure/helm/`, `ci_utils.py`, `grafana_*`,
  `ingress_generator.py`, `user_management.py` → per §4 (names may adjust once you see the
  call sites).
- **P2-7** `shell.py` → `infrastructure/shell.py`.

**P2-8 (the expensive one): sweep the tests.** After each sub-move:
`grep -rln "kubementat\.kubernetes_utils" tests src` → update imports and monkeypatch strings;
`uv run pytest -q` until green; then next sub-move. Doing 181 test files in one commit is
feasible with `sed` per symbol; keep one sub-move per commit so a broken rename bisects.

### Phase 3 — split `Automation` into `automations/` slices

Mapping (21 public methods; private helpers go with the method that calls them; the exact
method list is §6):

| New module | Gets |
|---|---|
| `automations/install/__init__.py` | `install_kubementat` as the named entry point orchestrating the rest, `check_dependencies_are_installed`, `validate_cluster_prompt`, `check_cluster_permissions`, `setup_pipelines`, `setup_secrets`, `setup_docker_registry_access` |
| `automations/uninstall/` | `uninstall_kubementat`, `uninstall_pipelines`, `namespaces_kubementat_owns`, `namespace_sweep`, `uninstall_linkerd`, `uninstall_nginx_ingress` |
| `automations/tunnels/` | `open_tunnel_grafana`, `open_tunnel_tekton_dashboard`, `open_tunnel_openbao` |
| `automations/accounts/` | `create_team_account`, `create_team_account_read_only`, `delete_team_account` (thin wrappers over `infrastructure/grafana` + `infrastructure/kubernetes`; `user_management` already does most of it) |
| `core/identity/context.py` | `context_name` — it answers "which kube context", which is identity, not automation |

The `Automation` class disappears; CLI commands import the slices
(`from kubementat.automations.install import install_kubementat`). `cli/commands/kubementat.py`,
`tunnels.py`, `users.py` update in the same commit.

### Phase 4 — split `tekton_utils.py` into `infrastructure/tekton/`

**P4-0: the census, run immediately before the split and not before then.** The mapping below is
*correct about which method goes where* and silent about everything that makes a god-object split
fail, so no ticket in this phase may be written without the table. Dispatch a read-only census
(`scout`, `:low`, ~20 minutes, edits nothing but its own output file) and write the split tickets
from what it returns, naming the sites it rules out.

It must answer these, because the plan cannot:

1. **Shared private helpers.** Every `_private` helper, and which of the four target files actually
   calls it. A helper used by `install.py` *and* `runs.py` is the whole failure mode of this phase:
   put it in the obvious file and the two modules import each other, recreating inside
   `infrastructure/tekton/` the cycle this plan just deleted. The census names the helpers that have
   two homes and says which one wins.
2. **What the methods actually depend on.** Per method: does it need a cluster (`ctx`, a client,
   `run`), or only a `Config` and the filesystem? The generation half is mostly the latter, and
   that is what decides whether `generation.py` can be pure functions over data — the same
   distinction `config_reads_index.py` was built on.
3. **How the tests reach `TektonUtils`.** Not the string-argument check, which Phase 1 proved is
   empty. **`monkeypatch.setattr(<object>, "name", ...)` in its object form is how this suite stubs
   things — 183 `setattr(` sites** — and the case that matters is the one that fails *silently*: a
   test patches `some_module.the_function`, the function later moves, and the module still binds the
   name because it imports it. The patch lands on a binding no caller reads, the test passes, and it
   asserts nothing. The census says, per method, which tests touch it and how, and flags every
   module-level binding that would keep patching after the move.
4. **The call graph that crosses files.** `setup_triggers` calls the generators and the apply
   primitive; `install` waits for the webhook; the cleanup job is install-shaped but named with the
   runs. Each crossing edge is either a legitimate call down, or a sign the file boundary is wrong.
5. **The public surface the CLI and `automations/install` use**, so the new entry points are named
   from callers and not from a guess about what is public.

Output: the table in `docs/census-tekton-split.md`, committed with the first split ticket so the
reasoning outlives the run; in the answer, the counts, the shared helpers, the tests that patch by
object, and the sites OUT. The split tickets cite the file and never absorb the whole table.

**Run the same census before `KubernetesUtils` (Phase 2) and before `Automation` (Phase 3).** Those
two classes are bigger than this one, and question 3 is the one that bites them hardest: 77 methods
across 11 importing modules is where a silently-unpatched test goes looking green.

Per §4: `install.py`, `triggers.py`, `generation.py`, `runs.py`. `TektonUtils` disappears;
each module is functions taking `ctx` (like the kubernetes split). CLI `tekton` commands and
`automations/install` (which calls `TektonUtils` today) update to the new entry points. The
pipeline-run polling constants (`PIPELINE_RUN_POLL_INTERVAL_SECONDS`, …) stay exported from
`runs.py`.

### Phase 5 — cleanup and documentation

- Update `docs/diagrams/src-class-diagram.excalidraw` (the editable source of the class diagram;
  the PNG is exported from it) so the module boxes, edges, and surface-count table match the new
  layout; re-export `src-class-diagram.png`. Update any text that describes module layout (`docs/README_PLATFORM_CONFIG.md`, `docs/README_TEKTON.md` — only
  if their module names changed).
- `CHANGELOG.md`: record every moved path and the removal of the `Automation` / `TektonUtils` /
  `KubernetesUtils` classes (they are public API per the diagram's surface counts — say so in
  the entry).
- Run the e2e suite once: `uv run python tests/e2e/suite.py`, attach `result.json`.
- Optional (never as a pytest test, per the rules): add `import-linter` to the dev extras and a
  `Makefile`/task `lint-imports` with the §4 contract, for humans/CI to run.
- Verify the dependency rule one last time with the AST import walk from §3.2 and paste its
  output into the final report.

## 6. Method-to-file assignment (exact)

**`KubernetesUtils` (77 public) → infrastructure/kubernetes/**
- `clients.py`: `target`, `api_client`, `apps_api`, `custom_objects_api`, `core_api`, `rbac_api`,
  `networking_api`, `authorization_api`, `apiextensions_api`, `version_api`, `dynamic_client`,
  `kubectl_args`, `self_can`, `cluster_is_reachable`, `api_version_is_served`,
  `get_apps_api`, `get_core_api`, `get_rbac_api`, `get_custom_objects_api`,
  `get_kubernetes_client`, `get_api_resources` (the `get_*` accessors are used by
  `tests/test_two_clusters_can_be_addressed_by_one_process_at_once.py` — carry them over as
  the plain accessor functions, same names, so that test only needs its import line changed)
- `namespaces.py`: `create_namespace`, `delete_namespace`, `list_namespaces`,
  `wait_namespaces_gone`, `namespace_phases`, `service_endpoint_addresses`
- `rbac.py`: `list_cluster_roles`, `list_cluster_role_bindings`, `list_roles`,
  `create_or_replace_role`, `create_or_replace_cluster_role`, `create_or_replace_role_binding`,
  `create_or_replace_cluster_role_binding`, `delete_role`, `delete_role_binding`,
  `delete_cluster_role`, `list_role_bindings`, `get_role_binding`
- `secrets.py`: `create_or_replace_secret`, `create_or_replace_docker_secret`, `list_secrets`,
  `delete_secret`, `create_or_replace_service_account_token_secret`
- `serviceaccounts.py`: `get_service_account`, `create_service_account`,
  `create_or_replace_service_account`, `list_service_accounts`,
  `patch_service_account_with_pull_secrets`, `get_service_account_token`, `delete_service_account`
- `customresources.py`: `get_custom_resource`, `list_custom_resources`,
  `create_or_replace_custom_resource`, `delete_custom_resource`,
  `list_custom_resource_definitions`, `delete_cluster_scoped`, `delete_ingress_class`,
  `list_ingress_classes`, `list_persistent_volume_claims`, `delete_persistent_volume_claim`
  (the ingress-class and PV/PVC methods are resource-shaped; if you want a `networking.py`
  and `storage.py`, split them here)
- `workloads.py`: `get_pod_name_for_deployment`, `list_pods_for_all_namespaces`, `list_pods`,
  `list_services`, `get_service_endpoint`, `get_nodes`, `analyze_deployment`, `list_pod_images`,
  `cluster_capacity`, `list_pvc_usage`
- `exec.py`: `exec_in_pod`, `get_pod_logs`, `stream_logs`, `stream_deployment_logs`,
  `open_deployment_shell`
- `tunnel.py`: `open_tunnel`

**`TektonUtils` (18 public) → infrastructure/tekton/**
- `install.py`: `install` (+ `_install_helm_deployer_cluster_role`, `_wait_for_pipeline_webhook`,
  `_preflight_tekton_api_version`, module-level `preflight_installed_tekton_api_versions`)
- `triggers.py`: `setup_triggers` (+ `_setup_trigger_rbac`, `_setup_webhook_secrets`,
  `_apply_team_trigger_resources`)
- `generation.py`: `generate_deploy_pipeline`, `generate_trigger_configs`, `generate_pipeline_run`,
  `generate_random_postfix` (+ template-resolution helpers)
- `runs.py`: `run_pipeline`, `wait_for_pipeline_run`, `list_pipelines`, `list_tasks`,
  `list_taskruns`, `list_pipeline_runs`, `list_all_tekton_resources`,
  `list_available_pipeline_runs`, `check_running_pipelines`, `cleanup_pipeline_runs`,
  `cleanup_pipeline_run`, `setup_cleanup_job` (the CronJob installer is install-shaped; if it
  reads better with `install.py`, put it there)

**`Automation` (21 public) → §Phase-3 table.**

## 7. Acceptance criteria (per phase: what "done" means)

1. `uv run pytest -q` green.
2. `git grep -n "<old-module-path>" src tests` empty for every path the phase retired.
3. The AST import walk shows: no cycles; no `core` → `infrastructure`/`automations`/`cli` edge;
   no `infrastructure` → `automations`/`cli` edge.
4. No new `click` import anywhere under `src/kubementat/` (the three `ClickException`
   subclasses are gone; `config_init`'s `click.echo` block became `logging.warning`, which is
   what the other 437 sites already do).
5. No test asserts anything about the repository's own source layout (rules forbid it).
6. `CHANGELOG.md` updated; no shim modules left behind (`git grep` proves old names are dead).

## 8. Execution notes for the implementing agent

- Work in a dedicated branch/worktree, one commit per sub-move (P2-1 … P2-8 each separately
  green). Use `mcp__codegraph__codegraph_explore` for impact analysis before touching any symbol;
  it is faster and more accurate than grep loops for the 51-caller `HelmUtils`-scale sweeps.
- When you move a function, **move its comments and docstrings with it verbatim**. These
  docstrings carry decisions ("why deferred import", "why stderr", "why create-or-replace") that
  are the project's institutional memory. Rewrite only where a statement became false.
- Monkeypatch strings are the hidden failure class. After each path change, run the suite twice:
  once normally, once with `-p no:randomly` (if used) — and for each test that changes pass/fail
  behaviour when you change a patch target, read the test to confirm it was patching what you
  think it was.
- `tests/e2e/suite.py` exercises the real VM; do not run it per-phase. One run after Phase 5,
  report its `result.json` with the git SHA.
- If a phase's sweep touches >20 files in tests, consider a subagent per package-move (max 3 in
  parallel, per repo rules), isolated worktrees; the orchestrator keeps `src/` ownership.
- Anything you discover that contradicts §3 (a new cycle, a caller that patches private state)
  is a stop-and-report item, not a thing to fix inline by redesigning the plan.

## 9. What explicitly is NOT in scope

- No behaviour changes, no new features, no configuration-format changes, no CLI UX changes.
- No renaming of public CLI verbs, no shims for old module paths, no re-export "compat" modules.
- No new architecture-enforcement tests inside `tests/` (enforce by review + optional
  `lint-imports` outside pytest).
- No touching `platform_config/` or git-crypt-encrypted files.
- No "improvements" to docstring style, logging levels, or comment prose while moving code.

## 10. What the tree looks like when the plan is finished (2026-11-07)

This section is the result, not a revision of §4–§7, and it is here so that a reader arriving after the last
ticket does not have to re-derive the layout from tables written against a tree that no longer exists. The
tables above stay as they were: they are the plan, dated. Every number below is `wc -l` on the path named.

**Three layers, and the rule is that nothing below may reach up** (`python3 .agent-work/import_graph.py src`
exits 0, with no cycles and no layer violations at HEAD `15be650`):

- `src/kubementat/core/` — 21 files, 4,528 LOC. `constants.py` (imports nothing), `version.py`,
  `quantities.py`, `assets/`, `config/` (nine named modules plus its `__init__.py`: `config.py`, `keys.py`,
  `paths.py`, `stamp.py`, `versions.py`, `derived.py`, `reads.py`, `release_index.py`, and `teams.py`), and
  `identity/` (five named modules plus its `__init__.py`: `kubeconfig.py` as the leaf, then `target.py`,
  `comparison.py`, `registry.py`, `context.py`).
- `src/kubementat/infrastructure/` — 24 files, 7,567 LOC. `kubernetes/` (12 files), `tekton/` (six: `generation.py`,
  `runs.py`, `install.py`, `triggers.py`, `preflight.py`, and an `__init__.py` that exposes modules and
  re-exports no names), `grafana/` (two), plus `helm.py`, `shell.py`, `user_management.py`.
- `src/kubementat/automations/` — 23 files, 4,777 LOC. `install/`, `uninstall/`, `tunnels/`, `accounts/`,
  `routing/`, `ci.py`, `ingress.py`. The team-`static.json` reader is *not* here; it sits below at
  `core/config/teams.py`, because three slices read it and the layer rule is what decided its home.
- `src/kubementat/` itself keeps 8 files: `errors.py` (all 37 exception classes), the five flat config
  verb modules the plan said to leave, `crypto_utils.py`, and `__init__.py`.

**The classes, and what answers their surface instead.** `Automation`'s 54 methods are module-level functions
that take `ctx`, one verb group per `automations/` slice. `KubernetesUtils` became nine area modules whose
union is what consumers import. `TektonUtils`' 18 public methods and 27 module functions are the five
`tekton/` modules. `cluster.py` is `core/identity/`, and `config.py`'s sections are `core/config/`. All of
them were public API by the diagram's surface counts, which is why `CHANGELOG.md` records each one as a
removal and not as a move.

**One rule moved, deliberately.** The team refusal lived in `TektonUtils.__init__`, where merely constructing
the object earned a team for every path. It now sits at the call sites that actually read a team-scoped
namespace, so a library caller holding a team-less `Context` is refused before the first apply rather than
after it.
