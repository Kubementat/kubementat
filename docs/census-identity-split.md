# Census: `src/kubementat/cluster.py` before the `core/identity/` split

Compiled read-only from the subject file and greps over `src/` and `tests/`. No source file was
edited and no git state was touched; the only file written is this one.

Subject file: `src/kubementat/cluster.py`, **1 474 lines** (`wc -l`), 44 module-level names —
18 module constants, 21 module functions, 5 classes — plus 42 class members, so 86 attributed
names. 26 of the 44 carry no leading underscore. Branch `v2-arch-refactoring`, HEAD `24e5e44`.

Three things stated up front, because two of them disagree with the numbers this census was handed:

* The file is **1 474 lines**, not the 1 554 the plan's size table records. The tree's number is used throughout.
* The suite was not run. `pytest -q --collect-only` at this SHA collects **1 776 tests**, which is
  consistent with the handed figure of 1 775 passed + 1 skipped, but nothing here proves green.
* `kubementat.cluster` appears **nowhere as a patch-target string**: the split's silent-failure risk is
  concentrated in the 13 sites that reach a name as an attribute instead of through an import, 10 of them through the
  module object (see §4b).

Method, verified two ways: the section map is `ast.parse` on the file (spans from `lineno`/`end_lineno`,
decorators included, so it does not depend on where a reader thinks a block ends), cross-checked against
`grep -n` for `^(class |def |^_?[A-Z][A-Z_0-9]* *=`. Cross-file numbers come from walking `ImportFrom`
and `Attribute` nodes in every `.py` under `src/` and `tests/`, cross-checked against `grep -rn` — so a
prose mention in a docstring cannot inflate an import count, and an alias (`from kubementat import
cluster as _cluster`) cannot hide one. The dependency graph in §5 was computed by mapping every
intra-file read to the target file its reader and its callee land in, then searching that graph for
cycles; the cycle claims below are that program's output, not an eyeball.

## 1. Section map

Target files come from the plan's `core/identity/` block; nothing here invents another split. **LOC**
counts the name's own statement block, comments attached to it included, counted once per line;
1 325 of the 1 474 lines are attributed and 149 are the module header, blank separators and the lines
between a class header and its first member.

**The single mapping gap:** the plan names five files but assigns 37 of the 44 module-level names. The
seven unassigned names are the identity key and source labels
(`CLUSTER_KUBE_CONTEXT_KEY`, `CLUSTER_KUBECONFIG_PATH_KEY`, `SOURCE_FLAG`, `SOURCE_ENV`,
`SOURCE_CONNECTION`, `SOURCE_SERVICE_ACCOUNT`, `STATIC_SOURCE_PREFIX`), and §5 shows they cannot stay
inside any of the five without a cycle. The proposal there is `core/constants.py` — a proposal, not the
plan's answer.

### `context.py` — 1 name + 18 members, 501 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `Context` | class (dataclass) | 932-1433 | 502 | 32 imports outside (2 in `src/`); members below |

Members: `environment:958`, `team:959`, `config:960`, `target:961`, `resolution_report:962`,
`require_team:970`, `require_target:999`, `for_configuration:1025`, `build:1070`,
`_service_account_layer:1183`, `_declaration_layers:1204`, `_statement_layers:1245`,
`_check_stated_cluster:1269`, `_stated_cluster_mismatch:1328`, `_process_value:1351`,
`_flag_value:1370`, `_nothing_declared:1377`, `_collision:1414`.

The class is 34 % of the file, and `build:1070-1181` alone is 112 lines — the largest single block in
the module, and the only place where all four resolution layers meet. Everything in `context.py` reads
upward into the other four files; nothing reads down into it.

### `target.py` — 1 name + 10 members, 223 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `ClusterTarget` | class (frozen dataclass) | 271-494 | 224 | 21 imports outside (2 in `src/`); read in-file by 11 sites in `comparison.py`, `registry.py`, `context.py` |

Members: `environment:282`, `context:283`, `kubeconfig_path:284`, `address:285`, `source:286`,
`from_kubeconfig:288`, `from_connection:327`, `from_service_account:389`, `client:439`, `__repr__:479`.
The plan also assigns `_normalise_address:148-156` here; §5 shows that exact assignment is what makes
the split circular, so this census puts it in `kubeconfig.py` instead and says why.

### `kubeconfig.py` — 17 names, 255 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `KUBECONFIG_ENV_VAR` | module const | 72 | 1 | `default_kubeconfig_path:118`, `_kubeconfig_files:170-171` |
| `IN_TREE_KUBECONFIG_FILENAME` | module const | 77 | 1 | `in_tree_kubeconfig:144`; 1 attribute read in tests |
| `SERVICE_ACCOUNT_ROOT` | module const | 83 | 1 | `_service_account_files:556`, `Context._service_account_layer:1200`; **patched on the module object 4×** |
| `SERVICE_ACCOUNT_TOKEN_FILE` | module const | 84 | 1 | `_service_account_files:556` |
| `SERVICE_ACCOUNT_CA_FILE` | module const | 85 | 1 | `_service_account_files:556` |
| `KUBERNETES_SERVICE_HOST_ENV` | module const | 86 | 1 | `_service_account_address:588` |
| `KUBERNETES_SERVICE_PORT_ENV` | module const | 87 | 1 | `_service_account_address:591` |
| `DEFAULT_KUBERNETES_SERVICE_PORT` | module const | 89 | 1 | `_service_account_address:591` |
| `KUBECONFIG_PATH_SOURCE_LABEL` | module const | 93 | 1 | `_kubeconfig_files:176` |
| `_CONNECTION_FIELD` | module const | 98 | 1 | `ClusterTarget.__repr__:487`, `.client:455`, `.from_connection:386` |
| `default_kubeconfig_path` | module function | 111-121 | 11 | `_kubeconfig_files:165`, `_library_config_file:634` |
| `in_tree_kubeconfig` | module function | 124-145 | 22 | `Context.build:1145`, `EnvironmentRegistry.resolve:751` |
| `_normalise_address` | module function | 148-156 | 9 | `ClusterTarget.from_connection:383`, `_address_in_kubeconfig:268`, `_service_account_address:593` |
| `_kubeconfig_files` | module function | 159-181 | 23 | `ClusterTarget.from_kubeconfig:317`, `Context._check_stated_cluster:1312` |
| `_files_label` | module function | 184-185 | 2 | `_address_in_kubeconfig:250,251,258,260,265,266` |
| `_read_kubeconfig` | module function | 188-230 | 43 | `_address_in_kubeconfig:244`, `Context._check_stated_cluster:1313`; imported by 1 test |
| `_address_in_kubeconfig` | module function | 233-268 | 36 | `ClusterTarget.from_kubeconfig:322` |

The two halves of this list are not the same subject. Nine of the 17 are kubeconfig-file reading
(`_kubeconfig_files`, `_read_kubeconfig`, `_address_in_kubeconfig`, `_files_label`,
`_library_config_file` as a target-side resident, the two path rules, and three env-var names); the
other eight are service-account material (`_service_account_files`, `_service_account_address`, the
four SA/`KUBERNETES_SERVICE_*` names) plus the connection document the loader is built from. If the
split is one file too many, the service-account half is the one that would come out; the plan does not
ask for that and this census does not propose it.

### `registry.py` — 9 names + 12 members, 275 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `EnvironmentRegistry` | class | 690-791 | 102 | 5 imports outside (1 in `src/`); read in-file by `Context.build:1163` |
| `EnvironmentResolution` | class (dataclass) | 637-658 | 22 | `EnvironmentRegistry.all:719`/`resolve:728-759`, `ClusterComparison:685-686` |
| `ClusterComparison` | class (dataclass) | 661-687 | 27 | `EnvironmentRegistry.collision_check:761,790` |
| `_static_declaration` | module function | 794-830 | 37 | `EnvironmentRegistry.resolve:738`, `_environment_declaration:906`, `Context._nothing_declared:1394`; imported by 1 test |
| `_declared_value` | module function | 833-837 | 5 | `_static_declaration:827,829` |
| `_declaration_state` | module function | 840-856 | 17 | `_static_declaration:828` |
| `environment_file_missing` | module function | 859-871 | 13 | `_environment_declaration:903` |
| `_environment_declaration` | module function | 874-906 | 33 | `Context.build:1119` |
| `_load_environment_config` | module function | 909-929 | 21 | `_environment_declaration:899` |

`EnvironmentResolution.environment:651`, `.target:652`, `.reason:653`, `.declared_context:654`,
`.resolved:656`; `ClusterComparison.target:684`, `.others:685`, `.collisions:686`, `.self_address:687`;
`EnvironmentRegistry.__init__:699`, `.environments:702`, `.all:719`, `.resolve:728`, `.collision_check:761`.

Note where the plan puts `ClusterComparison`: in `registry.py`, while `comparison.py` is given
`running_cluster_address` "and the 1:1 check primitives". That is a real split of one idea across two
files, and §5 accepts it because it stays acyclic — but a reader who expects the comparison *object*
beside the comparison *primitive* will look in the wrong file.

### `comparison.py` — 3 names, 64 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `running_cluster_address` | module function | 596-611 | 16 | `EnvironmentRegistry.collision_check:781` |
| `_addresses_the_running_cluster` | module function | 614-622 | 9 | `EnvironmentRegistry.collision_check:788`, `_report_line:1459` |
| `_report_line` | module function | 1436-1474 | 39 | `Context.build:1178` |

`_report_line` is the plan's gap: it has no row anywhere, it formats the line that says *which* sibling
was held back and why, it reads `ClusterTarget` and `_addresses_the_running_cluster`, and its only
caller is `Context.build`. The subject is the 1:1 check, so it goes to `comparison.py`; the plan's own
words "the 1:1 check primitives" cover it. That adds the edge `context.py → comparison.py`.

### `core/constants.py` — 7 names, 7 LOC (**proposal, not the plan**)

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `CLUSTER_KUBE_CONTEXT_KEY` | module const | 60 | 1 | 18 sites in-file across `target.py`, `registry.py`, `context.py`; 3 imports outside |
| `CLUSTER_KUBECONFIG_PATH_KEY` | module const | 61 | 1 | `_static_declaration:824`, `Context._declaration_layers:1229`; 2 imports outside |
| `SOURCE_FLAG` | module const | 66 | 1 | `ClusterTarget.from_kubeconfig:295` (its default), `Context._declaration_layers:1225` |
| `SOURCE_ENV` | module const | 67 | 1 | `Context._declaration_layers:1231` |
| `SOURCE_CONNECTION` | module const | 68 | 1 | `ClusterTarget.from_connection` |
| `SOURCE_SERVICE_ACCOUNT` | module const | 69 | 1 | `ClusterTarget.from_service_account`, `Context._service_account_layer:1201` |
| `STATIC_SOURCE_PREFIX` | module const | 70 | 1 | `EnvironmentRegistry.resolve:745`, `Context._declaration_layers` |

They name one vocabulary — *which layer said this cluster* — and they are read by three of the five new
files. Putting them in any of the five makes that file a dependency of files above it. The plan's own
layering note already says to prefer duplicating a shared constant into `core.constants` rather than
reach sideways, so this is an application of the plan, not a deviation from it.

## 2. The four helpers the plan names, judged by what they read

The plan's rule is that these land in the file whose subject they name. A name is not a subject: each
of the four is judged below by the values it touches.

* **`default_kubeconfig_path:111-121` — kubeconfig.** It reads `os.environ[KUBECONFIG_ENV_VAR]` and
  `Path.home()/".kube"/"config"`, nothing else: no `Config`, no environment directory, no filesystem
  probe beyond the returned string. It is the library's own rule applied at call time, which is the
  subject of kubeconfig resolution. `kubeconfig.py`, and the plan's rule agrees with its name.
* **`in_tree_kubeconfig:124-145` — kubeconfig, not the configuration root.** Its only computation is
  `Path(platform_config_dir) / environment / IN_TREE_KUBECONFIG_FILENAME` plus one `is_file()`. The
  argument is a root, which is what makes it look like a `registry.py` name, but a root is not what it
  answers: it answers *does this environment carry its own kubeconfig*. The absence of a `Config`
  argument is the proof — it never asks the layer what it thinks. So `kubeconfig.py`, and its two
  callers (`Context.build:1145`, `EnvironmentRegistry.resolve:751`) both sit above it.
* **`running_cluster_address:596-611` — comparison.** The body is one call,
  `_service_account_address()`, inside a `try` that swallows `ServiceAccountNotMountedError`. What
  decides the subject is the one caller: `EnvironmentRegistry.collision_check:781` uses it to *subtract*
  the cluster the command is standing on, and it is called by no resolution path at all. That is a
  1:1-check subject, so `comparison.py` — the plan names this file for it, and the value it reads
  confirms it.
* **`environment_file_missing:859-871` — registry.** It is a sentence builder. It reads
  `CLUSTER_KUBE_CONTEXT_KEY` only to name the missing key, and its subject is the `static.json` that is
  not there, which is the registry's declaration file. The docstring's own reason ("both halves of
  `Context.build` need it") argues for `context.py` by caller, which is the wrong test — a message about
  a declaration file is a declaration subject. `registry.py`, beside `_environment_declaration:903`,
  which is its only caller; had it stayed in `context.py` it would have added `registry → context`, the
  direction that turns the key constants into a cycle.

## 3. Names that leave, and every site that reaches them

All 44 names leave the file, so the work list is every site outside `cluster.py` that binds or reads
one of them by name. Counts, one number per kind:

| kind of reach | sites | `src/` | `tests/` | files |
|---|---|---|---|---|
| `from kubementat.cluster import <name>` — import statements | 48 | 5 | 43 | 44 (5 in `src/`) |
| … of which name-bindings, counted per name | **83** | **10** | **73** | 44 |
| `import kubementat.cluster` / `from kubementat import cluster` | 1 | 1 | 0 | `config_validate.py:60` |
| attribute reads `<alias>.NAME` (see §4b) | 6 | 5 | 1 | 2 |
| `monkeypatch.setattr(<module>, "NAME", …)` (see §4b) | 4 | 0 | 4 | 3 |
| `monkeypatch.setattr(<Class>, "member", …)` (see §4b) | 3 | 0 | 3 | 3 |
| patch-target strings naming `kubementat.cluster` | **0** | 0 | 0 | 0 |
| comments citing `cluster.py:<line>` — change with the move, break nothing | 17 | 10 | 7 | 8 |

Verified twice: the per-name table below is `ImportFrom`-node walks, and the total of 83 came back from
a second pass over the import lines with the continuation lines joined. Both passes agree on 83
bindings, 15 distinct names, 44 files, `src` 10 / `tests` 73.

The names with the most sites, with the whole list. Anything here that is not repointed is a
`ModuleNotFoundError` or `ImportError` at collection time, so this class of break fails loudly.

1. **`Context` — 32 sites (2 in `src/`, 30 in `tests/`)**: `src/cli/context.py:48`, `src/kubementat/kubernetes_utils.py:11`, and the following 30 — `tests/cluster_fixture.py` and 29
   test files, each of which imports the class the CLI hands to every command:
   `tests/cluster_fixture.py:27`, `test_a_command_with_no_team_semantics_still_names_its_cluster.py:27`,
   `test_a_context_that_configures_a_tree_addresses_no_cluster.py:20`,
   `test_a_deployed_release_is_not_moved_by_its_configuration.py:38`,
   `test_a_freshly_initialized_tree_can_be_installed.py:33`,
   `test_a_grafana_account_command_resolves_an_address_before_it_creates_anything.py:176`,
   `test_a_helm_charts_newest_version_is_read_from_helms_own_output.py:33`,
   `test_a_helm_component_is_uninstalled_from_the_namespace_the_config_names.py:37`,
   `test_a_run_that_names_no_configuration_root_never_reaches_kubectl.py:32`,
   `test_a_stated_cluster_is_checked_against_the_cluster_the_environment_declares.py:36`,
   `test_a_team_account_run_names_the_cluster_and_checks_the_verbs_it_needs.py:34`,
   `test_an_environment_that_carries_its_own_kubeconfig_is_read_from_it.py:28`,
   `test_an_environment_that_names_no_cluster_names_the_file.py:22`,
   `test_an_unknown_context_lists_the_contexts_the_file_has.py:16`,
   `test_an_unreadable_environment_is_named_and_not_dropped.py:25`,
   `test_helmfile_apply_asks_only_on_a_terminal.py:25`,
   `test_parity_helm_deployment_sessions.py:248`,
   `test_routing_install_applies_traefik_when_the_cluster_has_none.py:24`,
   `test_routing_presence_reads_the_cluster_rather_than_a_release_name_list.py:34`,
   `test_routing_status_reports_what_is_on_the_cluster_and_whose_it_is.py:34`,
   `test_the_ci_container_is_started_from_configuration_and_not_a_script.py:26`,
   `test_the_cluster_a_process_is_running_on_is_resolved_last.py:24`,
   `test_the_cluster_is_resolved_from_the_layer_that_declares_it.py:38`,
   `test_the_commands_that_refused_a_team_they_do_not_read.py:35`,
   `test_the_config_dir_flag_outranks_the_platform_config_variable.py:32`,
   `test_the_ingress_class_the_manifest_names_is_the_one_the_configuration_answers.py:290`,
   `test_the_release_listing_asks_for_the_statuses_helm_actually_has.py:33`,
   `test_the_siblings_the_check_compares_come_from_the_root_the_flag_names.py:26`,
   `test_two_clusters_can_be_addressed_by_one_process_at_once.py:31`,
   `test_two_environments_that_address_one_cluster_are_refused.py:30`.
   2. **`ClusterTarget` — 21 sites (2 `src`, 19 `tests`)**: `src/kubementat/kubernetes_utils.py:11`,
   `src/kubementat/helm_utils.py:12`; `tests/cluster_fixture.py:27`, `conftest.py:182`, `conftest.py:628`,
   `test_a_cluster_target_addresses_exactly_one_cluster.py:27`,
   `test_a_cluster_target_repr_never_prints_a_credential.py:14`,
   `test_a_grafana_account_command_resolves_an_address_before_it_creates_anything.py:178`,
   `test_a_locked_configuration_file_refuses_by_name_and_not_by_codec.py:55`,
   `test_a_run_that_names_no_configuration_root_never_reaches_kubectl.py:32`,
   `test_an_environment_that_carries_its_own_kubeconfig_is_read_from_it.py:167`,
   `test_an_unknown_context_lists_the_contexts_the_file_has.py:16`,
   `test_no_helm_call_is_addressed_by_the_process_any_more.py:355` and `:380`,
   `test_no_kubectl_is_addressed_by_the_process_any_more.py:237`,
   `test_routing_presence_reads_the_cluster_rather_than_a_release_name_list.py:34`,
   `test_routing_status_reports_what_is_on_the_cluster_and_whose_it_is.py:34`,
   `test_the_cluster_a_process_is_running_on_is_resolved_last.py:24`,
   `test_the_parity_record_names_the_cluster_it_touched.py:38`,
   `test_two_clusters_can_be_addressed_by_one_process_at_once.py:31`,
   `test_two_environments_that_address_one_cluster_are_refused.py:30`.
   `tests/cluster_fixture.py:27` is the hub — it builds `TARGET_DEV:59` and the target at `:371` straight
   from the constructor, so that one line feeds most of the 19.
3. **`STATIC_SOURCE_PREFIX` — 6 sites (1 `src`, 5 `tests`)**: `src/cli/commands/environments.py:25`,
   `tests/cluster_fixture.py:27`, `tests/conftest.py:182`,
   `tests/test_a_cluster_target_addresses_exactly_one_cluster.py:27`,
   `tests/test_the_cluster_a_process_is_running_on_is_resolved_last.py:24`,
   `tests/test_the_cluster_is_resolved_from_the_layer_that_declares_it.py:38`.
4. **`EnvironmentRegistry` — 5 sites (1 `src`, 4 `tests`)**: `src/cli/commands/environments.py:25`,
   `tests/test_an_unreadable_environment_is_named_and_not_dropped.py:25`,
   `tests/test_the_cluster_a_process_is_running_on_is_resolved_last.py:24`,
   `tests/test_the_environments_command_lists_what_the_check_resolves.py:27`,
   `tests/test_two_environments_that_address_one_cluster_are_refused.py:30`.
5. **`SOURCE_FLAG` — 4 sites, all `tests/`**: `test_a_cluster_target_addresses_exactly_one_cluster.py:27`,
   `test_a_stated_cluster_is_checked_against_the_cluster_the_environment_declares.py:36`,
   `test_the_cluster_a_process_is_running_on_is_resolved_last.py:24`,
   `test_the_cluster_is_resolved_from_the_layer_that_declares_it.py:38`.
6. **`CLUSTER_KUBE_CONTEXT_KEY` — 3 (2 `src`, 1 `tests`)**: `src/cli/context.py:48`,
   `src/kubementat/config_init.py:90`, `tests/test_config_validate_rules_the_tree_it_was_handed.py:51`.
   Both `src` sites are outside `core/`, so they are the *stayer* readers §6 warns about.
7. **`SOURCE_CONNECTION` — 3, all `tests/`**: `cluster_fixture.py:27`,
   `test_a_cluster_target_addresses_exactly_one_cluster.py:27`,
   `test_a_grafana_account_command_resolves_an_address_before_it_creates_anything.py:178`.
8. **`CLUSTER_KUBECONFIG_PATH_KEY` — 2 (1 `src`, 1 `tests`)**: `src/kubementat/config_init.py:90`,
   `tests/test_config_validate_rules_the_tree_it_was_handed.py:51`.
9. **`IN_TREE_KUBECONFIG_FILENAME` — 1 `src`**: `src/kubementat/config_init.py:90`.
10. **`KUBERNETES_SERVICE_HOST_ENV`, `KUBERNETES_SERVICE_PORT_ENV`, `SOURCE_ENV`,
    `SOURCE_SERVICE_ACCOUNT`, `_read_kubeconfig`, `_static_declaration` — 1 each**, all `tests/`:
    `cluster_fixture.py:27` (both env names), `test_a_cluster_target_addresses_exactly_one_cluster.py:27`
    (`SOURCE_ENV`), `test_the_cluster_a_process_is_running_on_is_resolved_last.py:24`
    (`SOURCE_SERVICE_ACCOUNT`), `test_a_locked_configuration_file_refuses_by_name_and_not_by_codec.py:55`
    (both privates — a test importing two private helpers by name is a patch target in itself).

The other 29 module-level names have no reach outside the file. Nothing imports
`ClusterComparison`, `EnvironmentResolution`, `running_cluster_address`,
`_addresses_the_running_cluster`, `_report_line`, `_connection_document`, `_as_pem_base64`,
`_service_account_address`, `_kubeconfig_files`, `default_kubeconfig_path`,
`_library_config_file`, `_load_environment_config`, `_declared_value`, `_declaration_state`, the
four `SOURCE_*`/`KUBECONFIG_*` constants, or `_CONNECTION_FIELD`: they move freely, and their
behaviour is only checked through the classes that call them.

`config_validate.py:60` is the one `src/` file that holds the module rather than a name, so it breaks
differently: its five reads (§4) keep resolving as long as *some* `kubementat/cluster.py` exists, which
is precisely how a stale shim keeps a green run while reading the wrong module.

## 4. The two forms of test coupling, counted separately

**(a) patch-target strings naming `kubementat.cluster`: 0 sites.** No `monkeypatch.setattr("kubementat.cluster.X", …)`
and no `patch("kubementat.cluster.X")` anywhere in `src/` or `tests/` — verified by an AST walk for string
constants matching `kubementat\.cluster` outside import statements, cross-checked with `grep -rn
"kubementat\.cluster" tests src` whose 48 hits are all import lines. This form is empty, so it decides nothing.

**(b) names read as attributes of an object: 13 sites in 7 files, three different shapes.**

Shape 1 — attribute reads off a module alias, 6 sites (5 in `src/`, 1 in `tests/`):

| file:line | what it reads | lands in |
|---|---|---|
| `src/kubementat/config_validate.py:419` | `_cluster.EnvironmentRegistry(root)` | `registry.py` |
| `src/kubementat/config_validate.py:431` | `_cluster.Context._collision(...)` | `context.py` |
| `src/kubementat/config_validate.py:548` | `_cluster.CLUSTER_KUBECONFIG_PATH_KEY` | `constants.py` (proposal) |
| `src/kubementat/config_validate.py:552` | `_cluster.CLUSTER_KUBECONFIG_PATH_KEY` | `constants.py` (proposal) |
| `src/kubementat/config_validate.py:587` | `_cluster.CLUSTER_KUBE_CONTEXT_KEY` | `constants.py` (proposal) |
| `tests/test_an_environment_that_carries_its_own_kubeconfig_is_read_from_it.py:150` | `assert cluster.IN_TREE_KUBECONFIG_FILENAME == "…"` | `kubeconfig.py` |

Shape 2 — `monkeypatch.setattr(<module object>, "NAME", …)`, 4 sites, all `tests/`, all on the **same** name:

* `tests/conftest.py:602` — `setattr(_cluster_module, "SERVICE_ACCOUNT_ROOT", Path("/no-pod-runs-tests-here"), raising=False)`
* `tests/cluster_fixture.py:260` and `:273` — `setattr(_cluster_module, "SERVICE_ACCOUNT_ROOT", mount)`
* `tests/test_the_cluster_a_process_is_running_on_is_resolved_last.py:301` — `setattr(cluster_module, "SERVICE_ACCOUNT_ROOT", tmp_path / "not-a-pod")`

Shape 3 — `monkeypatch.setattr(<Class>, "member", …)`, 3 sites, all `tests/`, all on `ClusterTarget`:
`tests/conftest.py:656`, `tests/test_two_clusters_can_be_addressed_by_one_process_at_once.py:140`
(both `ClusterTarget.client`), `tests/test_a_run_that_names_no_configuration_root_never_reaches_kubectl.py:59`
(`ClusterTarget.from_kubeconfig`). These three are safe *if* the class object stays one object; they are
the reason a compatibility shim that re-exports is dangerous rather than merely lazy.

**Why this decides the failure mode.** Shape 2 is the whole risk. All four sites patch
`SERVICE_ACCOUNT_ROOT`, which §1 puts in `kubeconfig.py`. Once the constant lives there and
`_service_account_files:556` reads it from there, patching the attribute on the old module object
changes a name nobody reads. Two of the four pass `raising=False`
(`conftest.py:602`), so the miss is silent: the fixture believes it has unmounted the pod, the layer
under test reads the real `/var/run/secrets/kubernetes.io/serviceaccount`, and the assertion about
in-cluster resolution is checked against a path that may or may not exist on the machine running pytest.
The other two use the default `raising=True` and fail loudly. That is the split passing while wrong,
and it is confined to 1 name and 4 sites, all of which must be retargeted to the new home in the same
commit as the move — the same fix pattern the previous three splits used.

Shape 1 is the quieter version of the same thing in `src/`: attribute reads off a module object do not
error when the module becomes a re-export, and `config_validate.py` reads three names that §1 puts in a
file that is not `cluster.py`. Repoint those five lines to direct imports, not to the shim.

Shape 3 needs a rule for the split rather than a fix: `ClusterTarget` must be imported from
`core/identity/target.py` by both `kubernetes_utils.py:11` (which does `isinstance(ctx_or_target,
ClusterTarget)` at `:324`) and by every test, because two class objects through two import paths make
that `isinstance` answer `False` in exactly the way that produces a refusal nobody can read.

## 5. Two-home private helpers, edge direction, and whether this can be a DAG

Seven private names are called by members that land in two different new files. Each row is the two
caller sets, verified by the AST walk in §1.

| helper | called from (file) | must live in | why, so neither side imports across for one call |
|---|---|---|---|
| `_kubeconfig_files` | `ClusterTarget.from_kubeconfig:317` (`target`), `Context._check_stated_cluster:1312` (`context`) | `kubeconfig.py` | Both callers are above it; `target → kubeconfig` already exists via `_address_in_kubeconfig:322`, so this is a free edge |
| `_read_kubeconfig` | `_address_in_kubeconfig:244` (`kubeconfig`), `Context._check_stated_cluster:1313` (`context`) | `kubeconfig.py` | It is the file-reading half of the same pair as `_kubeconfig_files`; splitting the two is how one call ends up imported across |
| `_normalise_address` | `ClusterTarget.from_connection:383` (`target`), `_address_in_kubeconfig:268` (`kubeconfig`), `_service_account_address:593` (`kubeconfig`) | **`kubeconfig.py`, overriding the plan** | Two of its three callers are in `kubeconfig.py`. Putting it in `target.py`, as §4's `target.py` row says, adds `kubeconfig → target` while `target → kubeconfig` already exists — the only cycle in the split (§5.1) |
| `_service_account_files` | `ClusterTarget.from_service_account:417` (`target`), `_service_account_address:573` (`kubeconfig`) | `kubeconfig.py` | Same home as its caller `_service_account_address`; the plan's "service-account material" row puts it there |
| `_service_account_address` | `ClusterTarget.from_service_account:416` (`target`), `running_cluster_address:609` (`comparison`) | `kubeconfig.py` | Both callers are above `kubeconfig.py`. Putting it beside `running_cluster_address` in `comparison.py` would make `comparison` read `_service_account_files`, which is also read by `target`, and the pair would need an edge in each direction |
| `_addresses_the_running_cluster` | `EnvironmentRegistry.collision_check:788` (`registry`), `_report_line:1459` (`comparison`) | `comparison.py` | It is the address-equality test the check and the report must agree on; `registry → comparison` already exists via `running_cluster_address:781` |
| `_static_declaration` | `EnvironmentRegistry.resolve:738` (`registry`), `_environment_declaration:906` (`registry`), `Context._nothing_declared:1394` (`context`) | `registry.py` | The two registry callers are its home and `context → registry` already exists via `EnvironmentRegistry:1163` |

Three more names have two callers in two files but only because they are *shared constants*, not
helpers: `SERVICE_ACCOUNT_ROOT` (`_service_account_files:556` in `kubeconfig`, `Context._service_account_layer:1200`
in `context`), `SOURCE_FLAG` (`ClusterTarget.from_kubeconfig:295` in `target`,
`Context._declaration_layers:1225` in `context`), `SOURCE_SERVICE_ACCOUNT` (`from_service_account` and
`_service_account_layer:1201`), and `CLUSTER_KUBE_CONTEXT_KEY` at 18 sites across three files. §1 puts
the keys in `core/constants.py`; `SERVICE_ACCOUNT_ROOT` stays in `kubeconfig.py`, which every reader
already imports.

### 5.1 Can it be a DAG — yes, and only on two conditions

Computed by mapping every read to `(reader's file, callee's file)` and searching the graph:

```
kubeconfig.py  <-  target.py  <-  comparison.py  <-  registry.py  <-  context.py
```

| edge | 13 edges total, by name |
|---|---|
| `target → kubeconfig` | `_address_in_kubeconfig`, `_kubeconfig_files`, `_library_config_file`, `_connection_document`, `_CONNECTION_FIELD`, `_CONNECTION_DOCUMENT_CONTEXT` |
| `comparison → target`, `comparison → kubeconfig` | `ClusterTarget`, `_service_account_address` |
| `registry → target`, `registry → kubeconfig`, `registry → comparison` | `ClusterTarget`, `in_tree_kubeconfig`, `running_cluster_address`, `_addresses_the_running_cluster` |
| `context → target`, `kubeconfig`, `registry`, `comparison` | `ClusterTarget` (and its two constructors), `_kubeconfig_files`, `_read_kubeconfig`, `in_tree_kubeconfig`, `EnvironmentRegistry`, `_static_declaration`, `_environment_declaration`, `_report_line` |
| `* → constants.py` (7 LOC, 3 readers) | `CLUSTER_KUBE_CONTEXT_KEY`, `CLUSTER_KUBECONFIG_PATH_KEY`, `SOURCE_FLAG/ENV/CONNECTION/SERVICE_ACCOUNT`, `STATIC_SOURCE_PREFIX` |

That graph is acyclic, and `kubeconfig.py` is the single leaf: nothing in it imports a sibling.

The two conditions:

1. **`_normalise_address` goes to `kubeconfig.py`, not `target.py`.** The plan's `target.py` row lists
   it, and 2 of its 3 callers are in `kubeconfig.py`. With it in `target.py`, the graph gains
   `kubeconfig → target` next to the existing `target → kubeconfig` and the DFS returns the 2-cycle
   `target.py ⇄ kubeconfig.py`. Nothing else in the split has this property: the other six two-home
   helpers all have a lower file available to them.
   *Minimal change*: keep the assignment table as written and drop `_normalise_address` one level down,
   into the file whose address-reading code calls it. It is a pure function of a string, so no state
   moves with it. Its own docstring ("whitespace and one trailing slash removed") is a string rule, not
   a `ClusterTarget` rule, which is what makes the change cheap.
2. **The key and source labels go below all five files.** With the keys in `constants.py` the graph
   holds. With `CLUSTER_KUBE_CONTEXT_KEY` placed inside `context.py` — the intuitive home, since it
   answers "which kube context" — `target.py` and `registry.py` must both read up into `context.py`, and
   the DFS returns 14 cycles, including the short ones `context ⇄ target` and `context ⇄ registry` and
   the three-file `context → comparison → target → context`. A key that three files name cannot live in
   one of the three.
   *Minimal change*: the 7 one-line constants go to `core/constants.py`, exactly where the plan already
   says to prefer putting a duplicated shared constant. `core/constants.py` imports nothing, so it is
   below everything by construction, and the 5 external import sites (§3 items 6, 8, 9) move with it.

If the split insists on keeping both assignments, the only other fix is to split out a sixth module for
address strings, which is a new split and out of scope.

## 6. What cannot move, or moves with a condition

* **Nothing here depends on `__file__`.** No `__file__`, no `importlib`, no `lru_cache`, no
  module-level dict built at import. Both env reads are inside functions (`default_kubeconfig_path:118`
  and `Context._process_value:1361`), so import order cannot change a resolution.
* **`SERVICE_ACCOUNT_ROOT:83` cannot keep being patched on the old module.** This is the one name whose
  behaviour depends on *which module object* it is read from, and §4b lists the 4 sites that assume it.
  The move is fine; the patch target is the thing that must move with it, in the same commit.
* **The whole point of `default_kubeconfig_path` and `_library_config_file` is import-order defiance**
  (`ClusterTarget.client:472`, `_kubeconfig_files:165`). The kubernetes library captures
  `KUBE_CONFIG_DEFAULT_LOCATION` at its own import time; these two functions exist so this module asks
  at call time instead. They must not be replaced by the library constant while the files move, and no
  new module under `core/identity/` should import that constant.
* **`ClusterTarget` carries state that is not a dataclass field.** `_CONNECTION_FIELD:98` is a private
  attribute key stashed onto a frozen instance via `object.__setattr__` in `from_connection:386` and
  read back by `getattr(self, _CONNECTION_FIELD, None)` in `.client:455` and `.__repr__:487`.
  `_connection_document:500-532` and `_CONNECTION_DOCUMENT_CONTEXT:497` must sit with the field, not
  beside `ClusterTarget`: §5's variant with the document in `kubeconfig.py` and the field in `target.py`
  returns the `kubeconfig ⇄ target` 2-cycle, so all four names go to `kubeconfig.py` together.
  The `__repr__` credential redaction is the behaviour that must survive — it is tested by name in
  `tests/test_a_cluster_target_repr_never_prints_a_credential.py:14`.
* **`EnvironmentRegistry.__init__:699` stores `platform_config_dir`** — the only constructor-built state
  in the module. It moves whole with the class; what must not happen is a module-level
  `platform_config_dir` default, since `environments:702` re-enumerates the root on every call and a
  cached root is what a two-root test would silently read.
* **Constants read by both a mover and a stayer.** `CLUSTER_KUBE_CONTEXT_KEY` and
  `CLUSTER_KUBECONFIG_PATH_KEY` are read by `src/cli/context.py:48` and `src/kubementat/config_init.py:90`,
  both of which stay put, and `IN_TREE_KUBECONFIG_FILENAME` by `config_init.py:90`. The keys go to
  `core/constants.py`, which is below both; `IN_TREE_KUBECONFIG_FILENAME` goes to `kubeconfig.py`, and
  `config_init.py:90` must be repointed there — it is a stayer that reads a mover, so a `kubementat/cluster.py`
  shim would hide the break rather than fix it.
* **The error types move nothing.** `cluster.py:45-54` imports eight names from `kubementat.errors`,
  all already at their final home, so no error class needs a decision here.

## 7. The `kubernetes_utils` seam

`src/kubementat/cluster.py` is 1 474 lines and `src/kubementat/kubernetes_utils.py` is 2 080; both
exist at `24e5e44`, and P2-5 moves the second one into `infrastructure/kubernetes/`.

Every edge between the two, in both directions:

| edge | site | what it is |
|---|---|---|
| `kubernetes_utils → cluster` | `kubernetes_utils.py:11` `from kubementat.cluster import ClusterTarget, Context` | module level, the only import |
| the same edge, used | `kubernetes_utils.py:321` `isinstance(ctx_or_target, Context)`, `:324` `isinstance(..., ClusterTarget)`, `:335` `def target(self) -> ClusterTarget` | a type identity dependency, not a call |
| `cluster → kubernetes_utils` | **none** | `cluster.py` imports only stdlib, `yaml`, `kubementat.core.config.config`, `kubementat.core.config.reads`, `kubementat.errors` (`cluster.py:34-54`) |

Siblings that carry the same edge and will be touched by the same rename: `helm_utils.py:12`
(`ClusterTarget`), `ci_utils.py`, `automation.py`, `tekton_utils.py`, `k8s_apply.py`,
`user_management.py`, `grafana_accounts.py`, `cli/commands/kubernetes.py`, `cli/commands/tunnels.py`,
`automations/routing/routing.py`, `automations/routing/traefik_presence.py` — all of them import
`kubernetes_utils`, and none of them imports `cluster` as a name (the `automation.py:149` hit is a
prose line, not an import).

Answer to the question the plan asks here: **neither move recreates a cycle.**

* P2-3 alone leaves one edge, `infrastructure/kubernetes → core/identity`, at `kubernetes_utils.py:11`,
  which is the direction the layering rule already allows (`infrastructure/*` imports `core/*`). The
  reverse does not exist and, given `cluster.py:34-54`, cannot appear as a side effect of the move.
* P2-5 alone is safe too: nothing under `core/` imports `kubernetes_utils` at `24e5e44` (verified by a
  grep over `src/kubementat/core/`, which returns only prose), so moving it changes no `core/` edge.
* Doing both leaves the graph as `core/identity/context.py ← cli ← automations ← infrastructure/kubernetes
  → core/identity`. The seam is a single import line, and the risk is not the direction but the class
  identity behind it: if `kubernetes_utils.py:11` is repointed to `kubementat.core.identity` while a
  test reaches `ClusterTarget` through a `kubementat/cluster.py` shim, the two `isinstance` checks at
  `:321` and `:324` start answering `False` against a class that is otherwise green. Repoint that line
  in the same commit as the move, and let the shim die with it.
* One seam to watch for the tests: `kubernetes_utils` exposes the `X(ctx)` construction P2-5 deletes
  (`:295-329`), and that constructor's argument is the `Context` P2-3 moves. Any test that constructs it
  with a `Context` reaches both files at once, so it fails on the split rather than on the move.
