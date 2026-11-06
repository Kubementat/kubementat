# Census: `src/kubementat/kubernetes_utils.py` before it is split

The file as it stands at `e9c9759` on `v2-arch-refactoring`: **2 080 lines**, one class `KubernetesUtils` spanning **294–2079**, and 293 lines above it holding 11 module-level functions, one small result type and 9 constants. Built with `ast.parse` over the file rather than grep; every count was checked a second way (line-wise regex over the class span, `git grep -l` per symbol, `find`+`grep` for the consumer lists).

The `target module` column below restates the assignment the split is expected to follow. Where this census and that assignment disagree, the file is the evidence and wins: nothing here is argued from a table's authority, and that table's counts are stale by design.

## 1. Headline

| what | count | where |
|---|---:|---|
| methods on the class | **81** = 77 public + 4 private | 294–2079 |
| code lines inside those methods | 1 467 | — |
| private methods | **4**: `__init__`, `_api`, `_namespaces_present`, `_require_namespace` | 310, 367, 820, 1739 |
| module-level functions | **11** | 70–291 |
| module-level type / constants | 1 (`DeleteOutcome`, 46–67) + 9 | 27–189 |
| public methods with no named target | **0** | — |
| private methods with no named target | **4** | same four |
| src modules importing this module | **10** | section 5 |
| src modules naming the class without importing it | **1** (`k8s_apply.py:133`, docstring) | section 5 |
| files under `tests/` mentioning the class or module | **39** | section 6 |
| of which import from the module / import the class | **21** / 18 | section 6 |
| `setattr(` sites in `tests/*.py` | **182** — 130 `monkeypatch.setattr` object-first, 49 string-first, 3 bare | section 6 |
| functions with two homes | **0** | section 4 |
| constants with two homes | **3** | section 4.2 |
| method-to-method edges that form a cycle | **1** (`workloads` ⇄ `exec`) | section 4.3 |
| methods no test file names | **23** | section 6 |

The one thing a method-to-file table misses is here, and it is not a function: section 4.3.

## 2. Method inventory

`kind`: public, constructor, private. `code` counts non-blank non-comment lines. The last column names what the body calls inside this file: module-level functions, private methods, and other methods of the class. Client accessors reached as attributes (`self.core_api`) are not repeated there. There is no indirection through a registry, so whatever is missing from that column is not called.

| method | kind | lines | code | target module | in-file names it calls |
|---|---|---:|---:|---|---|
| `__init__` | constructor | 310–332 | 21 | — | — |
| `target` | public | 335–349 | 14 | clients.py | — |
| `api_client` | public | 352–365 | 12 | clients.py | — |
| `_api` | private | 367–379 | 11 | — | — |
| `apps_api` | public | 383–384 | 2 | clients.py | `_api` |
| `custom_objects_api` | public | 387–388 | 2 | clients.py | `_api` |
| `core_api` | public | 391–392 | 2 | clients.py | `_api` |
| `rbac_api` | public | 395–396 | 2 | clients.py | `_api` |
| `networking_api` | public | 399–400 | 2 | clients.py | `_api` |
| `authorization_api` | public | 403–411 | 8 | clients.py | `_api` |
| `apiextensions_api` | public | 414–415 | 2 | clients.py | `_api` |
| `version_api` | public | 418–426 | 8 | clients.py | `_api` |
| `dynamic_client` | public | 429–436 | 7 | clients.py | — |
| `kubectl_args` | public | 442–449 | 7 | clients.py | `kubectl_target_args` |
| `self_can` | public | 454–467 | 13 | clients.py | — |
| `cluster_is_reachable` | public | 469–482 | 13 | clients.py | — |
| `list_cluster_roles` | public | 487–493 | 6 | rbac.py | — |
| `list_cluster_role_bindings` | public | 495–497 | 3 | rbac.py | — |
| `list_ingress_classes` | public | 499–512 | 12 | customresources.py | — |
| `delete_ingress_class` | public | 514–531 | 17 | customresources.py | `_reason` |
| `list_persistent_volume_claims` | public | 533–545 | 11 | customresources.py | — |
| `delete_persistent_volume_claim` | public | 547–574 | 25 | customresources.py | `_reason` |
| `list_custom_resource_definitions` | public | 576–590 | 12 | customresources.py | — |
| `delete_cluster_scoped` | public | 592–622 | 29 | customresources.py | `_reason` |
| `get_apps_api` | public | 624–625 | 2 | clients.py | — |
| `get_core_api` | public | 627–628 | 2 | clients.py | — |
| `get_rbac_api` | public | 630–631 | 2 | clients.py | — |
| `get_custom_objects_api` | public | 633–634 | 2 | clients.py | — |
| `get_kubernetes_client` | public | 636–637 | 2 | clients.py | — |
| `api_version_is_served` | public | 639–667 | 25 | clients.py | — |
| `create_namespace` | public | 670–692 | 21 | namespaces.py | — |
| `delete_namespace` | public | 694–716 | 21 | namespaces.py | — |
| `list_namespaces` | public | 718–728 | 9 | namespaces.py | — |
| `wait_namespaces_gone` | public | 730–761 | 28 | namespaces.py | `_namespaces_present` |
| `service_endpoint_addresses` | public | 763–797 | 31 | namespaces.py | `get_core_api` |
| `namespace_phases` | public | 799–818 | 16 | namespaces.py | — |
| `_namespaces_present` | private | 820–822 | 3 | — | `list_namespaces` |
| `create_or_replace_secret` | public | 825–871 | 42 | secrets.py | — |
| `create_or_replace_docker_secret` | public | 873–894 | 17 | secrets.py | `create_or_replace_secret` |
| `list_secrets` | public | 896–906 | 9 | secrets.py | — |
| `get_service_account` | public | 909–926 | 15 | serviceaccounts.py | — |
| `create_service_account` | public | 928–952 | 21 | serviceaccounts.py | — |
| `create_or_replace_service_account` | public | 954–993 | 35 | serviceaccounts.py | — |
| `list_service_accounts` | public | 995–1005 | 9 | serviceaccounts.py | — |
| `list_roles` | public | 1007–1017 | 9 | rbac.py | — |
| `patch_service_account_with_pull_secrets` | public | 1019–1053 | 24 | serviceaccounts.py | `get_core_api`, `get_kubernetes_client`, `get_service_account` |
| `create_or_replace_service_account_token_secret` | public | 1055–1086 | 30 | secrets.py | — |
| `get_service_account_token` | public | 1088–1138 | 47 | serviceaccounts.py | — |
| `get_custom_resource` | public | 1141–1161 | 19 | customresources.py | `get_custom_objects_api` |
| `list_custom_resources` | public | 1163–1182 | 17 | customresources.py | — |
| `create_or_replace_custom_resource` | public | 1184–1235 | 44 | customresources.py | `plural_for_kind` |
| `delete_custom_resource` | public | 1237–1253 | 16 | customresources.py | — |
| `create_or_replace_role` | public | 1263–1285 | 22 | rbac.py | — |
| `create_or_replace_cluster_role` | public | 1287–1301 | 14 | rbac.py | — |
| `create_or_replace_cluster_role_binding` | public | 1303–1317 | 14 | rbac.py | — |
| `delete_role` | public | 1319–1328 | 10 | rbac.py | — |
| `delete_role_binding` | public | 1330–1339 | 10 | rbac.py | — |
| `delete_cluster_role` | public | 1341–1354 | 13 | rbac.py | — |
| `create_or_replace_role_binding` | public | 1356–1371 | 16 | rbac.py | — |
| `list_role_bindings` | public | 1373–1388 | 13 | rbac.py | — |
| `get_role_binding` | public | 1390–1410 | 17 | rbac.py | — |
| `delete_secret` | public | 1412–1423 | 12 | secrets.py | — |
| `delete_service_account` | public | 1425–1436 | 12 | serviceaccounts.py | — |
| `open_tunnel` | public | 1439–1464 | 24 | tunnel.py | — |
| `get_pod_name_for_deployment` | public | 1466–1494 | 26 | workloads.py | `list_pods` |
| `list_pods_for_all_namespaces` | public | 1497–1508 | 10 | workloads.py | — |
| `list_pods` | public | 1510–1521 | 10 | workloads.py | — |
| `list_services` | public | 1523–1534 | 10 | workloads.py | — |
| `get_service_endpoint` | public | 1536–1578 | 38 | workloads.py | `list_services` |
| `get_pod_logs` | public | 1580–1604 | 22 | exec.py | — |
| `stream_logs` | public | 1606–1639 | 31 | exec.py | — |
| `exec_in_pod` | public | 1641–1668 | 26 | exec.py | — |
| `open_deployment_shell` | public | 1670–1709 | 35 | exec.py | `_require_namespace`, `exec_in_pod`, `get_pod_name_for_deployment` |
| `stream_deployment_logs` | public | 1711–1737 | 23 | exec.py | `_require_namespace`, `stream_logs` |
| `_require_namespace` | private | 1739–1764 | 22 | — | — |
| `get_nodes` | public | 1766–1774 | 8 | workloads.py | — |
| `get_api_resources` | public | 1776–1801 | 24 | clients.py | — |
| `analyze_deployment` | public | 1804–1863 | 56 | workloads.py | `_pod_summary`, `get_pod_logs`, `list_pods` |
| `list_pod_images` | public | 1865–1894 | 27 | workloads.py | `list_pods` |
| `cluster_capacity` | public | 1896–1996 | 90 | workloads.py | `_percent_of`, `parse_cpu`, `parse_memory`, `get_custom_objects_api`, `get_nodes` |
| `list_pvc_usage` | public | 1998–2079 | 73 | workloads.py | `_byte_quantity`, `_node_summary`, `_usage_percentage`, `get_nodes` |

Every public row is covered by the assignment, one row per method, no gaps and no stale names. The four private ones are not named in it. `_namespaces_present` (820) and `_api` (367) are each called from methods of one module, so they travel; `_require_namespace` (1739) does real work for the two deployment commands and would be dropped by a table-driven move. `__init__` (310–332) does two things — decide `Context`-versus-`ClusterTarget`, and store the injected `api_client` every client later comes from, which is the seam `tests/conftest.py:608` depends on. Deleting it deletes the seam.

## 3. Module-level names

| function | lines | code | callers in this file | callers outside |
|---|---|---:|---|---|
| `_reason` | 70–90 | 19 | `delete_ingress_class` (531), `delete_persistent_volume_claim` (574), `delete_cluster_scoped` (622) | none |
| `kubectl_target_args` | 93–120 | 26 | `kubectl_args` (449) | `ci_utils.py:28,117`, `automations/routing/routing.py`, 7 test files |
| `helm_target_args` | 123–147 | 24 | **none** | `automation.py:18`, `helm_utils.py:12,214,297`, 2 test files |
| `plural_for_kind` | 150–162 | 12 | `create_or_replace_custom_resource` (1208) | none |
| `parse_cpu` | 192–204 | 13 | `cluster_capacity` (1925, 1956, 1969) | none |
| `parse_memory` | 207–229 | 22 | `cluster_capacity` (1926, 1957, 1970), `_byte_quantity` (268) | `src/cli/commands/kubernetes.py:30` |
| `_percent_of` | 232–238 | 7 | `cluster_capacity` (1973, 1980) | none |
| `_node_summary` | 241–253 | 12 | `list_pvc_usage` (2044) | none |
| `_byte_quantity` | 256–269 | 13 | `list_pvc_usage` (2067, 2068, 2074) | none |
| `_usage_percentage` | 272–282 | 10 | `list_pvc_usage` (2075) | none |
| `_pod_summary` | 285–291 | 7 | `analyze_deployment` (1859, 1860) | none |

Plus `DeleteOutcome` (46–67, a `(deleted, reason)` tuple with `__new__`/`__bool__` and a `deleted`/`reason` pair of accessors) returned by the three delete methods above, and nine constants: `SERVICE_ACCOUNT_TOKEN_SECRET_TYPE` (27), `SERVICE_ACCOUNT_NAME_ANNOTATION` (28), `NAMESPACE_GONE_WAIT_S` (35), `NAMESPACE_POLL_INTERVAL_S` (36), `NODE_STATS_PATH` (43), `DEPLOYMENT_LABEL` (168), `MANAGED_BY_LABEL` (182), `MANAGED_BY_KUBEMENTAT` (183), `DEPLOYMENT_SHELL_COMMAND` (189).

Four observations before the split:

- Eight of the eleven are used nowhere outside this file: the six private ones (`_reason`, `_percent_of`,
  `_node_summary`, `_byte_quantity`, `_usage_percentage`, `_pod_summary`) plus `parse_cpu` and
  `plural_for_kind`.
- Five are pure functions of their arguments (`kubectl_target_args`, `helm_target_args`, `plural_for_kind`,
  `parse_cpu`, `parse_memory`), and three of those are read from another module —
  `kubectl_target_args` (`ci_utils.py:28`, used at 117), `helm_target_args` (`automation.py:18`,
  `helm_utils.py:12`), `parse_memory` (`src/cli/commands/kubernetes.py:30`) — so they cannot be carried
  along by the method that happens to call them.
- `MANAGED_BY_LABEL` and `MANAGED_BY_KUBEMENTAT` (182, 183) have **no use inside this file** — they exist here only because `automation.py:17` imports them from here.
- `helm_target_args` (123) has no caller inside this file at all: a helm argv builder parked between lines 93 and 150 of a kubernetes module. Cutting the file along the method rows carries it into `infrastructure/kubernetes/` for no reason.

## 4. Shared names, and who calls them — the part that breaks the split

### 4.1 Functions with two homes: zero

Checked helper by helper, caller by caller, against the module each caller is assigned to.

| helper | called by | modules that need it | verdict |
|---|---|---|---|
| `_api` (367) | the eight client accessors, at 384, 388, 392, 396, 400, 411, 415 and 426 | `clients.py` | one home |
| `_namespaces_present` (820) | `wait_namespaces_gone` (757, 760) | `namespaces.py` | one home |
| `_require_namespace` (1739) | `open_deployment_shell` (1703), `stream_deployment_logs` (1735) | `exec.py` only | one home |
| `_reason` (70) | `delete_ingress_class` (531), `delete_persistent_volume_claim` (574), `delete_cluster_scoped` (622) | `customresources.py` | one home |
| `DeleteOutcome` (46) | the same three | `customresources.py` | one home |
| `kubectl_target_args` (93) | `kubectl_args` (449) | `clients.py` | one home |
| `plural_for_kind` (150) | `create_or_replace_custom_resource` | `customresources.py` | one home |
| `parse_cpu` (192), `parse_memory` (207), `_percent_of` (232) | `cluster_capacity` | `workloads.py` | one home |
| `_node_summary` (241), `_byte_quantity` (256), `_usage_percentage` (272) | `list_pvc_usage` | `workloads.py` | one home |
| `_pod_summary` (285) | `analyze_deployment` | `workloads.py` | one home |

No function in this file has to be written twice.

### 4.2 Constants with two homes: three

The same test applied to names instead of functions does find the failure mode.

| name | read at | modules that need it |
|---|---|---|
| `DEPLOYMENT_LABEL` (168) | `open_deployment_shell` (1705) and `stream_deployment_logs` (1736) → `exec.py`; `analyze_deployment` (1823) → `workloads.py`; and outside in `automation.py:17`, which builds the same selector before `get_pod_name_for_deployment` (2566, 2592, 2620) | two |
| `SERVICE_ACCOUNT_TOKEN_SECRET_TYPE` (27) | `create_or_replace_service_account_token_secret` (1076) → `secrets.py`; `get_service_account_token` (1115) → `serviceaccounts.py` | two |
| `SERVICE_ACCOUNT_NAME_ANNOTATION` (28) | same two methods (1074 → `secrets.py`; 1116 → `serviceaccounts.py`) | two |

`DEPLOYMENT_LABEL` is one string rule with four authors and two target modules. Copying it into both is exactly the second-copy failure: the next deployment-addressing change fixes one file and silently rots the other, and here it would also rot `automation.py`, which builds the selector itself. Same shape for the two service-account names, which are a matched pair — a secret's type and the annotation that ties it back. Give all three one home both sides can import.

### 4.3 The edge that forms a cycle: `workloads` ⇄ `exec`

Of the 89 distinct method-to-method edges inside this file, 21 stay within one target module, 14 involve
one of the private helpers, and 52 run down to an accessor that lives in `clients.py`. Two point sideways
at each other:

- `analyze_deployment` (1804, `workloads.py`) calls `self.get_pod_logs(...)` at **1846**, which is assigned
  to `exec.py`.
- `open_deployment_shell` (1670, `exec.py`) calls `self.get_pod_name_for_deployment(...)` at **1704**,
  which is assigned to `workloads.py`.

Cut the file along the method rows and `exec.py` imports `workloads.py` for the pod lookup while
`workloads.py` imports `exec.py` for one log read — a cycle inside the new package, the shape the
dependency rule is meant to make impossible. It is a decision, not a mechanical move: `get_pod_logs`
(1580) is a single-pod read that `analyze_deployment` needs, so it belongs on the side that is below
the other, with `exec.py`'s streaming family calling it from there; or the label-selector lookup stays
in `workloads.py` and `analyze_deployment` stops reaching across for logs. What must not happen is a
second copy of either lookup to break the import: the selector built from `DEPLOYMENT_LABEL` (1823,
1705) is the rule that would then exist twice, once per module, and that is the failure this census
exists to catch.

### 4.4 If the file boundaries are redrawn, helpers gain homes

The current assignment keeps every helper single-home, and two tempting refinements would undo that:

- Splitting `customresources.py` into resource-shaped files moves the three delete methods apart, and `_reason` plus `DeleteOutcome` immediately serve two or three masters.
- Splitting `workloads.py` around its stats methods separates `cluster_capacity` from `list_pvc_usage`, which share `parse_memory`, `_byte_quantity`, `_percent_of`, `_node_summary`, `_usage_percentage` and `_pod_summary` — six names duplicating at once.

Hold the boundary, or name the shared module before moving anything into it.

## 5. Consumers

Ten modules import from `src/kubementat/kubernetes_utils.py`; one more names the class without
importing it. The `sites` column counts method references (calls and bound-method references), and the
methods are named where the number is more than one.

| file | imports (line) | what it imports | uses |
|---|---:|---|---|
| `src/cli/commands/kubernetes.py` | 30 | `KubernetesUtils`, `parse_memory` | 8 methods, six of them passed as callables: `analyze_deployment` (55), `list_pod_images` (73), `cluster_capacity` (88), `list_pvc_usage` (202), `open_deployment_shell` (250), `stream_deployment_logs` (422), plus `get_service_account_token` (265) and `create_or_replace_secret` (391) |
| `src/cli/commands/tunnels.py` | 22 | `KubernetesUtils` | instance at 141, `open_tunnel` passed on at 142 |
| `src/kubementat/automation.py` | 17 | `DEPLOYMENT_LABEL`, `MANAGED_BY_KUBEMENTAT`, `MANAGED_BY_LABEL`, `KubernetesUtils`, `helm_target_args` | 72 references over 38 methods; keeps one instance as `self.kubernetes_utils` (138) |
| `src/kubementat/tekton_utils.py` | 4 | `KubernetesUtils` | 25 references over 16 methods; instance at 942, a second one built at 1440 to hand to `apply_resources` |
| `src/kubementat/helm_utils.py` | 12 | `KubernetesUtils`, `helm_target_args` | instance at 115; `list_persistent_volume_claims` (626), `delete_persistent_volume_claim` (643), and it answers its own `target` property from the instance (126, used at 214, 297, 368, 584) |
| `src/kubementat/user_management.py` | 34 | `KubernetesUtils` | takes the instance as a parameter at 111, 224, 244; `target` (139), `self_can` (161), `get_service_account_token` (236), `kubectl_args` as an attribute (257) |
| `src/kubementat/grafana_accounts.py` | 51 | `KubernetesUtils` | instance at 153, `get_service_endpoint` (154) |
| `src/kubementat/automations/routing/routing.py` | 485, 585 | `KubernetesUtils`, function-local imports | `kubectl_args` (487), `list_ingress_classes` (588) |
| `src/kubementat/automations/routing/traefik_presence.py` | 55 | `KubernetesUtils` | builds a default instance at 444, then hands three of its members to the probe helpers: `target` (485), `list_ingress_classes` (525), `list_pods_for_all_namespaces` (554) |
| `src/kubementat/ci_utils.py` | 28 | `kubectl_target_args` | no method at all: builds its own argv (117) |
| `src/kubementat/k8s_apply.py` | none | names the class in prose at 133 | `create_or_replace_custom_resource` (188) on whatever object it is handed (signature 95) |

Fifty-five of the 77 public methods are referenced from outside the file. The other 22 are the client
accessors (`api_client`, `apps_api`, `core_api`, `rbac_api`, `networking_api`, `authorization_api`,
`apiextensions_api`, `version_api`, `custom_objects_api`, `dynamic_client`, the `get_*_api` family,
`get_kubernetes_client`, `get_api_resources`) and `exec_in_pod`, `stream_logs`, `get_pod_logs`,
`list_pods`, `list_services`, `delete_role` — used inside the module or by tests only.

Three call sites pass a whole instance across a module boundary, so those signatures and bodies change
with the class, not just their import line: `k8s_apply.apply_resources` (`k8s_apply.py:95`, called from
`automation.py:632` and `tekton_utils.py:1440`),
`tekton_utils.preflight_tekton_api_version` (`tekton_utils.py:129`) and
`preflight_installed_tekton_api_versions` (`tekton_utils.py:199`, called from `automation.py:190, 412`),
and `user_management.check_cluster_and_access` (`user_management.py:111`) / `read_service_account_token`
(224) / `local_cluster_settings` (244). They keep working only if whatever replaces the object answers
the same method names.

Files under `src/` that mention `KubernetesUtils` in prose or a type annotation but never import it:
`src/kubementat/k8s_apply.py` (133) alone.

## 6. How the tests reach the class

Counted as separate questions, because no one of them answers another.

| question | answer |
|---|---|
| files under `tests/` naming the class or the module | **39**; the class name itself appears 129 times in 26 files, plus 6 uses of the `RecordingKubernetesUtils` stand-ins in 2 files |
| files that import from `kubernetes_utils` | **21** |
| of those, importing the class itself | 18 |
| importing a module-level function only | 3 (`test_no_kubectl_is_addressed_by_the_process_any_more.py:238`, `test_no_helm_call_is_addressed_by_the_process_any_more.py:356`, `test_the_ci_container_is_started_from_configuration_and_not_a_script.py:25`) |
| `mock.patch("kubementat.kubernetes_utils.…")` | **0** |
| string targets naming this module | **5**, all `monkeypatch.setattr`: `kubementat.kubernetes_utils.shell.run` (`test_kubernetes_reporting_helpers.py:55`, `test_pvc_usage_reads_the_kubelets_and_names_the_nodes_that_stay_silent.py:97`), `kubementat.kubernetes_utils.NAMESPACE_GONE_WAIT_S` (`test_uninstall_sweeps_everything_kubementat_owns.py:1559, 1595`), `...NAMESPACE_POLL_INTERVAL_S` (1596) |

The `setattr` count, split the way it has to be split — **182** call sites in `tests/*.py`: 179 of them
`monkeypatch.setattr` (130 with an object as the first argument, 49 with a string) and 3 bare
`setattr(utils, attribute, api)`. One more line matches `setattr(` in grep and is a comment, not a call:

| form | sites | what it does |
|---|---:|---|
| object → instance, replacing a method | **5** | `test_a_rule_says_where_things_go_and_not_what_may_be_deleted.py:142` and `test_uninstall_sweeps_everything_kubementat_owns.py:1013, 1014, 1344, 1345` — swap `delete_namespace` / `delete_cluster_scoped` on an `Automation`'s instance |
| instance → an API attribute | **4** | `test_kubernetes_reporting_helpers.py:58`, `test_kubernetes_resource_helpers.py:77`, `test_kubernetes_service_account_tokens.py:52` (three bare `setattr` loops assigning `core_api`, `rbac_api`, … to the instance) and `test_command_execution_policy.py:393` (`monkeypatch`, replaces `get_pod_name_for_deployment`) |
| object → **another module's class binding** | **13** | every one is `monkeypatch.setattr(grafana_accounts, "KubernetesUtils", …)` in `test_a_grafana_account_command_resolves_an_address_before_it_creates_anything.py` (195, 225, 234, 256, 279, 299, 321, 337, 364, 391, 408, 455, 473) |
| string target naming this module | 5 | listed above |
| everything else | 155 | 111 object-form and 44 string-form, against other modules' objects and names |

The object-form set is the one that fails silently. A patch that replaces a method on an instance
survives a move, because the instance still carries the method; a patch that replaces the class binding
inside `grafana_accounts` only keeps working while that module still binds the name `KubernetesUtils`.
All 13 would keep passing while asserting nothing if the move made `grafana_accounts` build something
else from a differently named import.

**No fixture hands out the class.** Every test that wants one builds it — **56 construction sites across
16 files**, mostly `KubernetesUtils(TARGET_DEV, api_client=recorded_api)` and three of those 56 are prose mentions in `tests/conftest.py` (342, 401, 615), not constructions (for example
`tests/test_diagnostics_read_the_cluster_and_name_what_is_absent.py:86`,
`tests/test_a_shell_in_a_pod_and_a_log_stream_are_never_captured.py:107`,
`tests/test_command_execution_policy.py:384`). The single fixture in the loop, `recorded_api`
(`tests/conftest.py:608`), returns a recorder and installs it by patching `ClusterTarget.client` — it
never imports this module. 35 files reference `recorded_api` (34 test files plus `conftest.py` itself). So the seam is the constructor's
`api_client` parameter (310–332) plus `ClusterTarget.client`, and `tests/conftest.py:646` says so out
loud: a test driving `Automation` or `TektonUtils` cannot inject through the constructor it is hidden
behind, so it injects through the target.

Nothing subclasses the class. Two tests build a duck-typed stand-in named `RecordingKubernetesUtils`
(`test_applying_a_directory_applies_what_is_actually_in_it.py:42`,
`test_component_namespaces_come_from_configuration.py:68`), assigned over a real instance (89); the
methods they implement are the ones `apply_resources` and the namespace logic call.
`tests/test_the_tekton_install_reads_its_configuration_first.py:145` patches a private `TektonUtils`
helper, not this class.

**23 methods are named in no test file at all**: `_namespaces_present`, `_require_namespace`,
`apiextensions_api`, `authorization_api`, `cluster_is_reachable`, `create_or_replace_role_binding`,
`create_or_replace_service_account`, `delete_custom_resource`, `delete_persistent_volume_claim`,
`delete_secret`, `delete_service_account`, `get_apps_api`, `get_kubernetes_client`,
`list_cluster_role_bindings`, `list_cluster_roles`, `list_custom_resource_definitions`,
`list_persistent_volume_claims`, `list_services`, `namespace_phases`, `networking_api`,
`service_endpoint_addresses`, `version_api`, `wait_namespaces_gone`.

None of them is called directly by a test; all are reached through a consumer — `namespace_phases` (799), `wait_namespaces_gone` (730),
the cluster-role listings and the delete pairs all go through `automation.py`; `service_endpoint_addresses`
(763) goes through `tekton_utils.py:1080`; the client accessors go through `tekton_utils.py`,
`helm_utils.py` and `user_management.py`. The consequence for the split: a move
that mis-targets any of them is invisible to `pytest`, and only the e2e lane sees it. The worst of them
is `delete_persistent_volume_claim` (547–574): 28 lines, a delete that names a claim it must not touch,
two consumers (`automation.py`, `helm_utils.py:643`) and zero test contact.

## 7. Edges into the files that move later

Nothing in `kubernetes_utils.py` imports `automation.py`, `helm_utils.py`, `tekton_utils.py` or any
other module under `src/kubementat/` except `cluster.py` (11), `errors.py` (12–19) and `shell.py` (20).
The references to those three are prose in comments and docstrings (129, 172–173, 536–537), so this
module does not reach upward, and a move of it cannot pull anything along. It is the destination, not
the source.

| from | references | methods it calls | why it matters here |
|---|---:|---|---|
| `src/kubementat/automation.py` | 72 (38 methods) | the whole install/uninstall surface: `list_namespaces`, `create_namespace`, `namespace_phases`, `wait_namespaces_gone`, `delete_namespace`, `delete_cluster_scoped`, the RBAC pairs, `list_secrets`, `delete_secret`, `self_can`, `cluster_is_reachable`, `open_tunnel`, `get_pod_name_for_deployment`, … | holds one instance (138) and hands it to `k8s_apply` (632) and to `user_management` (938). Its own class moves later than this file does, so it is the largest thing a split here breaks |
| `src/kubementat/tekton_utils.py` | 25 (16) | `api_version_is_served` ×2, the cluster-role and binding creators ×5, `create_or_replace_service_account` ×3, the custom-resource set, `service_endpoint_addresses` (1080) | builds a second instance at 1440 purely to pass to `apply_resources`; also moves later |
| `src/kubementat/helm_utils.py` | 3 | `list_persistent_volume_claims` (626), `delete_persistent_volume_claim` (643), `target` (126) | its own move is in the same order of business as this one |
| `src/kubementat/user_management.py` | 4 | `target` (139), `self_can` (161), `get_service_account_token` (236), `kubectl_args` (257) | parameter-typed; annotation at 111 and 224 |
| `src/kubementat/k8s_apply.py` | 1 | `create_or_replace_custom_resource` (188) | untyped parameter (95); it already depends on the method name, not the type |
| `src/kubementat/automations/routing/routing.py` | 2 | `kubectl_args` (487), `list_ingress_classes` (588) | two function-local imports (485, 585) that survive a rename only if the string stays a real module path |
| `src/kubementat/automations/routing/traefik_presence.py` | 3 | `target` (485), `list_ingress_classes` (525), `list_pods_for_all_namespaces` (554) | hands three members to its probe helpers, so a name change there is invisible to an import sweep |
| `src/cli/commands/kubernetes.py` | 8 | see section 5 | six of eight are bound methods handed to `_read` / `_attach` helpers |
| `src/cli/commands/tunnels.py` | 1 | `open_tunnel` (142) | |
| `src/kubementat/grafana_accounts.py` | 1 | `get_service_endpoint` (154) | and 13 tests replace its class binding |

The ordering hazard: `automation.py` (72 references, 38 methods) and `tekton_utils.py` (25) both build
an instance the way the class is built today (`automation.py:138`, `tekton_utils.py:942` and 1440) and
both are moved after this file. If the class disappears here while they still expect it, they fail
first. The string paths a move must hunt down are the five in section 6 and the recorder seam in
`tests/conftest.py`.

## 8. What this rules in and out

1. The split can follow the method rows: 77 public methods, one row each, no gaps, no stale names, and
   no function that has to live in two places.
2. Carry the four private methods by name — the assignment does not mention them, and
   `_require_namespace` (1739) is 26 lines of behaviour two commands depend on.
3. Give `DEPLOYMENT_LABEL`, `SERVICE_ACCOUNT_TOKEN_SECRET_TYPE` and `SERVICE_ACCOUNT_NAME_ANNOTATION`
   one home that both importing modules can reach, instead of letting each side keep its copy.
4. Settle `workloads` ⇄ `exec` (section 4.3) before writing either file, and do not settle it with a
   second copy of the pod lookup.
5. Move `helm_target_args` (123) with helm, not with the kubernetes rows: this file never calls it.
6. Keep `api_client=` (310–332) and `ClusterTarget.client` as the seam. They are what the recorder and
   the 34 test files that use it depend on, and they are the only way the suite reaches a cluster.
7. Prove the 23 un-named methods from the consumer side after each move. No unit test can check them,
   because no unit test calls them.
