# P2-5 — `kubernetes_utils.py` → `src/kubementat/infrastructure/kubernetes/`: the ticket DAG

Measured at `ca2d93e` (`git show HEAD:src/kubementat/kubernetes_utils.py`), on `v2-arch-refactoring`.
Every number below was re-derived from the tree with `ast`, not from the census's table or this brief's
line numbers.

**Provenance.** `git diff -- src/kubementat/kubernetes_utils.py tests/conftest.py src/kubementat/automation.py
src/kubementat/grafana_accounts.py src/cli/commands/kubernetes.py src/cli/commands/tunnels.py
src/kubementat/automations/routing/` is empty: the file being split, the recorder fixture and six of the
nine consumers are byte-identical to `ca2d93e` right now. A second writer is in this working tree
18:10–18:25 (an error-class consolidation across `errors.py`, `config_*`, `core/config/reads.py`,
`core/identity/{kubeconfig,registry}.py`, `cli/context.py`, `cli/kmt.py`, `tekton_utils.py` and five tests);
**that diff changes no line that calls a `KubernetesUtils` method** — checked by grepping the diff for every
one of the 81 names. Only `tekton_utils.py`'s line numbers drift out of my measurements, so **no citation
below is by line number except in the inventory table, which is the one file the tickets edit and which
this ticket does not touch.**

See §8 for what happened to the tree while this plan was being written: HEAD moved to `94a4e88` at 18:42:48 and the file measured above is byte-identical in both commits, so nothing here needed re-deriving.

| the file | measured |
|---|---:|
| lines in `kubernetes_utils.py` | 1 977 (census took 2 080 at `e9c9759`) |
| `class KubernetesUtils` | 191–1976 |
| functions in the class | **81** = 77 public + 4 private (`__init__`, `_api`, `_namespaces_present`, `_require_namespace`) |
| code lines inside those 81 | 1 467 |
| module-level functions still here | **6** (`_reason`, `_percent_of`, `_node_summary`, `_byte_quantity`, `_usage_percentage`, `_pod_summary`) |
| module-level type / constants | `DeleteOutcome` + **9** constants |
| method-to-method edges inside the file | **89** (34 stay inside one target module, 55 cross it — 48 land on `clients.py`, of which 45 are accessors and 3 land on `kubectl_args`) |
| `src/` modules importing this module | **9** (the census says ten; `ci_utils.py` stopped being one at `b60bab7`) |
| `src/` construction sites of the class | 17 in 8 files |
| `src/` attribute reads of its methods | **130** (124 excluding `.target`, plus 6 reads of `.target` off the instance) |
| test files importing the class | 18 (all of them import the class itself) |
| test construction sites | 53 in 15 files, plus 3 prose mentions in `tests/conftest.py` |
| `setattr(` sites in `tests/*.py` | 182 = 126 `monkeypatch.setattr` object-first + 47 string-first + 6 `patch.setattr` + 3 bare |
| methods named in no test file | **23** — the census's list, exactly, no additions and no omissions |

## 0. The shape all eight tickets produce

1. `clients.py` owns **one small class** and nothing else. Its `__init__` is today's `__init__` moved
   verbatim — the same `*, api_client=None` keyword, the same `Context`-or-`ClusterTarget` branch, the same
   laziness (the target is resolved when a client is built, never in the constructor). Its 11 API
   accessors stay `functools.cached_property`, because that memo is the seam: three tests do
   `setattr(utils, "core_api", api)` and that only works on a cached_property. Call this object `KubeClients`.
2. Every resource method becomes a **module-level function whose first parameter is that object**:
   `def list_pods(k, namespace=None, label_selector=None)`. Nothing else about the body changes. The
   constructor's two jobs — decide Context-versus-target, and store the injected `api_client` — survive
   inside `KubeClients`, so `api_client=` and `ClusterTarget.client` stay exactly the seam they are today
   and `tests/conftest.py`'s `recorded_api` needs no edit.
3. **Cross-module calls are written module-qualified — `exec.get_pod_logs(k, …)`, never
   `from .exec import get_pod_logs`.** This file already binds `quantities` and `_target` that way, and its
   own comment says why: a name import leaves a second resolvable route to one definition, and a
   `monkeypatch` at the module nobody runs misses it. The same rule is what makes an object-first
   `monkeypatch.setattr(exec, "get_pod_logs", …)` land. It is not a style choice; it is the difference
   between a patch that bites and a patch that passes.
4. **The `infrastructure/kubernetes/__init__.py` exposes nothing.** The refactoring plan's P2-5 line about
   "exposing the union so importers need only one import" is a re-export, which Phase 2 forbade, and it
   would be the one file in this DAG that could keep a moved name alive. It is created as a package marker
   in T1 and stays empty.
5. **The invariant every ticket must be able to recite:** at every commit between T1 and T8, each of the 81
   names lives in exactly one place — either still in the class, or in its new module, never both. That is
   checkable with two greps per ticket and it is what makes a half-moved class legal. A moved name answers
   `AttributeError` off the class from the moment it moves.
6. **The bridge that makes the middle state survivable.** The class's `__init__` gains a third accepted
   argument in T1 — a `KubeClients` — and keeps accepting `Context` and `ClusterTarget` for the resource
   methods that still live on it. That branch is deleted in T8 with the class and is the only thing in this
   DAG that exists between two tickets. Without it no ordering can be green: a resource function needs a
   client set, and only T1 can hand one out.

Two things are *not* in this plan, on purpose: no `helm_target_args` (it lives in
`core/identity/target.py` since `b60bab7`, and this file has not called it for as long as the census says it
has not), and no `networking.py` / `storage.py` split of `customresources.py` (§4.4 refinement one, rejected).

## 1. The file list, with every row assigned

`why here, not in the caller` answers the question the brief asks: a method may only be asked for by the
file that *calls* it if it would otherwise be reachable from two places. `code` is non-blank non-comment
lines. Line ranges are given only because this is the file a worker holds open; they are `ca2d93e` ranges
and they run about 103 lines below the census's, which were taken at `e9c9759` before five helpers left.

### `clients.py` — 21 public + `_api` + the constructor (`KubeClients`)

| method | code | why here, not in the caller |
|---|---:|---|
| `__init__` 207–229 | 21 | It is the seam. It decides Context-versus-target and stores `api_client`; every client later comes from it. Deleting it deletes the recorder. |
| `target` 232–246 | 14 | Laziness is the whole design: a configuration-only command must build nothing. Belongs to the object that answers a cluster, not to the reads. |
| `api_client` 249–262 | 12 | Same. It reads `self.target`, so it cannot be duplicated by any consumer. |
| `_api` 264–276 | 11 | Called by eight of the nine factories below; the `isinstance(seam, ApiClient)` test is how a recorder answers four API classes without patching four globals. One home (census §4.1 agrees). |
| `apps_api` `custom_objects_api` `core_api` `rbac_api` `networking_api` `authorization_api` `apiextensions_api` `version_api` 280–411 | 2 ea | Factories, not reads. 48 of the 89 edges land here, so anything that calls them needs `k`, not the file that wanted the read. |
| `dynamic_client` 326–333 | 7 | Built from `api_client`; asked for by `get_api_resources` and by no consumer. |
| `kubectl_args` 339–346 | 7 | It is `_target.kubectl_target_args(self.target)` — an addressing rule, and three session calls read it as a *property*. Three consumers and `open_tunnel`/`stream_logs`/`exec_in_pod` need it, so putting it with the exec rows would hand the rule to two files. |
| `self_can` 351–364 | 13 | A permission probe, not a resource read; it composes `authorization_api`. `user_management` and `automation` both ask, so it is not `rbac`'s. |
| `cluster_is_reachable` 366–379 | 13 | `version_api` plus a sentence. Same argument: it addresses, it does not manage a resource. |
| `api_version_is_served` 536–564 | 25 | Discovery over the discovery API group. Two callers, both `preflight_*` in `tekton_utils.py`; it reads `custom_objects_api`, so it belongs below that. |
| `get_apps_api` `get_core_api` `get_rbac_api` `get_custom_objects_api` `get_kubernetes_client` 521–534 | 2 ea | Redundant by design. They exist so a test can ask for a client through one spelling; `test_two_clusters_can_be_addressed_by_one_process_at_once.py` reads 24 client members. Keep the names, make them members of `KubeClients`. |
| `get_api_resources` 1673–1698 | 24 | Wraps `dynamic_client`; a read of *what the cluster serves*, which is discovery again. |

Preamble: nothing from this file's module level, plus `client`, `dynamic`, `Context`, `ClusterTarget`,
`_target`, `errors`, and `quantities` (bound as modules — rule 3).

### `exec.py` — 8 public + `_require_namespace` + `DEPLOYMENT_SHELL_COMMAND`

| method | code | why here, not in the caller |
|---|---:|---|
| `get_pod_logs` 1477–1501 | 22 | The ruling's whole point: one single-pod read, and `analyze_deployment` needs it from above. Below it, so no file above has to be reached from below. |
| `list_pods` 1407–1418 | 10 | **The deviation this plan insists on.** The refactoring plan puts it in `workloads.py`; the ruling puts `get_pod_name_for_deployment` beside `get_pod_logs`; that lookup *calls* `list_pods`. Leaving `list_pods` in `workloads.py` is the cycle, wearing a different hat (see §2's T2). |
| `list_pods_for_all_namespaces` 1394–1405 | 10 | Edge-neutral — nothing in `exec.py` calls it. It comes along because pod listing belongs with pod reads, and one file would otherwise spell "which pod" twice. Flag this as the plan's choice, not the census's. |
| `get_pod_name_for_deployment` 1363–1391 | 26 | Ruling. It builds no selector of its own (it takes `selector`), so it is "which pod", and after it moves the only cross edge left points `workloads → exec`. |
| `stream_logs` 1503–1536 | 31 | Session call, `kubectl_args` from clients. |
| `exec_in_pod` 1538–1565 | 26 | Session call. |
| `open_deployment_shell` 1567–1606 | 35 | Calls `_require_namespace` + `exec_in_pod` + the lookup, all now in one file. |
| `stream_deployment_logs` 1608–1634 | 23 | Calls `_require_namespace` + `stream_logs`; same file. |
| `_require_namespace` 1636–1661 | 22 | Census §4.1 says it serves only these two commands. Verified: exactly two callers, both here. Carry it by name (census §8 item 2) — a table-driven move drops it. |

### `tunnel.py` — 1 public

`open_tunnel` 1336–1361 (24 code) — built on `kubectl_args`, owns a port-forward process, and is the only
method `cli/commands/tunnels.py` came for. It has no edge into `exec.py`, so it rides T2 for sizing, not
because anything calls it.

### `namespaces.py` — 6 public + `_namespaces_present` + two constants

`create_namespace` 567, `delete_namespace` 591, `list_namespaces` 615, `wait_namespaces_gone` 627,
`namespace_phases` 696, `service_endpoint_addresses` 660, `_namespaces_present` 717 (3 code, one caller).
`NAMESPACE_GONE_WAIT_S`, `NAMESPACE_POLL_INTERVAL_S` come here too: `wait_namespaces_gone` is their only
reader and three tests patch them **by string**, so their new home must be exactly one module string.
Why not in `workloads.py` because `service_endpoint_addresses` is about a Service? Because it is
`get_core_api(...)` on endpoints in a namespace, and `tekton_utils.py:1080` reads it that way.

### `secrets.py` — 5 public

`create_or_replace_secret` 722 (42 code), `create_or_replace_docker_secret` 770 (calls it — same file),
`list_secrets` 793, `create_or_replace_service_account_token_secret` 952, `delete_secret` 1309. The last
four all touch `core_api`; the token secret is here per the refactoring plan's table, and its constants are
**not** here — see the naming table below.

### `serviceaccounts.py` — 7 public

`get_service_account` 806, `create_service_account` 825, `create_or_replace_service_account` 851,
`list_service_accounts` 892, `patch_service_account_with_pull_secrets` 916 (calls `get_service_account`,
so same file), `get_service_account_token` 985 (47 code, the longest read in the split),
`delete_service_account` 1322.

### `rbac.py` — 12 public

`list_cluster_roles` 384, `list_cluster_role_bindings` 392, `list_roles` 904, `create_or_replace_role` 1160,
`create_or_replace_cluster_role` 1184, `create_or_replace_cluster_role_binding` 1200, `delete_role` 1216,
`delete_role_binding` 1227, `delete_cluster_role` 1238, `create_or_replace_role_binding` 1253,
`list_role_bindings` 1270, `get_role_binding` 1287. No helper and no constant; all twelve read an
`*_api` accessor and nothing else, which is why this file can be moved without naming anything.

### `customresources.py` — 10 public + `_reason` + `DeleteOutcome`

`get_custom_resource` 1038, `list_custom_resources` 1060, `create_or_replace_custom_resource` 1081 (its
`quantities.plural_for_kind` call is a module-qualified call to `core/quantities.py`, not an in-file name),
`delete_custom_resource` 1134, `list_custom_resource_definitions` 473, `delete_cluster_scoped` 489,
`list_ingress_classes` 396, `delete_ingress_class` 411, `list_persistent_volume_claims` 430,
`delete_persistent_volume_claim` 444. `_reason` and `DeleteOutcome` serve only the last three deletes of
the list and the ingress/PVC pair, all of which land here — one home, so the resource-shaped split stays
out (census §4.4 item one, rejected).

### `workloads.py` — 7 public + five helpers + `NODE_STATS_PATH`

`get_nodes` 1663, `get_service_endpoint` 1433 (calls `list_services` 1420 — both here), `analyze_deployment`
1701 (56 code), `list_pod_images` 1762, `cluster_capacity` 1793 (90 code), `list_pvc_usage` 1895 (73 code),
plus `_percent_of` 129, `_node_summary` 138, `_byte_quantity` 153, `_usage_percentage` 169, `_pod_summary`
182, and `NODE_STATS_PATH`. This is the thinnest file by method count (7) and the heaviest by lines
(302 of the 1 467), and it is the **only** file that depends on another new file. Census §4.4's second
refinement — splitting it around the stats methods — is rejected because `cluster_capacity` and
`list_pvc_usage` share `_percent_of`, `_node_summary`, `_byte_quantity`, `_usage_percentage` plus
`parse_memory`, which is already single-home in `core/quantities.py`.

### `naming.py` — 5 names, 0 methods

The three constants census §4.2 says have two homes, each given one, plus the two `MANAGED_BY_*` that this
file never reads and that therefore have no home to travel to:

| name | read by | decision |
|---|---|---|
| `DEPLOYMENT_LABEL` | `open_deployment_shell`, `stream_deployment_logs` (`exec.py`) and `analyze_deployment` (`workloads.py`) | `naming.py`. It cannot live in `exec.py`, because `workloads.py` would then import `exec.py` for a string as well as for `get_pod_logs`, and the two directions would be one edge doing two jobs. |
| `SERVICE_ACCOUNT_TOKEN_SECRET_TYPE` | `create_or_replace_service_account_token_secret` (`secrets.py`), `get_service_account_token` (`serviceaccounts.py`) | `naming.py`. Neither module is below the other — measured, zero edges between them — so picking either would add a coupling this file does not have today. |
| `SERVICE_ACCOUNT_NAME_ANNOTATION` | the same two, always paired with the type | same. The pair is one rule: the type says what it is, the annotation says whose. |
| `MANAGED_BY_LABEL`, `MANAGED_BY_KUBEMENTAT` | nothing in this file; `automation.py` only | `naming.py`, because the deleted file must leave no dangling import. Not a kubernetes rule that anyone is missing. |

Single-home names, where they land: `_reason` + `DeleteOutcome` → `customresources.py`;
`_percent_of`, `_node_summary`, `_byte_quantity`, `_usage_percentage`, `_pod_summary`, `NODE_STATS_PATH` →
`workloads.py`; `NAMESPACE_GONE_WAIT_S`, `NAMESPACE_POLL_INTERVAL_S` → `namespaces.py`;
`DEPLOYMENT_SHELL_COMMAND` → `exec.py`; `_namespaces_present` → `namespaces.py`; `_require_namespace` →
`exec.py`; `_api` → `clients.py`. `parse_cpu`, `parse_memory`, `plural_for_kind`, `kubectl_target_args` and
`helm_target_args` are already gone and are reached as `quantities.x` / `_target.x` — bind them as modules
in each new file, same as rule 3.

**Proof that no importer keeps a copy** (run it in every ticket that touches a name):
`git grep -n -F 'app.kubernetes.io/instance' src | grep -v core/constants` must return only `naming.py`;
`git grep -n -F 'kubernetes.io/service-account' src` must return only `naming.py` (and one prose sentence in
`user_management.py:228`'s docstring, which is prose and is not a definition). `core/constants.py:86–98` is
a component-selector *table* that spells the same release label inside data values; that is Phase 3's
consolidation to make, not this ticket's to break.

## 2. The order, as tickets

Eight tickets. Sizes are method rows; `src` and `tests` columns are attribute-access sites the ticket must
repoint, measured as above.

| # | writes | rows | `src` sites | `src` files | `tests` files | depends on |
|---|---|---:|---:|---:|---:|---|
| T1 | `clients.py`, `naming.py`, `infrastructure/kubernetes/__init__.py` | 23 | 33 | 8 | 8 | nothing |
| T2 | `exec.py`, `tunnel.py` | 10 | 11 | 4 | 4 | T1 |
| T3 | `namespaces.py` | 7 | 19 | 2 | 4 | T1 |
| T4 | `secrets.py`, `serviceaccounts.py` | 12 | 28 | 4 | 2 | T1 |
| T5 | `rbac.py` | 12 | 25 | 2 | 1 | T1 |
| T6 | `customresources.py` | 10 | 21 | 6 | 1 | T1 |
| T7 | `workloads.py` | 7 | 10 | 4 | 7 | T1, **T2** |
| T8 | deletes `kubernetes_utils.py` and the class | 0 | 17 constructions | 8 | 18 | T2…T7 |

The graph is a fan-out from T1 with exactly one file-to-file edge inside the new package:
`T7 → T2`. Everything else is `T1 → Tn`, and T8 is the join.

**Why T1 is first.** It is not the biggest ticket; it is the one every other row points at — 48 of the 89
internal edges land on a client accessor, and *every* resource function needs a `k` before it can exist.
Putting any resource ticket first means writing functions whose first parameter has no type, no
construction and no `api_client` seam, and the ticket cannot be green because `KubeClients` is a name that
does not exist yet. It also fixes the test story once: after T1, `KubeClients(TARGET_DEV, api_client=recorded_api)`
is a real thing and the 34 test sites that read a client member can be repointed to it while they keep
building a `KubernetesUtils` for the resource methods that have not moved yet.

**T1's size, honestly.** 23 rows sounds like more than one sitting, and 17 of them are two-line factories.
The real work is mechanical and wide: create the package marker (empty), move the constructor verbatim,
move `_api` and the nine cached_property factories, and then rewrite **62 `self.<accessor>` reads inside
the 56 methods the class still holds** to `self._k.<accessor>` (measured: 62 sites, one per line). If that
diff gets too long to hold, T1 splits cleanly into T1a (the package, `KubeClients`, the 11 factories, the
constructor branch) and T1b (`kubectl_args`, `self_can`, `cluster_is_reachable`, `api_version_is_served`,
`get_api_resources`, and the 62-site rewrite) — but nothing between them may be committed with both a class
accessor and its `KubeClients` twin alive.

**Where the cycle would appear if the order changed.**
- *If `get_pod_name_for_deployment` stayed in `workloads.py`, as the refactoring plan's table says:*
  `exec.py` would import `workloads.py` for the lookup (its caller `open_deployment_shell` lives in
  `exec.py`) while `workloads.py` imports `exec.py` for `get_pod_logs` (`analyze_deployment`). No ordering
  satisfies both, so the rule "a ticket may not depend on a file that does not exist yet" fails for **two**
  tickets, not one, and the DAG cannot be linearised. That is the only cycle the census found, and the
  ruling settles it by moving the lookup down and taking `list_pods` with it.
- *If `list_pods` had been left in `workloads.py` "for tidiness":* the same cycle reappears in exactly one
  edge (`get_pod_name_for_deployment → list_pods`), just hidden in a two-line body. This is why T2 is
  written with `list_pods` inside it, and why T2 must be read as *the pod side*, not as *the exec side*.
- *If T1 came second:* every resource ticket would have to keep reaching back into
  `kubernetes_utils.KubernetesUtils` for its accessor, which is a back edge into the file being deleted, and
  the 62 accessor reads would instead become 62 new parameters.
- *If T7 ran before T2:* `workloads.py` would import `exec.py` for `get_pod_logs` and `list_pods`; the import
  fails at collection, so T7 fails loudly rather than silently. Good ordering is cheap insurance.
- *If T8 ran before any of T2…T7:* the class disappears with 56 method bodies still wanting accessors, and
  `automation.py` and `tekton_utils.py` — the two biggest consumers, both moved later than this file — break
  first, exactly as census §7 warns. T8 is last and retargets both of their constructions.

**Middle order rationale.** The four middle tickets have no edges between each other (measured: zero method
calls between `namespaces`, `secrets`, `serviceaccounts`, `rbac`, `customresources`), so their order is free.
They are ordered by ascending number of files that would have to change, so the first of them proves the
machinery on the smallest sample and the file that forces a test redesign (`customresources`) comes after
the pattern is already twice proven. T7 goes last because it is the only one with an edge into a new module.

## 3. Consumer work, per ticket

Nine modules import this module — census §5's tenth, `ci_utils.py`, stopped importing it at `b60bab7` and now
pulls `kubectl_target_args` from `core/identity/target.py`. `k8s_apply.py` remains a consumer that imports
nothing and names the class only in a docstring.

The consumer-side rule for the whole split, stated once: `self.kubernetes_utils` keeps its name everywhere.
What it holds changes, in T8, from a `KubernetesUtils` to a `KubeClients`; a method call becomes a function
call with that object as its first argument, so `self.kubernetes_utils.create_namespace(ns)` becomes
`namespaces.create_namespace(self.kubernetes_utils, ns)`. The 17 construction sites are the whole of the
identity change, and they are all changed once, in T8.

| ticket | consumers touched | what changes there |
|---|---|---|
| T1 | `automation.py`, `helm_utils.py`, `user_management.py`, `grafana_accounts.py`, `tekton_utils.py`, `cli/commands/kubernetes.py`, `cli/commands/tunnels.py`, `routing.py`, `traefik_presence.py` | No call sites move: `k.core_api`, `k.kubectl_args`, `k.self_can`, `k.api_version_is_served`, `k.target` keep spelling, because they are members of `KubeClients`. Every `KubernetesUtils(x)` gains the option of `KubeClients(x)`; the class keeps accepting `Context`/`ClusterTarget` until T8. |
| T2 | `automation.py` (6), `cli/commands/kubernetes.py` (3), `cli/commands/tunnels.py` (1), `traefik_presence.py` (1) | The 3 `get_pod_name_for_deployment` calls (see below), `open_tunnel`, and the two `_attach(...)` helpers. `traefik_presence.py` hands `list_pods_for_all_namespaces` to a probe helper — that becomes a function reference, which is the one consumer change here a compiler cannot help with. |
| T3 | `automation.py` (17), `tekton_utils.py` (2) | The whole install/uninstall namespace sweep. This is where `automation.py`'s 17 sites go; it is also where the 3 string monkeypatches move (see §4). |
| T4 | `automation.py` (18), `tekton_utils.py` (6), `cli/commands/kubernetes.py` (3), `user_management.py` (1) | Token/secret plumbing across `automation.py` and `user_management.read_service_account_token`. |
| T5 | `automation.py` (15), `tekton_utils.py` (10) | RBAC creation and the uninstall sweeps, both pairs (`create_or_replace_*`, `delete_*`). |
| T6 | `automation.py` (10), `tekton_utils.py` (5), `helm_utils.py` (3), `k8s_apply.py` (1), `routing.py` (1), `traefik_presence.py` (1) | `k8s_apply.apply_resources`'s untyped parameter: the call at the end of its loop becomes `customresources.create_or_replace_custom_resource(k, …)`. Its two callers (`automation.py`, `tekton_utils.py`) hand it `self.kubernetes_utils`, which is unchanged. |
| T7 | `cli/commands/kubernetes.py` (4), `cli/kmt.py` (2), `automation.py` (2), `grafana_accounts.py` (2) | Six of the CLI's eight calls hand a **bound method** to `_read(fn, *args)` / `_attach(fn, *args)` (`_read(KubernetesUtils(ctx).analyze_deployment, namespace, deployment)`). With the class gone there is no bound method: each becomes `functools.partial(workloads.analyze_deployment, k)` with the same trailing arguments. `functools` must be imported by `cli/commands/kubernetes.py` — check it is not already dead there. `grafana_accounts.get_service_endpoint` is a plain two-line call. |
| T8 | all nine | 17 constructions retargeted to `KubeClients(ctx)`; `routing.py`'s two **function-local** imports become imports of `clients` and `customresources`; `traefik_presence.py`'s default-instance expression; `k8s_apply.py`'s docstring that names the class; `user_management.py`'s three parameter annotations (`kubernetes_utils: KubernetesUtils` → `kubernetes_utils: KubeClients`); the `kubementat/kubernetes_utils.py` key in `PENDING_KUBECTL_READS` (`test_no_kubectl_is_addressed_by_the_process_any_more.py`), whose entry is a zero that no test would flag — delete it anyway, since the module no longer exists to be scanned. |

**`automation.py`'s three `get_pod_name_for_deployment` calls are public surface, not internal calls.** They
are the three tunnel commands (`open_tunnel_grafana`, `open_tunnel_tekton_dashboard`, `open_tunnel_openbao`),
and each does the same two-step: derive `namespace` and `selector` from configuration, then
`self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)`, then hand the pod name to
`open_tunnel`. After T2 they read `exec.get_pod_name_for_deployment(self.kubernetes_utils, namespace, selector)`.
Two facts about them worth writing down, because they change what the ticket proves:

1. They pass a **selector from configuration** (`component_service_selector`, spelled in
   `core/constants.py`), **not** one built from `DEPLOYMENT_LABEL`. The census's §4.2 note that `automation.py`
   "builds the same selector" is **false in the tree** — see §6's correction list. So the constant's move to
   `naming.py` does not touch these three calls, and `automation.py`'s `DEPLOYMENT_LABEL` import is dead code
   that T8 deletes rather than retargets.
2. Because the pod lookup is a function now, a caller could pass any object. The three sites keep passing
   `self.kubernetes_utils`, which keeps the single addressing path.

The other two duck-typed consumers matter as much: `k8s_apply.apply_resources(kubernetes_utils, …)` (called
from `automation.py` and `tekton_utils.py`, plus two tests that pass their own recorder) and
`user_management.check_cluster_and_access` / `read_service_account_token` / `local_cluster_settings`, which
take the instance **as a parameter** and so change signature-wise, not import-wise.

## 4. Test work, per ticket

The suite reaches a cluster in exactly one way, and nothing in this split touches it:
`tests/conftest.py`'s `recorded_api` installs its recorder by patching `ClusterTarget.client`, and a test
that wants a client passes the recorder as `api_client=`. 35 files reference `recorded_api`. `mock.patch`
naming this module appears **zero** times.

The per-ticket rule, and it is what makes each ticket's test diff honest rather than cosmetic: *a ticket
that moves methods must leave no test reading a moved name off the class, and must prove that the
`api_client=` seam still installs through the new shape.* The proof is the same three checks every ticket
repeats: (a) `grep -rn "KubernetesUtils" tests/<the files listed>` finds only what T8 will delete;
(b) the three bare `setattr(utils, "core_api", api)` loops still swap an API object and change a recorded
call, which is what proves the cached_property memo survived; and (c) at least one test whose recorder is
installed via `ClusterTarget.client` rather than the constructor still records the moved call —
`test_a_run_that_names_no_configuration_root_never_reaches_kubectl.py`-shaped tests, not `api_client=` ones.

| ticket | test files whose call sites must be repointed | what else |
|---|---|---|
| T1 | `test_two_clusters_can_be_addressed_by_one_process_at_once.py` (13 sites — this is the file the plan says "only needs its import line changed"; 13 is why it needs more than that), `test_the_cluster_read_scripts_are_deleted_and_the_primitives_answer_for_them.py`, `test_kubernetes_reporting_helpers.py`, `test_parity_tekton_runs.py`, `test_tekton_api_version_preflight.py`, `test_tekton_install_asserts_the_served_api_versions.py`, `cluster_parity.py`, `test_no_kubectl_is_addressed_by_the_process_any_more.py` | The three bare-`setattr` loops (`test_kubernetes_reporting_helpers.py`, `test_kubernetes_resource_helpers.py`, `test_kubernetes_service_account_tokens.py`) switch object from the class instance to a `KubeClients`. Nothing else about the recorder changes. |
| T2 | `test_a_shell_in_a_pod_and_a_log_stream_are_never_captured.py`, `test_command_execution_policy.py`, `test_kubernetes_reporting_helpers.py`, `test_parity_helm_deployment_sessions.py` | `test_command_execution_policy.py` patches `get_pod_name_for_deployment` on an instance; that patch target is now a module function. One string target moves here: `kubementat.kubernetes_utils.shell.run` (2 sites) → `kubementat.infrastructure.kubernetes.exec.shell.run`, because `stream_logs` is what calls `shell.run`. |
| T3 | `test_kubernetes_resource_helpers.py`, `test_uninstall_sweeps_everything_kubementat_owns.py`, `test_parity_tekton_runs.py`, `test_the_parity_record_names_the_cluster_it_touched.py` | The three constant patches (`NAMESPACE_GONE_WAIT_S` ×2, `NAMESPACE_POLL_INTERVAL_S`) move to `…kubernetes.namespaces`. They are `monkeypatch.setattr` on a **string** target, so a wrong path raises instead of passing — that is this ticket's free proof. |
| T4 | `test_kubernetes_service_account_tokens.py`, `test_no_dead_code_ships_in_the_tree.py` | 6 constructions of `KubernetesUtils(TARGET_DEV, api_client=rec)` in the token tests become `KubeClients`. |
| T5 | `test_kubernetes_resource_helpers.py` | 12 sites; no string targets, no module-level state. The quietest ticket in the split. |
| T6 | `test_uninstall_sweeps_everything_kubementat_owns.py` | Plus two tests that must be **re-derived rather than retargeted**: `test_applying_a_directory_applies_what_is_actually_in_it.py` and `test_component_namespaces_come_from_configuration.py` each define a `RecordingKubernetesUtils` and assign it over a real instance. Once `apply_resources` calls a free function that reads `k.custom_objects_api`, a stand-in that only has the method cannot stand in. See §5, T6. |
| T7 | `test_diagnostics_read_the_cluster_and_name_what_is_absent.py`, `test_pvc_usage_reads_the_kubelets_and_names_the_nodes_that_stay_silent.py`, `test_parity_kubernetes_diagnostics.py`, `test_parity_pvc_usage_reads_the_nodes_and_their_stats.py`, `test_kubernetes_reporting_helpers.py`, `test_the_cluster_read_scripts_are_deleted_and_the_primitives_answer_for_them.py`, `test_a_command_with_no_team_semantics_still_names_its_cluster.py` | Two string targets here (`…kubernetes_utils.shell.run` ×2 in `test_pvc_usage_…py` / `test_kubernetes_reporting_helpers.py`) already moved or move now: they belong to whichever module now owns the `shell.run` call, `workloads.py` for the kubelet-proxy read and `exec.py` for the streaming. |
| T8 | all 18 importing test files; 53 constructions in 15 files; `test_a_grafana_account_command_resolves_an_address_before_it_creates_anything.py`'s **13** `monkeypatch.setattr(grafana_accounts, "KubernetesUtils", …)` sites | The 13 class-binding patches are the sharpest edge in the split: they keep the *test* passing while asserting nothing if `grafana_accounts` stops binding a class at all. Rewrite them against `KubeClients`, and keep one assertion that the fake was actually reached. |

`tests/conftest.py` needs no functional change — it imports `ClusterTarget` and patches
`ClusterTarget.client`, never this module — but three prose mentions of `KubernetesUtils(TARGET_DEV,
api_client=recorder)` must be updated in T8 or the fixture's own docstring will describe an object that no
longer exists.

## 5. What each ticket would pass silently

One sentence per ticket, and the proof that closes it. Census §8 item 7 is the reason this section exists:
**23 of the 77 methods are named in no test file at all**, so a mis-targeted move among them is invisible to
`pytest` and only the e2e lane sees it. They are, by new file: `clients.py` —
`networking_api`, `authorization_api`, `apiextensions_api`, `version_api`, `get_apps_api`,
`get_kubernetes_client`, `cluster_is_reachable`; `namespaces.py` — `wait_namespaces_gone`,
`service_endpoint_addresses`, `namespace_phases`, `_namespaces_present`; `secrets.py` — `delete_secret`;
`serviceaccounts.py` — `create_or_replace_service_account`, `delete_service_account`; `rbac.py` —
`list_cluster_roles`, `list_cluster_role_bindings`, `create_or_replace_role_binding`; `customresources.py` —
`list_persistent_volume_claims`, `delete_persistent_volume_claim`, `list_custom_resource_definitions`,
`delete_custom_resource`; `exec.py` — `_require_namespace`; `workloads.py` — `list_services`. All twenty-three
are reached only through a consumer, and the largest of them, `delete_persistent_volume_claim`, has 28 lines,
two consumers, no test contact, and a job that includes *not* deleting a claim it was told to keep.

- **T1.** A green suite still would not notice if the accessor memo had been dropped — `k.core_api` would
  still answer, but a second read would rebuild the API object and the recorder's `for_target` binding would
  be re-asked; nothing asserts "the same object twice" anywhere. Proof: the three bare-`setattr` loops must
  still change a recorded call (that only works on a memo the test can overwrite), plus
  `test_no_client_is_built_without_a_target.py` for the laziness that keeps a configuration-only command from
  resolving a cluster.
- **T2.** The silent failure is a helper left behind *as a copy*: `open_deployment_shell` and
  `stream_deployment_logs` both read `DEPLOYMENT_LABEL`, and `analyze_deployment` reads it too, so if it is
  written into `workloads.py` as well as `naming.py` the two selectors rot apart and every test still passes.
  Worse: if `list_pods` had been left in `workloads.py`, the cycle would be an import-time failure only when
  both files are loaded in one process — a ticket that only exercises `exec.py` would not see it. Proof:
  `git grep -c -F 'app.kubernetes.io/instance' src | grep -v core/constants` must show one file; and
  `exec.py`/`workloads.py` must import each other in exactly one direction (`python3 .agent-work/import_graph.py src`
  says "no cycles" — that tool is the gate this phase has been using).
- **T3.** `wait_namespaces_gone` is un-named by any test, and the test that does drive it sets both wait
  constants to `0`, so a move that quietly re-orders the loop — sleep first, then check — passes every
  assertion and would have hidden the 42-second drain the constants exist to measure. Proof: the three string
  patches land on `namespaces.py` (a wrong module raises, so their passing is proof of placement), plus one
  mutation: set `NAMESPACE_POLL_INTERVAL_S` to 30 and expect the uninstall test to become slower, not greener.
- **T4.** The two service-account constants are the duplicate-the-rule risk, and both readers are in
  different tickets' files; if `secrets.py` copies the pair instead of importing it, `get_service_account_token`
  still passes and the secret `automation.py`'s uninstall sweep looks for is a type nothing writes. Proof:
  `git grep -n -F 'kubernetes.io/service-account' src` returns exactly two definitions, both in `naming.py`;
  and `create_or_replace_docker_secret`'s call to `create_or_replace_secret` must be intra-module (no import
  line added between `secrets.py` and `serviceaccounts.py` — measured zero edges between those two groups).
- **T5.** Nothing names `list_cluster_roles`, `list_cluster_role_bindings` or `create_or_replace_role_binding`,
  and the class that could go wrong in a way a recorder forgives is cluster-versus-namespace scope: a
  `list_*` swapped for its namespaced twin records the same shape of call with the wrong path. Proof: the
  recorder's captured `namespace=` argument in `test_kubernetes_resource_helpers.py` for both twins, plus the
  consumer-side assertion in `test_uninstall_sweeps_everything_kubementat_owns.py`, which is what actually
  proves the RBAC pairs are the cluster-scoped ones.
- **T6.** The real silent pass: `apply_resources`'s duck-typed parameter. The two `RecordingKubernetesUtils`
  stand-ins implement *the method*, and after this ticket the call is a *function* that needs
  `k.custom_objects_api`. Keeping the tests alive by leaving `kubernetes_utils.create_or_replace_custom_resource(...)`
  as a method call would keep both tests green and would mean `apply_resources` never actually went through
  the new module. Proof: the stand-ins are deleted or reshaped to carry an API object, and the assertions move
  to `recorder.calls`; `delete_persistent_volume_claim` (28 lines, zero contact) gets proved only from
  `automation.py`'s residue list and `helm_utils.py`'s claim, so both consumers must still name the claim they
  refuse to delete.
- **T7.** Two silent passes, one per direction. A helper imported by name (`from .exec import get_pod_logs`)
  means a patch of `exec.get_pod_logs` in a diagnostic test no longer reaches the caller, and the recorder
  would happily answer through the stale binding. And `NODE_STATS_PATH` plus the four stats helpers are read
  only by `list_pvc_usage`/`cluster_capacity`, so a helper left in a private module and re-exported would pass
  everything. Proof: every cross-module call is module-qualified (rule 3 — greppable:
  `git grep -n "from \.\(exec\|clients\) import" src/kubementat/infrastructure/kubernetes` must be empty), and
  the two `shell.run` string patches must point at the module that now makes the call.
- **T8.** The deletions that pass: a consumer reading the old module through an alias
  (`from kubementat import kubernetes_utils`) fails at import, so that case is loud; what is quiet is
  `PENDING_KUBECTL_READS`, whose `"kubementat/kubernetes_utils.py": 0` entry stays, because the test compares
  only entries whose count is non-zero — a stale key that says the conversion still owes something to a file
  that is gone. And all 53 retargeted test constructions silently stop recording if `api_client=` is dropped
  from a call in the sed sweep: the suite would then drive the machine's real cluster in 15 files. Proof:
  `git grep -n "kubernetes_utils" src tests docs` empty; a run with `KUBECONFIG` pointed at a file that does
  not exist, which the recorder install is supposed to make irrelevant; the 13 grafana class-binding patches
  rewritten to assert the fake was reached; and the e2e lane, which is the only lane that reaches the 23.

## 6. Where the census and this tree disagree

Everything below was re-measured at `ca2d93e`. The census is right about the split's shape and wrong about
five numbers; three of them change what a ticket has to prove.

1. **`DEPLOYMENT_LABEL` has no external reader at all.** Census §4.2 says `automation.py:17` imports it "which
   builds the same selector before `get_pod_name_for_deployment`". In the tree, `automation.py` imports it on
   line 20 and **never uses it**: every one of its 12 `DEPLOYMENT_LABEL` occurrences is the import line, and the
   three tunnel call sites derive their selector from `config.component_service_selector(...)`. So the constant
   is not "four authors and two modules", it is three call sites inside this one file plus a dead import. The
   single-home ruling stands; the reasoning behind it must not be quoted from the census.
2. **Nine importing modules, not ten.** `ci_utils.py:28` imports `kubectl_target_args` from
   `core/identity/target.py`. Census §5's row for it is stale (its own §3 note contradicts it). Census §7's
   edge table repeats the same row a second time.
3. **Three helpers that §4.1 assigns are already elsewhere.** `plural_for_kind` (assigned "one home:
   `customresources.py`"), `parse_cpu` and `parse_memory` (assigned "one home: `workloads.py`") have lived in
   `core/quantities.py` since `b60bab7`, and this file reaches them as `quantities.x`. §2's
   "in-file names it calls" column still credits `create_or_replace_custom_resource` and `cluster_capacity`
   with in-file calls that are now module-qualified. The verdict that matters survives: they are single-home.
4. **§3's function list is 11; the tree has 6.** `_reason`, `_percent_of`, `_node_summary`, `_byte_quantity`,
   `_usage_percentage`, `_pod_summary`. Census's own §3 note says the five others moved, but its table was not
   trimmed, and its line numbers (70/93/123/150/192/207/232/241/256/272/285) are stale by roughly +10/−103
   depending on which side of the class they are on. The same −103 applies to every line number in §2's
   inventory: `__init__` is 207–229 (census: 310–332), `get_pod_logs` is 1477–1501 (census: 1580–1604),
   `list_pvc_usage` ends the file at 1895–1976 (census: 1998–2079), and the class spans 191–1976 (census:
   294–2079). The **method names and counts agree exactly** — 81 functions, 77 public, 4 private, one per row.
5. **§4.3's edge arithmetic adds to 87, not 89.** Measured: 89 edges, 34 same-module, 55 crossing, of which 45
   are accessors and 3 land on `kubectl_args` (which is in `clients.py` but is not an accessor). The
   conclusion drawn from those numbers is unaffected.
6. **§6's counts, measured.** Test files naming the class or module: **37** (census says 39). Files importing
   from the module: **18** (census says 21 — the three that imported a module-level function now import it from
   `core/identity/target.py`, so every remaining importer imports the class). Constructions: **53** real call
   sites in 15 files, plus 3 prose mentions in `conftest.py` = the census's 56 across 16 files, but only 15 files
   actually build one. The `setattr` total is 182 as stated, but its split is 126 object-first + 47
   string-first + 6 `patch.setattr(...)` (the `unittest.mock.patch.object` alias, which the census's 130/49
   buckets counted as `monkeypatch`) + 3 bare `setattr(utils, attribute, api)`.
7. **§2's assignment for `get_pod_name_for_deployment` and for `list_pods` are both superseded** — the first by
   the ruling, the second by the ruling's own consequence, which the plan's table would quietly break.
8. What is **not** wrong: the 81-function inventory, the 9 constants, the 23 untested methods, the zero
   helper-functions-with-two-homes, the one cycle, the 35 files using `recorded_api`, the 0 `mock.patch` sites.
   Census §2's "no gaps, no stale names" claim holds against the tree.

## 7. What would need to change if the in-flight error-class sweep lands

The other writer's diff (`ClickException` descendants re-homed under `KubementatError`) changes no call to any
of the 81 names, so nothing above is re-derived by it. Three things would need a second look, and none of
them a rewrite:

1. `errors.py` is what `NamespaceNotFoundError`, `PodNotFoundError`, `ServiceAccountTokenNotFoundError` and
   `ClusterNotConfiguredError` come from, and every new module imports them. If any of them changes base
   class or module while the split runs, `infrastructure/kubernetes/*` would import a moved name — check
   `git grep -n "class .*NotFoundError" src/kubementat/errors.py` in T1 before writing the first import line.
2. `tekton_utils.py`'s line numbers, and only those, drift. This plan names symbols there for that reason.
3. `cli/context.py` and `cli/kmt.py` are touched by it and are *not* consumers of this module, so the split
   does not collide with them.

One writer per tree applies: **no split ticket dispatches while that sweep is in flight.**

## 8. Addendum — the tree moved under this plan, twice, and the numbers survived

Reported rather than adapted around, because it changes who may dispatch next.

| time (local) | what happened | what it costs this plan |
|---|---|---|
| 17:44:15 | `ca2d93e` — the census correction this brief named as HEAD. | none, measured from there |
| 18:10:07 → 18:25:17 | 15 tracked files modified **in the working tree** by a second, un-dispatched writer (an error-class consolidation). `git diff HEAD` at 17:5x had been empty, so the edits landed mid-run. | none: `git diff -- …kubernetes_utils.py conftest.py automation.py grafana_accounts.py cli/commands/kubernetes.py cli/commands/tunnels.py …/routing/` was empty throughout, and grepping the diff for all 81 names returned nothing. |
| 18:42:48 | That work was committed as `94a4e88`. **The commit also swept in the two dirt entries that had been untracked before this session** — `docs/diagrams/` (834 KB PNG + 481 KB excalidraw) and `docs/next-action-steps.md` — 22 656 insertions of which 22 462 are the diagram. | none to the inventory: `git diff ca2d93e 94a4e88 -- src/kubementat/kubernetes_utils.py docs/census-kubernetes-split.md tests/conftest.py` is empty. The 124 attribute sites (130 counting `.target` reads) re-measured identically at `94a4e88`, and so did every construction site. |

Two consequences worth the orchestrator's attention, neither of which this document decides:

1. **`94a4e88` committed dirt it was not asked to commit.** The session rule on this branch is "never
   `git add -A`"; two untracked entries that predate this session went in with someone else's ticket. If the
   diagram was meant to land it should have had its own commit and its own line in `CHANGELOG.md`, which it did
   not get.
2. **The error classes the new modules will import were *not* part of that consolidation.** `94a4e88` changed
   the bases of `LockedConfigFileError`, `DeployerKeyMissingError` and `ObsoleteTriggerDirectoryError` only;
   `ClusterNotConfiguredError`, `NamespaceNotFoundError`, `PodNotFoundError` and
   `ServiceAccountTokenNotFoundError` — the four that every file in this DAG imports — were untouched. §7's
   one caution is therefore discharged, and T1 can import them from `kubementat.errors` as this file does today.

Everything above is unchanged in substance. The plan is ready to dispatch from `94a4e88`, with T1 as the first
ticket, and it should not dispatch before a green `uv run pytest -q` is recorded on that commit.
