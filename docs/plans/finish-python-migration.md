# Finish the Python Migration — Implementation Plan

> **Superseded by
> [`docs/plans/finish-python-migration-last-lap.md`](finish-python-migration-last-lap.md)** for
> anything in this file that is still *work*: the **§1.3b remaining-work table**, the §2 ledger rows
> that read *stays*, and every ticket that says OPEN or PARTIAL. **This file is not the to-do list.
> It is the migration's record and its deletion ledger (§7)** — which ticket replaced which script,
> which guard holds the deletion, which condition the row was waiting for. A ticket that landed is
> landed, whatever a table here still says: **FIN-11** (`213bc9b`, `kmt uninstall` in Python) and
> **FIN-19** (`kmt component uninstall`, §1.3b row 6b) both landed with lane evidence and are not to
> be re-opened from a row in §1.3b or §9. The longer pointer below says this at more length; the
> last-lap file's §2 is the to-do list and its §8 re-measures every number in either file.

> **SUPERSEDED FOR WORK — read
> [`docs/plans/finish-python-migration-last-lap.md`](finish-python-migration-last-lap.md) first, and
> read this file as history.** Everything in this plan that is still *work* — the §1.3b "remaining
> work" table, every §2 ledger row that says *stays*, the wave tickets that read OPEN or PARTIAL —
> was merged into that file on 2026-10-28, along with the dispatch queue that ran that day. That
> file is now the index, the execution order, and the place where the remaining work lives.
>
> **This file stays as the record and the deletion ledger** (§7). It is what makes a deleted script
> auditable: which ticket replaced it, which guard holds the deletion, and which condition the row
> was waiting for. Nothing here is renumbered, deleted, or reworded, and a row that says DONE here
> is DONE — do not re-open one because a §1.3b table still reads otherwise.
>
> Two sentences further down are stale and kept as written: the claim that this document is the
> **only** migration plan in the tree, and the `Verified against:` SHA. "What is left?" is answered
> by that file's §2, and its §8 re-measures every number quoted in either file.

> **Verified against:** branch `python_refactor_2` @ `9c95a75` (all Tekton v1 counts below are `grep`-measured, not estimated)
> **Purpose:** Close out the bash → Python transition, get `scripts/` to zero, move the tree to a proper
> `src/` layout, relocate generated trigger config to `platform_config`, and move Tekton from `v1beta1` to `v1`.
> **Audience:** an implementing agent (human or LLM) executing one ticket per PR.
> **Asking "what's left?":** read **§1.3a** first — it is the verified defect/ticket status (DONE / PARTIAL /
> OPEN with the command that proves it) — and `TODOs.md` for product work that is not this migration.
> Do not answer that question from a ticket row alone: two tickets in §1.3a are further along than their
> rows read, and two ids are overloaded.
> **Supersedes:** the 61-ticket `docs/planned-features/plan-python-refactor.md` (2026-06-25) and the status
> tables in `docs/TODOS_REFACTORING.md`. Both contained stale entries and were **deleted on 2026-10-21** (archived
> to `~/backups/kubementat/outdated-plans-20261021.tar.gz`) — so this document is not merely authoritative, it
> is the **only** migration plan in the tree. That is deliberate: while both existed, anything asking
> "what's left?" could find the older, more confident-looking file first.
> **Evidence base:** [`docs/plans/testsuite-requirements.md`](testsuite-requirements.md) (Wave T
> requirements, FR/INV level) and [`docs/plans/virt-runner-setup-research.md`](virt-runner-setup-research.md)
> — 2026-10-20 empirical findings on the VM primitive, k3s provisioning, measured timings, and **two
> upstream blockers that currently stop any `kmt install`**. Anything in this plan that the research
> contradicts has been corrected; each such spot cites the research.
>
> **Owner decisions incorporated (do not re-litigate in tickets):**
> 1. **`lib/` → `src/`** — standard src layout, `kubementat` becomes a real installed package (FIN-01).
> 2. **Generated triggers live in `platform_config/{env}/{team}/triggers/`**, not `tekton_ci/triggers/{TEAM}/`
>    (FIN-13, FIN-23). Generator *templates* stay in `tekton_ci/triggers/templates/{github,gitlab}/`.
>    *Amended 2026-11-04 by T-60 (config-code-separation §9): the team's generated triggers moved one
>    directory deeper, to `platform_config/{env}/{team}/tekton/triggers/`, beside the team's other
>    generated output. The templates' location is unchanged. A tree that still holds the directory this
>    decision named is refused by name at both generation and setup.*
> 3. **Tekton `v1beta1` → `v1` is in scope**, as Wave 7 (FIN-70…FIN-73). **Hard flip, no env override, and it
>    lands early** — immediately after Wave 0 — so Waves 1–4 are written against `v1` from day one.
> 4. **Generated `platform_config/{env}/{team}/…` stays committed to git for now.** A later project will move
>    all configuration out of the repo so an operator's `platform_config` is a separate repository against a
>    pinned `kubementat` version — do **not** design for that yet, but don't make it harder.
> 5. **Component namespaces become configuration, not code.** No namespace may be hardcoded in `src/`
>    (FIN-07): `static.json` is the source of truth, `constants.py` only holds fallback defaults.
> 6. **`kmt run-script` is removed at the end** (end of Wave 4). It is the last bash dependency in the CLI. **Done 2026-10-29, FIN-06 (`e6b3afd`)** — deleted and not preserved, with the guard in
> `tests/test_no_cli_command_starts_a_script_by_path.py` asserting the rule off the entry point's own
> registrations rather than off a list of names.
> 7. **Verification is manual; there is no merge gate.** Nothing blocks a PR on a lane result, and nothing
>    will be added to block one. A lane's output is evidence attached to the ticket (FIN-87), not a policy.
> 8. **No scheduled lanes.** No nightly, no cron, no runner. The `full` lane runs only when someone decides
>    it is worth their time.
> 9. **Guest image is pinned: `--distro ubuntu --release resolute` (Ubuntu 26.04)**, explicitly, recorded per
>    run. Ubuntu remains the only supported guest.
> 10. **The dead `twuni` repo is deleted, not replaced** (FIN-88); a different docker-registry solution comes
>     later. **Update 22 Oct 2026 (FIN-89):** the owner decided to **remove the dashboard component
>     outright** rather than swap its chart — a maintained chart comes later, if at all. Both landed
>     (`7243da9`). What is still undecided is *which* dashboard, if any, replaces it; the analysis below
>     is the starting point for that choice, not a commitment.
>
> **Two constraints that change ticket text more than any of the above:**
> - **NO BACKWARDS COMPATIBILITY.** See §1.7 — breaking renames, breaking CLI changes, breaking config-key
>   changes are all *expected*. One staging customer, and they will be re-initialized on a new cluster.
> - **Nothing may merge without a green real-VM integration test.** The E2E suite built on
>   [`~/dev/os_projects/virt-runner`](../../os_projects/virt-runner/README.md) (Wave T) is a **prerequisite**,
>   not a nice-to-have: §1.7 explains why `pytest` + argv shims are not sufficient here.

**File-reference convention:** every `path:line` below was written against `lib/kubementat/...` and
FIN-01 (`src/kubementat/...`) changed only the directory, so read `lib/` as `src/`. Line numbers
**did not all survive** FIN-02's command-execution cleanup: `automation.py` is ~15 lines shorter from
line 155 down (the retry loop and the `check=`/`text=` arguments went away) and
`kubernetes_utils.py` is 1 line longer (the `shell` import). Drifted references were corrected in
§1.3a as they were touched; the §1.3 audit table below is still historical. When a line reference and
the code disagree, the code wins — check the §1.3a status column first.

---

## 1. Verified current state

### 1.1 Hard numbers

Re-measured 2026-10-27, and re-measured again on 2026-10-28 against `badcf40` — these drift every time a
ticket lands, and they are what makes "what's left?" answerable without reading 57 rows. **The table below
is the 27 Oct measurement; the delta table under it is the one to act on.**

| Metric | Value |
|---|---|
| Shell scripts in repo (excl. `.git`, `.venv`) | **51** files / **3,743** LOC (`find . -name '*.sh' -not -path './.git/*' -not -path './.venv/*' | xargs wc -l`) — the row read **59** / **4,134** when it was last measured; FIN-60 deleted 8 product scripts and nothing else moved, so −8 files / −391 LOC |
| Shell scripts under `scripts/` | **44** files / **3,257** LOC (measured then; 52 / 3,648 at the commit before FIN-60) — the product scripts, i.e. the deletion ledger. FIN-60 executed the "already migrated — delete immediately" row: **8 of its 12 scripts are gone**, 4 are still owed and §7 says why for each. **Re-measured 2026-10-29: `find scripts -name '*.sh' | wc -l` → 2 / 4 LOC**, both in `scripts/utilities/kubernetes/` — `retrieve_node_addresses.sh` is two lines ending in a `jq`, `view_cluster_resource_versions.sh` is `kubectl api-resources -o wide` (`wc -l` on each: 2 and 2). Both went with the ledger rows that closed after FIN-60 (helmfile_apply, generate_ssh_key, generate_pipeline_run, setup_mysql_databases and **`run_pipeline.sh`, FIN-51b `85f1ea8`**), and FIN-51b is the row that moved the number last. The claim that used to be carried here — "**1 of the 4 still has a bash half to run** (`run_pipeline.sh`, whose tests still compare live bash)" — is false now: `test_parity_tekton_runs.py` runs no bash and records the deleted script from `git show 40f281b:…` instead. What survives those rows is the **one** pair whose condition is still open: the two one-liners above, whose commands FIN-44/45 never shipped. |
| Shell scripts elsewhere | **6** files / **297** LOC: `tests/e2e/stages/01-provision.sh` 147 (harness, not product), `tests/shims/_recorder.sh` 103 (the parity shims' recorder, FIN-03), `examples/kubernetes/` 2, `docker_files/**/{build,run_locally}.sh` 2. The row read **7** / **486** because `uninstall_kubementat.sh`'s 204 LOC were in it: **FIN-11 deleted that file**, so this is the honest count of what is left (the uncommitted `docker_files/**` work another agent carries is excluded, as it was when the row was measured) |
| Python files under `src/` | **18** — `src/kubementat/utils.py` is deleted (FIN-08); the empty files left are the three `__init__.py` package markers, which are empty by definition |
| Python LOC under `src/` | **3,676** (`find src -name '*.py' -not -path '*egg-info*' | xargs wc -l`). The row read 3,472 when it was last corrected; +107 of the drift is FIN-74's install-time assertion, the rest is what landed since that measurement |
| `kmt` commands registered | **19** (`uv run kmt --help`) |
| `kmt` commands that still shell out | **2** (`tekton-setup-triggers`, `run-ci-container`) — `uninstall` stopped shelling out (FIN-11, `213bc9b`), `login-kubernetes-dashboard` went with the dashboard component (FIN-89), and `run-script` is **deleted (FIN-06, `e6b3afd`)**, which is what makes the third slot empty rather than merely re-listed |
| Python unit tests | **262 passed**, no cluster (`uv run pytest -q`). This row read **0** in the earlier revision, **152** before FIN-07 made component namespaces configuration, and **249** before the two lane-probe tests landed; the suite now covers import hygiene, the command-execution primitive and its policy, the CLI surface, config-failure paths, config-path resolution (`KUBEMENTAT_HOME`, `KUBEMENTAT_PLATFORM_CONFIG_DIR`, the trigger dir), the `KubernetesUtils` primitives (FIN-04), who answers helmfile's confirmation, where a component namespace comes from, the API version every manifest speaks and the preflight that refuses a controller which cannot serve it, what each installed controller actually serves after the install runs, and — since FIN-03 — **what the cluster was asked to do** (34 parity tests over the 12 migrated script rows; since FIN-60, 8 of those rows compare a *record* of the deleted script rather than a bash run, and only `run_pipeline.sh` still has a live bash half), since FIN-08 that nothing shipped is unreachable (dead module, stub script, or a call that forgets the argument the library demands — 5 cases), since FIN-60 that no string in `src/` may name a script the ledger deleted (the AST scan of every `.sh` name `src/` uses, plus the CLI's one-line `Command not found` for a command whose script is gone), and — the +13 — that the E2E harness's own tunnel and Grafana probes measure what they claim, against a fake guest, instead of only inside a VM. **Since FIN-13 it also covers what a trigger setup asks for**: the seven RBAC objects in order and the namespace they land in, the 409 replace branch, the two webhook Secrets and the branch that skips one, which kinds go to which API group, and that `platform_config/{env}/{team}/triggers/` is read and the checkout is not |
| Manifests on `tekton.dev/v1beta1` | **0** files. `tekton_ci/` (38 occurrences / 38 files) and `examples/tekton_ci/` (20 occurrences / 16 files) were converted to `tekton.dev/v1` (FIN-71/72). The only `v1beta1` left in a manifest is `triggers.tekton.dev/v1beta1` on the **6** trigger generator templates, which is what Triggers 0.37.2 actually serves (measured: `v1beta1` served and stored on every trigger CRD, no `v1` — FIN-74) and not part of the flip — the substring trap that made naive greps useless is spelled out in §1.6 |
| Pinned Tekton versions | Pipelines **1.17.1**, Triggers **0.37.2**, Dashboard **0.73.0** (`templates/environment/static.json.template`, the `TEKTON_VERSION_*` keys; landed by FIN-74). The Dashboard gates the set — 0.73.0's release notes declare support for "Pipelines v1.15.x LTS, v1.17.x, and Triggers v0.37.x LTS". The release URL moved with them: `install_tekton.sh` builds every manifest URL from `TEKTON_RELEASE_BASE_URL=https://infra.tekton.dev/tekton-releases`, because the bucket it used before serves only what it kept (`v1.4.0` → 200, `v1.15.5` → 404) |
| Stubs / leftovers | **One leftover.** `scripts/automation/tekton/create_team_account.py` and `src/kubementat/utils.py` are deleted (FIN-08). `src/cli/kmt` (no extension, **53 LOC**) is still tracked: it is the pre-FIN-01 CLI entry file, `from commands import …` cannot resolve from the `src/` layout, and `[project.scripts]` binds `cli.kmt:main` — which is `src/cli/kmt.py`. So nothing imports it and nothing can. FIN-08's row said "`cli/kmt` is gone", and the *package-level* `cli/kmt` it meant is gone; this file was missed. Housekeeping, not a defect: `tests/test_config_path_resolution.py` inspects `src/cli/kmt.py` by name, so a rename/deletion is silent. What FIN-08 did not do is the *feature*: that landed later as FIN-30…FIN-33 (`src/kubementat/infrastructure/user_management.py`,
`kmt users …`), and the bash `create_team_account*.sh` implementations that were still the only path then are
now deleted with it. |

#### 1.1a Re-measured 2026-10-28 (`badcf40`)

| Metric | Value | Why it moved |
|---|---|---|
| Shell scripts under `scripts/` | **29** files / **1,640** LOC in the checkout (30 / 1,675 tracked at `HEAD`; was 34 / 1,872) | FIN-13 deleted `setup_triggers.sh` (269 LOC). Before that FIN-12 deleted `install_tekton.sh` (108 LOC), the three `scripts/tasks/install_{mysql,redis,nginx}_*.sh` were deleted (`44926c3`), five pipeline/example files went with them, and FIN-11's `uninstall_kubementat.sh` was already out of this count. |
| Shell scripts in the whole repo | **41** files / **2,273** LOC (was 42 / 2,540) | Same deletions. Nothing new was added; the harness is not product and is not counted here. |
| Python under `src/` | **20** files / **5,866** LOC (was 5,461) | `TektonUtils.setup_triggers()` with its named RBAC constants and builders (FIN-13), plus the per-document `apiVersion`/`kind` resolution `apply_resources` had to grow to apply a mixed-kind directory. Above that: `TektonUtils.install()` and its release-URL table, `ResourceWithoutNameError` and the recursive resource walk, `user_management.py` (296 LOC, FIN-30…33), `wait_namespaces_gone` / `namespace_phases` on `KubernetesUtils`, `_export_component_namespaces` and `_grafana_admin_credentials` on `HelmUtils`, `Config.teams()`. |
| `kmt` commands | **20** (`uv run kmt --help`) | Unchanged by FIN-13: `kmt tekton-setup-triggers` already existed and now runs Python instead of a script. `kmt users …` landed under FIN-62's `users` group; FIN-12 added none. |
| CLI commands that shell out | **0** — no `kmt` command starts a kubementat script, **and as of FIN-06 (`e6b3afd`) there is no longer an exception that takes the path from the operator**. The sentence this row carried ("except `kmt run-script`") was the last of its kind and is false now; the rule is asserted off the registered surface and not remembered | FIN-13 deleted `setup_triggers.sh`; `run-ci-container` is Python (`CiUtils`, FIN-16); and the Linkerd install `--enable-linkerd` used to reach is deleted with the mesh (owner decision 2026-10-28). FIN-14's half of this row closes without a port because there is nothing left to port. |
| Python unit tests | **432 passed**, ~16 s (was 388) — see §1.3b row 5a: 433 was measured over a tree that had two Linkerd suites and four cases over the deleted `run_kubementat_script` helper. The Linkerd suites are deleted with the mesh and replaced by the present/absent pair in `test_uninstall_sweeps_everything_kubementat_owns.py`; the helper's cases are gone because the helper is gone, and the check they protected (every `.sh` name in `src/` still has a file behind it) is still asserted in `test_command_execution_policy.py`. | +15 for FIN-13: 11 over `TektonUtils.setup_triggers()`'s branches and 4 over the record of the deleted script (the 8 RBAC objects, the two webhook Secrets, and which kinds go to which API group). +1 new rule in the parity reducer, because a binding's claim is its subjects and its roleRef and the harness had no rule for those kinds. Still no cluster needed. |
| Full lane | **93 asserts / 4 red** — `~/.kmt-e2e/runs/20261028T061713Z-full-badcf40`, 29 min | Four lanes since the 16-red baseline: `3e8b3f6` 89/13, `7269c9a` 93/12, `021bdaa` 93/5, `16c8196` 93/3, `badcf40` 93/4. §1.3c says what the four reds are. |
| Manifests on `tekton.dev/v1beta1` | **0** product manifests; 6 trigger generator templates on `triggers.tekton.dev/v1beta1` | Unchanged — that is what Triggers 0.37.2 serves (§1.6). |

### 1.2 What is genuinely done

- **Initialization** (`kmt initialize`) is fully Python: config template rendering, git-crypt init/gpg,
  deployer SSH key generation, directory scaffolding — `Automation.initialize()` + private helpers
  (`lib/kubementat/automation.py:87-381`).
- **Setup pipelines / secrets / docker-registry access** are Python and are what the CLI actually calls
  (`setup_pipelines`, `setup_secrets`, `setup_docker_registry_access`, `uninstall_pipelines`).
- **Pipeline execution, listing, cleanup** are Python (`TektonUtils.run_pipeline`, `list_all_tekton_resources`,
  `cleanup_pipeline_runs`).
- **Tunnels** grafana/tekton/polaris/vault/pod are Python (`Automation.open_tunnel_*`, `KubernetesUtils.open_tunnel`).
- **Helmfile apply** is Python (`HelmUtils.helmfile_apply`), used by `kmt helmfile-apply` and `install_kubementat`.
  Since this ticket it is also *scriptable*: the library takes `interactive: bool = False` and appends
  `--interactive` only when it is handed True, and the terminal decision lives in `kmt helmfile-apply`
  (`src/cli/commands/helm.py`) — ask when stdin is a tty, do not ask when it is not, `--yes`/`-y` for
  both cases. `uv run pytest -q tests/test_helmfile_apply_asks_only_on_a_terminal.py` → 10 passed;
  `PATH=<fake-helmfile> uv run kmt helmfile-apply dev --helmfile-label-filter group=routing < /dev/null`
  → exit 0 with no `--interactive` in argv. The `helm.helmfile-apply` lane row now passes `-y`, so the
  row applies instead of prompting — **the row itself still needs one green full-lane run** (TODOs.md "Now" 1).
- **Team pipelines/tasks/pipeline-runs** already live under `platform_config/{env}/{team}/tekton/…`
  (commit `fa0aa12`), consumed via `Config.tekton_team_*_dir`. **Triggers were left behind** — see §1.5.

- **Uninstall** is Python end to end (`Automation.uninstall_kubementat`, FIN-11). Namespace deletes are
  *waited for* now: `KubernetesUtils.wait_namespaces_gone` polls a bounded ceiling and a survivor is
  reported with the phase the cluster gives it, because a watch log measured 42 s of drain against the
  ~4 s the command used to report on. The residue asserts (`no-extra-namespaces`, `no-kubementat-releases`,
  `no-kubementat-claims`, `no-tekton-crds`, `no-helm-deployer-clusterrole`) are green on three consecutive
  lanes, and uninstall survives a `platform_config/{env}/<not-a-team>/` directory without giving up.
- **Team accounts** are Python (`src/kubementat/infrastructure/user_management.py`, 296 LOC) behind
  `kmt users create-team-account` / `create-team-account-read-only` / `delete-team-account`. The kubeconfig
  is a dict and `yaml.safe_dump`, `check_cluster_and_access` runs once, and the five bash implementations —
  including the duplicate that carried its own cluster check — are deleted. The eight lane rows that used to
  be *absent* are green.
- **Namespaces are configuration on both sides now.** Readers go through `Config.component_namespace`;
  `HelmUtils._export_component_namespaces` exports the same set into the environment `helmfile` evaluates,
  and `helmfile.yaml.gotmpl` spells each one `env "<KEY>_DEPLOYMENT_NAMESPACE" | default "<old literal>"`. The
  caveat belongs here rather than under a ticket: renaming the namespace of a release that owns
  **cluster-scoped** objects fails on helm's ownership annotation, so "namespaces are configuration" is true
  for everything except a live rename. TODOs item 8 carries that as a decision to make.
- **Grafana's admin credentials have exactly one source**, `platform_config/{environment}/static.encrypted.json`.
  `HelmUtils._grafana_admin_credentials` reads it, requires `GRAFANA_ADMIN_USER`, and refuses to apply
  anything when it is missing — the template's header no longer advertises an `export` that the code
  overrode, and no credential is handed to a child process as `None` any more.
- **Cluster addressing is Python and it is v2 work, not deferred work.** An environment is one cluster and
  `CLUSTER_KUBE_CONTEXT` says which: `kubementat/cluster.py` carries `ClusterTarget`, `Context` and
  `EnvironmentRegistry`, the `current-context` fallback is deleted, and every client is built from the target
  its `Context` carries — the library-for-many-clusters that `research-kubementat-improvements-…-10-27-2026.md`
  row N7 called a prerequisite. `kmt environments list` answers "what environments do I have and what cluster
  is each one" without contacting a cluster. This was the row this plan deferred to its own plan
  (§1.3b, "Not scheduled, on purpose"); that deferral is closed, and the design is
  [`../specifications/cluster-config-v2.md`](../specifications/cluster-config-v2.md), implemented through
  `eca4c31`.
- **The backing-service install automation is gone**, not migrated (owner decision 2026-10-27). Kubementat
  currently ships no working mysql/redis/nginx install; the replacement charts are a separate decision and
  explicitly not this migration.

**That is why this plan exists:** ~80% of the *logic* is Python, but 55 scripts remain, 5 CLI commands are
bash passthroughs, and `install_kubementat` still delegates three of its steps to bash.

### 1.3 Defects found while auditing (each reproduced)

| ID | Defect | Evidence |
|---|---|---|
| **D1** | **The library is loaded twice as two unrelated module trees.** `kubementat.*` (via `sys.path.append("…/lib")` in every CLI module) *and* `lib.kubementat.*` (via `pyproject` `include = ["cli*","lib*"]`). Module state, constants and `logging.basicConfig` are duplicated per process. | `python -c "import sys; sys.path.append('lib'); import kubementat.kubernetes_utils as a, lib.kubementat.kubernetes_utils as b; print(a.KubernetesUtils is b.KubernetesUtils)"` → `False` |
| **D2** | `import kubementat` fails in the venv without the `sys.path` hack → the package is not installed correctly; `kmt` works by import-order accident. | `uv run python -c "import kubementat"` → `ModuleNotFoundError` |
| **D3** | `kmt tunnel-polaris` does not exist. `cli/commands/tunnels.py` defines `def tunnel_polaris` **twice**; the second (vault, `name='tunnel-vault-ui'`) wins, so Polaris is never registered. | `uv run kmt --help` → `tunnel-vault-ui` present, no `tunnel-polaris` |
| **D4** | `KubernetesUtils.create_or_replace_role` raises `NameError` on the "already exists" path (undefined `name` in the log line and in `replace_namespaced_role(name=name)`). Re-running a role-creating command on a cluster that already has the role crashes. | `lib/kubementat/kubernetes_utils.py:369-384` |
| **D5** | `get_role_binding` defined twice (`:415`, `:457`); the second shadows the first, so the documented `-> V1RoleBinding` return is dead code. | `grep -n "def get_role_binding" lib/kubementat/kubernetes_utils.py` |
| **D6** | `Config._load_config` raises `UnboundLocalError` when team-level static files are missing (warning is logged, then the unbound names are returned). | `lib/kubementat/config.py:139-146` |
| ~~**D7**~~ | ~~`kmt run-script` scans `tekton_ci/automation/*.sh`, which does not exist → that half of the listing is always empty.~~ **CLOSED 2026-10-29 by FIN-06 (`e6b3afd`)**, deleted and not repaired: there is no listing left to be half-empty and no command that will execute a path an operator typed. | ~~`cli/commands/utilities.py:36`; `uv run kmt run-script`~~ |
| **D8** | `subprocess.check_call(f"{script_path} {environment} {team}", shell=True)` — argument injection / breakage on any team name with spaces or shell metacharacters, plus `os.chdir()` of the whole process. | `cli/commands/kubementat.py:25`, `tekton.py:29`, `tunnels.py:27`, `utilities.py:22,60` |
| **D9** | Team `dev1` hardcoded in the CLI → `kmt tunnel-grafana` silently reads the wrong team's config. | `cli/commands/tunnels.py:39,55,68,81`, `helm.py:19` |
| **D10** | `logging.basicConfig()` runs at `import kubementat.config` time, i.e. for every library consumer. | `lib/kubementat/config.py:6-11` |
| **D11** | `kubementat_main_dir = Path(__file__).parent.parent.parent` is correct from a checkout but resolves into site-packages for a real install; no `KUBEMENTAT_HOME` override. | `lib/kubementat/config.py:76` |
| **D12** | `KUBEMENTAT_DEPENDENCIES` still requires `jq` and `yq` — the two tools this migration exists to remove. | `lib/kubementat/automation.py:31-43` (now `src/kubementat/automation.py:162-173`, and `yq` is out of it — see §1.3a's D12 row) |
| **D13** | `Automation.apply_resources()` (the reusable "apply all YAML in a dir" primitive that Wave 1/2 tickets will lean on) only globs `*.yml`, is non-recursive, skips nothing else, and dereferences `resource['metadata']['name']` unguarded. | `lib/kubementat/automation.py:686-734` |

### 1.3a Defect and ticket status (verified, not remembered)

One table that answers "what is still to do" without reading 60 ticket rows. Statuses below
are from commands run against `python_refactor_2`, not from a memory of a previous session:
`DONE` = verified by the command in the Evidence column, `OPEN` = the command still reproduces
it, `PARTIAL` = fixed in one place and not the others.

| Defect | Status | Evidence / what closes it |
|---|---|---|
| D1, D2 (package imported twice / `import kubementat` fails) | **DONE** | `uv run python -c "import kubementat"` -> exit 0; `import lib.kubementat` -> `ModuleNotFoundError`. Locked by `tests/test_import_hygiene.py`. Closed by FIN-01 (FIN-08's dead-file cleanup is housekeeping, not part of D1/D2, and landed 2026-10-27). |
| D3 (`tunnel-polaris` not registered) | **DONE** | `uv run kmt --help` lists `tunnel-polaris`. The duplicate `def tunnel_polaris` is gone and the file says why. |
| D4 (`create_or_replace_role` NameError on the 409 path) | **DONE** | FIN-04: the method is now `(self, namespace, name, body)`, so the 409 branch has a name to log and to replace by. Its one caller was the `create_team_account` stub, which FIN-08 deleted. **FIN-30/31 arrived and took it back**: `user_management.py` is the caller now, so the primitive is not caller-less. Pinned by `tests/test_kubernetes_resource_helpers.py::test_a_role_that_already_exists_is_replaced_and_not_recreated`. |
| D5 (`get_role_binding` defined twice) | **DONE** | `grep -c "def get_role_binding" src/kubementat/kubernetes_utils.py` -> 1. The shadowing copy that answered a 404 with `None` is gone, so the documented `-> V1RoleBinding` is the only contract; a caller that wants the missing case asks for it. Pinned by `::test_role_binding_lookup_is_defined_once_so_the_documented_return_is_the_real_one`. |
| D6 (`UnboundLocalError` on missing team config) | **DONE** | `d6148f0`; `uv run kmt tekton-list dev dev1` -> `Error: No team_static config found at .../dev/dev1/static.json`, no traceback. `tests/test_config_missing_team_config.py`. The rest of FIN-05 landed alongside it: `KUBEMENTAT_HOME`, D10 and `tekton_team_trigger_dir` are all closed — `grep -rn KUBEMENTAT_HOME src/` finds code (`config.py:25,62`, `kmt.py:30`) and `uv run pytest -q tests/test_config_path_resolution.py` -> 15 passed. |
| D7 (`run-script` listing half-empty, execution takes any path) | **CLOSED 2026-10-29 by FIN-06 (`e6b3afd`)** — not fixed, deleted. The defect died the way the plan said it would: the verb that globbed `tekton_ci/automation/*.sh` and that would execute any path is gone, so neither half of D7 has a subject. The plan's note that it "dies with `kmt run-script` in FIN-60" was right about the shape and wrong about the ticket: FIN-60 landed without touching the verb, and FIN-06 is what closed it. |
| D8 (`shell=True`) | **DONE** | `grep -rn "shell=True" src --include="*.py"` -> 0 hits in code (the only mentions are prose in `src/kubementat/shell.py`); `subprocess` is imported by `src/kubementat/shell.py` alone. FIN-02 landed: `src/kubementat/shell.py::run` is the one primitive, the 4 remaining sites are argv, the 4 `os.chdir` calls became `cwd=`, and the 3 private `run_script` copies are `cli/helpers.py::run_kubementat_script`. Locked by `tests/test_shell_run.py` + `tests/test_command_execution_policy.py`. FIN-03 closed the part that row used to call OPEN: `tests/shims/` is repo-wide now, so a script that invents a command shows up in a parity failure rather than in a production incident. |
| D9 (`dev1` hardcoded) | **DONE** | `3697647`; `--team` (envvar `TEAM`, default `dev1`) on `helmfile-apply` + the four `tunnel-*` commands, shared as `cli/helpers.py::team_option`. `tests/test_team_option.py`. **The namespaces the tunnel commands select are no longer hardcoded either** — FIN-07 moved them to `*_DEPLOYMENT_NAMESPACE` in `static.json` (`Config.component_namespace()`), so `kmt tunnel-*` follows a renamed namespace now. That closes the loose end this row used to carry. |
| D10 (`logging.basicConfig` at import) | **DONE** | FIN-05: `src/kubementat/config.py` no longer calls it, `cli.kmt:main` calls `configure_logging()` on the way in. `uv run python -c "import logging, kubementat.config; print(len(logging.getLogger().handlers))"` -> `0`; pinned by `tests/test_config_path_resolution.py`. The CLI's INFO output is unchanged — `uv run kmt tekton-list dev dev1` still logs the same lines. |
| D11 (`kubementat_main_dir` resolves into site-packages, no override) | **DONE** | FIN-05: `Config.kubementat_main_dir = KUBEMENTAT_HOME or Path(__file__).parents[2]` (and the CLI resolves it the same way through `default_kubementat_main_dir`, so there is one mechanism, not two). `KUBEMENTAT_HOME=/somewhere uv run python -c "from kubementat.config import Config; print(Config('dev','dev1',preload_configs=False).kubementat_main_dir)"` -> `/somewhere`; `platform_config_dir` has the same shape via `KUBEMENTAT_PLATFORM_CONFIG_DIR`. Pinned by `tests/test_config_path_resolution.py`. |
| D12 (`jq`/`yq` still required) | **CLOSED 2026-10-29 by FIN-09 / D12 — half the row is a decision now, not an open defect** | `yq` is out of `KUBEMENTAT_DEPENDENCIES` (`src/kubementat/automation.py:162-173`): measured `grep -rl '\byq\b' scripts | wc -l` → **0**, and no argv in `src/**/*.py` puts it in command position, so nothing that runs needs it. **`jq` is kept deliberately** — no `src/` code path execs it, but `scripts/utilities/kubernetes/retrieve_node_addresses.sh` ships and pipes `kubectl get nodes -o=json` through it, and the check covers the distribution and not just the code; dropping it would turn a missing `jq` into a `bash: command not found` inside a script the operator was told to run. If that script goes (FIN-44/45), `jq` goes with it. Pinned by `tests/test_the_declared_dependencies_are_what_the_tree_needs.py`, which re-derives the list from the tree instead of restating it and fails both when `yq` is put back and when `jq` is removed while the script ships. What the guard cannot see: an argv assembled out of literal text, and what the CI image installs (`Dockerfile` still installs `yq` — another workstream's inventory). |
| D13 (`apply_resources` only globs `*.yml`, unguarded `metadata['name']`) | **DONE** | FIN-12. `automation.py` walks `**/*.yml` **and** `**/*.yaml` in a stable sorted order, raises `FileNotFoundError` naming the path when the directory is not there, skips an empty or non-mapping document with a log line, and raises `ResourceWithoutNameError` (file, document number, apiVersion, kind) instead of a `KeyError`. One directory is excluded by name: `templates/` holds generator inputs, and applying `tekton_ci/pipelines/templates/template-deploy-pipeline.yml` would create a resource called `deploy-pipeline-APP_NAME_PLACEHOLDER`. Pinned by `tests/test_applying_a_directory_applies_what_is_actually_in_it.py` (15 cases, including a check of the two real `tekton_ci/` roots this primitive applies). **What a lane still owes**: that a real `kmt tekton-setup-pipelines` still applies every Task and Pipeline in a live cluster; nothing here was executed against one. |

Three corrections, because they cost time:

- **FIN-46 means two things.** Wave 4's FIN-46 used to be `open_mysql_tunnel.sh` +
  `open_linkerd_dashboard_tunnel.sh`; only the mysql half is still **OPEN** (no
  `Automation.open_tunnel_mysql`), because the linkerd tunnel script is deleted with the mesh
  (owner decision 2026-10-28). `open_tunnel_linkerd` is out of scope for good, not unclaimed.
  The suite's capability comment ("the generic entry point is slated for deletion (FIN-46 / FIN-60)")
  used the same id for deleting `kmt run-script`; the path fix in `43990cb` carries that label.
  The run-script work belongs under **FIN-06** (dedupe `run_script`, D7/D8) with **FIN-02** for the argv
  rule, and is deleted by **FIN-60**. Use those ids from now on.
- **Two tickets are further along than their rows read.** FIN-11: `--yes`/`-y` exists (`8392889`) and
  `kmt uninstall` is Python since `213bc9b` — `uninstall_kubementat.sh` is deleted, the namespaces come from
  `Config`/`constants` rather than fourteen keys and two team names, and the PVCs are deleted instead of
  announced. **The lane proof has since arrived**: the residue asserts are green on `021bdaa`, `16c8196` and
  `badcf40`, and uninstall waits for the namespaces it deleted (§1.3c). FIN-62: the capability table
  carries a `fin62` marker on every command that still needs the grouped form, so that row is the
  authoritative count of what is left.
- **FIN-01's "everything else is a path-only change" is only half true.** `lib/kubementat` moved to the
  same depth as before, so `Config.kubementat_main_dir` (three levels up from `config.py`) is untouched —
  but `cli` moved from `<repo>/cli` to `<repo>/src/cli`, one level deeper. `cli/kmt.py:Config` computes the
  root from `__file__`, and every `run_script` resolves `<root>/scripts/...` and then `chdir`s into it, so
  the one-level mistake surfaces as a `FileNotFoundError` on a script path, not as an import error. Fixed
  in the same commit (`os.path.join(home, '..', '..')`) and pinned by
  `tests/test_import_hygiene.py::test_cli_root_is_the_repo_root`. FIN-05's `KUBEMENTAT_HOME` replaces this
  derivation entirely, which is the real answer — it landed that way: `cli.kmt.Config` now calls
  `kubementat.config.default_kubementat_main_dir()` instead of counting levels off its own `__file__`.

### 1.3b Remaining work, in execution order

Measured 2026-10-28 against `python_refactor_2` @ `badcf40`. Everything above this line is history; this is
the part that is still work. **The shape of the problem changed: four full lanes have run,** so a row can
cite a real cluster result instead of a unit suite, and where the two disagree the lane wins. Two tickets
this table used to call open are closed and verified by a lane (`FIN-11`, `FIN-30…FIN-33`). Read it as *do 1
before 2, 2 before 3*, and the rest in whatever order the worktrees allow.

| # | Ticket | What it actually is | Blocked by / blocked nothing | Effort |
|---|---|---|---|---|
| 1 | **FIN-50** | `kmt` importable inside a CI step. The design decision (runtime `uv run` from the cloned repo, image carries only `uv`) is already made. **The image work landed** (`ace958d`/`2f3b52f`), which removes this ticket's blocker — but nothing has been run against a pipeline pod since, so it is *unverified*, not verified. | Blocked by nothing now. **Unblocks 2 things nobody can otherwise delete.** | 8 h |
| 2 | **FIN-51** | The 4 CI manifests call `kmt` instead of `scripts/*.sh`. | After FIN-50. Deletes `scripts/tasks/**` and `create_secret_from_json.sh`. | 6 h |
| 2a | — | `run_pipeline.sh` — one of FIN-60's 4 held-back scripts, and the only one held back by **infrastructure** rather than by a missing `kmt` command: `tekton_ci/tasks/run-tekton-pipeline.yml:80` sourced it inside a pod with `python3` but no `uv`/`kmt`, so its parity test was also the only one that still ran live bash. **Closed 2026-10-29 by FIN-51b (`85f1ea8`)** — deleted, the Task is one step running `uv run --locked --no-dev kmt … --wait` with a declared `workingDir`, and the parity test records the bash blob instead of executing it. | | FIN-50. Not a separate ticket: it is the proof that FIN-50 worked. (The other three — `generate_ssh_key.sh`, `retrieve_node_addresses.sh`, `view_cluster_resource_versions.sh` — wait on FIN-44/45/62 to have somewhere to land.) | — |
| 2b | **— (new, no ticket)** | **Renaming a component namespace does not move the release.** `helmfile.yaml.gotmpl` reads the namespace from the config and `HelmUtils` exports it, so every reader follows a rename; helm refuses to re-install a release that owns cluster-scoped objects into the new namespace (`meta.helm.sh/release-namespace` ownership), and three lane rows are red on exactly that. It needs a **decision**, not code: rename-is-not-live (say so in the assert and the CHANGELOG) or rename-comes-with-a-release-scoped-uninstall (build it). Evidence: TODOs item 8, `20261028T061713Z-full-badcf40`. | Blocked by nothing. Blocks the three rename/signature rows from ever going green. | 2 h to decide, 0–4 h to implement |
| 3 | **FIN-30…FIN-33** | Team accounts in Python. **Landed and lane-green.** `src/kubementat/infrastructure/user_management.py` (296 LOC) ports the 14-function helper, `kmt users create-team-account` / `create-team-account-read-only` / `delete-team-account` are the CLI, the kubeconfig is a dict + `yaml.safe_dump`, and 5 bash scripts are deleted. | Nothing outstanding. **A lane still owes it one thing**: it is green, and that is the proof — 8 asserts, three lanes running. | 14 h — **done** |
| 4 | **FIN-11** | `kmt uninstall` as Python. **Landed `213bc9b`, and it waits now**: namespace deletes are polled to completion (`wait_namespaces_gone`, bounded), survivors are named with their phase and count as residue, `DONE` is withheld over an unfinished deletion, and a directory under the environment that is not a team is swept for what it declares instead of aborting the whole command. | Nothing outstanding — the five residue asserts are green on `021bdaa`, `16c8196` and `badcf40`. | 5 h — **done** |
| 5 | **FIN-12 / FIN-13 / FIN-14** | `TektonUtils.install()` and `TektonUtils.setup_triggers()` **landed** (see §5's FIN-12 and FIN-13 rows) — Linkerd install/uninstall is the last one. | **FIN-13 landed and its half of this row is closed**: the RBAC, the webhook Secrets and the team's trigger manifests are written by Python, and generated triggers are read from `platform_config/{env}/{team}/triggers/` (decision 2). What that moved into FIN-23's way is gone: **FIN-23 landed**, so generation and reading are both Python reading `Config.tekton_team_trigger_dir` (row 6d). FIN-14 blocks nothing. | 13 h — **12 h done** |
| 5a | **LINKERD-REMOVE** (no FIN id — the removal the owner's 2026-10-28 decision asked for) | Linkerd is out of the product, not migrated: `install_linkerd`/`uninstall_linkerd`, their constants and exceptions are gone from `Automation`, `--enable-linkerd/--disable-linkerd` are gone from `kmt install` with no alias and no warning, and the mesh's four scripts (`install_linkerd`, `uninstall_linkerd`, `generate_linkerd_certs`, `open_linkerd_dashboard_tunnel`), its `viz_config` template, its `LINKERD_*` config keys and its two `constants.DEFAULT_NAMESPACES` entries are deleted. `uninstall` grew a step that sweeps a mesh the cluster still carries. **State: implemented, unit-verified.** Evidence: `uv run pytest -q` -> 432 passed; `uv run kmt install --help` shows neither flag; `grep -ril linkerd src tests scripts templates tekton_ci examples` (minus `__pycache__`) hits only the removal step's own prose. Two cases in `test_uninstall_sweeps_everything_kubementat_owns.py` say so: Linkerd present -> its CRDs are deleted before its two namespaces and no `linkerd` binary is ever started; Linkerd absent -> one log line, nothing deleted, no residue. | Blocked by nothing. **What a lane owes**: (1) a cluster that really had the mesh — `kmt uninstall --yes` must leave no `linkerd.io` definition and no `linkerd`/`linkerd-viz` namespace; the CRD-before-namespace ordering is asserted only against fake `kubectl`, so a real namespace stuck in `Terminating` is the half this suite cannot prove; (2) that the mesh's absence is not a regression — the full lane's provision baseline must not grow a red row; (3) the CI image — `docker_files/ci_images/ubuntu_ci_minimal/Dockerfile` still installs the `linkerd` CLI and its `test.sh` asserts it, and that file belongs to another workstream, so the change there is **owed, not made here**. | 2 h — **done in code, lane owed** |
| 5b | **INGRESS-REMOVE** (no FIN id — the ingress-controller removal the owner's 2026-10-28 decision asked for; **it is not the ClusterIssuer half**, which stays under FIN-17) | nginx-ingress is out of the product, not migrated: its release and repository are gone from `helmfile.yaml.gotmpl`, its values template, its `NGINX_INGRESS_DEPLOYMENT_NAMESPACE` key and its `constants.DEFAULT_NAMESPACES` entry are deleted, and **nothing was built for what might replace it** — Traefik is an undecided question, not a placeholder. `install_routing.sh` survives, applies `group=routing`, and that group is cert-manager alone. `uninstall` grew a step that sweeps a controller the cluster still carries. **State: implemented, unit-verified.** Evidence: `uv run pytest -q` → **433 passed** (the template-key case the removed key parametrised is gone, −1; the controller's present/absent pair is new, +2); `uv run kmt --help` lists **20** commands, none new; `grep -rn "nginx-ingress\|NGINX_INGRESS" src templates` hits only the removal step's prose and its two constants; `grep -ril nginx scripts tests tekton_ci examples docs | grep -v __pycache__` hits the demo app (`nginx-example`), a label-selector string in `test_command_execution_policy.py`, the deleted-backing-service prose in the harness, `create_ingress_yml.sh`'s annotations and `README_TEKTON.md`'s dashboard example — all reported, none changed. Two cases in `test_uninstall_sweeps_everything_kubementat_owns.py` say so: controller present → its `IngressClass`es and release and namespace go, another controller's class survives, nothing named `nginx` is started; controller absent → one log line, nothing deleted, no residue. | Blocked by nothing. **What a lane owes**: (1) a cluster that really had the controller — `kmt uninstall --yes` must leave no `nginx-ingress` namespace, no release, and no `IngressClass` bound to `k8s.io/ingress-nginx`; the class-before-namespace order and the "read the release name off the cluster" branch are asserted only against fake `kubectl`/`helm`, so the real-cluster half is unproven here; (2) that a controller-free provision stays green — the baseline a lane provisions has no controller in it, so nothing should move, and that must be shown rather than assumed; (3) the **routing gap** itself — nothing in the lane installs an ingress any more, so every product path that used to be reachable through one (the `ClusterIssuer` HTTP01 solver, `create_ingress_yml.sh`, team `Ingress` manifests) is now only reachable on a cluster whose owner brought a controller. That is what the open decision has to answer. **2026-10-29: this row's open decision is answered, and only half the row is still owed.** The owner decided (`finish-python-migration-last-lap.md` §3.3) that Traefik takes the routing role, and FIN-17 landed it: `kmt routing install` / `kmt routing status`, the `traefik` release block in `helmfile.yaml.gotmpl:196-207` pinned to `42.1.3`, the values template under `templates/environment/kubementat_components/traefik/`, and `install_routing.sh` deleted — so the sentence above about nothing being built for a replacement is now history, and so is "that group is cert-manager alone". Of the three things this row said a lane owes: **(3) the routing gap is closed** — a product path that used to be reachable through an Ingress is reachable again, on a cluster that ran one command, and the routing lane proves it (`20261028T205357Z-routing-full-47b186f`, 24 routing asserts green). **(1) and (2) are still owed exactly as written** — a cluster that really carried nginx-ingress coming off clean, and a controller-free provision staying green. Both are still fake-client-proven, both belong to a lane, and neither is paid by the routing run. Keep this row as the removal's record; read FIN-17's Wave 2 row for what went in instead. | 2 h — **removal done in code, cluster half owed; the replacement landed and is not this row's** |
| 6 | **FIN-17…FIN-25** | Components and generators: the letsencrypt ClusterIssuers (the routing half of FIN-17 is withdrawn — row 5b), the dashboard pair, `HelmUtils.uninstall_release`, the three generators stamping `apiVersion` from `Config.TEKTON_API_VERSION` (already `v1`). The cleanup CronJob is **done** and moved out of this row into 6a. | FIN-04/05/07 landed. FIN-19/20/30 carried the last of the bash namespace values — FIN-46 deleted the pair that took them as arguments, and the re-measurement is in §9's namespace bullet: 4 assignments left, 3 of them config reads, 0 literals. | 23 h |
| 6a | **FIN-21** (the cleanup CronJob, and the last namespace the code remembered for itself) | `TektonUtils.setup_cleanup_job()` creates the namespace, the registry pull Secret, the ServiceAccount and its `imagePullSecrets`, the ClusterRole and ClusterRoleBinding that let the job delete `pipelineruns`, and the CronJob — seven create-or-replace operations in the order the script ran them, none of them `kubectl`. `kmt tekton-setup-cleanup-job <env> --team <team>` is the CLI, `scripts/automation/tekton/setup_tekton_pipelinerun_cleanup_job.sh` (220 LOC) is deleted, and the harness's `task.cleanup-job` row calls the command instead of `run-script`. **The namespace is the ticket's reason to exist**: it comes from `Config.component_namespace("cluster-cronjobs")` — `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE`, the key install deploys into and uninstall sweeps — where bash had `CRONJOB_NAMESPACE="cluster-cronjobs"` as its own constant. That is why two asserts were red: after a rename the job was created in one namespace and looked for in another. **State: implemented-unverified / verified-by-unit-suite / lane-required.** Evidence: `uv run pytest -q` → **439 passed** (433 before; +7 over this behaviour, −1 because the harness's cleanup-job row is no longer a `run-script` row that a case parametrises), and `tests/test_the_cleanup_job_is_created_in_the_namespace_the_config_names.py` → 7, one of which renames `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE` and asserts that the CronJob, the ServiceAccount, the namespace it is created in and the answer `Config` gives all move together; one compares the seven operations against the deleted heredocs read out of `git show 6d55f7d:scripts/automation/tekton/setup_tekton_pipelinerun_cleanup_job.sh`, payload included (schedule, both history limits, `concurrencyPolicy`, the pod labels, the image, the whole prune script); one answers 409 to every create and asserts the run replaces instead of failing. `uv run kmt --help` → **21** commands, one new, no alias. `grep -rn "setup_tekton_pipelinerun_cleanup_job" src tests README.md` hits only the blob citation and the guard that says it stays deleted. | Blocked by nothing. **What a lane owes**: (1) `assert:cleanup-cronjob-exists` and `assert:cleanup-cronjob-scheduled` on a cluster that really ran the command — every object here is written against a recording client, so "the API server accepted a batch/v1 CronJob sent through the custom-objects path and schedules it" is still a hypothesis; (2) `no-extra-namespaces` after uninstall, which is the same namespace seen from the other side; (3) that the job's pod pulls the CI image through the Secret this creates — a private registry is the case that fails quietly, and the fixture's credential block is a dict, not a registry; (4) that re-running the command on a cluster that has had the job for months changes nothing (the 409 branch is proven against a fake client, not a real one). | 4 h — **done in code, lane owed** |
| 6b | **FIN-19** (`HelmUtils.uninstall_release`, and the first command kubementat ever had for one component) | `kmt helm-component-uninstall <env> <component>` takes **one** helm component off a cluster: the namespace from `Config.component_namespace(component)`, the release **by the name the cluster reports** (`helm list --all-namespaces --all`, because a release that predates this repository's naming does not match a name constructed from a chart default), then the claims that release made, read by `app.kubernetes.io/instance=<release>` and deleted by name. `scripts/automation/components/uninstall_helm_deployment.sh` (58 LOC) is deleted, not wrapped. **The ticket is not a port**: the script had no caller in `src/` at all — its only mentions were documentation — so kubementat has never had a way to uninstall one helm component, and what landed is a command that did not exist. Two absences are normal outcomes and not warnings (release not present, release that made no claims); only a `helm uninstall` or `kubectl delete pvc` that failed for a real reason raises, and it raises **after** leaving the failed release's claims alone, because claims of a release that is still installed are that release's data. The claim half is also FR-61's advice-only failure (`docs/plans/testsuite-requirements.md:289`), now closed the same way `uninstall_kubementat` closes it. **State: implemented-unverified / verified-by-unit-suite / lane-required.** Evidence: `uv run pytest -q` → **452 passed** (439 before, +13), `uv run kmt --help` → **22** commands, one new, no alias and no `helm` subgroup; `tests/test_a_helm_component_is_uninstalled_from_the_namespace_the_config_names.py` → 13, one of which renames `POLARIS_DEPLOYMENT_NAMESPACE` and asserts the release, the claims and the namespace they are addressed in all move together, one compares what came off the cluster against the deleted script read out of `git show b2d471a:scripts/automation/components/uninstall_helm_deployment.sh` — where the script's claim half deletes **nothing**, because it is an `echo` line — and one runs the command as its own process so the surface and the `--team` plumbing are the real ones. `grep -rn uninstall_helm_deployment src tests docs README.md` hits only the blob citation, this row, and two plan rows that record the deletion. | Blocked by FIN-07, which landed. **What a lane owes**: (1) the command against a real release — `helm list`'s `--all` branch, a release somebody renamed at install time, and a namespace the cluster is still draining are all asserted against fake `helm`/`kubectl` only; (2) that the claim sweep really goes — a claim with a retained PV behind it is the case that fails quietly, and the fake answers with a dict; (3) `no-extra-namespaces` + the PVC assert after a component uninstall that is not a platform uninstall, which is a state the lane harness has never built before; (4) **the rename question 2b raises, from the other side**: if the answer is "rename comes with a release-scoped uninstall", this command is the half that would be used, and 2b's decision has to say whether it is allowed to delete claims. | 2 h — **done in code, lane owed** |
| 6c | **FIN-36** (`kmt service-account-token`, and the first command for reading a token that kubementat ever had) | `KubernetesUtils.get_service_account_token()` shipped with FIN-04 and had no caller, which is exactly the shape FIN-19's row has: nothing in `src/`, `tests/`, `tekton_ci/` or `README.md` reached `scripts/secret_management/retrieve_token_for_service_account.sh` (25 LOC), so what lands is a command and not a port. `kmt service-account-token <NAMESPACE> <SERVICE-ACCOUNT>` reads and prints the token and nothing else — a credential on stdout has to be redirectable, and the script's four lines of framing around it were not. The primitive learned one thing for this: a 404 from the Secret listing is now `NamespaceNotFoundError`, the conversion every other client read in that module already makes, because the namespace and the service account are different answers. **State: implemented-unverified / verified-by-unit-suite / lane-required.** Evidence: `uv run pytest -q` → **495 passed** (487 before, +8), `uv run kmt --help` → **29** commands, one new, no alias and no subgroup (`users` is still the only group); `tests/test_a_service_account_token_is_read_by_a_command.py` → 8, one of which compares the token against the deleted script read out of `git show 19b97d0:scripts/secret_management/retrieve_token_for_service_account.sh` — where the finding half is `grep "${SERVICE_ACCOUNT_NAME}-token"` and there is no `kubernetes.io/service-account.name` anywhere, so bash could only ever find a Secret that agreed with a naming convention, and the three states a token read meets on a cluster (found / no token secret / secret not filled in yet) are told apart, where the script answered two of them the same way and exited 0 for both. `grep -rn retrieve_token_for_service_account src tests README.md` hits only the blob citation and this row's two siblings. | Blocked by FIN-04, which landed 2026-10-28 alongside `create_k8s_secret_for_ssh_key.sh`, deleted in the same commit for the same reason and *not* migrated (§7). **What a lane owes**: (1) that a token minted for a real service account is printed byte-for-byte the way the script printed it — everything here is a recorder or a fake client, so the decode path has never touched a real `data.token`; (2) that the two typos (namespace, service account) come apart on a real cluster rather than both being a 404 the conversion guesses at; (3) that a deploy key declared only in `team_static_encrypted.SSH_DEPLOY_KEYS` still lands in its `TARGET_NAMESPACE` now the ssh-key script is gone, which is `kmt setup-secrets`' behaviour and has never been asserted against the deleted file. | 2 h — **done in code, lane owed** |
| 6d | **FIN-22 / FIN-23** (the two generators, and the first commands kubementat had for producing a team's own manifests) | `TektonUtils.generate_deploy_pipeline()` and `TektonUtils.generate_trigger_configs()` on `kubementat.tekton_utils`, behind `kmt tekton-generate-deploy-pipeline <env> <team> <app> [--git-url … --git-project-name … --git-server-host … --git-server-port … --git-server-ssh-user …]` and `kmt tekton-generate-triggers <env> <team> <app> --pipeline-name <name> [--trigger-type github\|gitlab]`. **Neither script had a caller**: `grep -rl generate_deploy_pipeline_from_template src tests/e2e tests/*.py tekton_ci README.md` answers nothing, nor does the same search for the trigger script, so both were reachable only by hand — and by hand at the right depth, because every path in them was `../../../tekton_ci/...`. **How it is generated**: `yaml.safe_load` of the template, substitution through the table `run_pipeline` already used (`_replace_placeholders_in_dict`, now table-driven via `CONFIG_PLACEHOLDER_SOURCES` and able to take the caller's own values), `yaml.safe_dump(..., sort_keys=False)` — no `sed`, no `envsubst`, no string templating, so key order and quoting are the template's own and a rerun writes the same bytes. `apiVersion` is stamped from `Config.TEKTON_API_VERSION` for the `tekton.dev` group only (an `Ingress` is not a Tekton object; the triggers group is `Config.TRIGGERS_API_VERSION` = `v1beta1`, which serves what Triggers 0.37.2 actually answers). **A placeholder with no value is an error and not a manifest**: `GenerationPlaceholderNotConfiguredError` names the key and the file it is read from, and nothing is written — the generator builds every document in memory before the first byte goes out, so even the directory is absent. bash's alternative was `sed` leaving `APP_GIT_SERVER_HOST_PLACEHOLDER` in a git URL and `jq -r` printing `null` into an image tag or an ingress host, both written, both reported as finished. Two rules the scripts had and this deliberately does not, stated because they look like losses: reruns **overwrite** (`check_file_exists`+`exit 1` meant the second run needed an `rm` first — the one after a review), and the templates' **comments do not reach the generated file**, so the review guidance the trigger templates leave in a comment is said by the command instead. Both output dirs are `Config` attributes, so nothing is built from a relative path: pipeline → `Config.tekton_team_pipeline_dir/` (**moved out of `tekton_ci/pipelines/`**, which is where `apply_resources`' `templates/` skip is what kept it honest), triggers → `Config.tekton_team_trigger_dir/<app>/`, where FIN-13 made the reader look. **State: implemented-unverified / verified-by-unit-suite / lane-required.** Evidence: `uv run pytest -q` → **592 passed** (572 before, +20), `uv run kmt --help` → **32** commands, two new, no aliases; `tests/test_generated_deploy_pipelines_and_trigger_configs_are_written_where_they_are_read.py` → 20, of which four run the deleted scripts out of `git show 07368d4:scripts/automation/tekton/{generate_deploy_pipeline_from_template,generate_trigger_config_from_templates}.sh` inside the same fixture checkout and compare the generated documents field by field, declaring the one place the two sides still differ (where the deploy pipeline is written); one stamps a stale `tekton.dev/v1beta1` into a template and asserts the generated file says `v1` while the `Ingress` keeps `networking.k8s.io/v1`; one removes `BASE_DOMAIN` from `static.json` and asserts the failure names it and that no file exists afterwards; two assert byte equality across two runs. `git check-ignore` says both output locations are visible to `git status` (see §1.5), and `find scripts -name '*.sh' \| wc -l` → **14 → 12**. | Blocked by FIN-05 (landed) and FIN-13 (landed). **What a lane owes**: (1) that the four generated trigger manifests and the generated pipeline really **apply** — every manifest here is compared to bash's own bytes and to the config that filled it, never to an API server, so "accepted by the cluster" is still a hypothesis; (2) `kmt tekton-setup-pipelines` picking a generated `deploy-pipeline-<app>.yml` up out of `platform_config/{env}/{team}/tekton/pipelines/` with the `templates/` directory *not* applied, which is the new half of the location rule; (3) a github and a gitlab webhook each firing the pipeline the `--pipeline-name` named, which is the only proof the four documents hang together; (4) that a generated manifest survives a `kmt uninstall` sweep, i.e. that the location change does not hide residue. | 5 h — **done in code, lane owed** |
| 7 | **FIN-40…FIN-46** | The 14 remaining `scripts/utilities/**`, then **`kmt run-script` deleted** — which is how D7 (still OPEN) finally dies. **Four of the seven rows are done: FIN-41, FIN-42 (three scripts, three commands) and FIN-44, FIN-45 (three more scripts, three more commands).** FIN-41/42 **State: implemented-unverified / verified-by-unit-suite / lane-required.** Evidence: `uv run pytest -q` → **464 passed** (452 before, +12: nine unit cases over the three reads — happy paths, deployment-missing, namespace-missing, metrics-server-absent, a metrics endpoint that answers 500 instead of 404, and the pods-of-the-namespace evidence half — and three parity cases that run the scripts' recorded blobs from `git show 3aac5cb:scripts/utilities/kubernetes/{analyze_deployment,cluster_capacity,retrieve_deployment_docker_image_versions}.sh` against the shims); `uv run kmt --help` → **25** commands, three new, no aliases; `grep -rn "analyze_deployment\|cluster_capacity\|retrieve_deployment_docker_image_versions" README.md scripts/ src/` hits only the new client methods and their CLI registrations. Two differences the parity tests declare instead of hide: a deployment is selected by `app.kubernetes.io/instance` (the release-name convention `stream_logs` and `helm_utils` both use) where the script used `app.kubernetes.io/name`, and logs are read per pod where bash read them by selector — the set of pods is what is compared, not the argv count. The harness rules this needed (`*_for_all_namespaces` reads, cluster-scoped custom-object reads) landed in the same commit and changed nothing that existed. **FIN-35 landed alongside on 2026-10-28 and took this row's last grafana caller off `run-script`**: the three `scripts/user_management/*grafana*.sh` are deleted, `tests/e2e/harness/config.py` drives `kmt users create-grafana-accounts`, and `run-script` no longer reaches grafana at all (§9). **FIN-44/45 State: implemented-unverified / verified-by-unit-suite / lane-required.** Evidence: `uv run pytest -q` → **487 passed** (~18 s; 464 before, +23: nineteen unit cases — `kmt deploy-ssh`/`deploy-logs` happy paths, the two not-captured assertions, container-named-as-its-own-flag, the release-label selector, a pod that is not there, a namespace that was never created (asserted on *both* commands, and on that no kubectl call happens at all), `kmt helm-version`'s happy read plus the two different sentences for an unadded repo and an unknown chart — and four parity cases, one of which is a case *against* the deleted script). `uv run kmt --help` → **28** commands, three new, no aliases. `grep -rln "ssh_into_helm_deployment\|tail_logs_for_helm_deployment\|get_latest_version_for_helm_chart" README.md docs/ scripts/` hits only the two plan rows that record the deletion and this file's own ledger line. The two sessions were read through `git show 9412bbe:scripts/utilities/helm/{ssh_into_helm_deployment,tail_logs_for_helm_deployment}.sh`, and what they declare is stated there rather than reduced away: `ssh_into_helm_deployment.sh` ran `kubectl -n dev1 get pods` and `kubectl exec -n "$1"`, so the lookup and the session were in two different namespaces — a defect the replacement could only survive by not keeping; and both scripts could not tell a namespace that does not exist from a deployment that is not installed, which is why both commands now read the namespace through the client first (the one read that bash never made). The third comparison is not a `compare` at all, and the file says why: a *helm* search reduces to a `run` carrying argv, so comparing it as a write would demand identical flags (`-l` there, `--output json` here) and both sides then get the same recorded answer, which the recorder cannot serve in two shapes; so it compares the question asked and shows the defect as an output difference — with a neighbouring chart in the table, `head -n 2 | tail -n 1 | awk '{print $2}'` printed **the neighbour's version** while `HelmUtils.get_latest_chart_version()` returned the one that was asked for. One new rule landed in `cluster_parity.py` for this (`exec`, which the harness had never been asked about: a session, not a mutation — the pod is named and the command is the payload), and it changed nothing existing. | Best done after FIN-50, so "CI still runs that" is not an excuse. Mechanical: the fan-out ticket. **What a lane still owes FIN-41/42**: (1) `cluster-capacity` agreeing with `kubectl describe node`'s Requests table on a real cluster — the quantity parsing (`500m`, `150000000n`, `128Mi`) is asserted against fixtures, and a node whose `allocatable` carries a surprise is the case that would show it; (2) a cluster **without** metrics-server answering without a traceback — the 404 branch is fake-client-only here; (3) `analyze-deployment` on a real CrashLooping pod, where the per-pod log read races the pod's own reschedule. **What a lane still owes FIN-44/45**: (1) `deploy-ssh` against a pod whose image really has `/bin/bash`, and — the branch that matters — against one that does not, because a distroless image is the case where kubectl's failure text is the whole answer and the CLI's one-line form of it is proven only against a fake `kubectl` that exits 1; (2) that neither session hangs when something *is* attached, which no recorder can prove: both commands were written against the rule that capturing them is a silent failure, and the assertion is that `capture=False` was handed to `shell.run`, not that a terminal was observed; (3) `deploy-logs` against a multi-container pod and against a namespace with several pods carrying the same release label, where `kubectl logs -l` reads more than one container and the `--container` half is fake-only here; (4) a real `helm search repo` index, so that the `--output json` shape this reads (`chart`, `version`) is the shape that helm actually emits on the version the lane installs — every chart-version answer in the suite comes from a recorded JSON body, and a helm that renames its own keys is the failure no unit test here would catch. | ~21 h — 12 h done |
| 8 | **FIN-09 / D12** | Remove `jq` (27 scripts) and `yq` (4) from `KUBEMENTAT_DEPENDENCIES`. | Nothing, until Waves 1-4 have migrated the scripts that call them. Early removal breaks scripts, not dependencies. | 1 h + whatever the scripts need |
| 8a | — | **FIN-09 / D12 landed 2026-10-29**, and the measured answer is not the row above. `yq`: gone — 0 scripts call it and no argv in `src/` names it. `jq`: **kept**, because `scripts/utilities/kubernetes/retrieve_node_addresses.sh` still ships and still calls it, and the check describes the distribution rather than only `src/`; deleting the script is FIN-44/45's call, so the tool stays until it does. `jq` (1 script, not 27) and `yq` (1 script's worth of callers, not 4) had already gone below the numbers this row carried before anything was touched. Evidence: `uv run pytest -q`, plus the new derived guard that fails on both mutations. | Blocked by nothing; it blocked nothing. | 1 h — **done** |
| 9 | **FIN-61 / FIN-62 / FIN-63** | Docs; the Click groups `kmt tekton|tunnel|users|helm component …` — renames, **no aliases**, so they must land with the commands they group; **CHANGELOG only — no version bump** (the owner bumps manually at the end of the refactor, ruling 2026-10-29). | The `fin62` markers in the harness capability table are the authoritative count of ungrouped commands. | 11 h |
| 10 | **Housekeeping** | Delete `src/cli/kmt` (§1.1 row above; note `src/cli/kmt` is **not** in the tree today — only `src/cli/kmt.py`, so this half may already be done and needs a look before it is claimed); CI pytest job (**FIN-10 — no CI runs any of these tests: the tree has no `.github/`, no `.tekton/` and no pipeline in `tekton_ci/` that mentions pytest, so this row is a request for CI and not a description of one. The owner deferred it; do not build it here.**); smoke lane attached per §8.4; the dashboard decision §1.8 B2. | Nothing. Purely unclaimed. | ~5 h |

**Not scheduled, on purpose:** externalizing `platform_config` to its own repository (decision 4 — do not
design for it yet, do not make it harder), ~~cluster-context/multi-cluster work~~, Flux/ArgoCD, the
dashboard replacement, and the backing-services rework (TODOs item 3). None of them is this migration.

**Update 2026-11-01/02 — one of these is off the list, and it is not because the promise was kept.**
Cluster-context/multi-cluster work is **in v2 and implemented through `eca4c31`**, in
[`../specifications/cluster-config-v2.md`](../specifications/cluster-config-v2.md). It was never a migration
task and it was never scheduled to be one: this row deferred it to a plan, the plan arrived on 2026-11-01,
and the fifteen commits that followed are why the row above now has a line through it. The other items are
unchanged — `platform_config` externalization is still decision 4's "not now", not a forgotten ticket.

### 1.3c What the four lanes measured (2026-10-28)

A lane result is only comparable to another lane result if the harness measured the same thing, and
three of the deltas below are harness measurement fixes rather than product changes. Read the
trajectory with that in mind — the assert count moved **88 → 89 → 93** because rows were added and
baseline measurement moved from `kmt install`'s output to the post-provision cluster, not because the
test suite grew by five.

| Lane | Asserts / red | What moved |
|---|---|---|
| `20261027T093845Z-full-d1cc5dc` | 88 / **16** | The baseline. Every fix below is measured against it. |
| `20261027T184758Z-full-3e8b3f6` | 89 / 13 | Step 0: the three measurement bugs. `no-extra-namespaces` was comparing against namespaces recorded *after* `kmt install`, so no residue could ever be reported; the tunnel stop was killing a subshell pid instead of the session leader, so ports stayed bound; the signature tables were asking for tokens the pages do not serve. |
| `20261027T230844Z-full-7269c9a` | 93 / 12 | Step 1: the mysql/redis/nginx automation deleted, which removed the four `NotFound`-on-docker.io rows. |
| `20261028T003348Z-full-021bdaa` | 93 / **5** | Steps 2–4: Python uninstall, team accounts, namespaces-as-config deploy half. Account asserts went from *absent* to green. |
| `20261028T025103Z-full-16c8196` | 93 / **3** | The drain wait landed, so `no-extra-namespaces` and the residue rows cleared. |
| `20261028T061713Z-full-badcf40` | 93 / 4 | Grafana's credentials moved to one source; that assert is gone, and the rename apply's verdict is recorded for the first time — which is what surfaced the real reason the rename is red. |

**What the four reds on `badcf40` are.** Two are one fact (`cap:helmfile-apply:standard-after-namespace-rename`
exit 1, and `namespace-rename-tunnel-still-correct`), one is that same fact seen from the probe
(`tunnel-signature:tunnel.grafana`, after renaming), and one is new and almost certainly a race:
the *plain* grafana signature row came back `body=empty` after being green on the lane before it.
The first three go away the moment the namespace-rename decision is made (TODOs item 8).

**Facts about the cluster that no unit test can give you, measured rather than assumed:**

- **Namespace deletion drains in ~42 s** on the lane's 8 vCPU guest, and nothing blocks it. `kmt uninstall`
  used to report ~4 s after firing the deletes, which is why the residue rows read as though the cluster
  was holding on to things. The fix is bounded polling, not a deletion re-order or a cascade change.
- **A release that owns cluster-scoped objects cannot be re-installed into a renamed namespace.** helm
  validates `meta.helm.sh/release-namespace` on import and refuses. This is why "namespaces are
  configuration" is not the same promise as "renames are free".
- **`helmfile` 1.7.0 has no `getEnv`**, and `requiredEnv` aborts the whole file, so the namespace
  template reads `env "<KEY>" | default "<literal>"`. Any other spelling fails the entire apply, not one
  release.
- **`cap:helmfile-apply:routing` is green on the last two lanes** (and was red before that). The
  traefik/nginx-ingress `hostPort` collision on the single-node guest is a race, not a fixed problem —
  k3s's traefik `svclb` and the routing group's nginx-ingress `svclb` both want 80/443. Treat a red there
  as a timing hit, and treat a lane that never hits it as luck.
- **The pinned bitnami images return `NotFound`** from docker.io, which is what made deleting the three
  backing-service automations cheaper than debugging them.

### 1.4 The CI-side blocker (the part most migration plans miss)

Four Tekton manifests executed `scripts/*.sh` **inside the pipeline pod** — not via `kmt`. Two of them
were deleted on 2026-10-27 (§2, "Update 27 Oct 2026") along with the scripts they ran; the two that
remain cannot simply be deleted, deleting them breaks CI.

| Manifest | Executes |
|---|---|
| `tekton_ci/tasks/run-tekton-pipeline.yml:80` | `source run_pipeline.sh …` (~70 LOC of bash that duplicates `TektonUtils.run_pipeline`, including its own polling loop) |
| `tekton_ci/tasks/create-secret-from-json.yml:47` | `scripts/secret_management/create_secret_from_json.sh` |
| ~~`tekton_ci/pipelines/setup-pipeline-mysql.yml:107,126`~~ | ~~`scripts/tasks/install_mysql_helm_chart.sh`, `scripts/tasks/setup_mysql_databases.sh`~~ — **deleted 2026-10-27** with the backing-service automation. `install_mysql_helm_chart.sh` is gone for good. `setup_mysql_databases.sh` survived that deletion as a task/trigger and not a deploy, and **went on
2026-10-29 with the Task that was its only route to a cluster** (FIN-51c, `0cf22b7`: it deleted
`tekton_ci/tasks/run-automation-script-for-environment.yml` plus the script, under the owner's rule
that a Task whose whole job is to execute a path you hand it goes); the two mysql Tasks, `create-mysql-database.yml`
and `execute-mysql-file.yml`, stay in the tree and stay applyable, run `mysql` inside their own step, and
never needed the script |
| ~~`examples/tekton_ci/pipelines/deploy-pipeline-nginx-reverse-proxy.yml:105`~~ | ~~`scripts/tasks/install_nginx_reverse_proxy.sh`~~ — **deleted 2026-10-27** with the backing-service automation |

The CI image (`docker_files/ci_images/ubuntu_ci_minimal/Dockerfile`) has `python3`/`python3-pip` but
**no `uv`, no `kmt`, and no baked project dependencies**, so "`uv run kmt …` inside the step" is not
available today. `TODOs.md` already logs it: *"bake python requirements into the ci base image so that kmt
will work without downloading dependencies for each task execution"*. **FIN-50 gates the `scripts/tasks/`
deletions.** `KUBEMENTAT_HOME` (FIN-05) is a hard prerequisite: in CI the repo is cloned into `/workspace/…`
while `kmt` would live in site-packages, so without it every config path resolves into site-packages (D11).

### 1.5 Trigger-config location (decision 2 in context)

- `tekton_ci/triggers/templates/{github,gitlab}/*.yml` — 8 tracked **generator templates** (stay, and
  stay the generator's only input).
- `tekton_ci/triggers/{smoke,dev1}/` — **generated output**, untracked (`git ls-files` shows only the 8
  templates). `.gitignore` no longer assumes that location: since FIN-13 it ignores everything under
  `tekton_ci/triggers/` except `templates/`, and `initialize` no longer creates `tekton_ci/triggers/{TEAM}/`.
- ~~`tests/reset_configuration.sh:21-22` deletes `tekton_ci/triggers/{smoke,dev1}`.~~ **That file is gone** —
  `a3d8865` dropped the three legacy test scripts along with it, so this line was already stale before
  FIN-13 landed; the `.gitignore` rule above is what keeps generated output out of the checkout now.
- `Config.tekton_team_trigger_dir` (`platform_config/{env}/{team}/triggers`) **is** what the flow reads:
  `TektonUtils.setup_triggers()` applies that directory and nothing else, so the hardcoded
  `setup_triggers.sh:267` `-f $dir` under `tekton_ci/triggers/{TEAM}` is gone with the script.

Moving generated triggers into `platform_config/{env}/{team}/triggers/` makes them **git-tracked platform
config**, consistent with commit `fa0aa12`. `initialize` already `makedirs(..., exist_ok=True)`s the
team's trigger directory (a sibling of the pipelines/tasks/pipeline-runs dirs it creates), and FIN-13
told the generator to write there too, so generation and reading agree. Since **FIN-23** the generator is
`TektonUtils.generate_trigger_configs()` behind `kmt tekton-generate-triggers` — the script that did it is
deleted, and both halves of that agreement are now Python reading the same `Config` attribute: the writer
writes `Config.tekton_team_trigger_dir/{app}/`, `_apply_team_trigger_resources()` applies
`Config.tekton_team_trigger_dir`, and nothing anywhere reads a `tekton_ci/triggers/{TEAM}/` path. Observed
on 2026-10-28 by running the command in a scratch checkout seeded from this repository's `.gitignore`
(`git status --short -uall` afterwards, five files, all untracked and none ignored):

```
?? platform_config/dev/demo/tekton/pipelines/deploy-pipeline-nginx-example.yml
?? platform_config/dev/demo/triggers/nginx-example/nginx-example-github-event-listener-ingress.yml
?? platform_config/dev/demo/triggers/nginx-example/nginx-example-github-event-listener.yml
?? platform_config/dev/demo/triggers/nginx-example/nginx-example-github-trigger-binding.yml
?? platform_config/dev/demo/triggers/nginx-example/nginx-example-github-trigger-template.yml
```

which is the point of the rule above: generated configuration is reviewed, so it is not ignored; the only
thing under `tekton_ci/triggers/` that is *not* ignored is the 8 templates, and the generators are the only
thing that reads them. (The same check is asserted rather than re-run: the last case in
`tests/test_generated_deploy_pipelines_and_trigger_configs_are_written_where_they_are_read.py` asks
`git check-ignore` for both locations.)

### 1.6 Tekton `v1beta1` surface (decision 3, measured field by field)

Pipelines **1.4.0** is already pinned, and `tekton.dev/v1` has been GA since Pipelines 1.0 — **no controller
version bump is required.** The migration ran and landed (FIN-70…FIN-73); the table is the *measured*
surface, the counts in the last column are what the tree held before the flip, and what says the flip is
finished is `tests/test_tekton_manifest_api_versions.py`.

| v1beta1 | v1 | Where in this repo | Count / state |
|---|---|---|---|
| `apiVersion: tekton.dev/v1beta1` | `tekton.dev/v1` | 38 occurrences in `tekton_ci/` (one per file, incl. the `PipelineRun` nested in a TriggerTemplate), 20 occurrences in `examples/tekton_ci/` (16 files — `hello_world_pipeline.yml` has 3 documents); nothing outside those two trees | **58 files / 58 occurrences at HEAD → 0 now.** *Corrected: an earlier revision said "42 files / 44 occurrences" for `tekton_ci/` and "64 occurrences" in total; `git show HEAD` measured 38 + 20.* |
| `PipelineRun.spec.serviceAccountName` | `PipelineRun.spec.taskRunTemplate.serviceAccountName` | 12 in `PipelineRun` files (4 `tekton_ci/pipeline-runs/` + 8 `examples/`) + 2 inside `template-trigger-template.yml` | **14 done** — plus **2 that did NOT change**: `EventListener.spec.resources.kubernetesResource.spec.template.spec.serviceAccountName: tekton-triggers-sa` (Pod field, 2 files) |
| `PipelineRun.spec.timeout` | `PipelineRun.spec.timeouts.pipeline` | 12 pipeline-run files + 2 TriggerTemplate files | **14 done** |
| `Task.spec.steps[].resources` | `…steps[].computeResources` | `tekton_ci/tasks/*.yml` (17 files: 15 with one step, `build-and-push-docker-image.yml` and `terraform-cli.yml` with two) | **19 done** (*corrected from the "20 sites" of the earlier revision — measured 19 in HEAD, 19 now*) |
| `Task.spec.resources` (`inputs:`/`outputs:`) | **removed** — replaced by a workspace, not by params | `examples/tekton_ci/tasks/task_examples.yml` (`input-example-task`) | **1 done.** The plan said `→ params`; params cannot express a filesystem path, so the git input became `workspaces: git-source` and the step reads `$(workspaces.git-source.path)`. The same file's `PipelineResource` document was **deleted** — v1 does not serve that kind at all, so bumping its apiVersion would emit a manifest that cannot apply |
| `taskRunSpecs[].taskServiceAccountName`, `taskPodTemplate`, `spec.podTemplate`, `stepOverrides`, `sidecars` overrides, `ClusterTask`, `*.bundle` | — | not present | 0 |

**Two traps that make `sed` the wrong tool:**

1. `spec.resources:` appears in **three unrelated shapes**, only one of which is renamed. (a) `Task.spec.steps[].resources`
   → `computeResources` (the rename target). (b) `volumeClaimTemplate.spec.resources.requests` — a *Kubernetes* PVC
   field, ~10 sites in pipeline-runs and trigger templates. (c) `EventListener.spec.resources` and the container
   `resources` inside an EventListener's embedded pod template (`template-event-listener.yml:23,30`) — Triggers/Pod
   fields. A blanket `sed -i 's/resources:/computeResources:/g'` silently corrupts storage requests and EventListeners.
2. The Triggers API (`triggers.tekton.dev/v1beta1` — **6 template files carry it, and it is a substring of
   `tekton.dev/v1beta1`, so naive greps match it**) is **not** in scope — Triggers is pinned at 0.33.0 and
   only `tekton.dev` moves to v1. `Config.TEKTON_API_GROUP/VERSION` is used only for Pipelines/Tasks
   (`tekton_utils.py:41-42,89-90,239-240`, `automation.py:633-644,773-785`), so this is safe.
   **The grep an earlier revision proposed for the assertion is itself wrong:** `grep -rnE
   "[^s]tekton\.dev/v1beta1"` does *not* exclude Triggers, because the `.` in `triggers.tekton.dev` is
   matched by `[^s]`. Use the exact-string form — `grep -rn "apiVersion: tekton.dev/v1beta1" tekton_ci
   examples` — which says nothing about Triggers either way, and read the Triggers exception separately.

**Generated** manifests get their `apiVersion` from `Config.TEKTON_API_VERSION`, so FIN-22/23/24 (the
generators) must stamp v1 rather than copy the template verbatim. `tekton_ci/pipelines/templates/template-deploy-pipeline.yml:1`
is now `tekton.dev/v1`, and `scripts/automation/tekton/generate_pipeline_run.sh` emits v1 plus both nested
fields; those generators still write the literal rather than reading it from `Config`, which is FIN-24's
half, not Wave 7's.

### 1.7 Two constraints that override other guidance

**(a) No backwards compatibility is required — treat breaking change as the default.**

The only customer runs kubementat in a staging system that **will be re-initialized on another Kubernetes
cluster** as part of this work. There is no upgrade path to preserve, no external library consumer, no
version negotiation. This is a *simplification license*, and it should be used, not hinted at:

| Instead of… | Do… |
|---|---|
| Keeping flat command names (`kmt tunnel-grafana`) with Click-group aliases (old FIN-62) | Rename to the grouped form outright and fix the docs (`kmt tunnel grafana`). FIN-62 |
| Config-key compatibility shims when a key moves (e.g. namespaces, FIN-07) | Rename the key, update the template, document it in the CHANGELOG. No fallback reads |
| Tolerating "old-style" team dirs in `Config` | Fail loudly with "run `kmt initialize` again". FIN-05 |
| Deprecation warnings for 1 release | Delete. The `kmt` version bump (0.x → 0.5.0, **applied manually at the end of the refactor** — owner ruling 2026-10-29) *is* the migration note |
| Preserving `kmt run-script` as an escape hatch | ~~Remove it in FIN-46/FIN-60 (decision 6)~~ — **done by FIN-06 (`e6b3afd`)**, and note it was FIN-06 and not FIN-46/FIN-60: those two landed while the verb was still registered, which is why the rule needed a second ticket to reach the CLI after it reached CI |
| Silent drift absorbed by a forgiving `create_or_replace*` helper | Idempotency *is* a product requirement — this row is not about back-compat; keep the helpers forgiving, but the shim in the row above still gets deleted |

The corollary: **do not write defensive code**. Half the complexity in a migration plan is compatibility
code for an audience that doesn't exist. Deleting it also means deleting the tests that exist only to
exercise it.

**(b) A real-VM integration suite does not exist — and it is the only verification mechanism there will be.**

`pytest` is configured with zero tests, and the only end-to-end check is `tests/test_installation.sh`, which
(a) blocks on `read -p` and `kubectl config current-context` — so it cannot run unattended,
(b) never uninstalls and never asserts namespaces/CRDs are gone, (c) runs against whatever cluster the
developer's kubeconfig points at — a regression can silently destroy a real cluster, and
(d) pins `KUBERNETES_DEFAULT_STORAGE_CLASS='hostpath'`, which is not what a k3s single-node cluster
provides (`local-path`). That last item is the archetype of what this suite exists to catch: a hardcoded
assumption that survives four migrations because nothing re-runs the real thing.

Wave T (§5) builds the disposable-VM harness with `virt-runner`: create a guest → install a single-node
cluster → install kubementat completely → exercise main features → uninstall → destroy the VM.

**This replaces a merge gate, it does not become one.** Verification stays manual (decision 3): whoever
finishes a ticket runs the smoke lane and records the run directory. Nothing blocks a merge, no CI reads a
lane result, and the repo has no CI of its own. That makes the suite's ergonomics — one command, < 25 min,
no ceremony — part of correctness rather than convenience: a suite that is annoying to run will not get run,
and in a migration with no automated verification that means the tickets silently lose their evidence.

### 1.8 Two upstream blockers the Wave T research uncovered

The research run installed things for real instead of trusting the plan, and found that **no `kmt install`
can currently complete** — on the host or in a VM. Both are product/upstream problems, not VM problems, so
they are tickets now: B1 stops every install, B2 means the release the smoke lane selects is dead code.

| # | Blocker | Why it's fatal | Ticket |
|---|---|---|---|
| **B1** | The `twuni` helm repo (`https://helm.twun.io`) is unreachable — from the guest **and** the host. helmfile processes *all* repos before applying any group filter, so the failure happens in `repo add`, before a single release deploys | Every `kmt install`, for **any** `--helmfile-installation-group`, dies at the same point. This is invisible in unit tests and argv shims | **FIN-88** — decided: delete the entry |
| **B2** | The `kubernetes-dashboard` chart repo (`https://kubernetes.github.io/dashboard/`) 404s — the project was **archived 21 Jan 2026** | The `group: dashboard` release that gates the smoke lane installs nothing. Not a VM problem: it fails the same way on the host | **FIN-89** — **resolved 22 Oct 2026 by deletion**: the release, its values, the component scripts and `kmt login-kubernetes-dashboard` are gone. The analysis below survives for a future replacement |

**Update (22 Oct 2026).** The owner chose removal over a swap, so nothing points at
`kubernetes/dashboard` any more and the smoke lane installs `standard` instead of
`dashboard`. Two facts decided it: the component was only half wired (`kmt
login-kubernetes-dashboard` reads the token of a ServiceAccount that no part of the install
path creates, and port-forwards a Kong service only the retired chart had), and the
replacement choice is a product decision that should not be rushed while the migration is in
flight. The candidate table below stays as the starting point for whoever picks it up.

**B2, decided direction: kubementat keeps a dashboard — but not this chart.**

`kubernetes/dashboard` was archived **21 Jan 2026**; its helm repo 404s and installs receive no fixes,
security fixes included ([repo](https://github.com/kubernetes-retired/dashboard),
[sig-ui notice](https://groups.google.com/g/kubernetes-sig-ui/c/vpYIRDMysek/m/wd2iedUKDwAJ)). The owner wants
the dashboard component to survive, so the release stays — pointing at a **maintained** dashboard.

| Candidate | Why it fits / what it costs |
|---|---|
| **Headlamp** (recommended) | `helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/` → `headlamp/headlamp`. A `kubernetes-sigs` project, plain https chart repo — **the same mechanism every other repo in our helmfile already uses**. Logs in with a **ServiceAccount bearer token under RBAC**, which is exactly what our install script creates (`kubernetes-dashboard-read-only-cluster-user` + token secret) and what FR-54 already asserts. One Deployment, no PVC → stays inside the smoke budget. |
| KubeGlass | Viable and has a published migration guide off the archived dashboard, but its chart ships as an **`oci://` artifact**, a different mechanism from every https repo in `helmfile.yaml.gotmpl`, and its maintenance/license posture was not verified here. If chosen, verify the OCI chart works under helmfile first. |

**What swapping actually touches** (verified in this tree, so the effort is not guesswork): the release block
(`helmfile.yaml.gotmpl:198-206`, chart + `values.encrypted.yaml` path + namespace) and its `platform_config/dev/`
copy · `templates/environment/kubementat_components/kubernetes_dashboard/values.encrypted.yaml` →
new values dir · `scripts/automation/components/install_kubernetes_dashboard.sh` +
`uninstall_kubernetes_dashboard.sh` · `scripts/utilities/login_kubernetes_dashboard.sh`, whose
**port-forward target is `svc/kubernetes-dashboard-kong-proxy`** — a service that exists only because the
retired chart fronted itself with Kong, and which Headlamp does not have. That one line is the swap's real
risk, and it is why this is a product ticket and not a harness fix. Per §1.7a: rename outright, no aliases.

Both are exactly the kind of rot the suite exists to detect, and both were *only* detectable by running
`helmfile apply` against a real cluster. FIN-88 is settled (delete it; a replacement registry solution comes
later); FIN-89 is not, and until it is, the smoke lane has no cheap release to install and every lane's
install assertion is unverifiable — plan the sequence with that in mind (see §6).

---

## 2. Migration ledger (authoritative)

> **Read §1.1a and §1.3c before this ledger.** The ledger is the complete record of which script became
> what, and it does not age row by row; the *current* counts and what a lane showed do. Rows in
> "delete-only" and "not migrated" still describe work that exists, but where a row below disagrees with
> §1.1a, §1.3c or `TODOs.md`, the newer document is right and this row is history — the team-account rows
> (`FIN-30…FIN-33`) and the uninstall row (`FIN-11`) are the two places that disagree today.

Status key: **A** — migrated, delete only · **B** — shell-out (Python exists but calls bash) ·
**C** — not migrated · **X** — not a product script.

Under `scripts/`: **19 A · 5 B · 36 C** (= 55 product scripts + 1 stub counted separately — the stub,
`create_team_account.py`, is deleted by FIN-08, so 55 product scripts and nothing else counted twice).

**Update (FIN-60):** that tally is the inventory as audited, not today's tree, and the classes do not
sum to what is now on disk: **44 scripts / 3,257 LOC remain** (measured). What moved is class A —
FIN-60 deleted 8 of its rows (§7), listed in §2.1 with the row that replaced them.

**Update 22 Oct 2026:** three of these are already gone — `install_kubernetes_dashboard.sh`,
`uninstall_kubernetes_dashboard.sh` and `login_kubernetes_dashboard.sh` were deleted with the
dashboard component (`7243da9`), which takes the §2.2 shell-outs from 5 to 4 and closes FIN-15
as "deleted", not "migrated". FIN-17/18 for those two are withdrawn the same way.

**Update 27 Oct 2026 (backing services):** three more of the `tasks` rows are gone, and not by a
migration. Owner decision 2026-10-27 deleted the MySQL / Redis / NGINX install automation, because the
pinned bitnami images return `NotFound` from docker.io and all three were red on the last full lane
(`docs/plans/lane-verification-3e8b3f6.md` §2.1): `install_mysql_helm_chart.sh` (82),
`install_redis_helm_chart.sh` (88) and `install_nginx_reverse_proxy.sh` (47) are deleted, so the §2.3
`tasks` row is **1** (`setup_mysql_databases.sh`, 79) and class C is **33**, not 36. **2026-10-29: that
last `tasks` member is gone too** — FIN-51c (`0cf22b7`) deleted `scripts/tasks/` — so the row is **0**,
and every count in the rest of this section is older than that. The tree on disk
agrees: **41 scripts / 3,040 LOC** under `scripts/` now (measured), which is the 44 / 3,257 above
minus these three. `FIN-39` is therefore closed as "deleted, will be replaced", not migrated:
kubementat ships no working backing-service install until the standard-helm-chart path lands, and
nothing in this tree pretends otherwise. The `kmt` CLI never had a command for these three
(`uv run kmt --help` → 19 before and after), so no user-facing command disappeared. Their setup
pipelines went with them (§1.4).

### 2.1 A — delete-only (15 rows; verify with a parity test, do not reimplement) — **6 remain**

FIN-08 deleted the stub, and FIN-60 deleted 8 of the 12 "delete immediately" scripts, each verified
by a parity test that ran the bash (`git show ccb6976:scripts/...` is any of them — the runs are
recorded in `tests/test_parity_*.py`). **5** rows stay (6 before `open_pod_tunnel.sh` went with
FIN-46, 2026-10-28) and they are held by the reason in their caveat, not by oversight.

> **2026-10-29: 3 rows stay.** The two that last-lap §2 row 9 called orphans went that day —
> `helmfile_apply.sh` and `generate_ssh_key.sh` — each turned from a caveat into a deletion, and
> each now guarded by a test that asserts the file stays deleted rather than by a comparison that
> needed it present. `tests/test_parity_generate_ssh_key.py` is gone with the script it ran, and
> the two guards are
> `tests/test_the_helmfile_argv_is_built_in_python_and_the_script_stays_deleted.py` and
> `tests/test_the_deployer_ssh_keypair_is_written_by_the_library_and_the_script_stays_deleted.py`.
> What is still held by its caveat was the CI half (`run_pipeline.sh`) — **that half closed
> 2026-10-29, FIN-51b `85f1ea8`**, when the Task stopped `source`ing it and the wait moved into the
> command with a bound. What remains held by a caveat is the two one-liners whose commands FIN-44/45
> never shipped. `find scripts -name '*.sh' | wc -l` → **2** (re-measured; the row said 9).

| File | LOC | Python equivalent | Caveat |
|---|---|---|---|
| `scripts/automation/tekton/configure_docker_registry_access.sh` | 118 | `Automation.setup_docker_registry_access()` | **deleted by FIN-60** — parity: `test_parity_secrets.py` |
| `scripts/automation/tekton/configure_secrets.sh` | 67 | `Automation.setup_secrets()` | **deleted by FIN-60** — parity: `test_parity_secrets.py`; it labelled the Secret and the client does not, which that file states |
| `scripts/automation/tekton/uninstall_pipelines.sh` | 44 | `Automation.uninstall_pipelines()` | **deleted by FIN-60** — parity: `test_parity_tekton_runs.py`; its `|| true` also tolerated a refused delete |
| `scripts/automation/tekton/list_tekton_resources.sh` | 44 | `TektonUtils.list_all_tekton_resources()` | **deleted by FIN-60** — parity: `test_parity_tekton_runs.py`, which compares the four kinds it printed |
| `scripts/automation/tekton/run_pipeline.sh` | 147 | `TektonUtils.run_pipeline()` + `wait_for_pipeline_run()` | **deleted 2026-10-29 by FIN-51b (`85f1ea8`)**. The hold was "FIN-50 first", and it discharged: the CI image carries `uv` (`docker_files/ci_images/ubuntu_ci_minimal/Dockerfile:131`) and `tekton_ci/tasks/run-tekton-pipeline.yml` is now one step running `uv run --locked --no-dev kmt tekton-run-pipeline … --wait`, declared with `workingDir:` so uv can find the project. Its parity test no longer runs live bash — the bash half is recorded from `git show 40f281b:…` and compared against the Python path, which is what the last four `test_parity_tekton_runs.py` cases had to become. **Still owed a lane:** that the step really starts a pod, finds the project, and blocks for the run is a hypothesis until one is started, and no host here starts one. |
| `scripts/automation/components/helmfile_apply.sh` | 66 | `HelmUtils.helmfile_apply()` | The row said *still `source`d by `install_routing.sh:32`, `install_kubernetes_dashboard.sh:36` → delete after FIN-17/18*. **Deleted 2026-10-29, last-lap §2 row 9** — the condition closed. both sourcers are gone (`install_routing.sh` → `kmt routing install`, `install_kubernetes_dashboard.sh` → deleted with the retired chart, 22 Oct), and `grep -rn "helmfile_apply"` over `src/ tests/ tekton_ci/ templates/ examples/` plus every surviving `.sh` under `scripts/` hits nothing but the docs. Nothing was ported, because nothing called it — the argv it built by hand (`helmfile apply --color -f <components>/helmfile.yaml.gotmpl -l <filter>`) is already assembled in Python at `src/kubementat/helm_utils.py:75`, reached from `src/kubementat/automation.py:543`, `src/kubementat/automations/routing/routing.py:246` and `src/cli/commands/helm.py:93`. Guard: `tests/test_the_helmfile_argv_is_built_in_python_and_the_script_stays_deleted.py` (file absent, no code root names it, and the argv reaches `helmfile` with no `.sh` token and no `shell=True`). The dangling `HelmUtils.components_dir` that pointed at the now-empty directory went with it. |
| `scripts/utilities/tunneling/open_pod_tunnel.sh` | 33 | `KubernetesUtils.open_tunnel()` | **deleted 2026-10-28** — `kmt tunnel-pod` already forwarded the namespace, pod and both ports this script took as arguments, so the row was "delete last" and not "migrate"; the parity record it fed is repointed into `test_parity_tunnels.py` |
| `scripts/utilities/tunneling/open_grafana_tunnel.sh` | 38 | `Automation.open_tunnel_grafana()` | **deleted by FIN-60** — parity: `test_parity_tunnels.py` (the `pod/null` branch lives in that file as a record) |
| `scripts/utilities/tunneling/open_tekton_dashboard_tunnel.sh` | 20 | `Automation.open_tunnel_tekton_dashboard()` | **deleted by FIN-60** — parity: `test_parity_tunnels.py` |
| `scripts/utilities/tunneling/open_polaris_tunnel.sh` | 30 | `Automation.open_tunnel_polaris()` | **deleted by FIN-60** — parity: `test_parity_tunnels.py`; local-port default differs (8082 vs 8080) |
| `scripts/utilities/tunneling/open_vault_ui_tunnel.sh` | 30 | `Automation.open_tunnel_vault()` | **deleted by FIN-60** — parity: `test_parity_tunnels.py`; local-port default differs (8205 vs 8200) |
| `scripts/secret_management/generate_ssh_key.sh` | 35 | `CryptoUtils.generate_ssh_key_pair()` | The row said *add `kmt generate-ssh-key` first — still not there (`uv run kmt --help` → 19 commands, none of them that), so FIN-60 left the row; the library is also not yet the same behaviour: bash skips an existing key, the library overwrites it (`test_parity_generate_ssh_key.py`)*. **Deleted 2026-10-29, last-lap §2 row 9.** Both caveats re-read rather than dismissed. *`kmt generate-ssh-key` first — still not there*: still not there (`uv run kmt --help` registers none), and not the blocker — the script was never the implementation of a command; `kmt initialize` is what generates a deployer key, and the script had no caller in `src/`, `tests/`, `tekton_ci/`, `templates/`, `examples/` or any sibling `.sh`. *bash skips an existing key, the library overwrites it*: true of the library alone, closed at the only call site — `src/kubementat/automation.py:203` guards `generate_ssh_key_pair` (line 205) with `os.path.exists`, which is bash's `[[ ! -f $KEYNAME ]]` on the Python side. The guard asserts that off the source, so it cannot rot into a sentence: `tests/test_the_deployer_ssh_keypair_is_written_by_the_library_and_the_script_stays_deleted.py`. One recorded difference survives: bash wrote OpenSSH's private-key container, the library writes a classic RSA PEM (`TraditionalOpenSSL`). `base64_library.sh` stays, sourced by `create_secret_from_json.sh` alone now (`tekton_ci/tasks/create-secret-from-json.yml`, FIN-51). |
| `scripts/utilities/kubernetes/retrieve_node_addresses.sh` | 2 | one-liner | fold into `kmt node-addresses` — the command does not exist (FIN-44/45), so the row stays: deleting it now would leave no way to get node addresses from `kmt` |
| `scripts/utilities/kubernetes/view_cluster_resource_versions.sh` | 2 | one-liner | fold into `kmt api-resources` — same blocker, and the parity test says the same thing in its own words |
| `scripts/automation/tekton/create_team_account.py` | 181 | *stub, marked "not working"* | **deleted by FIN-08** — dead code that inflated the ledger; the Python implementation is still FIN-31's |

### 2.2 B — CLI/automation still shells out (6; highest user-visible value)

| File | LOC | Called from | Ticket |
|---|---|---|---|
| `uninstall_kubementat.sh` (repo root) | 204 (deleted) | was `kmt uninstall` → `run_kubementat_script()` | **FIN-11 — deleted `213bc9b`**, replaced by `Automation.uninstall_kubementat()`. Nothing in `src/` names it any more, and `test_component_namespaces_come_from_configuration.py` asserts the file is absent so a re-added script cannot coexist with the Python path. |
| `scripts/automation/components/install_tekton.sh` | 108 (deleted) | was `install_kubementat()` → `_run_install_script()` | **FIN-12 — deleted**. Replaced by `TektonUtils.install()`, which applies the three pinned releases through `shell.run` and writes `helm-deployer-cluster-role` through the client. Nothing in `src/` names it any more (`grep -rn "\.sh" src` returns the two that remain), and `test_parity_tekton_installs_the_pinned_releases.py` judges the Python path against what the deleted script sent. |
| `scripts/automation/tekton/setup_triggers.sh` | 269 | `install_kubementat():438` **and** `kmt tekton-setup-triggers` | FIN-13 |
| `scripts/automation/components/install_linkerd.sh` | 89 (deleted) | was `install_kubementat():445`, behind `--enable-linkerd` | **deleted 2026-10-28 with Linkerd** — FIN-14 is withdrawn, so this row closes by removal and not by a port |
| `scripts/utilities/login_kubernetes_dashboard.sh` | 55 | `kmt login-kubernetes-dashboard` | FIN-15 |
| `scripts/utilities/run_ci_container.sh` | 34 | `kmt run-ci-container` | FIN-16 |

### 2.3 C — not migrated (33 — 36 as audited, minus the three backing-service scripts deleted 2026-10-27)

**components (3 — two more went when the mesh was deleted, and one is deleted by FIN-19):** `install_routing.sh` (76) · `install_kubernetes_dashboard.sh` (99) · `uninstall_kubernetes_dashboard.sh` (31). `uninstall_linkerd.sh` (37) and `generate_linkerd_certs.sh` (63) are deleted, not migrated (owner decision 2026-10-28). `uninstall_helm_deployment.sh` (58) is **deleted by FIN-19** rather than migrated: it had no caller in `src/` at all, so there was no call site to port — what landed is a command that did not exist before (`kmt helm-component-uninstall`, §1.3b row 6b), judged against the blob at `git show b2d471a:scripts/automation/components/uninstall_helm_deployment.sh`.
**tekton (5):** `setup_tekton_pipelinerun_cleanup_job.sh` (194) · `generate_pipeline_run.sh` (140) · `generate_trigger_config_from_templates.sh` (121) · `create_team_account.sh` (95) · `generate_deploy_pipeline_from_template.sh` (71)
**secret_management (4 — two deleted 2026-10-28, two stay):** ~~`create_k8s_secret_for_ssh_key.sh` (44)~~ **deleted 2026-10-28**, not migrated: no caller in `src/`, `tests/`, `tekton_ci/` or `README.md`, and the act is `KubernetesUtils.create_or_replace_secret()` driven by `kmt setup-secrets` over `SSH_DEPLOY_KEYS` (§7's FIN-30..39 row). The gap that remains is stated there and not filled here: no command takes *one key file + one namespace*, which is the shape the script took. ~~`retrieve_token_for_service_account.sh` (25)~~ **deleted by FIN-36**, replaced by `kmt service-account-token`.   `create_secret_from_json.sh` (32) · `generate_ssh_key.sh` (35) · `base64_library.sh` (12) all stay — the first is called from `tekton_ci/tasks/create-secret-from-json.yml` (FIN-51 gate), the second has `kmt generate-ssh-key` unwritten, and the third is the library both of them `source`. **2026-10-29 (last-lap §2 row 9):** the second is deleted — `CryptoUtils.generate_ssh_key_pair` behind `Automation.initialize`, no caller anywhere. So what holds `base64_library.sh` back is one script and not two: `create_secret_from_json.sh`, whose FIN-51 gate is unchanged, and its `source "${0%/*}/base64_library.sh"` is the only reference the library has left. `find scripts -name '*.sh' | wc -l` → **9**.
**tasks (0 — the last went 2026-10-29):** `setup_mysql_databases.sh` (79) is **deleted by FIN-51c**
(`0cf22b7`) together with `tekton_ci/tasks/run-automation-script-for-environment.yml`, the Task that ran
it, under the owner's rule that a Task whose whole purpose is to execute a path is not a product verb.
`install_mysql_helm_chart.sh`, `install_redis_helm_chart.sh` and `install_nginx_reverse_proxy.sh` were deleted by owner decision 2026-10-27 rather than migrated, see the update above
**user_management (7)** — **all 7 are now gone**: the four team-account ones with FIN-30…FIN-33, and `create_grafana_accounts.sh` (40) · `create_grafana_read_only_account.sh` (52) · `grafana_helpers.sh` (69) with FIN-35 (2026-10-28), whose commands are `kmt users create-grafana-accounts` and `kmt users create-grafana-read-only-account`. `user_management_helpers.sh` (310) went with the port to `src/kubementat/infrastructure/user_management.py`. `scripts/user_management/` is not in the tree.
**utilities/kubernetes (5):** `list_pvc_usage.sh` (56) · `create_ingress_yml.sh` (54) · `cluster_capacity.sh` (53) · `analyze_deployment.sh` (23) · `retrieve_deployment_docker_image_versions.sh` (12)
**utilities/helm (3 — none of them scripts any more, FIN-44/45):** `ssh_into_helm_deployment.sh` (31) · `tail_logs_for_helm_deployment.sh` (27) · `get_latest_version_for_helm_chart.sh` (17), gone in favour of `kmt deploy-ssh`, `kmt deploy-logs` and `kmt helm-version`
**utilities/tunneling (0 — the last two went 2026-10-28):** `open_mysql_tunnel.sh` (34) is **migrated by FIN-46** into `Automation.open_tunnel_mysql()` / `kmt tunnel-mysql`, and `open_pod_tunnel.sh` (33) — which is what it `source`d — is **deleted without a new command**, because `kmt tunnel-pod` had long been the same `kubectl port-forward` with the same four arguments. Both are recorded, not remembered: `tests/test_parity_tunnels.py` reads the pair out of `git show 609e757:scripts/utilities/tunneling/open_mysql_tunnel.sh` and keeps the comparison alive.

### 2.4 X — out of scope for this migration

`tests/{test_installation,uninstall,reset_configuration}.sh`
(teaching examples and dev-only helpers — these get replaced by the Wave T suite, see FIN-85, not migrated)

---

## 3. Strategy

**Order matters more than volume.** Running 36 mechanical migrations before fixing §1.3 and §1.6 yields 36
copies of the same bugs, 36 hand-rolled `subprocess` calls, and 36 untested code paths.

Rules for every ticket:

0. **A real-VM run, attached — not a gate.** Before a ticket is accepted, run the smoke lane and record the
   run directory on the ticket (Wave T, FIN-80…FIN-89). It is not a precondition enforced by anything; the
   only enforcement is that a ticket with no run attached has no evidence. Argv shims and unit tests are
   necessary but *not* the evidence — the evidence is "kubementat installed, worked, and uninstalled cleanly
   on a fresh VM". If the lane is red, that is a finding to file, not a reason to stop working.
1. **One script per PR.** Migrate → wire CLI → prove parity → delete the script in the *same* PR. Never
   leave a "both for now" state; that is how the repo reached 55 scripts.
2. **Parity is proven by argv, not opinion.** FIN-03's shim harness runs the old bash and the new Python
   against fake `kubectl`/`helm`/`helmfile`/`tkn`/`linkerd` binaries and asserts the recorded argv lists
   match. Highest-leverage technique for *cheap* proof; the VM suite is the technique for *real* proof.
3. **No new `subprocess` call sites outside `src/kubementat/shell.py`** (FIN-02): argv lists only, never
   `shell=True`, one logging/retry/dry-run choke point.
4. **No new `sys.path.append`.** FIN-01 removes them; a hygiene test fails CI if they reappear.
5. **Breaking changes are expected — do not add compatibility shims.** (§1.7a.) Rename commands, keys, and
   file layouts freely; keep `docs/` and `CHANGELOG.md` honest instead. Any PR that adds a deprecated
   alias, a fallback config read, or an "if old layout" branch needs a written reason in its description.
6. **Structural decisions (src layout, trigger location, API version, namespace config) land in dedicated
   tickets before the migrations that depend on them.** FIN-01 and FIN-02 are blockers for everything.
7. **Tekton v1 (Wave 7) is atomic and lands early:** manifests + `constants.py` + generators + docs in one
   PR, **hard flip, no env override**. Never mix `v1beta1` and `v1` in one applied tree. It ships right
   after Wave 0 so no later ticket is written against the beta API.

Suggested commit convention (matches the `T001:`… history): `FIN-NN: <what>`.

---

## 4. Target architecture

```
pyproject.toml                        # packages found under src/; entry point cli.kmt:main
src/
  cli/
    kmt.py                            # single entry point; wiring; (deletes cli/kmt duplicate)
    helpers.py          (NEW)         # shared run_script (until scripts/ is empty), --verbose, ctx defaults
    commands/
      kubementat.py tekton.py tunnels.py helm.py utilities.py
      (later) components.py users.py tasks.py secrets.py
  kubementat/
    __init__.py                       # version only; no logic, no logging
    constants.py        (NEW)         # component→namespace/selector/port table; TEKTON_API_* defaults
    shell.py            (NEW)         # run(), CommandResult, retry, dry-run — the only subprocess site
    config.py                         # + KUBEMENTAT_HOME, + tekton_team_trigger_dir, fix D6, no logging side effect
    kubernetes_utils.py               # + missing RBAC/exec/logs primitives, delete duplicate defs
    helm_utils.py                     # + chart version lookup, release helpers
    tekton_utils.py                   # + setup_triggers, cleanup job, generators (stamp apiVersion)
    crypto_utils.py                   # + linkerd CA generation via `cryptography`
    automation.py                     # orchestrator only
    user_management.py  (NEW)         # team accounts, kubeconfig generation
    grafana_client.py   (NEW)         # Grafana REST API
    database_utils.py   (NEW)         # MySQL provisioning (PyMySQL)
    ci_utils.py         (NEW)         # run_ci_container
tools/                  (NEW, throwaway) tekton_v1_migrate.py — YAML-aware v1 transformer (FIN-71); **deleted once it had converted both trees** (FIN-72)
tests/
  unit/                 (NEW)         # parity shims, import hygiene, CLI surface
  shims/                (NEW)         # argv-recording fake kubectl/helm/helmfile/tkn/linkerd
  e2e/                  (NEW, Wave T)
    suite.py                          # virt-runner orchestrator: create → stages → assert → destroy
    stages/{01-provision,02-install-kubementat,99-teardown}.sh
    lanes.yaml                        # smoke|full: VM shape, helmfile group, feature list
```

Rationale for the non-obvious choices:

- **`src/` layout, both packages moved (`git mv lib/kubementat src/kubementat && git mv cli src/cli`).**
  One `where = ["src"]` eliminates the dual-import root cause (D1) by construction. `Path(__file__).parents[2]`
  still yields the repo root from `src/kubementat/config.py`, exactly as from `lib/kubementat/config.py`, so
  no path-derivation change is needed — but `KUBEMENTAT_HOME` is still required for site-packages/CI installs (D11).
- **`constants.py`** — namespaces are hardcoded on *both* sides today (bash: `GRAFANA_DEPLOYMENT_NAMESPACE="grafana"`
  per script; Python: `namespace = 'grafana'` with `# TODO: make this a config` at `automation.py:994,1020,1044,1068`).
  One table ends the drift and is a prerequisite for the config-driven uninstall (FIN-11).
- **Triggers** — `Config.tekton_team_trigger_dir = platform_config/{env}/{team}/triggers` and
  `Config.tekton_trigger_template_dir = tekton_ci/triggers/templates`, mirroring the existing `tekton_team_*` pattern.
- **`TEKTON_API_VERSION` becomes `"v1"` outright** (FIN-70, decision: hard flip, no env override).
- **Component namespaces are read from configuration** (decision 5), never hardcoded. `constants.py` holds
  only the fallback defaults; `Config.component_namespace("grafana")` is the single accessor. All 12
  `*_DEPLOYMENT_NAMESPACE` keys get added to `templates/environment/static.json.template` — **15** landed,
  because `goldilocks`, `vpa` and `tekton-pipelines-resolvers` are namespaces `uninstall_kubementat.sh`
  deletes and no Python function names. (The earlier note that `KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE`
  "exists there" was wrong: it exists in `platform_config/dev/static.json`, generated by an older template,
  and the template has never had a namespace key at all. Reconciling that key is FIN-89's, not this one's.)
- **No new CLI framework, no config DSL.** Click is installed and working; the gap is content, not structure.

---

## 5. Work packages

> **This is the ticket ledger.** There are no ticket *files* anywhere in the tree: the earlier
> `.tickets/python-refactor/` directory held 35 tickets from the superseded plan (all still marked
> `⬜ open`, every one of its topics already absorbed into a FIN ticket here — e.g.
> `user_management_helpers.sh`→FIN-30, `generate_linkerd_certs.sh`→FIN-20, `create_k8s_secret_for_ssh_key.sh`→FIN-37,
> `setup_tekton_pipelinerun_cleanup_job.sh`→FIN-21, the two missing tunnels→FIN-46) and was **deleted on
> 2026-10-21**. If you need a work-queue
> format, generate `FIN-xx` files from the tables below — do not resurrect the old ones.

Effort = focused agent time. "Cx" = blast radius.

| Wave | Tickets | Focus | Hours |
|---|---|---|---|
| **T** | 10 | real-VM E2E verification (**lands first**, manually run) + 2 unblockers it found | 47 |
| **0** | 10 | packaging, `shell.py`, test harness, K8s primitives, config, namespaces-as-config | 34.5 |
| **7** | 4 | Tekton `v1` hard flip (**lands next**) | 13 |
| **1** | 6 | core install/uninstall path | 23 |
| **2** | 9 | components + generators + cleanup job | 27 |
| **3** | 10 | users, Grafana, secrets, backing services | 32 |
| **4** | 7 | utilities tail (parallel-friendly) | ~23 |
| **5** | 2 | CI-container execution | 14 |
| **6** | 5 | docs, CLI grouping (renames, no aliases), deletion ledger | ~12 |
| **Total** | **~57** | | **~226 h** |

**What is landed, by ticket** — verified the same way §1.3a verifies defects, on `f885a6e` + `05214d4`:

| Ticket | State | The command or commit that says so |
|---|---|---|
| FIN-01 | landed `eb37c34` | `uv run python -c "import kubementat"` from `/tmp` → exit 0; `rg sys.path.append src/` → empty |
| FIN-02 | landed `4f78754` | `subprocess` is imported by `src/kubementat/shell.py` alone; 0 `shell=True` in code |
| FIN-04 | landed `05214d4` | `grep -c "def get_role_binding" src/kubementat/kubernetes_utils.py` → 1; 127 unit tests, no cluster. Its detail section carries the landed shape and the caveat that **no caller uses the new primitives yet**, so no lane can see them. |
| FIN-05 | landed | D6 (`d6148f0`) plus the rest: `KUBEMENTAT_HOME`, `KUBEMENTAT_PLATFORM_CONFIG_DIR`, no logging side effect, `tekton_team_trigger_dir` (+ the `initialize` scaffold for it). `grep -rn KUBEMENTAT_HOME src/` finds code, and `uv run pytest -q tests/test_config_path_resolution.py` -> 15 passed. **Not landed, and deliberately out of this ticket's scope:** the `TEKTON_API_VERSION` override (it dies with the hard flip in FIN-70) and moving generated trigger *files* (FIN-23). |
| FIN-08 | **landed 2026-10-27** | Both files are gone: `src/kubementat/utils.py` (zero bytes, imported by nothing) and `scripts/automation/tekton/create_team_account.py` (whose `create_service_account` call was missing the `secrets` argument and would have `TypeError'd`). `grep -rn "kubementat.utils\|create_team_account.py" src tests scripts` hits only the gitignored `src/kubementat.egg-info/SOURCES.txt`; `uv run kmt --help` is unchanged (19 commands); `uv run pytest -q` → 237 passed, with `tests/test_no_dead_code_ships_in_the_tree.py` locking the rule so the corpses cannot return. **The feature is still open** — this deleted a stub, it did not implement `Automation.create_team_account` (FIN-31), and no `.sh` was touched. |
| FIN-07 | landed | `grep -rn "= *'grafana'\|= *'prometheus'\|= *'vault'\|= *'polaris'" src/kubementat` → empty; `uv run pytest -q` → 177 passed. `Config.component_namespace()` / `component_service_selector()` are the only readers, `constants.py` holds the fallback, and 19 `*_DEPLOYMENT_NAMESPACE` / `*_SERVICE_SELECTOR` keys ship in `templates/environment/static.json.template`. **Partial on the bash side:** the two scripts the ticket named were converted, and 12 scripts still assign 22 of these literals (`open_*_tunnel.sh` → FIN-46, `install_tekton.sh` / `install_linkerd.sh` / `uninstall_linkerd.sh` → FIN-19/FIN-20, `create_team_account*.sh` / `delete_team_account.sh` / `user_management_helpers.sh` → FIN-30). |
| FIN-85 | landed | `ls tests/*.sh` → nothing; the three prompting bash scripts are gone, replaced by `tests/e2e/`. |
| FIN-88 | landed `7243da9` | `git grep -n twuni -- templates/` → empty; the `registry` release is commented out. **Note for anyone running a lane from a working checkout:** `initialize` copies that helmfile template into `platform_config/{env}/kubementat_components/` (`automation.py:121`), so a tree initialized before this change still has a generated copy carrying the `twuni` entry. Re-initializing clears it; only the tracked template is product. |
| FIN-89 | landed `7243da9`, **as removal** | No `kubernetes-dashboard` release in any tracked helmfile template, and the three dashboard scripts plus `kmt login-kubernetes-dashboard` are gone. The *replacement* is undecided (§1.8 B2) — so "landed" here means the blocker is closed, not that kubementat installs a dashboard. |
| FIN-70 | landed (Wave 7) | `src/kubementat/config.py` says `TEKTON_API_VERSION = "v1"` with no env override (`monkeypatch.setenv` changes nothing); `preflight_tekton_api_version` + `KubernetesUtils.api_version_is_served` refuse to apply anything when the cluster serves no `tekton.dev/v1`. `uv run pytest -q tests/test_tekton_api_version_preflight.py` |
| FIN-71 | landed (Wave 7) | `git diff --stat tekton_ci` — 38 files, comments intact (a `safe_dump` rewrite would have dropped 307 comment lines). The transformer's own guards held: the two EventListener `resources` blocks and the 10 `volumeClaimTemplate.spec.resources` blocks were left alone |
| FIN-72 | landed (Wave 7) | `git diff --stat examples/tekton_ci` — 16 files; `generate_pipeline_run.sh` emits `tekton.dev/v1` + nested `taskRunTemplate`/`timeouts`; `tools/tekton_v1_migrate.py` is deleted (never committed, so it is simply gone). `grep -rn "apiVersion: tekton.dev/v1beta1" tekton_ci examples` → empty |
| FIN-73 | landed (Wave 7) | `CHANGELOG.md` Unreleased section (hard flip, no override, what operators must do, what did *not* change) and `docs/README_TEKTON.md` (pinned-version/API-version table + migration section) |
| FIN-74 | landed (Wave 7) | The pins moved to Pipelines **1.17.1** / Triggers **0.37.2** / Dashboard **0.73.0**, and `install_tekton.sh` builds its four release URLs from one `TEKTON_RELEASE_BASE_URL` (`infra.tekton.dev`) because the old bucket 404s what the new pins are. The install now asserts the served versions through the existing preflight path (`preflight_installed_tekton_api_versions`, called from `install_kubementat` after `install_tekton.sh` and before helmfile). `grep -rn "storage.googleapis.com/tekton-releases" scripts src templates` → empty; `uv run pytest -q tests/test_tekton_install_asserts_the_served_api_versions.py` → 10 passed; `uv run pytest -q` → 247 passed |
| Wave T | harness runs | `tests/e2e/suite.py --lane {smoke,full} [--keep-vm]` drives a real VM: the full lane recorded 88 asserts / **16 red** (`~/.kmt-e2e/runs/20261027T093845Z-full-d1cc5dc`). A ticket whose harness exists but whose asserts are red is **not** done — the asserts are the ticket, and the reds are TODOs items 1–5. Two of those are harness-side and landed 2026-10-28 without a lane (TODOs 4/5): the tunnel probe now follows grafana's 302 and stops the tunnel by process group with the port polled until free, and the Grafana API probe logs in with the credentials `platform_config/{env}/static.encrypted.json` names to an address the node can reach. `uv run pytest -q tests/test_the_tunnel_probe_measures_the_tunnel_it_started.py tests/test_the_grafana_probe_reads_the_run_own_configuration.py` → 13 passed. What that changes per assert — which red is expected to go green and which one stays red because the product owns it — is stated in TODOs item 5, not guessed here. |

Everything not named above is open. Concretely, as of `92de42c`: **eight of the ten Wave 0 tickets are
landed** (all of Wave 0 except FIN-06, which is partial, plus FIN-09 and FIN-10), **Wave 7 is landed and
its version bump is in**, and most of Waves 1-7 are untouched. §1.3b is the ordered remainder; the numbers
in the ticket rows are effort, not progress.

Wave T is not "extra" — it is what makes the remaining work safe, and it is the thing currently missing:
**about 111 h of ticket work remains, and not one hour of it has been proved against a cluster since
`d1cc5dc`.** If capacity is limited, the minimum coherent slice is still Wave T + what is left of Wave 0 +
FIN-50/51 + Wave 1: a verified, Python-only install path. Note that Wave T includes FIN-88/FIN-89: the
suite is physically unable to install anything until FIN-88 lands, so they are scheduled with Wave T rather
than with the Waves that follow it.

### Wave 0 — Make the rest cheap (do this first, in parallel with Wave T)

| ID | Ticket | Deps | Effort | Cx |
|---|---|---|---|---|
| **FIN-01** | Move `lib/`+`cli/` → `src/`, fix packaging, delete `sys.path` hacks — **landed `eb37c34`** | — | 3h | Med |
| **FIN-02** | `src/kubementat/shell.py`; remove all `shell=True` call sites — **landed `4f78754`** | — | 3h | Med |
| **FIN-03** | Test harness: argv-recording shims + CLI surface tests — **landed 2026-10-27** | FIN-02 | 8h | Med-High |
| **FIN-04** | `KubernetesUtils`: fix D4/D5, add missing RBAC/namespace primitives — **landed `05214d4`**, primitives still caller-less until FIN-30/31 | — | 4h | Med |
| **FIN-05** | `Config`: `KUBEMENTAT_HOME`, fix D6, no logging side effect, `tekton_team_trigger_dir` — **landed 2026-10-28**. *Deliberately not done:* "API version overridable" — FIN-70 makes `v1` a hard flip with no override, and a knob here would be the shim §1.7a forbids | FIN-01 | 3h | Low-Med |
| **FIN-06** | CLI hygiene: D3 tunnel collision (DONE), D9 `dev1` (DONE), dedupe `run_script` (DONE, `cli/helpers.py::run_kubementat_script`) — ~~partial: the `--verbose/--quiet` root flags were never added and D7's dead `tekton_ci/automation/*.sh` glob is still OPEN~~ **the D7 half closed 2026-10-29 (`e6b3afd`)** by deleting the command that held the glob; what remains genuinely unshipped from this row is the `--verbose/--quiet` root flags, and that is a separate feature and not residue | FIN-01 | 3h | Low-Med |
| **FIN-07** | Make component namespaces configuration, not code: `Config.component_namespace()`, defaults in `constants.py`, add all `*_DEPLOYMENT_NAMESPACE` keys to the templates, and replace every hardcoded namespace in Python **and** bash — **landed 2026-10-28 for `src/`, the templates, the two scripts that caused lane reds, and the harness's own guess. 12 bash scripts still hold namespace literals** | FIN-05 | 6h | Med |
| **FIN-08** | Delete `cli/kmt`, `src/kubementat/utils.py`, `create_team_account.py` stub, `__pycache__` leftovers — **landed 2026-10-27** (`cli/kmt` went with FIN-01; no `__pycache__` was tracked, the stale bytecode of the deleted module went with it) | — | 0.5h | Low |
| **FIN-09** | Drop `jq`/`yq` from `KUBEMENTAT_DEPENDENCIES`; replace `python3`/`pip` with `uv` — **the `yq` half landed 2026-10-29** (§1.3a's D12 row has the measurement, and `jq` is kept on purpose because a shipped script calls it). The `python3`/`pip`→`uv` half is **not** done: `pip` is still declared while nothing execs it but `uv`, which is exactly the kind of entry the new guard flags as carried-without-a-proof rather than attested | — | 1h | Low |
| **FIN-10** | CI: add a pytest job so this wave's tests actually run | FIN-03 | 3h | Low-Med |

**FIN-01 detail (decision 1).**
```bash
git mv lib/kubementat src/kubementat
git mv cli src/cli
```
```toml
[tool.setuptools.packages.find]
where = ["src"]
include = ["kubementat*", "cli*"]
```
Then, in the same commit: delete every `sys.path.append(...)` from the 5 `commands/*.py` modules, change
`from lib.kubementat.helm_utils import HelmUtils` → `from kubementat.helm_utils import …`, delete the
dangling `lib/` dir and `kubementat.egg-info/`, re-run `uv sync`, and update `.gitignore` (`**/__pycache__/`
is already handled by earlier tickets T002/T005).
Regression tests to add: `subprocess.run([sys.executable, "-c", "import kubementat"], cwd="/tmp")` → exit 0;
`import lib.kubementat` → must raise `ImportError`; `from cli.kmt import main` → works from `/tmp`.
Everything else is a path-only change: line references in this doc stay valid.

**FIN-02 detail (`shell.py`).**
```python
def run(argv: list[str], *, cwd=None, env=None, capture=False, check=True,
        retries=0, retry_backoff=0.5, dry_run=False) -> CommandResult
```

> **Landed shape.** `kubementat.shell.run` also takes `input=` (the six `kubectl auth can-i`
> probes all answer themselves "yes", and needed `text=True` next to it before). `retries`
> sleeps a constant `retry_backoff`, which is what the hand-rolled loop in
> `_run_install_script` did; there is no exponential growth. A non-zero exit raises
> `CommandExecutionError`, not `CalledProcessError`, so any caller that caught the
> subprocess exception had to be re-targeted — `validate_cluster_prompt`,
> `check_cluster_permissions`, `_run_install_script` and `get_pod_name_for_deployment`.
Rules: argv list only; never `shell=True`; `capture=True` stores stdout; non-zero raises
`CommandExecutionError` carrying the full argv. Replaces `_run_install_script` (`automation.py:529`) and the
three `run_script` copies, and the ~18 `subprocess.run` sites in `automation.py` (`:158, 342-353, 379, 457,
475, 495-505`).

**FIN-03 detail (the parity harness).**
- `tests/shims/{kubectl,helm,helmfile,tkn,linkerd,git-crypt,mysql}` append argv to `$SHIM_LOG`, exit 0.
- `tests/conftest.py` fixtures: `fake_binaries` (prepend shim dir to `PATH`), `fixture_repo` (`tmp_path`
  seeded from `templates/` + a minimal `platform_config/dev/fixture/`), `dry_run_env` (`KUBEMENTAT_HOME`).
- `tests/test_parity_*.py`: run `bash script.sh args` and the new Python command on identical args; assert
  the two `SHIM_LOG` call lists match (allowing incidental reordering of pure read calls like `kubectl get`).
- `tests/test_cli_surface.py`: assert the exact expected command set of `kmt --help` and that no name is
  registered twice. This one test alone catches D3.
- `tests/test_import_hygiene.py`: FIN-01's assertions + `rg sys.path.append src/` empty.
- Cluster-touching tests get `@pytest.mark.integration`, skipped by default.

> **Landed shape.** The shims are eight symlinks onto `tests/shims/_recorder.sh`, which records argv,
> cwd and (base64) stdin, and answers from `$SHIM_RESPONSE_DIR`. One recorder rather than nine, because
> a bug in the recorder multiplied across every parity test — and it has one: each call is written as
> **one** `printf >> log`, so the two halves of `kubectl create … --dry-run=client -o yaml | kubectl
> apply -f -` cannot interleave their records. `yq` is a shim too (YAML⇄JSON only) for the same reason:
> `run_pipeline.sh` uses `yq e -j`, which is mikefarah v4 syntax, and a host with python-yq fails that
> script on its first line — which is worth knowing before deleting anything, and is a FIN-09 fact.
>
> The comparison is **not** a text diff of two argv lists, because that cannot work: bash speaks `kubectl`
> and the Python replacement usually speaks the generated client, and `kubectl apply -f -` and
> `create_or_replace_secret` are the same act with no textual overlap. So `tests/cluster_parity.py`
> reduces both sides to `ClusterOperation(verb, kind, namespace, name, facts)` under four rules written
> into its module docstring — apply/create/replace are one verb (`ensure`), a read is a read (`get`,
> `describe`, `list` all collapse, and the *name* goes with them), writes stay ordered and reads do not,
> and the payload is compared where it is the claim (Secret type + data keys, imagePullSecrets, the whole
> substituted `PipelineRun` manifest). Anything it cannot recognise raises `ParityError` instead of being
> skipped, and a difference has to be *declared* to pass. `ApiRecorder` is the Python side's `$SHIM_LOG`.
>
> **What it proved, which is the point of the ticket.** bash and Python agree on the cluster for all 12
> FIN-60 scripts (`uv run pytest -q tests/test_parity_tunnels.py tests/test_parity_secrets.py
> tests/test_parity_tekton_runs.py tests/test_parity_kubernetes_reads.py
> tests/test_parity_generate_ssh_key.py` → 34 passed). Six differences came out of it, all documented in
> the test that found them rather than averaged away: the tunnels' `kubectl config current-context` (bash
> only, reads the local kubeconfig); polaris/vault local-port defaults (CLI: 8082/8205, script: 8080/8200
> — driven equal in the parity tests, so it cannot hide); `configure_secrets.sh` labels the Secret it
> applies and the client does not; a not-installed component makes bash forward to `pod/null` and exit 0;
> `uninstall_pipelines.sh`'s `|| true` also tolerates a *refused* delete, which `delete_namespace`'s
> 404-only tolerance does not; `run_pipeline.sh`'s refusal of a parallel run exits 1 while the CLI exits 0
> because the command module drops the return value.
>
> **FIN-60 acted on it.** 8 of the 12 were deleted in the next round (§7), and the parity suite is the same 34 it was, because
> a deleted script's half of a comparison does not need a run to survive: the tests now carry the
> operations the script sent, recorded from the script, and still run the Python side for real. One
> test folded into the branch it was describing, so the count is 34 for a different reason than the
> 34 before.
>
> **What it cannot prove,** and no lane can substitute for that claim: the two rows whose Python
> equivalent is a *library call* (`retrieve_node_addresses.sh`, `view_cluster_resource_versions.sh`) have
> no CLI command yet, so their commands must be added before FIN-60 deletes them; the scripts' jq/yq/sed
> output formatting is not compared (the harness compares operations and payloads, not stdout); and
> nothing here proves a real cluster honours the same API. FIN-60's deletions still want a smoke lane.

**FIN-04 detail.** Fix `create_or_replace_role` (accept `name`, or read `body["metadata"]["name"]`, and use
`replace_namespaced_role`); delete the duplicate `get_role_binding`; add `create_or_replace_cluster_role`,
`create_or_replace_cluster_role_binding`, `delete_cluster_role`, `delete_role`, `delete_role_binding`,
`create_or_replace_service_account_token_secret`, `get_service_account_token`, `list_namespaces`,
`delete_namespace(ignore_not_found=True)`, `exec_in_pod`, `stream_logs`, `get_nodes`, `get_api_resources`,
`list_pods`, `get_pod_logs`. Nearly every Wave 2/4 ticket needs ≥3 of these — hence a single prerequisite.

> **Landed shape.** The name is an argument of every `create_or_replace_{role,role_binding,cluster_role,
> cluster_role_binding}` rather than a read out of `body` — `body` arrives both as a client model and as a
> plain dict, and picking one of the two paths was the guess that hid D4. Deleting a namespaced role, a
> role binding or the cluster role tolerates 404 (§3's tolerance rule, not a blanket `|| true`: every other
> status reaches the caller), and `delete_namespace` grew `ignore_not_found` so an uninstall that must
> *prove* absence can ask loudly. `get_service_account_token` locates the Secret by its
> `kubernetes.io/service-account.name` annotation instead of the `-token` name convention, so it still works
> where 1.24 stopped minting tokens — that is the FIN-36 half, landed early. `exec_in_pod` and `stream_logs`
> go through `shell.run` with argv and inherit the FIN-02 rules; `get_pod_logs`, `list_pods`, `get_nodes`
> and `list_namespaces` are client reads.
>
> Two guards exist because a fake client hides two different mistakes: the 409 branch, which no test that
> starts from a pristine API can see, and the generated client's method names (`list_namespaced_pods` is not
> a method; `list_namespaced_pod` is). No caller in `src/` uses the new primitives yet — they are the
> prerequisite, and each lands with the ticket that needs it. **Not verified against a live cluster:** none
> was reachable on the development host, and §8.4 lets a Wave 0 ticket carry the unit suite as its evidence.

**FIN-05 detail.** `kubementat_main_dir = os.environ.get("KUBEMENTAT_HOME") or Path(__file__).parents[2]`;
`platform_config_dir` overridable by `KUBEMENTAT_PLATFORM_CONFIG_DIR`; initialize `team_static`/
`team_static_encrypted` to `None` before the `os.path.exists` branches so a missing team config raises a
pointed `FileNotFoundError` instead of `UnboundLocalError`; move `logging.basicConfig` to the CLI entry point.

> **Landed shape.** Precedence is **argument > environment variable > built-in default**, so a test or a
> one-off run can redirect one instance without setting a variable for the whole machine. Both variables
> fail loudly when they are *set but empty* — the plan's `os.environ.get(...) or default` formula would
> have fallen back to the checkout there, which is D11's shape again (config read from the wrong tree,
> nothing said). `kubementat.config.default_kubementat_main_dir()` is the one derivation and
> `cli.kmt.Config` calls it, which retires the `os.path.join(home, '..', '..')` correction FIN-01 had to
> make. `configure_logging()` moved with it: `import kubementat` installs no handler and changes no level,
> and the library's `logging.*` calls now answer to whoever imported it.
>
> `Config.tekton_team_trigger_dir` is `{platform_config_dir}/{environment}/{team}/triggers`, a sibling of
> the pipelines/tasks/pipeline-runs dirs, and `initialize` scaffolds it. **FIN-13 moved the read side**:
> `TektonUtils.setup_triggers()` applies that directory, `initialize` no longer creates
> `tekton_ci/triggers/{TEAM}/`, and `generate_trigger_config_from_templates.sh` now writes into
> `platform_config` as well, so the two sides agree. What FIN-23 still owes is the **generator itself** —
> those four `sed` passes are still bash, and its parity test is what proves the generated manifests are
> byte-identical to what the templates + substitutions produced.
>
> Two things in the ticket title deliberately did **not** land: the `TEKTON_API_VERSION` override (FIN-70
> makes `v1` a hard flip with *no* env override, so a knob here would be exactly the shim §1.7a forbids),
> and any relocation of generated trigger content.

**FIN-06 detail.** Split `tunnels.py` so `tunnel_polaris` and `tunnel_vault_ui` are distinct functions and
both are registered; thread a real `--team`/`TEAM` default into every tunnel/helm command instead of
`'dev1'`; fix `utilities.py` to scan `scripts/**/`; move `run_script` into `src/cli/helpers.py`; add
`--verbose/--quiet` on the root group.

**FIN-07 detail (decision 5).** Today namespaces live in three inconsistent places:
`login_kubernetes_dashboard.sh:22` *reads* `KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE` from `static.json`;
`uninstall_kubementat.sh` hardcodes ~15 of them; Python hardcodes 4 (`automation.py:994,1020,1044,1068`,
each with `# TODO: make this a config`). Converge on:

1. `Config.component_namespace(component: str) -> str` reading `env_static['<COMPONENT>_DEPLOYMENT_NAMESPACE']`,
   falling back to `constants.DEFAULT_NAMESPACES[component]` and logging once when the fallback is used.
2. Add the missing keys to `templates/environment/static.json.template`: `GRAFANA_`, `PROMETHEUS_`, `LOKI_`,
   `VAULT_`, `POLARIS_`, `TEKTON_`, `TEKTON_DASHBOARD_`, `CERT_MANAGER_`,
   `NGINX_INGRESS_`, `CLUSTER_CRONJOBS_` — each suffixed `DEPLOYMENT_NAMESPACE` — plus
   `*_SERVICE_SELECTOR` for the tunnel commands, so FIN-46 does not reintroduce hardcoded selectors.
3. Per §1.7a: no fallback reads of *old* locations, no per-script constants. A missing key must fail with an
   error naming the file to edit, not silently default.
4. `initialize` writes the keys, so an already-initialized repo picks them up only on re-initialization —
   explicitly acceptable here (§1.7a). This is also the ticket that makes FIN-11 (config-driven uninstall)
   possible, and it must land before it.

> **Landed shape.** `Config.component_namespace(component)` and `Config.component_service_selector(component)`
> read one key from `env_static` each, derived from the component name by
> `kubementat.config.component_config_key` (`tekton-dashboard` → `TEKTON_DASHBOARD_DEPLOYMENT_NAMESPACE`) so a key is
> never spelled out per call site. An unknown component raises rather than defaulting — deploying into a
> namespace named after the typo is the same bug pointing the other way. A key that is missing **or empty**
> falls back to `constants.DEFAULT_NAMESPACES` and warns once per `Config` instance, naming the key *and the
> file*: a config tree written before these keys existed has to keep working, and the warning is what makes the
> default visible. (§1.7a's "fail loudly" lands here as *fail visibly*, because item 1 above asks for the
> fallback itself.) `constants.DEFAULT_SERVICE_SELECTORS` exists for the same reason: the tunnels interpolate a
> selector, and a `*_SERVICE_SELECTOR` key nothing reads would be dead config.
>
> On the bash side there is no `constants.py` to fall back to, so a missing key is fatal rather than warned
> about; both scripts converted here error naming the file, before they delete anything. **14 scripts assigned
> a component namespace before this change, and 12 of them still do** — 22 assignments across the four
> `open_*_tunnel.sh` (FIN-46), `install_tekton.sh`
> (FIN-19/FIN-20), and the four user-management scripts (FIN-30). §9's checkbox is about `src/` and decision 5
> says `src/`, so the scripts half is recorded here rather than pretended done; `uv run pytest -q
> tests/test_component_namespaces_come_from_configuration.py` is what holds `src/` to it.
>
> Two choices worth knowing before touching this again: `tekton` is the component whose default namespace is
> `tekton-pipelines`, so the dashboard tunnel asks for `tekton` and not `tekton-dashboard`; and
> `uninstall_kubementat.sh` now deletes `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE` too, which is the namespace the
> lane's two `cleanup-cronjob-*` asserts were looking for. The per-team `APP_DEPLOYMENT_NAMESPACE` /
> `PIPELINE_NAMESPACE` values were left alone — a team is not a component, and enumerating teams is FIN-11's.


### Wave T — Real-VM integration suite on `virt-runner` (**LANDS FIRST — it is the verification mechanism, manually run**)

Nothing in Waves 1–7 merges without a green smoke run. This wave is deliberately last in the numbering and
first in the schedule; if you implement only one thing from this document, implement this one.

**Mechanism** ([`~/dev/os_projects/virt-runner/README.md`](../../os_projects/virt-runner/README.md)):
`virt-runner create NAME --json` boots a KVM guest you can then drive over SSH; `--script FILE` runs a
host-side script inside the guest once SSH is verified; `virt-runner destroy NAME` tears it down. That maps
1:1 onto the required loop: **create VM → install single-node cluster → install kubementat fully → exercise
main features → uninstall → destroy VM**.

| ID | Ticket | Effort |
|---|---|---|
| FIN-80 | Harness skeleton `tests/e2e/suite.py` (stdlib + `subprocess` only): unique VM name, clean leftover VMs before starting, `create → stage* → assert → destroy` in a `finally`, parse the `--json` envelope, surface `error.code`/`stage`, flags `--lane` and `--keep-vm` | 8h |
| FIN-81 | Provision stage `stages/01-provision.sh`: k3s single-node (`curl -sfL https://get.k3s.io \| sudo sh -` — **measured 9.6 s**, no `INSTALL_K3S_*` flags, no `apt upgrade`, no kernel-module prep needed) + prerequisites (`helm`, `helmfile` **1.8.2** not 2.x, `helm-diff`, `tkn`, `uv`, `git`, `git-crypt`, **`yq`** and **`python3-pip`** — both required by `KUBEMENTAT_DEPENDENCIES` and both previously missing from this list; `jq` is already in the cloud image). **2026-10-29: `yq` is no longer required by that check** (FIN-09 removed it — 0 scripts call it), so the guest only needs it if something else in the lane wants it. `pip` still is. Then make kubeconfig usable: write `write-kubeconfig-mode: 644` to `/etc/rancher/k3s/config.yaml` and restart k3s (`chmod` alone does not survive a restart). Prune the 4 stale `initialize` marker files in the guest copy. See research §3.3–§3.5 (measured recipe) | 8h |
| FIN-82 | Install+run stage `stages/02-install-kubementat.sh`: `kmt initialize → install → tekton-setup-pipelines → tekton-setup-triggers → setup-docker-registry-access → setup-secrets → tekton-run-pipeline hello-world`, asserting the PipelineRun reaches `Succeeded` by **bounded polling of `kubectl get pipelinerun -o jsonpath`** (FR-32 — never by scraping `tkn … describe/logs`, and never by `sleep`), passing `--yes` to `kmt install`/`kmt uninstall` (the `--force`-equivalent FR-30a landed in `30a940f`/`8392889`). The smoke lane deliberately uses **dummy** `AUTOMATION_GIT_*` values — `hello-world-pipeline.yml` takes no git/volume params, only a public CI image, so no credentials are needed | 4h |
| FIN-83 | Teardown stage `stages/99-teardown.sh`: `kmt uninstall --force`, then assert zero kubementat namespaces, zero `tekton.dev` CRDs, no `helm-deployer-cluster-role`, **no kubementat PVCs**, with `kubectl get ns` snapshots before/after, plus FR-63's three confirmation cases. **This is what proves FIN-11** | 3h |
| FIN-84 | Feature-coverage stage for the full lane: mysql/redis tasks, kubernetes-dashboard install+login, every `kmt tunnel-*` (short timeout + HTTP probe), user management (create / read-only / delete), grafana account creation | 8h |
| FIN-85 | Remove all human interaction from the suite: the `read -p` prompts and `kubectl config current-context` gates in `tests/*.sh` make them un-runnable unattended; replace the 3 bash test scripts (closes FIN-24's test half) | 2h |
| FIN-86 | Wire the entry point: `uv run python tests/e2e/suite.py --lane smoke` (and `--lane full`), no Makefile needed (the repo has none), the KVM host prerequisite (`virt-runner setup`) documented in `docs/README_DEVELOPMENT_FLOW.md` including the re-login-for-groups gotcha. **No scheduling, no CI hook, no wrapper script** — per decision 3/4 a lane starts only when a human types the command | 3h |
| FIN-87 | Artifact: persist per-run `scripts[].exit_code`, VM spec and namespace snapshots next to the ticket record so a finished ticket cites an actual run | 2h |
| **FIN-88** | **Delete the dead `twuni` repo (decided).** Remove the `twuni` entry at `helmfile.yaml.gotmpl:62` **and** the `registry` release that depends on it (`chart: twuni/docker-registry`, lines ~253-259, `group: extra`, plus its `docker_registry/values.encrypted.yaml` reference) in both `templates/environment/kubementat_components/` and `platform_config/dev/kubementat_components/`. No vendoring, no shim. `kmt setup-docker-registry-access` stays — it creates a Secret, not a chart — but note in the CHANGELOG that no in-cluster registry is deployed by kubementat until a replacement solution lands | 3h |
| **FIN-89** | **Swap the retired dashboard for a maintained one (decided: we keep a dashboard).** Confirm the chart by spike, then: new repo + release in both `helmfile.yaml.gotmpl` copies, values dir renamed, `install_kubernetes_dashboard.sh` / `uninstall_kubernetes_dashboard.sh` / `login_kubernetes_dashboard.sh` updated, and the `KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE` key reconciled with FIN-07's namespaces-as-config. **Verify the `svc/kubernetes-dashboard-kong-proxy` port-forward in `login_kubernetes_dashboard.sh:56`** — Headlamp exposes a different service, so this breaks unless fixed. No alias, no back-compat (§1.7a) | 6h |

**FIN-88 is a prerequisite for FIN-82/FIN-83** — without it, `kmt install` cannot succeed in any lane.
FIN-89's direction is decided (keep a dashboard, swap the chart), so it is product work scheduled alongside
Wave T rather than a blocker waiting on a decision. See §1.8.

**Lanes:**

| Lane | When | VM | helmfile group | Assertions | Target |
|---|---|---|---|---|---|
| `smoke` | every ticket, run by hand | 4 vCPU / 6 GiB / 25 GiB (measured allocatable: `cpu=4`, `memory=5842 MiB`, `pods=110`) | `standard` via `--helmfile-installation-group` (changed 22 Oct 2026: `dashboard` no longer exists, FIN-89) | initialize, install, pipelines+triggers+secrets, hello-world `Succeeded`, clean uninstall | < 25 min |
| `full` | whenever someone decides it is worth ~1 h of their time | 8 vCPU / 16 GiB / 50 GiB | `standard` + `routing` + `dashboard` | everything above + mysql/redis, dashboards, tunnels, user mgmt, grafana, idempotent re-install | < 2 h |

The `smoke` lane's group choice went through two revisions and is now **settled**: the original plan asked for
a tekton-only `group: core` label, requirements decision **D5** rejects new groups and reuses the existing
`dashboard` group instead, and the dashboard release under it is being replaced rather than deleted (§1.8 B2).
So: no new label, `--helmfile-installation-group dashboard` stays the mechanism, and what it installs becomes
a maintained dashboard instead of an archived one.

**Measured, replacing estimates** ([research §4](virt-runner-setup-research.md)): create (warm cache)
16–30 s · `cp -a` of the tree over virtiofs 1.4 s · k3s 9.6 s · kubeconfig fix ~8 s · node `Ready` ≤ 10 s ·
tooling ~63 s · `uv sync --all-extras` 2.9 s · `kmt initialize` 1.2 s · destroy 0.3 s.
**create + provision + initialize ≈ 2:00**, inside the 3:40 the lane budget assumed. Only install/feature
timings remain estimates, and they cannot be measured until FIN-88/FIN-89 land.

**Harness constraints, given virt-runner's actual semantics:**

- **`--mount` is read-write.** `kmt initialize` mutates the repo (`git-crypt init` writes into `.git/`, drops
  gpg/ssh/git-crypt key files, writes `platform_config/dev/*`). Use the mount only to *deliver* the tree,
  then `cp -a /mnt/kubementat /var/tmp/kmt-e2e` inside the guest and work there — otherwise every E2E run
  re-initializes the developer's actual checkout. (`destroy` never touches the host dir, which is cold
  comfort.)
- **A failing `--script` leaves the VM running** (`script-failed`, exit 1). `destroy` must be unconditional
  in the harness unless `--keep-vm` was passed.
- **`--json` means exactly one JSON document on stdout**, stage output goes to stderr. Never parse stderr,
  and don't let a stage echo stray JSON.
- **Unique VM names** (`kmt-e2e-<ticket>-<n>`) because Waves 2–4 fan out across agents, plus a pre-clean of
  leftovers the way virt-runner's own `tests/integration_test.py` does — and only destroy VMs you created.
- **Keep the guest image cached** (`~/vm-images/<distro>/<release>/`): ~2 min cold vs ~20 s warm is the
  difference between a pleasant and an abandoned smoke lane.
- **Pin the guest explicitly:** `--distro ubuntu --release resolute` (Ubuntu 26.04), passed on the
  command line rather than inherited from virt-runner's profile default, and recorded in `result.json`.
  The cached image on this host is already `resolute-server-cloudimg-amd64.img`, so this changes nothing
  today — but a profile default change would otherwise silently move which OS the entire suite proves.
  Ubuntu is the only supported guest (§7 exclusions).
- The host needs `virt-runner setup` once, including the re-login for the `libvirt`/`kvm` groups, and an
  active `vm-pool` pool + `default` network — surface that as a readable preflight failure, not a stack trace.
  On this machine `setup --check` is already green, so nothing is needed per run (research §2).
- **`destroy` is not idempotent** (research F3): destroying a nonexistent VM exits 1 with
  `vm-not-defined`. INV-2's unconditional destroy must treat *that code* as success and every other error
  as loud.
- **Never use the raw `vm.ssh_command`.** A reused DHCP lease aborts the connection with
  `REMOTE HOST IDENTIFICATION HAS CHANGED` (hit once mid-run). Use a hardened line with
  `BatchMode=yes`, `StrictHostKeyChecking=accept-new` and a per-run `UserKnownHostsFile`; pipe multi-line
  stages via `ssh … 'bash -s'` (research §3.1 / F5).
- **Preflight must check `df`, not `pool-info`** (research F2): the pool reported 92 GiB available while
  `df` showed 49 G — sparse qcow2 accounting lies. Budget ≥ 20 G host-free per smoke VM; measured real
  host writes are ~16 GiB per idle smoke VM and `destroy` reclaims them exactly.
- **`kmt initialize` refuses to run on the copied tree** if `platform_config/{dev}/{,smoke/}static{.encrypted,}.json`
  exist — and a normal dev checkout has them as untracked leftovers. Deleting exactly those 4 files in the
  guest copy is the *one sanctioned mutation* of the copied tree (research §3.2 / F10); the harness must do it and
  nothing else may.
- **The k3s kubeconfig is unusable by default**: `/etc/rancher/k3s/k3s.yaml` is `0600 root`, and
  `load_kube_config()` in `KubernetesUtils.__init__` makes even `kmt initialize` fail. The durable fix is
  `write-kubeconfig-mode: 644` + restart, not `chmod` (research §3.4).

**Why real VMs and not mocks** — the bug classes this catches that §5's shims cannot:

1. A wrong storage-class assumption (`hostpath` vs `local-path`) that leaves PVCs unschedulable forever.
   Note the research's correction: `local-path` on k3s is `WaitForFirstConsumer`, so a probe PVC stays
   `Pending` **by design** until something consumes it — a naive "PVC must bind" assertion fails on a
   healthy cluster. Requirement FR-21 was rewritten for this.
2. Commands that crash on the **second** run against a cluster that already has the resources — i.e. **D4**,
   live in `create_or_replace_role` today, and invisible to any test that starts from a pristine API.
3. Uninstall that leaves namespaces or CRDs behind — FIN-11's entire acceptance criterion.
4. Tunnels that bind a port but select the wrong pod, or break because a namespace moved (FIN-07's payoff).
5. `v1` resources that apply cleanly but never run — the signature failure mode of an early, hard API flip.
6. Helm/helmfile drift between templates, `values.encrypted.yaml` and the actual chart version.

---

### Wave 1 — Close the core install/uninstall path (6 tickets)

After this wave `kmt install`/`kmt uninstall` are 100% Python and `scripts/automation/**` has no runtime dependency.

| ID | Ticket | Deps | Effort | Cx |
|---|---|---|---|---|
| FIN-11 | `Automation.uninstall_kubementat()` + `uninstall_component(component)` | FIN-02,04,07 | 5h | Med-High |
| FIN-12 | `TektonUtils.install()` — pipelines, dashboard, triggers, `helm-deployer-cluster-role` | FIN-02,04 | 3h | **landed** — verified by the unit suite + the shim harness; a lane still owes the cluster half |
| FIN-13 | `TektonUtils.setup_triggers()` | FIN-04,05,07 | 6h | **landed** — `implemented-unverified`; the unit suite + the parity record cover it, a lane still owes the cluster half |
| ~~FIN-14~~ | ~~`Automation.install_linkerd()` / `uninstall_linkerd()`~~ — **withdrawn 2026-10-28**: kubementat does not use Linkerd, so there is nothing to install. `uninstall` does sweep a leftover mesh (§1.3b row 5a). | FIN-02,07 | ~~4h~~ 0h | out |
| FIN-15 | `DashboardUtils.get_login_token()` + tunnel | FIN-04 | 2h | Low |
| FIN-16 | `CiUtils.run_ci_container()` | FIN-04,07 | 3h | Med |

**FIN-11 — uninstall.** Replace the 153-line bash with: print the cluster it is about to wipe (context name
**and** node list, read from the cluster) → state that the deletion is irreversible → read one confirmation
from stdin → delete. **The flag is `--force`, not `--yes`** (reconciled with requirements FR-63/FR-64 and
FIN-83, which assert it): with `--force` it deletes immediately and **never reads stdin**; without it, if
stdin is not a terminal it must fail closed with a message naming `--force` — never hang, never delete.
(The old `read -p` loop in `uninstall_kubementat.sh:29-35` is the reason the old suite needed a human.
FIN-62 should unify this with `tekton-uninstall-pipelines`' `-y`.) Then: delete per-team namespaces derived
from `platform_config/{env}/*/static.json` (**not** `dev1`/`smoke` literals) → delete component namespaces
via `Config.component_namespace()`, each `ignore_not_found=True` → take Linkerd off the cluster when it is
still there (its `linkerd.io` definitions first, then its two namespaces, tolerant when the mesh was never
installed) → delete Tekton/Triggers/Resolution CRDs → delete
`helm-deployer-cluster-role` → print the before/after namespace diff. Bash's final `kubectl api-resources`
dump becomes `--explain`, not default output. **Every kubementat-owned namespace and every PVC it created
must go** (FR-61): the bash `uninstall_helm_deployment.sh:57-58` currently only *prints advice* telling the
operator to delete PVCs by hand, and advice-only is a requirement failure here. **That failure is closed in
code by FIN-19** (§1.3b row 6b): `HelmUtils.uninstall_release()` deletes the claims its release made, by the
`app.kubernetes.io/instance` label, in the same command that deletes the release — the same mechanism this
step already uses over the whole platform (`_delete_claims_in`). What stays owed is the lane assert that
proves a claim is gone on a real cluster, not the mechanism.

**Landed as `213bc9b`, with one deviation from this paragraph.** The flag stayed `--yes`/`-y`, not
`--force`: that is what `8392889` shipped, the capability table's `uninstall` row passes `--yes`, and two of
FR-63's three cases are asserted against that spelling (`confirmation-fails-closed`,
`uninstall-advertises-yes-flag`). Renaming it would have made a green lane assert red for a cosmetic reason,
so the command's behaviour matches the paragraph and its *name* matches what is already published. The other
difference is where the confirmation lives: the CLI raises the `UsageError`, the library prints the cluster
and asks, and answering "no" is `Cancelled uninstall script.` with nothing deleted. `--explain` was not
implemented; the discovery dump is simply not printed, which is what the paragraph wanted anyway.

**FIN-12 — `TektonUtils.install()`. Landed, `implemented-unverified` in the lane sense.** It replaced
the last bash script on the default `kmt install` path: four `kubectl apply` calls assembled from one
`TEKTON_RELEASE_BASE_URL` and the three `TEKTON_VERSION_*` pins (`release_manifests_to_apply`), the
Pipelines release addressed without a namespace and the other two addressed at
`Config.component_namespace("tekton")`, and `helm-deployer-cluster-role` written through
`KubernetesUtils.create_or_replace_cluster_role` — FIN-04's caller-less primitive finally has a caller.
Three deliberate differences from the deleted script, each pinned by its own test:

* the ClusterRole is created-or-replaced, so a rerun is the same write (the `409` branch), and a
  refusal that is not a 409 is not swallowed;
* a missing or blank `TEKTON_VERSION_*` key raises `TektonReleaseVersionNotConfiguredError` naming the
  key **before** the first command runs — bash read it with `jq -r`, got `null`, and turned the whole
  failure into a 404 on a URL nobody wrote down;
verification evidence: `uv run pytest -q` -> **417 passed** in ~18 s, of which
`tests/test_parity_tekton_installs_the_pinned_releases.py` (7) compares the Python path against what the
deleted script sent — four identical URL fetches, one identical ClusterRole operation, and the record
that the install executes `kubectl` and nothing else — and
`tests/test_the_tekton_install_reads_its_configuration_first.py` (7) covers the happy path, the 409
upgrade branch, a refusal that is not a 409, the missing-pin branch, and that a refused manifest stops
the install. `uv run pytest -q tests/test_tekton_install_asserts_the_served_api_versions.py` -> 10
passed, now run against the Python installer instead of against bash, and `uv run kmt --help` -> 20
commands, no duplicates, nothing new: this ticket added no command.

**What a lane still owes**, in the ticket's own words and in order of what it would catch:
(1) that a real `kubectl apply` of those four URLs lands Pipelines 1.17.1 / Triggers 0.37.2 /
Dashboard 0.73.0 on a clean cluster — this host reaches no API server, so no test here has ever fetched
one; (2) that the client-written ClusterRole is as accepted as the heredoc was, including on a cluster
whose RBAC API answers the create differently; (3) that dropping the two informational reads did not
remove an operator-visible signal anyone depended on; (4) that `kmt install` end to end still installs
(now with one fewer process and one fewer file). None of that is a doubt about the code — it is the
part of the definition of done no unit suite can pay.

**FIN-13 — `TektonUtils.setup_triggers()`. Landed, `implemented-unverified` in the lane sense.** It
replaced the largest script left on the default install path (269 LOC) and the last `tekton.py` command
that shelled out. Three parts, in the script's order, because the objects depend on each other — the
shared ClusterRole before the binding that names it, the Role and its ServiceAccount before the
RoleBinding that binds them, all of it before the EventListener that asks for `tekton-triggers-sa` by
name:

1. seven RBAC objects through `KubernetesUtils` create-or-replace primitives
   (`create_or_replace_cluster_role`, `..._role`, `..._role_binding`, `..._cluster_role_binding`,
   `..._service_account`), so nothing here calls `kubectl` at all. "Already exists" is a replace and not
   an error; a refusal that is not a 409 is not swallowed. The names are module constants because an
   EventListener's pod asks for `tekton-triggers-sa` by name — that string is cluster contract, not
   configuration. The ClusterRoleBinding's name carries the team, which is what stops team B's setup
   from rewriting team A's binding.
2. the two webhook Secrets from the team's `static.encrypted.json`, written only for the provider that
   has a token. This is the one place the port is deliberately **not** equivalent: `jq -r` of an absent
   key answered `null`, which passed the script's `!= ""` guard, so bash created a Secret holding
   `base64("null")` and an EventListener validating against it rejects every webhook quietly. Absent,
   empty, blank and JSON `null` are the same answer here, and the log says which provider was skipped.
3. the team's generated manifests through `Automation.apply_resources(group=TRIGGERS_API_GROUP)`, read
   from `Config.tekton_team_trigger_dir` — that is decision 2, executed here rather than deferred, so
   the generator's output and the flow's input were one change of location and not two.

Four things worth stating, because a reader who only sees the diff will not:

* **A mixed-kind directory is the reason `apply_resources` had to grow.** A team's `triggers/` holds an
  EventListener, a TriggerBinding, a TriggerTemplate **and** an Ingress; applying all four as
  `kind=EventListener`, `group=triggers.tekton.dev` sends three of them to an API group that does not
  answer for them. `kind`/`group`/`version` are now what the caller says applies to documents that do
  not say otherwise, and each document's own `apiVersion`/`kind` wins. A core-group document
  (`apiVersion: v1`) is refused with a message naming the alternative, because the custom-objects
  client addresses `/apis/<group>/<version>/…` and not `/api/v1/…`. `plural_for_kind` replaces
  `f"{kind.lower()}s"`, which asked for `ingresss`.
* `create_or_replace_secret` and `create_or_replace_service_account` grew a `labels=` argument. The
  heredocs labelled everything `managed-by: kubementat` (and `team` for the namespaced objects); an
  unlabelled object is indistinguishable from one the operator wrote.
* The install call site's `retry_on_failure=True` is gone. Each operation is create-or-replace, so the
  retry was a way of re-running a failing `apply`; a repeat run is now the retry.
* A team with no generated triggers is a state, not a failure: the directory is not there, the RBAC is
  still all of it needed, the run finishes. `apply_resources` still raises for a directory that is
  named by config and does not exist.

**State and evidence.** `implemented-unverified`. `uv run pytest -q` → **433 passed** in ~18 s
(`e354bcc`), of which `tests/test_the_trigger_setup_asks_the_cluster_for_the_same_rbac.py` (4) compares
the Python path against a record of the deleted script's eight RBAC heredocs — same objects, same
namespaces, same rules, same order — plus the two webhook Secrets' shape, and
`tests/test_the_trigger_setup_writes_rbac_the_way_the_manifest_did.py` (11) covers the happy path, the
409 replace branch for two kinds, the missing-namespace refusal that writes nothing, the
configured/unconfigured Secret branch, both label shapes, an absent trigger directory, reading
`platform_config/.../triggers/` instead of the checkout, and that each kind is addressed under the group
that answers for it. `uv run kmt --help` → 20 commands, unchanged, no aliases; the only deleted
behaviour is a script. `grep -rn setup_triggers.sh src/` → nothing (the policy file's ledger test fails
if a deleted script is still named).

**What a lane still owes:** (1) that a real API server accepts all seven objects as written — the rules
for `podsecuritypolicies` (`policy`) and `ingresses` under `extensions` are still in them because that
is what the script sent, and a modern cluster simply never asks about them; (2) that the EventListener
pods kubementat creates can actually read `ClusterTriggerBinding`s through the cluster-scoped binding;
(3) that applying a real generated Ingress through the custom-objects path produces the same object
`kubectl apply -f` produced, including for the `nginx` annotations; (4) that `kmt install` end to end
still brings the triggers up. None of that is payable without a cluster, so none of it is claimed.

**FIN-14 — withdrawn, not done.** The owner decided on 2026-10-28 that kubementat does not use
Linkerd, so this ticket has no subject: there is no install to port to `shell.run`, no HA flag to
read, no `viz_config` to apply, and no `--enable-linkerd` to reach them. Everything the ticket
named is deleted instead — `install_linkerd.sh`, `uninstall_linkerd.sh`, `generate_linkerd_certs.sh`,
`open_linkerd_dashboard_tunnel.sh`, the `linkerd` helm repository and commented chart block in
`helmfile.yaml.gotmpl`, the `viz_config.encrypted.yaml.template`, the `LINKERD_*` keys in
`static.json.template`, and both `constants.DEFAULT_NAMESPACES` entries — and one thing is built in
its place: `uninstall` takes a mesh the cluster still carries off the cluster, which is what §1.3b
row 5a says and a lane still has to prove.


### Wave 2 — Components, generators, cleanup job (9 tickets)

| ID | Ticket | Deps | Effort |
|---|---|---|---|
| FIN-17 | `Automation.install_routing()` + letsencrypt ClusterIssuers — **the routing half is withdrawn 2026-10-28** (the controller `install_routing()` would have installed is out of the product, §1.3b row 5b) and **landed 2026-10-29 on the owner's answer to the question that half was waiting for** (`finish-python-migration-last-lap.md` §3.3): `src/kubementat/automations/routing/{routing.py,traefik_presence.py}` + the `kmt routing install [--yes] [--update]` / `kmt routing status` group, both commits `2347583` and `643ed6e`, with `install_routing.sh` deleted and its `group=routing` apply replaced by a decision read off the cluster (`group=routing`, or `group=routing,component_name!=traefik` on a skip — `routing.py:69-70`). The ClusterIssuers land too, and their solver is config-driven, not `nginx`: `http01.ingress.ingressClassName` from `TRAEFIK_INGRESS_CLASS` (`routing.py:280,496`). **State: landed, unit-verified** (`uv run pytest -q` → 704 passed / 1 skipped), **lane-proven on the `routing` lane only** (`~/.kmt-e2e/runs/20261028T205357Z-routing-full-47b186f/result.json`: 94 asserts, 4 red, none of them routing — the 4 are `last-lap` §5's, red on `full` at the same SHA — and 24 asserts that name routing, all green: k3s's bundled controller disabled and gone before the first command, `kmt install` installing no controller, one class bound to `traefik.io/ingress-controller`, 80→443 = 308, TLS terminating from a cert-manager-issued certificate, the second install a no-op skip, `--update` applying the CRDs before the release, uninstall leaving no namespace and no Traefik-shaped CRD). **What that run does not prove:** `ingressClass.isDefaultClass: true` and the `version: "42.1.3"` pin both landed the next morning (`643ed6e`), HTTP-01 against real DNS/ACME is out of reach for the VM lane, the everyday `full` lane has not run since the group got this applier and its `_expect_release_set` is *known-red* on a declared `traefik` until §7.2b's *declared-and-applicable* rule lands (`stages.py:726-737`), §7.2's four full-lane guard asserts never landed, and the INGRESS-REMOVE cluster half in row 5b is still owed. The 25 vs 24 question is settled by the artifact: 24 assertions name routing there, so **"25/25" as written in the plan and in `2347583`'s message is one over the evidence** — same run, same green, fewer asserts. Effort: **done** (was 1 h after the withdrawal, and it is 0 h now). | FIN-02,04 | ~~3h~~ ~~1h~~ **done** |
| FIN-18 | `Automation.install/uninstall_kubernetes_dashboard()` | FIN-02,04 | 3h |
| FIN-19 | `HelmUtils.uninstall_release(component)` + `kmt helm-component-uninstall <env> <component>` (replaces `uninstall_helm_deployment.sh`, which had **no caller in `src/`** — so this is a newcommand and not a port) — **landed**: namespace from `Config.component_namespace`, release name read off the cluster, claims deleted by label. Script deleted, no alias. **State: implemented-unverified / verified-by-unit-suite / lane-required** (§1.3b row 6b) | FIN-07 | ~~2h~~ done | **Name note (`d5f321b`): the command this row documents is `kmt component uninstall` now**, renamed onto the `kmt component` group by FIN-25 — same implementation, no alias, so this row's history is correct and its command name is not. |
| ~~FIN-20~~ | ~~`CryptoUtils.generate_linkerd_ca_certs()` via `cryptography`, no openssl subprocess~~ — **withdrawn 2026-10-28 with Linkerd.** `generate_linkerd_certs.sh` is deleted rather than ported, so the CA the ticket would have generated has no consumer. `cryptography` stays a dependency for what else uses it. | FIN-02 | ~~3h~~ 0h |
| FIN-21 | `TektonUtils.setup_cleanup_job()` (CronJob + RBAC, **namespace from `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE`, not from the code**) — **landed**: `kmt tekton-setup-cleanup-job <env> --team <team>`, script deleted, no alias. **State: implemented-unverified / verified-by-unit-suite / lane-required** (§1.3b row 6a) | FIN-04 | ~~4h~~ done |
| FIN-22 | `TektonUtils.generate_deploy_pipeline(app, --git-url, --git-project-name, --git-server-host, --git-server-port, --git-server-ssh-user)` + `kmt tekton-generate-deploy-pipeline <env> <team> <app>` — **landed**: template read with `yaml.safe_load`, substituted through the table `run_pipeline` uses, `apiVersion` stamped from `Config.TEKTON_API_VERSION` for the `tekton.dev` group only, written to `Config.tekton_team_pipeline_dir/deploy-pipeline-<app>.yml` (**moved out of `tekton_ci/pipelines/`**), reruns overwrite and write the same bytes. `scripts/automation/tekton/generate_deploy_pipeline_from_template.sh` (71) deleted, no alias — **the script had no caller in `src/`, so this is a new command and not a port**. **State: implemented-unverified / verified-by-unit-suite / lane-required** (§1.3b row 6d) | FIN-05 | ~~2h~~ done |
| FIN-23 | `TektonUtils.generate_trigger_configs(app, --pipeline-name, --trigger-type)` + `kmt tekton-generate-triggers <env> <team> <app>` → **writes to `platform_config/{env}/{team}/triggers/<app>/`**, which is exactly where FIN-13 made the reader look; generator *templates* stay in `tekton_ci/triggers/templates/{github,gitlab}/` and the trigger type selects which directory is read. Same substitution and the same `apiVersion` rule as FIN-22. `scripts/automation/tekton/generate_trigger_config_from_templates.sh` (123) deleted, no alias — **the script had no caller in `src/`**. **State: implemented-unverified / verified-by-unit-suite / lane-required** (§1.3b row 6d) | FIN-05 | ~~3h~~ done |
| FIN-24 | `TektonUtils.generate_pipeline_run()` (the bash `tests/*.sh` replacement moved to FIN-85) | FIN-03 | 5h |
| FIN-25 | ~~`kmt component install/uninstall <name>` umbrella wiring FIN-17..21~~ **LANDED 2026-10-29 (`d5f321b`)** as a **group**, not two flat names — which is what FIN-61's regrouping needs to know. `install` applies through `helmfile -l component_name=<name>`; the installable set is **derived** from `DEFAULT_NAMESPACES ∩ component_release_index()`, 6 of 13 components, the rest refused as namespaces with the reason stated. `uninstall` is FIN-19's implementation and the row above is what it renamed onto this group. | FIN-17..21 | 2h |

Notes that save implementers time:
- FIN-17: helmfile group `group=routing` is invoked by the script as `./helmfile_apply.sh`; call
  `self.helm_utils.helmfile_apply(environment, 'group=routing')` — **no `interactive=True`**. The rule landed
  with the `--yes` work: the library asks only when it is told to, and the install path applies without asking
  (`kmt install` confirms through `validate_cluster_prompt`, and `kmt install --yes` would not reach a
  helmfile prompt). ClusterIssuers via
  `create_or_replace_custom_resource(group='cert-manager.io', version='v1', plural_identifier='clusterissuers')`,
  email from `CLUSTER_MANAGER_EMAIL`.
  **2026-10-29, how it actually landed** (keep the shape, not the guess): the apply is a filter that can
  *exclude* the controller rather than always naming `group=routing` — `INSTALL_LABEL_FILTER` /
  `SKIP_LABEL_FILTER` in `routing.py:69-70`, handed to `HelmUtils.helmfile_apply(..., interactive=False)`, so
  the no-prompt rule this note asked for holds. The issuers are **not** applied through
  `create_or_replace_custom_resource`; they are built as dicts and applied with
  `kubectl apply -f -` (`routing.py:481-482`), with the solver's class from config. `CLUSTER_MANAGER_EMAIL`
  is read the way this note said, but it raises with the file's path when it is empty or still a
  `REPLACEME_*` placeholder (`routing.py:512-519`) instead of applying an issuer that cannot register an
  ACME account.
- FIN-22/23 landed the way this note asked, and it is worth keeping for FIN-24: `yaml.safe_load` +
  `TektonUtils._replace_placeholders_in_dict` rather than string templating, so quoting and key order
  stay deterministic and a rerun writes the same bytes. The method is now **table-driven**
  (`CONFIG_PLACEHOLDER_SOURCES` in `tekton_utils.py`) and takes the caller's own values
  (`extra_values=`), which is what lets a generator substitute `APP_NAME_PLACEHOLDER` — a value no
  `static.json` holds — through the same walk `run_pipeline` uses. It fixed two latent faults on the way:
  the recursion dropped the caller's values below the first nesting level, and a value read from config
  as an int raised `TypeError` inside `str.replace` instead of substituting. **Stamp `apiVersion` from
  `Config.TEKTON_API_VERSION`** — but for the `tekton.dev` group only: `networking.k8s.io/v1` is not a
  Tekton object and the triggers group is `Config.TRIGGERS_API_VERSION` (`v1beta1`), so a blanket rewrite
  would be a manifest nothing serves. The third rule, which is new: **a placeholder that has no value is
  an error that names the key, not a manifest** (`GenerationPlaceholderNotConfiguredError`).
- FIN-23: generator *templates* stay at `tekton_ci/triggers/templates/{github,gitlab}/` (tracked product
  assets); only the generated output moves. **Both halves of that move are done, and the two
  bookkeeping edits this note asked for are gone**: `tests/reset_configuration.sh` was deleted by
  `a3d8865` along with the three legacy test scripts, so there is no line 21 to update, and `.gitignore`
  already carries the `tekton_ci/triggers/**` + `!templates/` rule that FIN-13 put there. What replaced
  the bookkeeping is an assertion: the last case in
  `tests/test_generated_deploy_pipelines_and_trigger_configs_are_written_where_they_are_read.py` asks
  `git check-ignore` — the rule `git status` actually runs — for one ignored template location and both
  generated locations.

### Wave 3 — Users, Grafana, secrets, backing services (10 tickets)

| ID | Ticket | Deps | Effort |
|---|---|---|---|
| FIN-30 | `UserManagementUtils` — port `user_management_helpers.sh` (14 functions, 310 LOC) | FIN-04 | 5h |
| FIN-31 | `Automation.create_team_account()` (replaces the bash script; the broken Python stub that used to pretend otherwise was deleted by FIN-08) | FIN-30 | 4h |
| FIN-32 | `Automation.create_team_account_read_only()` | FIN-30 | 3h |
| FIN-33 | `Automation.delete_team_account()` | FIN-04 | 2h |
| FIN-34 | `GrafanaClient` (`grafana_client.py`, `requests`) — **landed 2026-10-28**: `src/kubementat/grafana_client.py`, one class, no command, and **no script deleted** (`grafana_helpers.sh` and the two scripts that source it are still in the tree and still driven by `tests/e2e/harness/{config,stages}.py`). **State: implemented-unverified / verified-by-unit-suite / lane-required / not wired to a CLI.** The ticket it fixes is TODOs item 4's measurement that `cap:create-grafana-accounts` is vacuous, and the shape of the class is exactly four losses inverted: **a non-2xx raises** with the status and grafana's own `message` (where `curl` inside `$(...)` lost the answer to `set -e`), **an empty body raises** (where `jq '.users[0]'` on nothing exited **0**), **"already exists" is read from a 412 that says so** rather than from the absence of output (which is what bash mistook for a skip), and **there is no default base URL** (the scripts' `http://127.0.0.1:31827` is a port no lane opens, so the address was the first lost step). Six flows are in the helper and all are covered: two request senders using basic auth, a bearer header builder that has no caller, a user search that takes `.users[0]`, a create that reads the id out of the response, the "skip if the search said something" guard, and a `gpg --gen-random` password generator. There is nothing else in the helper: **no login call and no session cookie** — the helper sends the pair on every request, which is why the constructor prefers the token, falls back to the pair, and names the key when neither is configured (`badcf40`'s source of truth, read by the same `Config.get('env_static_encrypted')` route as `helm_utils._grafana_admin_credentials`). **Two absences are stated rather than invented:** the helper takes a `team_id`, threads it into `create_user`, and drops it in a payload that never mentions it, so no membership call exists anywhere and `add_user_to_team` is shipped as the method that sentence describes with **no caller in this tree**; and `create_grafana_read_only_account.sh` sets **no role**, so "read only" is a filename — the method grants `Viewer`, and asks for the script's role-free behaviour explicitly through `ensure_account(org_role=None)`, which is tested as the deliberate parity case. The search hit is filtered to the email asked for, because `.users[0]` on Grafana's fuzzy query could report a new address as taken when somebody's *name* contained the string. **Evidence:** `uv run pytest -q` → **536 passed** (~18 s; 495 before, +41, all in `tests/test_a_grafana_account_is_answered_for_and_not_assumed.py`, driven by an injected fake `requests.Session` — no live HTTP and no localhost bind anywhere in the file); `uv run kmt --help` → **29** commands, **unchanged**: this ticket adds no command; `grep -rn "subprocess" src/kubementat/grafana_client.py` → none, and no `jq` and no shell call. `pyproject.toml` gained `requests>=2.32.5`, and `uv.lock` moved **two lines** (`{ name = "requests" }` in the package's dependencies and `specifier = ">=2.32.5"` in `requires-dist`) — `requests` was already resolved as a transitive dependency of `kubernetes`, so **nothing resolved, versioned or downloaded differently**, and `uv sync --locked` passes against the edited lock rather than rewriting it. The pair is read the same way `helm_utils` reads it — `config.get('env_static_encrypted')` in both — and no credential literal appears in `src/`; `grep -rn "GRAFANA_ADMIN_USER" src/` gains `grafana_client.py`, whose other hits are the messages that name the key. | Blocked by FIN-02, which landed. **What a lane owes**: (1) that a real grafana answers 412 with a message that contains "already exists" — every duplicate case here is a fixture, and a grafana that answers 412 silently is a grafana that this client reports as a failure rather than as an account; (2) that `orgRole: "Viewer"` is honoured by the version the lane installs, which is a claim about one HTTP version field and is proven against nothing but a fake; (3) the whole wiring — FIN-35 has to read `GRAFANA_USERS` out of the encrypted config, call the client for each row, and make the harness's `cap:create-grafana-accounts` row run the command, which is the only thing that can close TODOs item 4: **the row cannot stop being vacuous until a caller exists.** | 4 h — **done in code, no caller yet** |
| FIN-35 | `kmt users create-grafana-accounts <env> [--team] [--grafana-url]` + `kmt users create-grafana-read-only-account <env> <email> [name]`, with `src/kubementat/grafana_accounts.py` as the flow and **three scripts deleted**: `create_grafana_accounts.sh` (40), `create_grafana_read_only_account.sh` (52), `grafana_helpers.sh` (69) — the last two of `scripts/user_management/`, which is now empty and gone. The wiring half FIN-34's row asked for, and the part that fixes the vacuous row is the **address**: it is resolved, never assumed — (1) `--grafana-url` from the operator, (2) the grafana Service's ClusterIP, read through `Config.component_namespace("grafana")` + `Config.component_service_selector("grafana")` (the same `GRAFANA_SERVICE_SELECTOR` `kmt tunnel-grafana` selects pods with) and resolved by the new `KubernetesUtils.get_service_endpoint()`, (3) otherwise `GrafanaConfigurationError`, naming the flag, the selector and the namespace. No new config key, no `127.0.0.1` default, no silent success. Rows come from `GRAFANA_USERS` in `platform_config/{env}/static.encrypted.json` — the file `helm_utils` and the client already agree on — and every row goes through `ensure_account(org_role=None)`, which is **the script's role-free behaviour**, not `create_read_only_account()`'s `Viewer`. **State: implemented-unverified / verified-by-unit-suite / lane-required.** Evidence: `uv run pytest -q` → **560 passed** (~18 s; 536 before, **+24** — 16 in `tests/test_a_grafana_account_command_resolves_an_address_before_it_creates_anything.py`, driven by an injected fake `requests.Session` and a fake that answers with one ClusterIP, and 5 in `tests/test_parity_grafana_account_scripts.py`, which runs the three deleted blobs out of `git show 5c306bd:…` against a `curl` that logs and answers and compares endpoints, query and create payload against the command, plus 3 in `tests/test_the_grafana_probe_reads_the_run_own_configuration.py`, which drives the stage's two new asserts both ways so neither can be satisfied by always saying the same thing); `uv run kmt --help` → **29** commands, unchanged (the two commands are on the `users` group, 5 commands there, no aliases, no flat names); `grep -rn "subprocess\|jq" src/kubementat/grafana_accounts.py src/cli/commands/users.py` → nothing but prose; `find scripts -name '*.sh' | wc -l` → **19 → 16**, and `scripts/user_management/` no longer exists. The **vacuous-row guard** is one test that runs both sides: `test_an_unreachable_grafana_makes_the_script_green_and_the_command_red` shows the deleted script exiting **0** on an unanswered request (`jq '.users[0]'` on nothing, `if` reading the nothing as a skip) and the command exiting **1** on the same fixture and the same silence. Two behaviours are carried over rather than improved, and both are open questions rather than decisions: `create_grafana_read_only_account.sh` sets **no role**, so "read only" is a filename and this command grants nothing (the client's `Viewer` path is untested against a real Grafana and untouched); and `add_user_to_team` still has **no caller** — `GRAFANA_USERS` rows carry email/name/password and no team, so a `--team-id` would be invented behaviour, not a port. Harness: the `task.create-grafana-accounts` row drives the command with no `--grafana-url` (resolving the address *is* what the row measures), and `feature_grafana_accounts` gained `grafana-account-rows-declared` and `grafana-account-created` — the first fails the row when `GRAFANA_USERS` is empty, the second looks for the declared emails in `/api/org/users` after the create. | FIN-34 | 2 h — **done in code and in the suite 2026-10-28; the lane is the proof owed.** What the lane must show: (1) the ClusterIP resolution against a real Service — a headless grafana, a selector that matches nothing, or a namespace rename all resolve to the failure message, and that branch is proven only against a fake that returns `''`; (2) that the created account really is in `/api/org/users` afterwards (`grafana-account-created`), which the whole ticket turns on and which no unit test can see; (3) that the guest's `platform_config/dev/static.encrypted.json` declares at least one `GRAFANA_USERS` row — today it is one row (`replaceme@example.com`, `type: read-only`), and if that key is ever emptied the row is vacuous again for a reason in the config rather than in the code; (4) that a Grafana that is not installed or not reachable makes the lane red instead of green, which is the difference between FIN-35 landing and FIN-35 having landed. |
| FIN-36 | `KubernetesUtils.get_service_account_token()` (replaces `retrieve_token_for_service_account.sh`) | FIN-04 | 2h — **landed 2026-10-28**: the primitive shipped with FIN-04, `kmt service-account-token <NAMESPACE> <SERVICE-ACCOUNT>` is its caller now, the script is deleted in the same commit, and `tests/test_a_service_account_token_is_read_by_a_command.py` holds the blob record (`git show 19b97d0:scripts/secret_management/retrieve_token_for_service_account.sh`) plus the three branches — token found, no token secret, secret not yet filled in |
| FIN-37 | `KubernetesUtils.create_secret_from_json()` + ssh-key secret | FIN-04 | 2h |
| ~~FIN-38~~ | ~~`DatabaseUtils.setup_mysql_databases()` (`PyMySQL`, retry loop)~~ — **withdrawn 2026-10-29: there is nothing left to wrap.** FIN-52 removed the config key it would have read (`8d9c318`) and FIN-51c deleted the script it would have implemented (`0cf22b7`), under the owner's ruling that creating databases and users from configuration is **retired and not relocated**. A replacement is a new ticket against a helm chart source (`TODOs.md` "Refactoring backing services"), which is what that sentence always meant | ~~FIN-05~~ | — |
| FIN-39 | ~~`Automation.install_mysql / install_redis / install_nginx_reverse_proxy()` (helm via `shell.run`)~~ — **withdrawn 2026-10-27**: the three scripts are deleted, not migrated (§2). A replacement is a new ticket against a different chart source, not this row | ~~FIN-07~~ | — |

- `create_team_account.sh` emits a full kubeconfig block (`print_kubeconfig_for_service_account`). The
  kubeconfig text **is** the user-facing product — build it as a dict + `yaml.safe_dump`, token via FIN-36.
- Grafana: the bash helper uses a user/session flow against `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`
  from `static.encrypted.json`; prefer an API token if configured, otherwise fail with an actionable message.
  Add `requests` to `pyproject` dependencies. **FIN-34 landed both halves of this sentence 2026-10-28, with
  one correction to make reading it back easier: there is no session flow in the helper.** The helper sends
  the pair on every request and its `construct_auth_header` bearer function has no caller, so the client
  prefers the token, falls back to basic auth, and raises with the key named when neither is set. The
  dependency landed with the ticket; see the FIN-34 row above for the lock's two lines.
- `base64_library.sh` is covered by the stdlib — delete with FIN-37, no ticket. **It cannot go
  yet, and 2026-10-28 proved why**: `create_secret_from_json.sh` and `generate_ssh_key.sh` still
  `source "${0%/*}/base64_library.sh"`, and the first is still run from inside the pipeline pod
  (`tekton_ci/tasks/create-secret-from-json.yml`). Deleting the library takes two live scripts
  down with it; the row lands when those two do. **Closed 2026-10-29 (FIN-51):** both sourcing
  scripts are now gone, so the condition stated here is the one that met. What the check also found
  is that the condition was never the whole truth — `base64_encode` is called by nothing in the tree,
  including by the two scripts that `source` it, so the library's exposure was one line of prose and
  not twelve lines of behaviour. It is deleted; the `scripts/secret_management/` directory with it.
  Guard: `tests/test_the_secret_json_scripts_are_gone_and_the_task_runs_the_command.py`.
- FIN-39's scripts also ran from CI (`tekton_ci/pipelines/setup-pipeline-mysql.yml`); that manifest is
  deleted, so the FIN-51 gate no longer applies to them. What survives has no CI manifest of its own:
  `setup_mysql_databases.sh` is **deleted** (FIN-51c, `0cf22b7`) — it reached a cluster through
  `kmt run-script`, and deleting that verb is that ticket's own job — while `create-mysql-database.yml` /
  `execute-mysql-file.yml` remain Task definitions that run `mysql` in their own step and were never
  callers of the script.

### Wave 4 — Utilities (7 tickets; mechanical tail — good candidate for parallel fan-out)

| ID | Replaces | Target | CLI |
|---|---|---|---|
| FIN-40 | `list_pvc_usage.sh` (56) | `KubernetesUtils.list_pvc_usage()` — Python client, drop the `kubectl proxy` + curl dance | `kmt pvc-usage` |
| FIN-41 | `cluster_capacity.sh` (53) | `KubernetesUtils.cluster_capacity()` — nodes + metrics API, graceful fallback without metrics-server | `kmt cluster-capacity` — **landed**, see §1.3b row 7 |
| FIN-42 | `analyze_deployment.sh` (23), `retrieve_deployment_docker_image_versions.sh` (12) | `KubernetesUtils.analyze_deployment()`, `list_pod_images()` | `kmt analyze-deployment`, `kmt pod-images` — **landed**, see §1.3b row 7 |
| FIN-43 | `create_ingress_yml.sh` (54) | `IngressGenerator.generate()` — pure function, `yaml.safe_dump`, cert-manager annotations + TLS | `kmt create-ingress` |
| FIN-44 | `ssh_into_helm_deployment.sh` (31), `tail_logs_for_helm_deployment.sh` (27) — **both deleted** | `KubernetesUtils.exec_in_pod()`, `stream_logs()` (reuse `get_pod_name_for_deployment`), wrapped in `open_deployment_shell()` / `stream_deployment_logs()` so the pod lookup is not re-derived next to the call | `kmt deploy-ssh`, `kmt deploy-logs` — **landed**, see §1.3b row 7 |
| FIN-45 | `get_latest_version_for_helm_chart.sh` (17) — **deleted** | `HelmUtils.get_latest_chart_version(chart, repo)`, reading `helm search repo … --output json` (no `jq`, no `--versions`, and the chart **address** is compared rather than the second line of a table) | `kmt helm-version` — **landed**, see §1.3b row 7. Flat, like the rest of the surface: the only group on it is `users`, so `kmt helm version` was never going to be what anyone typed |
| FIN-46 | `open_mysql_tunnel.sh` (34) — only this half survives; `open_linkerd_dashboard_tunnel.sh` (9) is deleted with the mesh | `Automation.open_tunnel_mysql()` | `kmt tunnel-mysql` |

The 5 already-Python tunnel methods don't cover this gap; FIN-46 is the only new tunnel work.

**FIN-46 — landed 2026-10-28 (`6b18f68`). State: implemented-unverified / verified-by-unit-suite /
lane-required.** Two scripts, and only one of them needed a command:

* **`open_pod_tunnel.sh` was compared before it was deleted, and the comparison found no gap.** It
  printed the current kubectl context and then forwarded `namespace`, `pod`, `local:remote` and an
  address — all four of them arguments it did not resolve. `kmt tunnel-pod` takes the same four and
  calls the same `KubernetesUtils.open_tunnel()`. So the script is deleted, the `current-context` read
  stays a recorded bash-only difference (it reads a local kubeconfig and never touched the cluster),
  and **no second generic tunnel command was added** — two of those is two ways to be wrong about one
  pod, which is what `tunnel-polaris`/`tunnel_vault_ui` already proved (§1.3 D3).
* **`open_mysql_tunnel.sh` needed the command, and its two arguments were the gap.** It forwarded a
  namespace and a pod two humans typed; nothing in the script resolved either. `open_tunnel_mysql()`
  reads both keys the deleted installer asserted rather than trusted:
  `APP_DEPLOYMENT_NAMESPACE` (it compared `MYSQL_HOST` against
  `mysql.${APP_DEPLOYMENT_NAMESPACE}.svc.cluster.local` and exited 2 on a mismatch — so the namespace
  was a derived fact, which is why `kmt tunnel-mysql <NAMESPACE>` would have been the same coin toss)
  and `MYSQL_DEPLOYMENT_NAME` as the `app.kubernetes.io/instance` the pods carry. Neither has a
  default: kubementat stopped installing MySQL on 2026-10-27, so a missing key is a
  `MysqlNotConfiguredError` naming the file rather than a `"mysql"` that might tunnel to somebody
  else's database.

Evidence: `uv run pytest -q` → **572 passed** (560 before; +8 unit over namespace/rename/selector/
not-captured/not-there/missing-config/port-bound, +4 parity), `uv run kmt --help` → **30** commands,
one new, no alias, `users` still the only group. The pair is deleted from `609e757` and the
comparison lives in `tests/test_parity_tunnels.py`, which declares the one read bash never made as
`python_only` instead of dropping it. **One defect this ticket found rather than designed:** the
`tunnel-*` commands returned their code and Click ignores return values, so a `kubectl port-forward`
that refused an already-bound port printed its complaint and exited 0; every tunnel now fails on a
non-zero code (the port-bound case is asserted, on both the new command and `tunnel-pod`).

**What a lane still owes the MySQL tunnel:** (1) that `kmt tunnel-mysql` reaches a real MySQL pod —
every assertion here is a shim or a fake client, so "the statefulset pods carry the release label the
chart that pinned `9.3.2` wrote" is still a hypothesis, and the quiet failure is a cluster where the
release labels its pods `app=mysql` and the lookup answers `pod not found` for a database that is
running; (2) that the tunnel really forwards and is killable — the harness's process-group rule must
reach `kubectl port-forward` as a direct child of the CLI (FIN-02), and a survivor would leave 3306
bound under a name no one owns; (3) that a port already bound on the guest fails the lane's assert
rather than being read as a healthy tunnel, which is the assert `assert:tunnel-port-freed` exists to
make non-vacuous; (4) that a team with no `MYSQL_DEPLOYMENT_NAME` gets the config error and not a
half-open tunnel, which is a state no lane has built because nothing installs MySQL any more.

### Wave 5 — CI-container execution (2 tickets; gates `scripts/tasks/` deletion)

| ID | Ticket | Deps | Effort |
|---|---|---|---|
| FIN-50 | Make `kmt` available inside the CI image and inside pipeline steps | FIN-01 | 8h |
| FIN-51 | Rewrite the 4 CI manifests to call `kmt` instead of `scripts/*.sh` | FIN-50,39,37 | 6h |

**FIN-50 design (decision: runtime resolution, not baked):** the image gets **only `uv`** — no `uv tool
install`, no baked dependencies, no `kmt` in site-packages. Each CI step runs kmt from the repo that the
pipeline already cloned:
```dockerfile
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
```
```bash
cd "/workspace/$AUTOMATION_GIT_PROJECT_NAME"
uv run --locked --no-dev kmt tekton-run-pipeline "$ENVIRONMENT" "$TEAM" "$PIPELINE_RUN_FILE"
```
Rationale, explicitly accepted: **during heavy development you do not want to rebuild an image for every
lib change.** The repo *is* the implementation under test, so the version of kmt that runs always matches
the code being tested. Cost: dependency resolution per step (seconds with `--locked`, and `UV_CACHE_DIR` on
a writable emptyDir keeps it cheap), and the step needs network access to PyPI the first time on a given
node. `KUBEMENTAT_HOME` (FIN-05) is still set so config paths resolve regardless of cwd.

Then `run-tekton-pipeline.yml` collapses to a single `uv run … kmt tekton-run-pipeline …` plus a Python
polling step, deleting the ~70 LOC bash loop that duplicates `TektonUtils.run_pipeline`.

**FIN-51:** after this, `scripts/tasks/*.sh` and `create_secret_from_json.sh` become deletable. Until then
they stay.

### Wave 6 — Docs, CLI surface, deletion ledger (5 tickets)

| ID | Ticket | Effort |
|---|---|---|
| FIN-60 | **landed 2026-10-28** for the rows whose wave has landed — 8 scripts / 391 LOC gone from §7's "already migrated" row, and §7 states which 4 this ticket could not delete and the condition each one is waiting on (§1.4 for `run_pipeline.sh`, FIN-44/45 and FIN-31's command for the rest). Nothing needed repointing except one README sentence. `uv run pytest -q` → 249 passed, with the deleted scripts' comparisons kept as records so the Python path stays answerable to them | 1h per wave, 1 wave executed |
| FIN-61 | Update `README.md`, `docs/README_TEKTON.md` (`cd scripts/automation/tekton` → `kmt tekton-…`), `docs/README_PLATFORM_CONFIG.md`, `AGENTS.md`, `docs/index-*.md` (incl. the new `src/` layout and the trigger location) | 4h |
| FIN-62 | Click groups (`kmt tekton …`, `kmt tunnel …`, `kmt users …`, `kmt component …`, `kmt helm …`) — **renames, no aliases** (§1.7a) — plus `--help` text on every command; land it *with* the commands that need it, not at the end | 6h |
| FIN-63 | `CHANGELOG.md` and an upgrade note covering the `lib/`→`src/` and `tekton.dev/v1` changes. **No version bump** — the owner bumps manually at the end of the refactor (2026-10-29 ruling), which supersedes the version-bump half of this row | 1h |
| FIN-64 | ~~Delete `docs/planned-features/plan-python-refactor.md` and `docs/TODOS_REFACTORING.md`~~ — **done 2026-10-21**, both deleted; only the housekeeping half remains: keep this plan's status columns current as tickets land | 1h → 15m |

### Wave 7 — Tekton `v1beta1` → `v1` (decision 3; **lands early, hard flip**) (5 tickets)

**Ordering: this wave runs immediately after Wave 0 and before Waves 1–4 fan out.** Rationale: every
migration ticket that applies a Tekton resource would otherwise be written and verified against the beta
API, and Wave 7 would then be a second verification pass over ~35 tickets. Early is cheaper even though it
puts a 58-file diff in front of everyone sooner — keep that diff on its own branch, merge it alone.

| ID | Ticket | Deps | Effort | State |
|---|---|---|---|---|
| FIN-70 | `Config.TEKTON_API_VERSION = "v1"` (`src/kubementat/config.py`, not `constants.py`) — **no env override, no fallback** — plus a preflight assertion that the controller serves `v1`, so a too-old controller fails with a clear message instead of a 404 per resource | FIN-05 | 2h | **landed** — `TektonControllerWithoutConfiguredApiError` + `KubernetesUtils.api_version_is_served`, asked once per command and before `setup_pipelines` applies anything. `uv run pytest -q tests/test_tekton_api_version_preflight.py` |
| FIN-71 | YAML-aware transformer `tools/tekton_v1_migrate.py` + converted manifests in `tekton_ci/` (38 files / 38 `apiVersion` occurrences + field renames) | — | 6h | **landed** — `git diff --stat tekton_ci` → 38 files, 81/69 lines, comments intact (a `safe_dump` rewrite would have taken 307 comment lines with it). The tool's guards held: the 2 EventListener `resources` blocks and the 10 `volumeClaimTemplate.spec.resources` blocks were reported as "stays `resources`" rather than renamed |
| FIN-72 | Same transform for `examples/tekton_ci/` (16 files / 20 occurrences) and the generator templates; delete the throwaway tool | FIN-71 | 3h | **landed** — 16 files, `generate_pipeline_run.sh` emits v1 + `taskRunTemplate` + `timeouts`, and `tools/tekton_v1_migrate.py` is gone from the tree (it was never committed, so there is no deletion to review) |
| FIN-73 | Docs: Tekton version table, CHANGELOG migration note, `docs/README_TEKTON.md` | FIN-71,72 | 2h | **landed** — `CHANGELOG.md` (Unreleased, breaking, what operators must do), `docs/README_TEKTON.md` (version table + migration section + what must not change) |
| FIN-74 | Raise the pins to what upstream serves now, move the release URLs with them (the old bucket 404s the new pins), and make an install that left the wrong API versions running fail loudly instead of 404-ing later | FIN-70,72 | 3h | **landed** — 1.17.1 / 0.37.2 / 0.73.0 (the Dashboard's own support statement gates the triplet), one `TEKTON_RELEASE_BASE_URL`, Triggers `interceptors.yaml` applied alongside the release, `preflight_installed_tekton_api_versions` asserting `tekton.dev/v1` and `triggers.tekton.dev/v1beta1` after the install. `uv run pytest -q tests/test_tekton_install_asserts_the_served_api_versions.py` → 10 passed |

All four tickets are one wave and one commit. The manifest inventory test that proves the wave is
`tests/test_tekton_manifest_api_versions.py`: 11 cases over every manifest in both trees, including the
three shapes the rename must miss (`volumeClaimTemplate.spec.resources`,
`EventListener.spec.resources`, the `v1beta1` that belongs to Triggers).

**What did *not* change, and what FIN-74 did.** Converting the manifests did not need a controller
bump — `tekton.dev/v1` has been the serving version since Pipelines 1.0 — and it did not touch the
Triggers API: `triggers.tekton.dev/v1beta1` is what Triggers 0.37.2 serves (`v1beta1` served and stored
on every CRD, no `v1`), so flipping that string would produce six manifests no controller in this tree
answers to. What FIN-74 changed is what the install *applies*, not what the tree *writes*: the pinned
versions, the bucket those pins resolve against, and a post-install assertion that the cluster that
came back actually serves the two versions the tree speaks. Same API surface, newer controllers.

The wave is *applied* in the tree; it is *verified on a cluster* by the Wave T smoke lane, which has not
run against v1 yet (see TODOs). The lane owes it three runs on the pinned triplet: `hello-world-pipeline-run`
reaching `Succeeded`, and one local plus one remote helm-deploy pipeline run crossing namespaces.

**The transformer must be YAML-aware** (per §1.6): load with `yaml.safe_load_all`, apply path-based rules,
dump with `yaml.safe_dump(..., sort_keys=False)` per file, then **review the diff**. Never `sed`. Rules:
1. `apiVersion: tekton.dev/v1beta1` → `tekton.dev/v1` (all kinds whose group is `tekton.dev`;
   **leave `triggers.tekton.dev` alone**).
2. `PipelineRun.spec.serviceAccountName` → `spec.taskRunTemplate.serviceAccountName` (14). Skip the
   `EventListener` pod-level `serviceAccountName: tekton-triggers-sa` (2 sites).
3. `PipelineRun.spec.timeout` → `spec.timeouts.pipeline` (14).
4. `Task.spec.steps[].resources` → `steps[].computeResources` (19 — measured, the "20" of this plan's
   earlier revision was off by one), i.e. only under `steps`/`stepTemplate`/
   `sidecars`. Guard: skip any `resources` node whose sibling is `requests`/`limits` under a `spec` whose
   parent is `volumeClaimTemplate`, and skip `EventListener.spec.resources`.
5. Rewrite `examples/tekton_ci/tasks/task_examples.yml` (`spec.resources.inputs` → a **workspace**, not
   params — see §1.6) by hand, and delete the `PipelineResource` document in the same file, which v1
   does not serve.
6. Assert afterwards: `grep -rn "apiVersion: tekton.dev/v1beta1" tekton_ci examples` → **empty**. Do
   *not* use the `grep -rnE "[^s]tekton\.dev/v1beta1"` form this plan used to print — `[^s]` matches the
   `.` of `triggers.tekton.dev`, so it never excluded Triggers. Read the two halves separately:
   `grep -rln "triggers\.tekton\.dev/v1beta1" tekton_ci` must still list the 6 generator templates, and
   `tests/test_tekton_manifest_api_versions.py` is what proves every PVC and EventListener `resources`
   block is intact (a `grep`-for-`resources:` check cannot tell a block that is present from one that
   was renamed).

**Rollout order:** manifests, `Config.TEKTON_API_VERSION`, and the generators (FIN-22/23/24 stamping `apiVersion` from
`Config.TEKTON_API_VERSION` rather than copying the template) flip in one PR, applied with
`kmt tekton-setup-pipelines` against Pipelines 1.4.0. Because `Config.TEKTON_API_VERSION` is a single global
used by every `create_or_replace_custom_resource` call, a half-migrated tree means some resources are
read/written with the wrong `apiVersion` — hence "atomic", rule 7 in §3. **No rollback knob:** the recovery
path is a git revert plus `kmt tekton-setup-pipelines` again, which is acceptable because the only staging
customer is being re-initialized (§1.7a). The Wave T smoke lane (`hello-world-pipeline-run.yml`) is the
verification that the hard flip is safe before it reaches `main`.

---

## 6. Dependency graph

**Execution order is not optional:** `Wave T` (at least FIN-80 + FIN-81) and `Wave 0` land first — Wave T so
that verification exists before there is something worth verifying, Wave 0 as the technical baseline. Then
**Wave 7 (Tekton v1, hard flip) merges alone**, and only after that do Waves 1–4 fan out.

```
FIN-80 FIN-81 ─────────────────► verification available for every ticket below
                                 (run manually; nothing blocks a merge)
FIN-01 ──┬─► FIN-02 ──┬─► FIN-11  FIN-12  FIN-14 ─┐
FIN-03 ──┤           ├─► FIN-13                   ├──► FIN-25 ──► FIN-60 (wave1 deletions)
FIN-05 ──┤           ├─► FIN-15  FIN-16 ──────────┘
FIN-07 ──┘           │
FIN-04 ──────────────┴─► FIN-17..FIN-24 ─────────► FIN-60 (wave2 deletions)

FIN-05 ──► FIN-70 ──► FIN-71 ──► FIN-72 ──► FIN-73   (early + hard: merges before waves 1-4)
                  └──► FIN-22/23/24 stamp apiVersion from Config

FIN-30 ──► FIN-31 FIN-32 FIN-33
FIN-34 ──► FIN-35            FIN-36 FIN-37 ─┐
FIN-05 ──► FIN-38 (FIN-39 withdrawn 27 Oct: scripts deleted, not migrated)
                 ─────────────────────────┼─► FIN-50 ──► FIN-51 ──► (tasks/ + secret_management/ deletions)
FIN-04 ──► FIN-40..FIN-46 ──────────────────► FIN-60 (wave3/4 deletions; run-script removal is FIN-06)

FIN-82 FIN-83 ──► evidence for FIN-11 FIN-13   FIN-84 ──► evidence for FIN-17..FIN-46
FIN-88          ──► HARD prerequisite for FIN-82 FIN-83 (`kmt install` cannot finish without it)
FIN-89          ──► dashboard swap: product work, scheduled here; the smoke lane depends on the release installing
FIN-85 ──► replaces tests/*.sh               FIN-86 FIN-87 last (entry point + artifacts)
```

Waves 2, 3, 4 are mutually independent and can run concurrently. **One writer per worktree** — parallelize
by worktree, not by editing one branch. `constants.py`, `shell.py`, `pyproject.toml`,
`templates/environment/static.json.template` and `tekton_ci/**.yml` are high-contention: land Wave 0 and
Wave 7 alone before fan-out. The E2E suite serialises implicitly — a shared KVM host runs one VM per lane at
a time, so two agents both wanting a smoke run queue rather than race (FIN-86 should say so explicitly).

## 7. Deletion ledger

Delete exactly when the row's condition is met. A file whose wave has not landed must not be touched.

| Path | Delete in |
|---|---|
| `scripts/automation/tekton/{configure_secrets,configure_docker_registry_access,uninstall_pipelines,list_tekton_resources}.sh`, `scripts/utilities/tunneling/open_{grafana,tekton_dashboard,polaris,vault_ui}_tunnel.sh` | **deleted by FIN-60** — 8 scripts / 391 LOC. Each had a parity test that ran the bash (`tests/test_parity_{tunnels,secrets,tekton_runs}.py`) and they were green before the deletion (`uv run pytest -q tests/test_parity_*.py` → 34 passed). The comparisons did not go away with the scripts: what each one asked the cluster is recorded in the same test file, so the Python path is still judged against the deleted implementation and not against itself. References: `grep -rn` over `tekton_ci/`, `examples/`, `src/`, `tests/` and the READMEs turned up exactly one that mattered — the sentence in `docs/README_TEKTON.md` that told a human about `list_tekton_resources.sh`, which now names `kmt tekton-list`. |
| `scripts/automation/tekton/run_pipeline.sh` | **deleted 2026-10-29 by FIN-51b (`85f1ea8`)**. The row said *stays*, for one stated reason: `tekton_ci/tasks/run-tekton-pipeline.yml:80` sourced it inside the pipeline pod, and the CI image had `python3` but no `uv`, no `kmt` and no project dependencies, so the reference could not be repointed in one commit without breaking CI — with FIN-50 owning that. **FIN-50's image half landed (`f9a86f4`) and FIN-51b repointed the reference**, so the hold discharged rather than being waved away: the image carries `uv`, the Task is one step running `uv run --locked --no-dev kmt tekton-run-pipeline … --wait`, and `workingDir:` is declared on the step because uv resolves `kmt` only from the project and the image installs no entry point of its own. The bash half is recorded and not executed: `test_parity_tekton_runs.py` reads the `sed` table out of `git show 40f281b:scripts/automation/tekton/run_pipeline.sh` and judges the Python substitution against it, so the four converted cases still compare against the deleted implementation and not against themselves. Guard: `tests/test_the_run_pipeline_script_is_deleted_and_no_manifest_sources_it.py`. **What the row still owes is a pod, not a test**: that the step starts, finds the project, and blocks through a real inner run is unverified here and says so. |
| `scripts/secret_management/generate_ssh_key.sh` | The row said **stays**, because *"add `kmt generate-ssh-key` first"* was unmet (`uv run kmt --help` registered 30 commands, re-measured 2026-10-28 after FIN-46 added `tunnel-mysql`, none of them that one) and *"the library call behind it is not yet equivalent: bash leaves a key that exists where it is, the library rewrites it"* (`test_parity_generate_ssh_key.py`, in its own docstring). **Deleted 2026-10-29 — last-lap §2 row 9**, and the two conditions above re-read rather than dismissed. *The command does not exist*: still true (`uv run kmt --help` registers none), and irrelevant — this script was never the implementation of a verb, so nothing was lost by deleting it; `kmt initialize` is the command that generates a deployer key, and `grep` over `src/ tests/ tekton_ci/ templates/ examples/` and every surviving `.sh` finds no caller. *The library is not equivalent*: closed where it is reached, because the one call site guards the call with `os.path.exists` (`src/kubementat/automation.py:203`, calling line 205) — bash's `[[ ! -f $KEYNAME ]]` moved to the caller and not into the library. The guard asserts that off the source instead of remembering it: `tests/test_the_deployer_ssh_keypair_is_written_by_the_library_and_the_script_stays_deleted.py`, which also records the one difference that did survive — OpenSSH's private-key container from bash, classic RSA PEM from the library, both 2048-bit, both base64-encoded once on the way into `static.encrypted.json`. What an operator loses is `kmt run-script --args scripts/secret_management/generate_ssh_key.sh`, which answers `Script not found` for a script the ledger deleted, as this row's own CLI test has always made it. |
| `scripts/utilities/kubernetes/{retrieve_node_addresses,view_cluster_resource_versions}.sh` | **stays** — their commands (`kmt node-addresses`, `kmt api-resources` — FIN-44/45) do not exist, and their Python halves are library calls (`get_nodes()`, `get_api_resources()`), so deleting these two would take the only operator-reachable path to both facts with it. Their parity file states the same condition on its own rows. |

**Verified first, and by the tests and the tree rather than by the report:** all 12 rows had a green parity test, and the 8 that were deleted had nothing else referring to them. What FIN-60 could not do is the 4 rows whose own ledger conditions are unmet — see the rows above. (Re-read 2026-10-29: **2 of those 4 closed** — `helmfile_apply.sh` and `generate_ssh_key.sh`, both recorded above — so what is still held by an unmet condition is the CI half (`run_pipeline.sh`, FIN-50) and the two one-liners whose commands FIN-44/45 never shipped.) (`scripts/automation/tekton/create_team_account.py` used to sit in this row too; FIN-08 deleted it as dead code, and it is still absent — `grep -rn "create_team_account.py" src tests scripts` hits nothing.)
| `scripts/automation/components/{install_tekton,install_linkerd}.sh`, `scripts/automation/tekton/setup_triggers.sh`, `scripts/utilities/{login_kubernetes_dashboard,run_ci_container}.sh` | FIN-11..16 — FIN-11's own row (`uninstall_kubementat.sh`) is deleted, so this row starts at FIN-12. **`install_tekton.sh` deleted by FIN-12**, with its parity record left behind in `tests/test_parity_tekton_installs_the_pinned_releases.py`. **`setup_triggers.sh` deleted by FIN-13**, with its record left behind in `tests/test_the_trigger_setup_asks_the_cluster_for_the_same_rbac.py` — eight RBAC heredocs plus the two webhook Secrets, reduced by the same reducer the live comparisons use, so the Python path is still judged against what the script asked for and not against itself. References: the two strings in `src/` that named it (`automation.py`'s install call site and `cli/commands/tekton.py`'s command body) are gone with the port, `grep -rn setup_triggers.sh src/` is empty, and `docs/README_TEKTON.md`'s "cd scripts/automation/tekton && ./setup_triggers.sh" block now names `uv run kmt tekton-setup-triggers`. Of the two scripts this row still owed, **`install_linkerd.sh` is deleted rather than migrated** (Linkerd is out of the product, owner decision 2026-10-28 — so there is no parity record to leave behind, because there is no Python path that should behave like it), and `run_ci_container.sh` went with `CiUtils` (FIN-16). |
| `scripts/automation/components/{install_routing,install_kubernetes_dashboard,helmfile_apply}.sh`, `scripts/automation/tekton/{generate_deploy_pipeline_from_template,generate_trigger_config_from_templates,generate_pipeline_run,create_team_account}.sh` | FIN-17..25. Two went out of this row: **`setup_tekton_pipelinerun_cleanup_job.sh` deleted by FIN-21** (§1.3b row 6a), and **`uninstall_helm_deployment.sh` (58) deleted by FIN-19** (§1.3b row 6b) — not migrated, because nothing in `src/` ever called it, with its record left behind in `tests/test_a_helm_component_is_uninstalled_from_the_namespace_the_config_names.py` so the command is still judged against what the script asked the cluster and not against itself. **Two more went the same way on 2026-10-28: `generate_deploy_pipeline_from_template.sh` (71) and `generate_trigger_config_from_templates.sh` (123), deleted by FIN-22/FIN-23** (§1.3b row 6d) — same shape as FIN-19's row, because `grep -rl` over `src tests/e2e tests/*.py tekton_ci README.md` finds no caller for either, so what landed is `kmt tekton-generate-deploy-pipeline` and `kmt tekton-generate-triggers`, judged against `git show 07368d4:...` in `tests/test_generated_deploy_pipelines_and_trigger_configs_are_written_where_they_are_read.py`. `create_team_account.sh` is gone with FIN-30..FIN-33 (§1.3b row 3), which left `generate_pipeline_run.sh` as the only script in this row still running — **and it is gone too as of 2026-10-29 (FIN-24), so this row now holds no live script**; see its own row below. **`helmfile_apply.sh` is deleted 2026-10-29 (last-lap §2 row 9)**, which is why this row can say that: its two sourcers went before it — `install_routing.sh` with `kmt routing install`, `install_kubernetes_dashboard.sh` with the retired chart (§1.3c's 22-Oct update) — so the "delete after FIN-17/18" condition this row carried is met, not waived. It is deleted rather than migrated because no code ever execed it: the argv it hand-built is already `HelmUtils.helmfile_apply` (`src/kubementat/helm_utils.py:75`), reached from `kmt install` (`src/kubementat/automation.py:543`), `kmt routing install` (`src/kubementat/automations/routing/routing.py:246`) and `kmt helmfile-apply` (`src/cli/commands/helm.py:93`), and the empty `scripts/automation/components/` it kept alive is gone with it. Guard: `tests/test_the_helmfile_argv_is_built_in_python_and_the_script_stays_deleted.py` |
| `scripts/automation/tekton/generate_pipeline_run.sh` (140) | **deleted 2026-10-29 by FIN-24** — the last of the three generators, migrated to `TektonUtils.generate_pipeline_run(pipeline_file)` (`src/kubementat/tekton_utils.py:1545`) and reachable as `kmt tekton-generate-pipeline-run <env> <team> <pipeline-file>` (`src/cli/commands/tekton.py`, registered `src/cli/kmt.py:72`). No alias, no shim, same commit as the script. **Orphan proof, in the form this row asked for**: `git grep -n generate_pipeline_run -- src tests tekton_ci templates examples '*.sh'` answers only the script's own usage block (`generate_pipeline_run.sh:39-40`) and the Python names that replaced it — so, like FIN-19/22/23 before it, this is a new command and not a port, and it had been reachable only by knowing the filename and standing at the right depth (`../../../tekton_ci/...`). `README.md:214-215` is `helmfile_apply.sh`, checked and not it. **The decision that is not a port**: bash wrote `tekton_ci/pipeline-runs/<TEAM>/`, under `Config.tekton_global_pipeline_run_dir` — which `list_available_pipeline_runs` does not look in and `run_pipeline` only reads through a `global/` prefix — so the script's own output was invisible to both of the commands that consume it. Generated output is one team's configuration and goes to `Config.tekton_team_pipeline_run_dir`, the location FIN-22 established for the deploy-pipeline generator, and the tests prove it with `git check-ignore` rather than with prose: the generated path is visible to `git status`, and the `tekton_ci/pipeline-runs/` case that a generated run had to be tidied out of the way for is what the move is avoiding. **The guard**: `tests/test_generated_pipeline_runs_are_written_where_the_run_commands_read_them.py` → **39 tests**, of which the parity half runs `git show 894f587:scripts/automation/tekton/generate_pipeline_run.sh` inside the same fixture checkout and asserts the record's bytes against the generator's next to a list of the **eight** places they deliberately disagree, each row proven by both halves rather than described — the parameter set is the pipeline's and not bash's eleven (`environment` and `team` in every file it wrote, which the controller rejects); a parameter is passed once (bash wrote `tekton-ci-image-name`/`-tag` twice, once filled and once as `TODO`); workspaces come from the source (three of the nine pipelines in the tree declare none and got a volume claim anyway); a `Task`, a multi-document file, a nameless pipeline and a nameless *parameter* are refused instead of interpolated as `null`; a pipeline declaring no parameters is generated instead of taking `jq`'s error and `set -e` down with it; the output directory is created and not demanded; and the apiVersion is read from `Config` rather than recited. It also asserts the one refusal bash had and this generator deliberately keeps, stronger than FIN-22/23's: a generated *run* carries parameters a team hand-edits, so `GenerationTargetExistsError` refuses a rerun over a file that exists. `uv run pytest -q` → **751 passed, 1 skipped**; `find scripts -name '*.sh' \| wc -l` → **9 → 8**. `scripts/automation/tekton/run_pipeline.sh` is untouched (FIN-50). **State: implemented-unverified / verified-by-unit-suite / lane-required**: every claim here is a document compared to a document or to `Config`, never to an API server, so "the generated run applies and starts the pipeline it names" is still a hypothesis a lane has to discharge. |
| `scripts/user_management/*.sh` (all 7), `scripts/secret_management/{create_k8s_secret_for_ssh_key,create_secret_from_json,retrieve_token_for_service_account,base64_library}.sh`, `scripts/tasks/*.sh` | FIN-30..39 (and FIN-51 for `tasks/` + `create_secret_from_json.sh`). **Two left 2026-10-28, and they left for different reasons.** `retrieve_token_for_service_account.sh` (25) is **deleted by FIN-36** — the library half landed with FIN-04 and had no caller, so what landed is a command that did not exist before (`kmt service-account-token`), judged against the blob at `git show 19b97d0:scripts/secret_management/retrieve_token_for_service_account.sh` and left there so the command is still measured against what the script asked the cluster. `create_k8s_secret_for_ssh_key.sh` (44) is **deleted, not migrated** — no caller anywhere but the docs, the primitive (`create_or_replace_secret`) is already reached by `kmt setup-secrets`, and what a reader has to know is which shape is *not* covered: the script took a key file and a namespace, and no `kmt` command does. Both `base64_library.sh` (12) and `grafana_helpers.sh` (69) **stayed**, and both would have been wrong to delete then. `base64_library.sh` is `source`d by `create_secret_from_json.sh` (still run from `tekton_ci/tasks/create-secret-from-json.yml`) and by `generate_ssh_key.sh`; it leaves with FIN-37/§2.3, when those go. **`grafana_helpers.sh` (69) left with the three it sourced: all of `scripts/user_management/` is now gone (FIN-35, 2026-10-28)**, because the harness stopped depending on it — `tests/e2e/harness/config.py` drives `kmt users create-grafana-accounts` and the helper's seven functions are covered by `GrafanaClient` (`tests/test_parity_grafana_account_scripts.py` maps them one by one, including the `gpg --gen-random` generator that became `secrets`). Deleting a helper the harness depended on was the right call to defer until there was a command to depend on instead, and that call was not this row's to make. |
| `scripts/utilities/{kubernetes,helm}/*.sh` (remaining) | FIN-40..45 — **landed**. The `tunneling/` half of this row is closed: `scripts/utilities/tunneling/` is **gone in its entirety** (FIN-46, 2026-10-28), the four component tunnels by FIN-60 and `open_mysql_tunnel.sh` by a command rather than a wrapper. **FIN-40 closed the last read of the `kubernetes/` half 2026-10-29**: `list_pvc_usage.sh` (56) is deleted, replaced by `KubernetesUtils.list_pvc_usage()` (`src/kubementat/kubernetes_utils.py:1507`, which asks each node's `/stats/summary` through `CoreV1Api.connect_get_node_proxy_with_path` where bash started `kubectl proxy`, `sleep 2`'d, curled `127.0.0.1:8001` and `pkill -9`'d it) and by `kmt pvc-usage` (`src/cli/commands/kubernetes.py:166`, registered `src/cli/kmt.py:101`, `EXPECTED_COMMANDS` row `tests/test_cli_surface.py:81`). No alias and no shim, deleted in the same commit; `git grep -n list_pvc_usage -- src tests tekton_ci templates examples` hits only the parity file's blob citation, and the guard that keeps it that way is `tests/test_no_dead_code_ships_in_the_tree.py::test_the_pvc_usage_script_is_gone_and_nothing_reaches_for_it`. The comparison is `tests/test_parity_pvc_usage_reads_the_nodes_and_their_stats.py` → 3: one runs `git show 49c8739:scripts/utilities/kubernetes/list_pvc_usage.sh` through the shims and compares the operations, one proves both sides asked for the identical node-proxy path (assembled out of the blob's own `KUBEAPI` line, not asserted), and one runs the blob's own `getPVCs` function over the same two `stats/summary` documents and checks that both sides report the same claims with the same byte counts. **The declared differences are all of them and all of them are in that file's docstring**: bash's transport (`curl` is not a cluster tool and never enters the shim log, so the proxy session is bash-only and the two Python reads are declared), the `awk`/`numfmt`/`column` formatting layer (`PVC 1K-blocks` there where the command prints the bytes the kubelet sent; `5G` where `numfmt --to=iec` writes `5.0G`; `%.0f%%` against one decimal), and the zero capacity — where bash's `jq` exits 5 with `number (5) and number (0) cannot be divided because the divisor is zero` and the command answers `n/a` and keeps the report. Also learned from the blob rather than assumed: the script's exit code is `pkill`'s, so it read 1 in the harness with nothing to kill, which is why the comparison is of calls and not of statuses. `uv run pytest -q` → **766 passed, 1 skipped** (751 before, +15); `find scripts -name '\*.sh' | wc -l` → **7** from 8. **State: implemented-unverified / verified-by-unit-suite / lane-required** — no API server answered any of it, so "the kubelet serves `/stats/summary` the way the fixture does, and `kmt pvc-usage` prints claims that are bound on this cluster" is a hypothesis a lane owes. ``create_ingress_yml.sh` in this row is **FIN-43, landed 2026-10-29, and it closed the rest of this row**: it is deleted, and its manifest is built by `src/kubementat/automations/ingress.py` and reached as `kmt create-ingress` (`src/cli/commands/utilities.py:27`, registered `src/cli/kmt.py:88`, `EXPECTED_COMMANDS` row `tests/test_cli_surface.py:92`). No alias, no shim, deleted in the same commit. What was load-bearing in it survives verbatim and is pinned: `cert-manager.io/cluster-issuer: letsencrypt`, `kubernetes.io/tls-acme: "true"`, and the `spec.tls` block whose secret is `<service>-cert` — the issuer name is still referenced from `tekton_ci/triggers/templates/{github,gitlab}/template-event-listener-ingress.yml:34`, which is why it was not renamed. The class is answered through `Config` and not `jq`: `Config._component_value("traefik", "INGRESS_CLASS", DEFAULT_INGRESS_CLASSES)`, the same key (`TRAEFIK_INGRESS_CLASS`) and the same `traefik` fallback `routing.py:107/280` reads, so a generated Ingress and the cert-manager solver that has to reach it cannot disagree; the script's `jq -r '.TRAEFIK_INGRESS_CLASS // "traefik"'` could only be silent about that file. The two reasons bash printed its warning stay two sentences (`ingress_generator.py:_read_ingress_class`), because they have two different fixes, and the warning goes to stderr while the manifest goes to stdout — the separation the script had to keep because its whole output was the YAML. `uv run pytest -q` → **799 passed, 1 skipped** (766 before, +33: 25 unit cases in `tests/test_generated_ingress_manifests_name_the_service_the_class_and_the_issuer.py`, 7 in `tests/test_parity_create_ingress_writes_the_same_ingress_as_the_script.py`, and the stays-deleted guard `tests/test_no_dead_code_ships_in_the_tree.py::test_the_ingress_manifest_script_is_gone_and_the_command_answers_for_it`); `find scripts -name '\*.sh' | wc -l` → **6** from 7. The parity comparison runs `git show bb30fb2:scripts/utilities/kubernetes/create_ingress_yml.sh` back into the fixture at the depth it was deleted from, so bash's own `../../../platform_config` climb and `Config`'s `KUBEMENTAT_HOME` descent meet on one `static.json`. **The declared differences are all of them, and all of them are in that file's docstring**: the two comments inside the heredoc (the only text the migration drops on purpose), `"true"` against `'true'`, the extra warn-once line `config.py` emits where bash stayed silent when the file was there and the key was not, and the port — bash interpolated `$SERVICE_PORT` wherever it was told, so `http` produced `number: http`, and Python refuses to build that document. `jq` is not a cluster tool and never enters the shim log, so the operation comparison is between two empties: **neither side ever asked a cluster anything**, and that is asserted rather than assumed. **State: implemented-unverified / verified-by-unit-suite / lane-required** — the one thing no fake client can prove is that an Ingress this writes really binds on a real cluster: `apiVersion`/`kind` resolving to the networking v1 endpoint is proven against a patched API class, and a Traefik whose `IngressController.className` is not what the environment's `static.json` says is exactly the case the fallback warning exists for and has never been observed. |
| `scripts/utilities/tunneling/open_pod_tunnel.sh` | **deleted 2026-10-28, last, as its condition said.** `kmt tunnel-pod` did everything this script did (it was the same `KubernetesUtils.open_tunnel()` on the same four arguments, all of them the operator's), so the row closes by deletion and **not** by a new command — a second generic tunnel command would be a second way to be wrong about the same pod. Its `kubectl config current-context` read is declared as a bash-only difference in `tests/test_parity_tunnels.py`, where the record of the whole pair now lives. |

**Command removals (not files, but part of the ledger):** `kmt run-script` disappears at the end of Wave 4
(decision 6) — delete the command, its `--args` handling and the `glob` scan, with no alias; and
`kmt login-kubernetes-dashboard` / the flat `tunnel-*` names are renamed to Click groups in FIN-62 without
aliases (§1.7a).

End state: `find scripts -name '*.sh' | wc -l` → **0**; and with FIN-85's replacement of the three
`tests/*.sh` scripts, `find . -name '*.sh' -not -path './examples/*' -not -path './docker_files/*' | wc -l` →
**0** as well. (An earlier revision of this row said 3, which contradicts FIN-85 — the test scripts are
replaced by `tests/e2e/`, not kept.)

---

## 8. Definition of done (per ticket)

1. New behaviour is reachable through `kmt`, not only as a library call.
2. A parity test (FIN-03 pattern) proves the Python path issues the same external commands as the deleted script.
3. A unit test covers the happy path plus the "resource already exists" and "resource missing" branches.
4. **A smoke-lane E2E run that exercises this behaviour is attached to the PR** (FIN-86/FIN-87), for any
   ticket in Waves 1–7 — by hand, because nothing enforces it (decision 7). For tickets in Wave 0 the harness
   may be the unit suite only, since Wave T lands in parallel.
5. The old script is deleted in the same PR; `rg <script-name>` returns nothing outside `docs/finished-tickets/`.
6. `uv run pytest` passes; `uv run kmt --help` lists the new command; no deprecated alias or fallback read
   was added (§1.7a).
7. Any README that referenced the script is updated (FIN-61 may batch, but the ticket isn't done until the stale reference is gone).
8. This plan's status column is updated.

## 9. Migration exit criteria

- [ ] `scripts/` contains no `.sh` files (see §7).
- [ ] **`uv run python tests/e2e/suite.py --lane smoke` passes** — initialize → install → hello-world
      pipeline `Succeeded` → uninstall with zero residue → VM destroyed. Plus one green `full` lane, run by
      whoever decided it was worth an hour. **The evidence is attached to the tickets; it is not a gate.**
- [x] **FIN-88 is fixed** (`7243da9`) — the `twuni` entry and its `registry` release are gone from
      `templates/environment/kubementat_components/helmfile.yaml.gotmpl` (`git grep -n twuni -- templates/` →
      empty). One caveat the lane should not mistake for a bug: `initialize` copies that template into
      `platform_config/{env}/kubementat_components/` (`automation.py:121`), so a tree initialized before
      this change still carries the entry in its *generated* copy — re-initialize, or delete that file.
- [x] **Linkerd is out of the product** (owner decision 2026-10-28, §1.3b row 5a) — no Linkerd is
      installed by any path, `kmt install` has no `--enable-linkerd`/`--disable-linkerd` (no alias, no
      deprecation shim, §1.7a), and `kmt uninstall` takes a mesh the cluster still carries off it:
      `linkerd.io` definitions first, then the `linkerd` and `linkerd-viz` namespaces, tolerant when
      the mesh was never there. Verified: `uv run pytest -q` -> 432 passed, `grep -ril linkerd src tests
      scripts templates tekton_ci examples` (minus `__pycache__`) hits only the removal step's prose.
      **A lane still owes** the cluster half (§1.3b row 5a), so this checkbox says the product changed,
      not that a cluster confirmed it.
- [ ] **FIN-89 is fixed** — *not met as written.* The blocker is closed (`7243da9` removed the release, the
      values, the three scripts and the login command), but kubementat now installs **no** dashboard, so
      "the dashboard comes from a maintained chart" is still a product decision, not a checkbox (§1.8 B2).
- [x] `tests/*.sh` contain no `read -p` prompts (FIN-85) and the 3 bash test scripts are gone (`ls tests/*.sh`
      → nothing; `tests/e2e/` replaces them).
- [x] No `subprocess` call from `src/cli/`; all shell access lives in `src/kubementat/shell.py` (0 hits).
- [x] `rg "sys.path.append" src/` empty; `python -c "import kubementat"` works from any cwd (verified from
      `/tmp`); `import lib.kubementat` fails (`tests/test_import_hygiene.py`).
- [ ] `jq`/`yq` are absent from `KUBEMENTAT_DEPENDENCIES` and not required for a full `kmt install` / `kmt uninstall`. **Half met, half decided against** (2026-10-29): `yq` is absent and nothing requires it; `jq` is present because one shipped script calls it, which is the criterion's stated intent ("not required for a full install") inverted — removing it would make the run *less* clear, not more. See §1.3a's D12 row.
- [ ] `kmt install` and `kmt uninstall` complete with zero bash scripts present.
- [x] **No hardcoded component namespaces in `src/`** — `grep -rn "= *'grafana'\|= *'prometheus'\|= *'vault'\|= *'polaris'" src/kubementat` is empty, and every namespace resolves through `Config.component_namespace()` (decision 5). FIN-07: `uv run pytest -q tests/test_component_namespaces_come_from_configuration.py` → 25 passed, which also locks the two facts that grep cannot — `src/` holds no namespace literal at all (`constants.py` is the fallback, nothing else), and `constants.py` agrees with `templates/environment/static.json.template` key for key. **The bash half, re-measured on 2026-10-28 after FIN-46 (it is no longer 12 scripts / 22 assignments):** `grep -rnE "[A-Z_]*NAMESPACE[A-Z_]*" scripts --include=*.sh` → **21 lines in 4 scripts** (`automation/tekton/run_pipeline.sh`, `automation/tekton/generate_pipeline_run.sh`, `secret_management/create_secret_from_json.sh`, `tasks/setup_mysql_databases.sh`) — **re-measured 2026-10-29: the same grep answers 9 lines in 1 script** (`run_pipeline.sh` alone), because the other three files each went with their own tickets and `tasks/setup_mysql_databases.sh` was the last of them (`0cf22b7`). The original sentence counted **4 assignments** among its 21 lines: 3 read `static.json` through `jq`, so they are config-driven rather than literal, and 1 (`create_secret_from_json.sh:12`) copies the operator's first argument. So **zero bash scripts assign a namespace literal today**: what remains is either a config read or a value a human handed in, and the 10 lines / 2 assignments this ticket removed were the two tunnel scripts, both of which took the namespace as `$1` (measured at `609e757`: 31 lines in 6 scripts, 6 assignments — 4 config reads + 2 argv copies). What still says `dev1` is 1 usage/example line (`automation/tekton/run_pipeline.sh:35`) — help text that never reaches `kubectl`, corrected wherever the script it belongs to is deleted; the other four lines this sentence used to count went with `create_ingress_yml.sh`, `generate_pipeline_run.sh`, `create_secret_from_json.sh` and `generate_trigger_config_from_templates.sh` (FIN-43, FIN-24, FIN-51a, FIN-23). The reason FIN-19/20/30 still own this half rather than this checkbox is the one that remains., so they are config-driven rather than literal, and 1 (`create_secret_from_json.sh:12`) copies the operator's first argument. So **zero bash scripts assign a namespace literal today**: what remains is either a config read or a value a human handed in, and the 10 lines / 2 assignments this ticket removed were the two tunnel scripts, both of which took the namespace as `$1` (measured at `609e757`: 31 lines in 6 scripts, 6 assignments — 4 config reads + 2 argv copies). What still says `dev1` is 2 usage/example lines (`automation/tekton/run_pipeline.sh:35`, `secret_management/create_secret_from_json.sh:17`) — help text that never reaches `kubectl`, corrected wherever the script it belongs to is deleted, and 3 of the 5 lines this sentence used to count went with `create_ingress_yml.sh`, `generate_pipeline_run.sh` and `generate_trigger_config_from_templates.sh` (FIN-43, FIN-24, FIN-23). The reason FIN-19/20/30 still own this half rather than this checkbox is the two that remain.
- [x] No compatibility shims anywhere: `grep -rniE "deprecat|back.?compat|legacy|fallback to old" src/`
      returns nothing at all, not even comments (§1.7a).
- [x] All 55 original `scripts/**` behaviours are either a documented `kmt` command or an explicitly removed feature in the CHANGELOG. `kmt run-script` is gone (**FIN-06, `e6b3afd`**) — with the honest tail the checkbox would otherwise skip: the two surviving `scripts/utilities/kubernetes/*.sh` are present, have Python equivalents, and lost their only operator route when this command went. `get_nodes()` is reached from the cluster-capacity read (`kubernetes_utils.py:1423`); **`get_api_resources()` is reached by nothing in `src/`**, so `view_cluster_resource_versions.sh` is a Python equivalent with no command on top of it. The behaviour is half-migrated until `kmt node-addresses` / `kmt api-resources` land.
- [x] **`kmt run-script` has lost its last grafana caller** (FIN-35, 2026-10-28). `scripts/user_management/{create_grafana_accounts,create_grafana_read_only_account,grafana_helpers}.sh` are deleted — 19 → 16 scripts under `scripts/`, and the whole `user_management/` directory with them — and `tests/e2e/harness/config.py` drives `kmt users create-grafana-accounts <env> --team <team> --yes` with **no** `--grafana-url`, so the address is resolved from the cluster instead of being handed in. What made the row vacuous is gone with it: the row no longer runs a `curl` inside a `$( )` against `http://127.0.0.1:31827`, and an unreachable Grafana now fails the command (`GrafanaError`, exit 1) where bash printed "the user already exists, skipping" and exited 0. Two things this checkbox does **not** claim: that a lane has run it (§5 FIN-35 owes the real-VM proof), and that the row can never be vacuous again — it still can, if `GRAFANA_USERS` in the environment's `static.encrypted.json` declares no rows, which is why `grafana-account-rows-declared` asserts that before the create and `grafana-account-created` asserts the accounts afterwards.
- [ ] `pytest` runs in CI; CLI surface tests green. **Half met:** `uv run pytest -q tests/test_cli_surface.py`
      → 5 passed (exact `--help` set, no duplicate name, no callback under two names, `tunnel-polaris` calls
      `open_tunnel_polaris`) — but there is still no CI job to run it in (FIN-10).
- [x] **A parity harness exists, so deleting a migrated script is legal.** `tests/shims/` + `tests/cluster_parity.py`
      + `uv run pytest -q tests/test_parity_tunnels.py tests/test_parity_secrets.py
      tests/test_parity_tekton_runs.py tests/test_parity_kubernetes_reads.py
      tests/test_parity_generate_ssh_key.py` → 34 passed, covering all 12 of FIN-60's
      "delete immediately" scripts. FIN-03 deleted nothing itself and **FIN-60 executed the row: 8 of
      the 12 are gone** (§7 names the 4 that stayed and why). Deleting a script also means deleting
      the *assumption* that its bash will always be there to compare against, so the harness's rows for
      the deleted scripts compare against a recorded expectation instead, and two new tests in
      `tests/test_command_execution_policy.py` fail if `src/` names a script the ledger has already
      taken. **FIN-12 added the seventh parity file** —
      `tests/test_parity_tekton_installs_the_pinned_releases.py`, comparing the Python install against a
      record of the deleted installer, and it needed one new rule to do it honestly: RBAC rules are now
      reduced identically from `apiGroups` and from `api_groups`, because a manifest and a client model
      spell one rule two ways and comparing them as JSON would have reported a difference that is not
      one (`cluster_parity._rule_triple`). **FIN-13 added the eighth** —
      `tests/test_the_trigger_setup_asks_the_cluster_for_the_same_rbac.py`, which records the deleted
      script's eight RBAC heredocs as `RecordedCall`s and reduces them through
      `operations_from_calls`, so the deleted script is compared as what it asked the cluster and not as
      text — and it needed a second new rule, because a `RoleBinding`'s claim is *who is bound to what*:
      `cluster_parity.payload_facts` had no branch for `rolebinding`/`clusterrolebinding`, and a typed
      client model and a YAML manifest both fall back to comparing their whole body, which differs by
      key spelling (`api_group` against `apiGroup`) rather than by anything the operator decided.
      It proves commands and payloads,
      not a cluster's behaviour: the lane rows that exercise these paths still need their runs.
- [x] **Wave 7 landed** — `grep -rn "apiVersion: tekton.dev/v1beta1" tekton_ci examples` returns nothing,
      `Config.TEKTON_API_VERSION == "v1"` with **no env override** present, and the Triggers templates keep
      `triggers.tekton.dev/v1beta1` deliberately (6 generator templates; a different API group — the
      `grep -rnE "[^s]tekton\.dev/v1beta1"` form this row used to print never excluded it, see §1.6).
      `uv run pytest -q tests/test_tekton_manifest_api_versions.py` → 11 passed, which covers what no grep
      can: the `resources:` block of every PVC and every EventListener. Since FIN-74 the wave also carries
      its own version bump (Pipelines 1.17.1 / Triggers 0.37.2 / Dashboard 0.73.0, one release base URL,
      and a post-install assertion that the cluster serves `tekton.dev/v1` and
      `triggers.tekton.dev/v1beta1`: `uv run pytest -q
      tests/test_tekton_install_asserts_the_served_api_versions.py` → 10 passed). **The cluster half is not
      met:** the Wave T smoke lane has not run against v1, so "it applies to a real Pipelines 1.17.1" is
      still someone else's evidence (TODOs).
- [x] **Generated trigger config is read from `platform_config/{env}/{team}/triggers/`, and
      `tekton_ci/triggers/` holds only `templates/`** — since FIN-13. `TektonUtils.setup_triggers()` applies
      `Config.tekton_team_trigger_dir` and nothing else (`tests/test_the_trigger_setup_writes_rbac_the_way_the_manifest_did.py::test_the_generated_manifests_are_read_from_platform_config_and_not_the_checkout`
      writes the same manifest into both places and asserts only the `platform_config` one is applied);
      `initialize` no longer creates `tekton_ci/triggers/{TEAM}/`; the generator writes into
      `platform_config/{env}/{team}/triggers/{APP_NAME}/`; and `.gitignore` ignores everything under
      `tekton_ci/triggers/` except `templates/`, so the old location stops being an assumption on a
      clean checkout. `find tekton_ci/triggers -maxdepth 1 -type d` on a fresh tree returns the
      directory itself and `templates/` only. **Still owed:** the generator itself is bash
      (`generate_trigger_config_from_templates.sh`, FIN-23's port), so "produced under" is proven by a
      `sed`-driven path and not yet by a test of Python generation; and the two pre-existing generated
      directories under `tekton_ci/triggers/{smoke,dev1}/` in a working tree are leftovers, not product.
- [ ] `docs/` and `README.md` no longer instruct users to `cd scripts/…`, document the `src/` layout, the
      renamed command groups, the new `*_DEPLOYMENT_NAMESPACE` config keys, and how to run the E2E suite.

## 10. Risks, gotchas, non-goals

| Risk / gotcha | Mitigation |
|---|---|
| Silent behaviour drift between bash and Python (ordering, `|| true`, output format) | Parity-by-argv (FIN-03). Never accept "looks equivalent". |
| Deleting a script that CI still runs | §1.4 lists them; `scripts/tasks/*` gated on FIN-51. |
| `sed`-style Tekton v1 migration corrupts PVC/EventListener `resources:` blocks | YAML-aware transformer + the guard rules in FIN-71 + `tests/test_tekton_manifest_api_versions.py`, which asserts the shape of every PVC and EventListener `resources` block instead of greping for one. A `resources:`-for-`requests:` grep was the plan's earlier answer and it cannot distinguish a renamed block from a present one. |
| Mixed `v1beta1`/`v1` in one applied tree | One global `TEKTON_API_VERSION`; flip manifests, constant, and generators in one PR (§3 rule 7). |
| `apply_resources()` mishandles `.yaml`/nested dirs/missing `metadata` (D13) | Fix before reusing it for triggers/pipelines. |
| The Kubernetes client can't fully replicate `kubectl apply` for CRs | Prefer `kubectl apply -f -` via `shell.run` where the CR client falls short, and document it as a deliberate exception rather than silently diverging. |
| Re-running a command must be safe | Every create goes through a `create_or_replace*` helper; every delete tolerates 404. D4 is exactly this bug, already live — and only a *real* cluster run catches it (FIN-82). |
| CI install path drifts from the repo | FIN-50 deliberately resolves `kmt` via `uv run` from the cloned repo: no image rebuild per change, but the step needs PyPI access and `--locked` discipline. `src/` layout (FIN-01) is a prerequisite. |
| E2E suite becomes the bottleneck | With verification manual-only, the real risk is that it stops being run. Keep smoke < 25 min, make it one command with no ceremony, keep the lane's release minimal (the `dashboard` group per D5, pending FIN-89 — **not** a new `group: core`), cache the guest image. If lane time creeps, cut assertions from smoke; do not skip runs. |
| E2E run mutates the developer's checkout | Copy the tree inside the guest; never run `initialize` against a `--mount`ed working tree (§Wave T). |
| VM sizing too small → pods evict and the failure looks like a kubementat bug | Fail fast on `Pending` PVC / `Evicted` pods with an explicit "increase --ram/--disk" message instead of a generic timeout. |
| Stale guest image cache hides a provisioning regression | Document `rm -rf ~/vm-images/<distro>/<release>/` in `docs/README_DEVELOPMENT_FLOW.md`; wipe it when the pinned `resolute` image needs re-fetching. **No scheduler does this** (decision 4), so it belongs in the human-facing recipe. |
| Parallel agents collide | One worktree per concurrent ticket; Wave 0 and Wave 7 land alone before fan-out; E2E lane serialises on the KVM host. |
| Temptation to bundle unrelated refactors into migration PRs | Explicit ticket boundaries; structural decisions are FIN-01/05/07/70, not side effects. |
| **Re-adding compatibility code out of caution** | §1.7a is a standing instruction: shims, aliases and fallbacks need a written reason in the PR description. Reviewers should reject them by default. |
| **Upstream repo rot goes unnoticed** | Two repos are already dead (§1.8: `twuni`, kubernetes-dashboard). With no CI nothing catches the next retirement until a human runs the suite — so the smoke lane asserts `helm repo update` succeeds, failing within the first minute instead of surfacing days later as a mysterious install error. |
| **Harness-side workarounds hiding product bugs** | Research found the tempting shortcuts: `kubectl delete pvc` in teardown, `chmod` instead of `write-kubeconfig-mode`, piping `yes` into `kmt install`. None of these may be used as fixes — they belong in the finding. FR-61 says explicitly that a correct harness going red is an authorised **product** finding. |
| **VM/host-key and capacity traps the harness owns** | Reused DHCP leases break strict host-key checking; `pool-info` reports different free space than `df`; `destroy` errors on a missing VM. Each is a harness requirement (FR-12, FR-22, INV-2), not a flake to retry. |

**Non-goals for this plan:** externalizing `platform_config` to a separate repository (the intended
end-state for operators — a DevOps user gets *only* their own `platform_config` repo plus a pinned
`kubementat` version — explicitly **not** now, per decision 4), and Flux/ArgoCD GitOps, which is still
tracked in `TODOs.md` and deserves its own plan. Cluster-context/multi-cluster support is **not** a non-goal
of v2: this plan deferred it and v2 did it. Read the row below as history.

**2026-11-01/02 — the deferred item is done, not deferred any more.**
[`../specifications/cluster-config-v2.md`](../specifications/cluster-config-v2.md) encoded the owner's
decisions of 2026-11-01 and is implemented through `eca4c31` (fifteen commits, `08d513d`…`eca4c31`, each one
closing a ticket in that spec's own order). What that means for the claims this plan makes:
`environment` is the first positional of every cluster-touching command, `CLUSTER_KUBE_CONTEXT` is required
and the `current-context` fallback is deleted, `config.load_kube_config()` is gone from `src/` and every
client is built from a `ClusterTarget`, helm and helmfile receive `--kube-context`/`--kubeconfig` as argv, an
environment that resolves to the cluster the command is standing on is excluded from the 1:1 comparison, and
`from_service_account` is the last layer of the resolution order. Nothing in this plan is changed by any of
that except this row and §1.2's deferral; the ledger below is a migration ledger and the cluster work was
never a migration. That document also corrects one claim on this plan's risk table: *"The Kubernetes client can't fully replicate `kubectl apply` for CRs"* was an assumption,
not a measurement — `DynamicClient.server_side_apply(..., force_conflicts=…)` exists in the pinned
`kubernetes==33.1.0`. The exception it argues for is still kept, for a different reason (spec §10.1). Tekton **Triggers** API
versioning (`triggers.tekton.dev`) stays at `v1beta1` — 0.37.2 serves `v1alpha1` and `v1beta1` and no
`v1`, measured from its release.yaml by FIN-74 — and is *not* part of Wave 7.
