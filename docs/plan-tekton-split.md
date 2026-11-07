# P4 — `tekton_utils.py` → `src/kubementat/infrastructure/tekton/`: the ticket DAG

Measured at `7fa006d` (`git show HEAD:src/kubementat/tekton_utils.py`), on `v2-arch-refactoring`,
tree clean, suite **1777 passed / 1 skipped**. Written from `.agent-work/census-tekton-split.md`
(2 311 lines, taken at `522e070`) plus the tree probes in §7. Where the census and the tree disagree,
§7 corrects the census; that section is part of the work order, not a footnote.

**No number here was re-derived from the census's scripts and no number here is a guess.** Anything the
census did not measure is printed `unmeasured`, with the one command that measures it.

The package has **five modules and no more** — this is the owner's ruling and it is not reopened by any
ticket. `TektonUtils` disappears.

## 0. The shape every ticket produces

1. Five files, functions only: `generation.py`, `preflight.py`, `runs.py`, `install.py`, `triggers.py`.
   No class, no base class, no mixin.
2. **Every function takes `ctx` first** (the plan's §4 rule, "functions taking `ctx` like the kubernetes
   split"), and `__init__` is *dissolved, not moved*: measured, 28 reads of `self.team` /
   `self.pipeline_namespace` in the file, and `ctx.require_team(...)` occurs exactly once today, in the
   constructor (`:935`). A moved function reads what the constructor read, from `ctx`, where it needs it.
   Where the *refusal sentence* lands is a locked-test question and belongs to P4-6 (§3, item 5).
3. **Cross-module calls are module-qualified** — `generation._replace_placeholders_in_dict(...)`, never
   `from .generation import ...`. This is not style: the suite patches by *module object*, and a name
   import creates a second binding that a `monkeypatch.setattr` at the module nobody runs misses.
4. **The bridge that makes the middle state green: `tekton_utils.py` delegates by *call*, never by
   `from … import`.** Until P4-6 deletes it, the class keeps `__init__` (so the 16 instantiation sites in
   9 test files keep constructing) and each moved name keeps a one-line body that calls into its new
   module. A delegating call re-reads the module attribute on every call, so a constant patch or a
   function patch still lands. A `from … import` line **freezes** the binding and is exactly the silent
   patch machine the census's §3 rows A and C describe — so a ticket may not add one, not even to keep an
   import looking stable, and the two *production* importers are repointed in the ticket that moves the
   function they import (P4-2), not in P4-6.
5. **The invariant recitable at every commit** from P4-1 to P4-6: each of the 63 names
   (27 module-level functions + 36 methods) lives in exactly one place — in `tekton_utils.py`, **or** in
   its module, never both. Two greps per ticket: `grep -c "^def \|^    def " <module>` and
   `grep -n "def <moved name>" src/kubementat/tekton_utils.py`.
6. **One ticket can fail and leave the tree green**, because nothing before it depends on its module
   existing: the DAG's edges are all "the shim needs the target", and the shim is the file being deleted.
   The exception is P4-3 → P4-1/P4-2 (its five call sites have to resolve somewhere) and that is a
   loud ImportError, never a silent pass.

**What green means, once for every ticket:** `uv run pytest -q` → `1777 passed / 1 skipped`.
Each ticket additionally names the files its sites live in. **Per-file test counts are `unmeasured` in
this document** — the census measured *which* files, not *how many*, and no ticket should guess:
`uv run pytest --collect-only -q <the ticket's files> | tail -1`.

## 1. Why the five-module assignment is legal: the cross-group edges

Five call sites, three distinct edges, **no pair that points both ways** — that is the whole
justification for homing the placeholder helper in `generation.py` and for `preflight.py` existing at all.
Line numbers are `7fa006d` call sites, not def lines.

| edge | call sites | direction |
|---|---|---|
| `runs → generation` | `run_pipeline:2157` (`generate_random_postfix`), `run_pipeline:2164` (`_replace_placeholders_in_dict`) | one-way |
| `runs → preflight` | `_list_tekton_resource:1962`, `run_pipeline:2174` (both `_preflight_tekton_api_version`) | one-way |
| `triggers → generation` | `_apply_team_trigger_resources:1417` (`_refuse_obsolete_team_trigger_dir`) | one-way |

**The census names one of these the other way and it is worth printing both.** Census §4 edge 3 says
`generation.generate_trigger_configs → triggers._refuse_obsolete_team_trigger_dir`. The tree has the call
at **two** sites — `:1417` (triggers, `_apply_team_trigger_resources`) and `:1562` (generation,
`generate_trigger_configs`) — so "used by two groups" is right in both readings, and the *import* direction
is decided by which file wins, which is what the assignment below does: with the helper in `generation.py`
the only import edge is `triggers → generation` and generation's own `:1562` call is intra-module. **I
believe the table above** (it is the direction that survives the ruling), and it is the direction that
matters, because `generation.py` imports none of `runs`/`install`/`triggers`/`preflight` and is therefore
the sink. The legality of the five modules does not hinge on which reading you take; a cycle would need
edges both ways, and there are none under either.

## 2. The order, as tickets

Six tickets, leaf-first. `names` = moved names (function + method rows, from the census's own table).

| # | writes / deletes | names | depends on |
|---|---|---:|---|
| P4-1 | `generation.py`, package marker `infrastructure/tekton/__init__.py` (empty) | 21 | nothing |
| P4-2 | `preflight.py`; adds 2 names to `generation.py` | 6 | P4-1 (marker) |
| P4-3 | `runs.py` | 21 | P4-1, **P4-2** |
| P4-4 | `install.py` | 6 | P4-2 |
| P4-5 | `triggers.py` | 9 | P4-1, P4-2 |
| P4-6 | deletes `tekton_utils.py`; fills `__init__.py`; retargets every consumer | 0 (+`__init__`) | P4-3…P4-5 |

**P4-1 — `generation.py`, first.** Leaf-first because it is the sink: zero outgoing edges, so its module
exists before anything can point at it, and it is the only ticket that can be green on its own with no
bridge knowledge. Create `generation.py` with 12 methods → free functions (`ctx` first):
`generate_deploy_pipeline`, `generate_trigger_configs`, `generate_pipeline_run`,
`_declared_pipeline_parameters`, `_pipeline_run_parameter_value`, `_declared_pipeline_workspaces`,
`_pipeline_run_document`, `_configuration_placeholder_values`, `_placeholder_sources`,
`_load_generation_templates`, `_substitute_generation_placeholders`, `generate_random_postfix`
(the last one has **no `self` today** — hoist it, do not thread `ctx` through a body that never used it),
and 9 module-level functions: `_stamp_tekton_api_version`, `_unfilled_placeholders`,
`_trigger_output_file_name`, `_substitute_placeholder_token`, `_first_ingress_host`, `_is_supplied`,
`_safe_name_component`, `_unfilled_source_description`, `_write_generation_output`. Move generation's
`PIPELINE_RUN_*` / `*_PLACEHOLDER_SOURCES` / template constants with it.
`_replace_placeholders_in_dict` and `_refuse_obsolete_team_trigger_dir` do **not** ride P4-1; they are
two-homes and go to P4-2, where the edges appear and are proven legal in one commit.
*Green:* `uv run pytest -q` → 1777/1. Ticket files: `test_generated_deploy_pipelines_and_trigger_configs_are_written_where_they_are_read.py`,
`test_generated_pipeline_runs_are_written_where_the_run_commands_read_them.py`, `test_tekton_manifest_api_versions.py`,
`test_parity_tekton_runs.py`. One call site moves in this ticket, and it is a method call:
`tests/test_generated_pipeline_runs_are_written_where_the_run_commands_read_them.py:212`
(`utils(checkout)._replace_placeholders_in_dict(document)` — off the class it becomes
`generation._replace_placeholders_in_dict(document)`; loud, so it cannot be forgotten).

**P4-2 — `preflight.py`, and the two-homes helpers.** Creates `preflight.py` with
`preflight_tekton_api_version`, `preflight_installed_tekton_api_versions`, `_served_within_a_bounded_wait`
(all module-level, `kubernetes_utils`/`ctx` first) and `_preflight_tekton_api_version(kubernetes_utils, config)`
(the method that is not a member of any of the five groups' homes). Adds to `generation.py`:
`_replace_placeholders_in_dict`, `_refuse_obsolete_team_trigger_dir`. Also carries the
`tekton_api_verified` cache (see below) and repoints the two production importers, which is the point of
doing preflight before `runs.py`: `automations/install/pipelines.py:25,81` and
`automations/install/kubementat.py:26,91` must keep the *same spelling* from `…infrastructure.tekton.preflight`.
*Green:* `uv run pytest -q` → 1777/1. Ticket files: `test_tekton_api_version_preflight.py`,
`test_tekton_install_asserts_the_served_api_versions.py`.

**The cache, decided here because no ticket may improvise it.** The census calls `tekton_api_verified`
"shared mutable state on the class"; the tree says the *read* is class-level (`:921`) and the *write* is
`self.tekton_api_verified = True` (`:944`) — an instance-dict shadow, so the cache is **per instance**
today, and no test touches it (`grep -rn tekton_api_verified tests` → no hits, measured). A module-level
`bool` therefore silently widens the cache to the whole process, and a second cluster addressed in one
process would stop verifying. So: key the cache **by the object that answers the cluster** (the same
identity `KubernetesUtils`/`ClusterTarget` carries), not by a bare flag, and add the assertion that two
instances against two targets verify twice — `test_two_clusters_can_be_addressed_by_one_process_at_once.py`
is the shape, and if it has no tekton analogue, *this ticket is the place that case is worth writing*, because
it is the only case the split can regress silently.

**P4-3 — `runs.py`.** Depends on P4-2: 3 of its 5 cross-group call sites resolve into `preflight.py`, and
2 into `generation.py`. Names (15 methods + 6 functions): `list_pipelines`, `list_tasks`, `list_taskruns`,
`list_pipeline_runs`, `list_all_tekton_resources`, `setup_cleanup_job`, `_setup_cleanup_job_pull_secret`,
`_list_tekton_resource`, `cleanup_pipeline_runs`, `cleanup_pipeline_run`, `list_available_pipeline_runs`,
`check_running_pipelines`, `run_pipeline`, `wait_for_pipeline_run`, `_pipeline_run_or_none`,
`cleanup_job_cluster_role`, `cleanup_job_command`, `cleanup_job_image`, `cleanup_pipelinerun_cron_job`,
`_resolve_pipeline_run_file_path`, `_first_status_condition`. `list_available_pipeline_runs` is the one
member the plan's four buckets never placed: filesystem-only, exactly one caller
(`cli/commands/tekton.py:72`), read-side twin of `run_pipeline`'s `global/` rule — so it is **`runs.py` by
symmetry, decided by this document and not by the plan**, and the ticket's commit message says so.
The `CLEANUP_JOB_*` constants and `CLEANUP_JOBS_TO_KEEP` come here; the latter has no consumer outside
these files, so it needs no export. `PIPELINE_RUN_POLL_INTERVAL_SECONDS` / `PIPELINE_RUN_TIMEOUT_SECONDS`
stay exported from `runs.py` because the CLI reads the poll interval as a `click` default
(`cli/commands/tekton.py:28,60`) — the only constant the command layer reads.
**Bind `time` in `runs.py` as a module** (`import time`) — this ticket is where the one string-form patch
with a `time` dependency lands (§3 item 1).
*Green:* `uv run pytest -q` → 1777/1. Ticket files: `test_the_cli_waits_for_a_tekton_pipeline_run_it_started_and_reports_the_run_it_saw.py`,
`test_parity_tekton_runs.py`, `test_generated_pipeline_runs_are_written_where_the_run_commands_read_them.py`,
`test_the_cleanup_job_is_created_in_the_namespace_the_config_names.py`.

**P4-4 — `install.py`.** Depends on P4-2 (`preflight`'s home is taken; `install.py` must not become its
owner). Names: `install`, `_install_helm_deployer_cluster_role`, `_wait_for_pipeline_webhook`,
`_release_manifest_url`, `release_manifests_to_apply`, `helm_deployer_cluster_role`, with constants
`TEKTON_RELEASE_BASE_URL`, `RELEASE_MANIFESTS`, `HELM_DEPLOYER_CLUSTER_ROLE_NAME`
(repoint `automations/uninstall/kubementat.py:24`, which imports it and re-exports it as
`HELM_DEPLOYER_CLUSTER_ROLE` at `:46,128` — the only non-CLI production consumer needing a name kept
alive), `HELM_DEPLOYER_RULE_API_GROUPS`, `PIPELINE_WEBHOOK_SERVICE_NAME`, `WEBHOOK_READY_TIMEOUT_SECONDS`,
`WEBHOOK_READY_POLL_SECONDS`, `DISCOVERY_ATTEMPTS`, `DISCOVERY_DELAY_SECONDS`.
**In this ticket `install()` stops being a bound method**, which is the moment §3 item 4 goes silent — so
the instance patch is repointed in this ticket, not in P4-6.
*Green:* `uv run pytest -q` → 1777/1. Ticket files: `test_the_tekton_install_reads_its_configuration_first.py`,
`test_the_tekton_install_waits_for_the_webhook_the_next_release_must_call.py`,
`test_parity_tekton_installs_the_pinned_releases.py`, `test_tekton_install_asserts_the_served_api_versions.py`.

**P4-5 — `triggers.py`.** Names: `setup_triggers`, `_setup_trigger_rbac`, `_setup_webhook_secrets`,
`_apply_team_trigger_resources`, `webhook_job_cluster_role_binding_name`, `_trigger_labels`,
`bindings_and_interceptors_cluster_role`, `trigger_controller_role`, `trigger_webhook_job_role`, with the
`TRIGGER_*` set, `BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME`, `WEBHOOK_SECRETS` (imported by name by
`test_the_trigger_setup_asks_the_cluster_for_the_same_rbac.py:26`). `_apply_team_trigger_resources` is the
one place in the file that still needs a raw `Context` — it builds `KubernetesUtils(self.ctx)` inline
(`:1428`) to hand to `apply.apply_resources`, so its free-function signature must carry `ctx` and keep
calling `apply.apply_resources(...)` **through the module attribute**, never `from … import apply_resources`
(census row D is only "correct today" while that holds).
*Green:* `uv run pytest -q` → 1777/1. Ticket files: `test_the_trigger_setup_writes_rbac_the_way_the_manifest_did.py`,
`test_the_trigger_setup_asks_the_cluster_for_the_same_rbac.py`,
`test_the_manifest_layers_apply_product_then_environment_then_team.py`.

**P4-6 — delete `tekton_utils.py`, install the `__init__.py`, retarget the consumers.**
Deletes the shim and the class. Touches: `cli/commands/tekton.py` (its `TektonUtils` import, `:72`
instance call, and the `click` default that reads the poll interval), the two `automations/install`
modules if P4-2 left anything, `automations/uninstall` if P4-4 left anything, the 16 instantiation sites in
9 test files, the three path-keyed test tables, the five source-text readers (§3 item 5), the SURVIVORS
key (§3 item 6), and the `PENDING_KUBECTL_READS` entry `"kubementat/tekton_utils.py": 0`, which is inert
and must be deleted anyway because the scanned path no longer exists.
*Green:* `uv run pytest -q` → 1777/1, plus `uv run python tests/e2e/suite.py` is **not** this ticket's
gate (it is Phase 5's), and `grep -rn "tekton_utils" src/` must return nothing.

## 3. Test work, carried per ticket and never deferred to a sweep

Census §3 measured **11 `setattr` sites in `tests/` that touch tekton**, **2** string-form patch targets,
**1** instance patch, and the path-keyed tables. The table names every site the census named; a site the
census only counted but did not name is marked `named-in-table: no`, because a ticket can only fix a site
it can point at.

| # | site | must patch after the move | ticket |
|---|---|---|---|
| 1 | `test_the_cli_waits_…:178`, string `"kubementat.tekton_utils.time.monotonic"` (string-form **A**) | `kubementat.infrastructure.tekton.runs.time.monotonic` — **only works if `runs.py` binds `import time` itself**, which is why rule P4-3 makes that explicit. The loop moves, so `monotonic`'s caller is `runs.py`. | P4-3 |
| 2 | same file `:255`/`:43` (`SOURCE`, `ast.parse`) — file-level rule "no sleep at import; exactly one `sleep_seconds` call" | becomes a rule over `runs.py` (the loop's home) *and* a per-file rule over the package; the assert's meaning changes even when it passes. See item 5. | P4-3 |
| 3 | `test_the_tekton_install_waits_for_the_webhook_the_next_release_must_call.py:80,81,218` (`from kubementat import tekton_utils` at `:34`) | `…infrastructure.tekton.install` — three constant patches (`WEBHOOK_READY_*`, `PIPELINE_WEBHOOK_SERVICE_NAME`). **Silent the instant the shim `from … import`s the constant instead of delegating**, and then the loop runs the real 300 s/1 s schedule while the test asserts it waited. | P4-4 |
| 4 | `test_the_tekton_install_reads_its_configuration_first.py:146`, `setattr(utils, "_install_helm_deployer_cluster_role", …)` on an instance (**highest severity in the phase**) | `setattr(install, "_install_helm_deployer_cluster_role", …)` as a *module function*. P4-4 is where `install()` stops calling a bound method: from then on this patch is a **no-op** and "the role was written once, before the manifests" survives only as luck of the recorder. Keep one assertion that the substitute was actually reached. | P4-4 |
| 4b | same file `:145`, string `"kubementat.tekton_utils.shell.run"` (string-form **B**) | `…infrastructure.tekton.install.shell.run`, and only if `install.py` binds `shell` as a module (never `from …shell import run`). Otherwise real `shell.run` executes and the loud failure is a PATH/fake question, not an import question. | P4-4 |
| 5 | `test_team_option.py:111`, `setattr(tekton_cli_module, "TektonUtils", recorder)` | correct **only while the CLI binds a class**; after P4-6 there is no such binding and the patch retargets to `cli.commands.tekton.<function>` / `…tekton.runs.run_pipeline`. This is the tekton twin of the kubernetes split's 13 grafana sites: it keeps the test green while asserting nothing. | P4-6 |
| 6 | 7–8 rows the census groups as row D — `test_tekton_api_version_preflight.py:129-136`, `test_the_configuration_key_secret…:150-152`, `test_the_deployer_secrets…:154-156`, `test_the_manifest_layers…:219-222`, `test_the_a_team_that_names_no_registry…:83`, `test_command_execution_policy.py:490,513`, `test_component_namespaces_come_from_configuration.py:95` | **Nothing.** They patch `automations/install/*` slice modules, not tekton. They are listed so a ticket *proves* it left them alone by running them, and so that the rule they depend on survives: `triggers.py`/`install.py` must keep calling `apply.apply_resources(...)` module-qualified. | run-in-P4-2, P4-4, P4-5 as applicable |

**The 11 versus the named sites — one honest disagreement.** The census's headline is 11 sites that touch
tekton; its own table names 5 in the "must repoint" sense (items 3, 4, 4b, 5, and item 1's object-first
neighbour) plus the ~8 row-D sites that do not touch tekton's modules at all. Per the brief-2 instruction
this document does **not** re-count. What is carried is the per-site list above, which is what a ticket can
act on, and the count of what changes: **5 repointed, ~8 proved untouched.** If a worker's own count lands
on a different 11, the sites that differ are row-D ones and the answer is the same: run them.

**The EXEMPT key.** `tests/test_a_locked_configuration_file_refuses_by_name_and_not_by_codec.py:446`
holds `("kubementat/tekton_utils.py", "run_pipeline")`. `run_pipeline` lives in `runs.py` from P4-3, so the
raw-read exemption stops matching — and if the walker only checks declared sites, the seam test does not
fail: it **drops a site from its coverage claim and keeps passing**. Therefore P4-3, the ticket that moves
the function, also rewrites the key to `("kubementat/infrastructure/tekton/runs.py", "run_pipeline")`. A key
that "keeps its old spelling" is this row, and this row is the definition of a silent pass.

**The five source-text readers** (5 read points in 4 files — census row "×2" plus the two single reads; the
brief's "five tests" counts read points, not files). Each must become a *package-wide scan*, and the
widening is only safe if the assertion it carries today is written down:

| reader | asserts today | after the split | ticket |
|---|---|---|---|
| `test_the_cli_waits_…:255` | `tekton_utils.py` does not sleep at import and calls `sleep_seconds` exactly once | the loop is in `runs.py`; the import-sleep half becomes a rule over all five files, the once-per-loop half becomes one-per-file. Assert the pair per file, not across four files. | P4-3 |
| `test_tekton_install_asserts_the_served_api_versions.py:39,198` (`INSTALL_SOURCE`) | exactly one base-URL assignment, the literal `"previous/v"`, and the three `TEKTON_VERSION_*` spellings | "one base URL" is a *package* invariant; scanning `install.py` alone re-states it too narrowly, scanning the glob re-states it correctly. `TEKTON_VERSION_*` are **not** module constants — they live in `templates/environment/static.json.template` — so keep that half of the read pointed at the template, not at a module. | P4-4 (read points 2, 3, 4) — the template half stays P4-6's |
| `test_component_namespaces_come_from_configuration.py:815` | `tekton_utils.py` contains no namespace literal of its own | a `infrastructure/tekton/*.py` glob. A per-file rule over a file that no longer holds the job code is a guard about nothing. | P4-6 |
| `test_the_cleanup_job_is_created_in_the_namespace_the_config_names.py:315` | same rule | same glob; the cleanup job code is in `runs.py` after P4-3, so this can be widened in P4-3 and re-checked in P4-6. | P4-3 |

**`test_the_commands_that_refused_a_team_they_do_not_read.py:46` is a sixth source-text reader** the census's
"×2" row does not count. It keys `SURVIVORS["src/kubementat/tekton_utils.py"] = ("the namespace the Tekton
objects live in", "config.get('team_static')")` and asserts *both* the sentence and the evidence string, so
it is loud — and it is the test that locks the team refusal `__init__` used to earn (`:935`, the only
`require_team` in the file). Dissolving the constructor (§0.2) means the refusal moves into function bodies,
so P4-6 must: (a) keep the exact sentence and the `config.get('team_static')` read wherever
`PIPELINE_NAMESPACE` is read, and (b) update the SURVIVORS key set, whose size is a *coverage claim* — if
more than one module carries the sentence the table gains keys, and "same count with new keys" is the honest
edit, not "delete the tekton key". **Behaviour consequence to name in the commit message:** a team-less
context will now refuse at the first method that needs a namespace rather than at construction; that is the
only user-visible change in the phase and it needs a test that says so.

**16 instantiation sites** (`TektonUtils(fake_ctx)` then a method call) across 9 files survive a method-body
move *iff* the class stays constructible — which it does, by §0.4, until P4-6. P4-6 rewrites all 16 to the
new entry points; no ticket before P4-6 may touch them.

## 4. The three helpers with two homes, and the single home each wins

Exactly as the census named them, filtered through the no-sixth-module ruling.

| helper | census's own recommendation | home here | why not the obvious one |
|---|---|---|---|
| `_preflight_tekton_api_version` | "a third file (`preflight.py` or the package `__init__`) imported by both `install.py` and `runs.py`" | **`preflight.py`** | Its obvious home `install.py` would make `runs.py` import `install.py` for one function, and it carries the cache. `__init__` is rejected: a helper in the union file is the one place a moved name can be kept alive (and §5 rules the union empty of names). |
| `_replace_placeholders_in_dict` | "give it a third home (`placeholders.py`) rather than picking a side" | **`generation.py`** | The census's recommendation is a sixth module, which the ruling forbids. It is pure dict work over `Config`, both of its callers (`run_pipeline:2164`, `_substitute_generation_placeholders:1950`) already point that way, and nothing in generation imports back. A sixth module buys nothing: the edge exists either way. |
| `_refuse_obsolete_team_trigger_dir` | "a package-level `_paths.py`/`_guards.py`, or `generation.py` … `triggers.py` can hold a thin wrapper" | **`generation.py`** | `_paths.py`/`_guards.py` is a sixth module. `generation.py` is where the refusal matters and `triggers.py` keeps the `:1417` call as a plain cross-module call. Note §7: the tree already holds a *second, deliberately different* implementation of this guard in `config_init.py`. |

A fourth name the brief flagged as possibly two-homed is not: `_trigger_labels` has **zero** cross-group
edges (its callers are `_setup_trigger_rbac`, `_setup_webhook_secrets` and the three role builders, all
triggers), so it moves with `triggers.py` freely. What is shared is its *contract*, which five places
restate instead of importing (`automations/uninstall/residue.py:280`, `infrastructure/kubernetes/constants.py:36`,
`cli/commands/kubernetes.py:273`, `tests/test_uninstall_sweeps_everything_kubementat_owns.py:224`,
`tests/test_a_secret_is_created_from_the_keys_of_a_json_file.py:24`). The ticket adds docstrings naming that
sweep; it changes no import.

## 5. `infrastructure/tekton/__init__.py`: modules only, no re-exports

**Ruling: the file exposes the five modules and *no names*. No `from .runs import …`, no `__all__` of
functions, no compatibility shim.** Created empty as a package marker in P4-1, still empty of names in P4-6
(after P4-6 it may name the modules for the import-linter contract and for nothing else).

The reason is tekton-specific, and it is the reason the kubernetes answer would have been wrong here by
accident rather than right by habit: the kubernetes split's own plan asked for "the union so importers need
only one import", and that re-export is precisely what the Phase 2 tickets had to work around. The kubernetes
precedent says *empty* — but here, empty is not just inherited, it is demanded by the sites in §3: this
phase's failure mode is constant-and-function patches that land on a stale binding (items 1, 3, 4, 5 and the
EXEMPT row), and a union file is a **second binding for every name it re-exports**, indistinguishable from
the first to `monkeypatch`. Six of the phase's sites would be ambiguous instead of singular. Exporting
nothing makes every patch target one unambiguous module attribute, which is what §0.3 is for.

Secondary, and stated so no one reopens it: keeping `preflight_tekton_api_version` reachable through
`tekton_utils` is unnecessary, because the two production importers are **two lines** and they are repointed
in P4-2 where a wrong path fails at import. An `__init__` that "keeps the old names alive" is how a phase
that is supposed to delete a module ends up with a module that merely moved.

## 6. Unresolved rows, named as unresolved — settle these before the ticket that needs them

**Row 1 (assigned, settle first): does `tests/test_parity_tekton_runs.py` reach `run_pipeline` at class
level or CLI level?** The census tried, read the imports (`CONFIG_PLACEHOLDER_SOURCES` only) and
`cluster_parity.py`, and stopped at a helper it did not open. This decides whether that file is edited at
all, and it is the file that sits on edges from both `runs.py` (item 1 of §3's cousin) and `generation.py`
(`_substitute_placeholder_token`, and `CONFIG_PLACEHOLDER_SOURCES` imported at `:47`). **Assigned to P4-1 as
its first commit**: `sed -n '1,80p' tests/cluster_parity.py` and one grep for `TektonUtils(` across
`test_parity_tekton_runs.py` + `cluster_parity.py` — the file either constructs the class (then it is a
P4-3/P4-6 editing target) or it does not (then it is only a *run target*, and touching it is a mistake).
**Do not write P4-3 until this answer is in the ticket's notes.** A wrong guess here does not fail; it edits
a file that did not need editing.

**Row 2 (assigned, no guess needed): `list_available_pipeline_runs`.** The plan's four buckets did not place
it and the census left it unresolved by the brief's own wording. It is decided in this document — `runs.py`
— on measured grounds: filesystem-only, one caller (`cli/commands/tekton.py:72`), read-side twin of
`run_pipeline`'s `global/` prefix rule. The doc claims this decision, not the plan.

**Row 3: census line drift.** Its edge table cites def lines; this document's call-site lines are in §1.
Both are correct, and a ticket that greps for a def line to find a call site is the only way this could be
misread.

## 7. Measured, not inherited

Every number above comes from the census's table or from one command in this tree, listed here. Nothing was
re-derived with an `ast` graph.

| claim | command | result |
|---|---|---|
| file is 2 311 lines, tree clean, HEAD `7fa006d` | `git rev-parse --short HEAD; git status --porcelain; wc -l src/kubementat/tekton_utils.py` | `7fa006d`, clean, 2311 |
| the 5 call sites and their 3 edges | `sed -n '1417p;1962p;2157p;2164p;2174p' src/kubementat/tekton_utils.py` | exactly the five lines in §1 |
| `generate_trigger_configs` also calls the guard | `grep -rn "_refuse_obsolete_team_trigger_dir" --include=*.py src tests` | calls at `:1417` and `:1562` |
| production importers keep the spelling | `grep -rn "preflight_(installed_)?tekton_api_version" --include=*.py src \| grep -v tekton_utils.py` | `automations/install/kubementat.py:26,91`, `…/pipelines.py:25,81` |
| the cache is per instance, not process-wide | `grep -rn "tekton_api_verified" src tests` | `:921` class attr, `:944` `self.` write, **0 test hits** |
| `list_available_pipeline_runs` has one caller | `grep -rn "list_available_pipeline_runs" --include=*.py src tests` | 1 production call, `cli/commands/tekton.py:72`; all test hits are prose |
| the placeholder helper's two callers | `grep -rn "_replace_placeholders_in_dict" --include=*.py src tests` | `:1950`, `:2164`, plus test `…pipeline_runs_are_written…:212` |
| 5 source-text readers | `grep -rn "tekton_utils.py" <the four files> \| grep "read_text\|SOURCE ="` | `:39`, `:815`, `:315`, `:43` (+ `:198` second read point) |
| the EXEMPT key exists with that exact key | `grep -rn 'tekton_utils.py", "run_pipeline"' tests/` | `test_a_locked_configuration_file_refuses_by_name_and_not_by_codec.py:446` |
| `require_team` happens once, in the constructor | `grep -n "require_team" src/kubementat/tekton_utils.py` | `:935` only |
| 28 constructor-state reads to dissolve | `grep -c "self\.team\b\|self\.pipeline_namespace\b" src/kubementat/tekton_utils.py` | 28 |
| per-file test counts | *unmeasured* | `uv run pytest --collect-only -q <files> \| tail -1` |

### Corrections to the census (the next ticket reads this, so they are not editorial)

1. **`_refuse_obsolete_team_trigger_dir` already exists twice, deliberately, and the two must stay two.**
   `config_init.py:305` defines the same guard, called at `config_init.py:417` and `:731`, and its own
   docstring says "The reader refuses the same directory, in `tekton_utils.py`". The bodies are **not**
   equivalent: the tekton copy raises `errors.ObsoleteTriggerDirectoryError` and computes the path from
   `config.tekton_team_trigger_dir.parent.parent`; the `config_init` copy raises `click.ClickException`,
   computes from `config.team_static_config_path.parent`, and returns early when `config.team is None`.
   So: move the tekton copy to `generation.py`, and **do not consolidate** — deduping would change the
   exception type an error-class test asserts on. This is also the strongest available reason not to put
   the helper in a package-level `_guards.py`.
2. **`tekton_api_verified` is per-instance, not "shared mutable state on the class"** as census §2's note
   reads. See P4-2; the fix is a keyed cache, not a module bool.
3. **`_preflight_tekton_api_version` is a method, not a module-level function**, so it is not counted in the
   census's 27; it is one of the 36 methods, and its home (`preflight.py`) holds 1 method + 3 functions.
4. **The census's §3 "5 tests reading the source text" is one file short** —
   `test_the_commands_that_refused_a_team_they_do_not_read.py:46` reads the same path for an evidence
   string and is a locked-survivors assertion, louder than the rest (§3 item 6).
5. **`CLEANUP_JOBS_TO_KEEP` needs no export** beyond `runs.py` (the census's constants table already
   corrected the brief here; restated so no ticket exports it by reflex). `CLEANUP_JOB_LABELS` is quoted by
   `residue.py` and two tests, so its *spelling* must survive even as its home moves.
