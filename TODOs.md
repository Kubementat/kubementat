# Kubementat Project TODO list
This list is a list of things that need to be done for improving Kubementat.

> **Superseded by [`docs/plans/finish-python-migration-last-lap.md`](docs/plans/finish-python-migration-last-lap.md)
> for the `Now` list's migration entries.** That file holds the ordered remainder, the owner
> decisions, and the commands that re-measure every number quoted below. **Do not open a migration
> ticket from this file.** Rows here and in the plan still list landed tickets — **FIN-11**
> (`kmt uninstall` in Python, `213bc9b`, lane-verified) and **FIN-19** (`kmt component uninstall`,
> 13 unit cases) among them — and a row that reads *stays* or *still tracked* is a sentence from an
> older date, not a claim about the tree. Read the last-lap file first; read this one for product
> work, decisions, and what has been closed.

> **The `Now` list further down is not the whole of what is left, and it is not where migration work
> is scheduled.** Every entry here that is really Python-migration work — replacing a `scripts/**`
> script, a `kmt run-script` call site, or a bash parity test — is tracked once, in execution order,
> in [`docs/plans/finish-python-migration-last-lap.md`](docs/plans/finish-python-migration-last-lap.md),
> together with the measurements that decide whether a row is still work (`find scripts -name '*.sh'
> | wc -l`, the unit-suite tail, the lane artifacts) and the owner decisions that would otherwise be
> re-litigated every time an agent reads this file. (The `How to read this file` bullet below sends
> you to `docs/plans/finish-python-migration.md` for migration work; that pointer is superseded the
> same way, and that file is meant to be read as history and as the deletion ledger.)
>
> **This file stays as the product list and the ledger of what has been closed.** Entries are not
> renumbered, deleted, or reworded, and an entry marked `[DONE]` here is `[DONE]` — the numbers
> quoted inside those entries are older than the tickets that changed them, which is exactly why the
> last-lap file exists. **Do not open a migration ticket from the `Now` list.** Read that file's **§2.1**
> first: it is an ordered list of the **seven** things that are left (new rows **18–24**, plus rows 6, 7 and 17
> carried forward), each with its measured state and a *done when*. §2.2 is the landed ledger, so a row that
> landed cannot be mistaken for work; §3 holds the answered owner decisions, §4 is what only a lane can pay,
> §5 is what the lane's reds actually mean, and §8 re-measures every number in either file.

**How to read this file**
- `[OPEN]` nobody has started it · `[IN PROGRESS]` a branch is out · `[BLOCKED]` waiting on something else
  (the blocker is named) · `[DECIDED]` the decision is made, the work is not · `[DONE]` closed and
  verified by the command or lane run named in the entry
- Migration work is tracked once in [`docs/plans/finish-python-migration-last-lap.md`](docs/plans/finish-python-migration-last-lap.md):
  **§2.1** is the ordered list of open rows, **§2.2** is the landed ledger. The older
  [`docs/plans/finish-python-migration.md`](docs/plans/finish-python-migration.md) is history and the deletion
  ledger (§7), not a to-do list. This file is for product work, decisions, and anything the plan would only get
  in the way of.
- Every `[OPEN]` entry below "Now" carries numbered action steps and a *done when* line. If you cannot
  start from the steps, the entry needs more research, not more courage.

## If you were just initialized

Five facts that cost an agent an afternoon when it does not know them. Everything else is in the two
documents this file points at.

1. **Branch and state.** `python_refactor_2`. Verify before you trust any number here:
   `git log --oneline -1`, `uv run pytest -q` (**918 passed, 1 skipped** at `4e00cf5`, ~52 s; a clean *worktree*
   gives 874 passed / 45 skipped, the 45 being parity tests that need `git show <sha>:scripts/...`), and
   `ls -t ~/.kmt-e2e/runs | head -1` for the newest lane. Numbers in the entries below are older than this.
2. **A lane is a decision, not a check.** `uv run python tests/e2e/suite.py --lane full --scope full` needs
   `virt-runner` 0.6.0, gives up the whole machine, and measured **17 minutes** (106 asserts, 10 red) at
   `4e00cf5`. The unit suite is the fast loop; the lane is the proof. When the two disagree, the lane wins and
   the plan gets corrected, not the test.
3. **What not to do.** Never `git add -A` — commit only the files you changed, because other work (the
   platform_config rework, `docs/research/**`) shares this tree. No `subprocess` import or `shell=True`
   outside `src/kubementat/shell.py`, no `os.chdir`. No ticket ids in test names
   (`tests/test_test_name_conventions.py` will fail the run). No backwards-compat shims and no aliases for
   renamed commands — those are owner decisions, not preferences.
4. **The open decision before code** is answered. Item 8 below (renaming a component namespace) was decided
   on 2026-10-29 — namespaces are static product defaults and a live rename is not a supported operation
   (last-lap §3.1). Item 13 below is the one that still needs a mechanism decision, not a verdict.
5. **Order of work.** Plan **§2.1** is the ordered remainder, and it is short: **18** (uninstall sweeps what
   install put there) → **19** (the tunnel signature rows) → **21** (`jq` out, `scripts/` empty) → **22**
   (FIN-61/62/63 docs + command groups) → **23** (FIN-10, CI runs the suite — parallelisable) → **24** and
   **17** (hardening, no lane) → **20** (the grafana config, an owner decision). Item 8 below is the
   namespace-rename boundary; item 13 below is what the last `full` lane found. **Housekeeping is unclaimed.**
   No `kmt` command shells out to a kubementat script any more, and `scripts/` is down to two 2-line wrappers
   that nothing execs.

## Now — next actions

> **Re-measured 2026-10-29 (`4e00cf5`), and that reading supersedes every number below.** `uv run pytest -q` →
> **918 passed, 1 skipped** (clean worktree: 874 / 45 skipped). `uv run kmt --help` → **36** commands, 11 of
> them still flat. `find scripts -name '*.sh'` → **2** (4 LOC), both 2-line `kubectl` wrappers nothing execs.
> A `full`/`full` lane ran: **106 asserts, 10 red, 17 min**
> (`~/.kmt-e2e/runs/20261029T223248Z-full-full-4e00cf5/result.json`). The lane moved three things from *owed*
> to *paid* — the cleanup-CronJob asserts, all eight team-account asserts, the reinstall cycle — and it
> produced two new findings: **item 13** (uninstall residue, which the plan had mislabelled as "not this
> migration's") and **item 5** (the Tekton tunnel signature went red after four green lanes). The ordered
> remainder is last-lap **§2.1**: 18 → 19 → 21 → 22 → 23 → 24 → 17 → 20.

Ordered by value.

**Current state as of 2026-10-28 (`badcf40`).** Twenty-nine commits since the last time this block was
written, and **four full lanes have run since** — the board is now evidence-led rather than
unit-suite-led. Read every `Landed:` line below against the lane named next to it.
- Unit suite: `uv run pytest -q` → **433 passed**, ~17 s, no cluster (it was **262** when this block last
  read as the only evidence, **388** at `badcf40`, **417** after FIN-12, **433** after FIN-13, **432** once
  Linkerd came out: the two Linkerd install/uninstall suites are deleted with the mesh and replaced by the
  present/absent pair in `tests/test_uninstall_sweeps_everything_kubementat_owns.py`, and the four cases
  that exercised the deleted `run_kubementat_script` helper are gone with it — the check they protected, that
  no `src/` string may name a `.sh` that is not there, is still asserted in
  `tests/test_command_execution_policy.py`), and **433** again with the ingress controller out — the
  `NGINX_INGRESS_DEPLOYMENT_NAMESPACE` case that `test_component_namespaces_come_from_configuration.py`
  parametrised off the template loses its key (−1) and the controller's present/absent pair is new (+2).
  Named for the behaviour it proves;
  `tests/test_test_name_conventions.py` fails the build if a ticket id creeps back into a file or case
  name (see `tests/README.md`).
- Full lane: `~/.kmt-e2e/runs/20261028T061713Z-full-badcf40` → **93 asserts / 4 red**, 29 min. The trail is
  `d1cc5dc` (88/16) → `3e8b3f6` (89/13) → `7269c9a` (93/12) → `021bdaa` (93/**5**) → `16c8196` (93/**3**) →
  `badcf40` (93/**4**). The four reds, and the reason each one is red, are item **8** — the honest
  summary is that two are one cluster-scoped helm fact nobody has decided about, one is the same fact
  seen from the probe, and one is a tunnel probe that returned an empty body after passing the lane
  before it.
- Scripts: **14 files / 911 LOC** in the checkout, re-measured 2026-10-28 after FIN-46 (`find
  scripts -name '*.sh' | wc -l` → 14, and `find scripts -name '*.sh' -exec wc -l {} + | tail -1` →
  911; **21 / 1,312 across the whole repo** by the command plan §1.1 names — **16 / 1,161** with
  `examples/` and `docker_files/` excluded, which is the number §7's end-state row counts toward 0.
  Down from 19 / 1,139 (FIN-35's count) and from 16 / 978 (`open_pod_tunnel.sh` 33 + `open_mysql_tunnel.sh`
  34 = 67 LOC went with FIN-46): `retrieve_token_for_service_account.sh` (25 LOC) went with
  FIN-36 and is now `kmt service-account-token`, and `create_k8s_secret_for_ssh_key.sh` (44 LOC) went
  with it as an unreachable script whose act is `KubernetesUtils.create_or_replace_secret()` driven by
  `kmt setup-secrets`. **Two stayed and were checked rather than deleted:** `base64_library.sh`, which
  `create_secret_from_json.sh` and `generate_ssh_key.sh` still `source` and which
  `tekton_ci/tasks/create-secret-from-json.yml` still runs, and `grafana_helpers.sh`, which
  `create_grafana_read_only_account.sh` `source`s and the harness's Grafana rows still reach. Both are
  FIN-37 and FIN-34/35's to retire, not this ticket's; see §7's FIN-30..39 row.

  *(2026-10-29, FIN-51: both halves of that sentence are closed. `grafana_helpers.sh` left with
  `scripts/user_management/` (FIN-35) and `base64_library.sh` left with the last script that sourced
  it — the check that kept it alive found that its one function, `base64_encode`, is called by nothing
  in the tree, so the `source` line was load-bearing only as a sentence. Guard:
  `tests/test_the_secret_json_scripts_are_gone_and_the_task_runs_the_command.py`.)*
  `ssh_into_helm_deployment.sh` (31 LOC),
  `tail_logs_for_helm_deployment.sh` (27 LOC) and `get_latest_version_for_helm_chart.sh` (17 LOC)
  are gone with FIN-44/FIN-45, replaced by `kmt deploy-ssh` / `kmt deploy-logs` /
  `kmt helm-version` — and `analyze_deployment.sh` (23 LOC),
  `retrieve_deployment_docker_image_versions.sh` (12 LOC) and `cluster_capacity.sh` (53 LOC) went
  with FIN-41/42, replaced by `kmt analyze-deployment` / `kmt pod-images` /
  `kmt cluster-capacity` — and the mysql/redis/nginx install scripts
  are gone, the Tekton installer is gone, `setup_triggers.sh` is gone, the four Linkerd scripts went with
  the mesh: `install_linkerd.sh`, `uninstall_linkerd.sh`, `generate_linkerd_certs.sh` and
  `open_linkerd_dashboard_tunnel.sh`, `setup_tekton_pipelinerun_cleanup_job.sh` (220 LOC) is gone too,
  replaced by `TektonUtils.setup_cleanup_job()`, and **`uninstall_helm_deployment.sh` (58 LOC) is gone** —
  item 12. No script went with the ingress controller: `install_routing.sh` is still here (85 LOC, was 76)
  and still applies `group=routing`; what changed is that the group is cert-manager now, and the script says
  so in its header. `src/` was **22** files / **7,410** LOC when FIN-36 last measured it and is
  **24 / 8,506** re-measured 2026-10-28 (FIN-46 added `open_tunnel_mysql()` and the `tunnel-mysql`
  command), which grew `kubernetes_utils.py` by the namespace-404 conversion and
  `cli/commands/kubernetes.py` by the token command. **30** `kmt` commands, no duplicates
  (`uv run kmt --help`; `kmt install` lost `--enable-linkerd/--disable-linkerd`,
  `tekton-setup-cleanup-job`, `helm-component-uninstall`, `helm-version`, the cluster reads
  `analyze-deployment` / `pod-images` / `cluster-capacity`, the two helm-deployment sessions,
  `service-account-token`, and now `tunnel-mysql` are the added ones — no alias, no shim, because
  the scripts they replace are deleted in the same commit). The e2e harness no longer needs `run-script` for the
  cleanup job: `tests/e2e/harness/config.py`'s `task.cleanup-job` row calls `kmt tekton-setup-cleanup-job
  {env} --team {team}` directly, so one fewer row goes through the operator's escape hatch. Python
  shells out to **0** kubementat scripts — `run-ci-container` is `CiUtils` now, and there is no Linkerd
  install to shell out to; no CLI command uses `shell=True` and `subprocess` is still only
  `src/kubementat/shell.py`. **0** product manifests on `tekton.dev/v1beta1`; the 6 trigger generator
  templates are on `triggers.tekton.dev/v1beta1`, which is what Triggers 0.37.2 serves. Tekton pinned at
  **Pipelines 1.17.1 / Triggers 0.37.2 / Dashboard 0.73.0**, installed from `infra.tekton.dev` by
  `TektonUtils.install()`.
- Scripts, re-measured 2026-10-28 (FIN-22 / FIN-23): `find scripts -name '*.sh' | wc -l` → **14 → 12**,
  and `find scripts -name '*.sh' -exec wc -l {} + | tail -1` → **911 → 717**. What went is the pair the
  ledger said was only reachable by hand —
  `scripts/automation/tekton/generate_deploy_pipeline_from_template.sh` (71 LOC) and
  `scripts/automation/tekton/generate_trigger_config_from_templates.sh` (123 LOC) — and **neither had a
  caller to port**: `grep -rl generate_deploy_pipeline_from_template src tests/e2e tests/*.py tekton_ci
  README.md` answers nothing, same for the trigger script, so what landed is two commands that did not
  exist (`kmt tekton-generate-deploy-pipeline`, `kmt tekton-generate-triggers`) and not a library call
  with a script behind it. `kmt --help` → **32** commands. Repo-wide that is **19 files / 1,118 LOC**
  (`find . -name '*.sh'`, `.git` excluded), **14 / 967** with `examples/` and `docker_files/` excluded,
  which is the number §7's end-state row counts toward 0 — so the two generators take it down one more
  step without touching the 12 that are still called from CI manifests or `run-script`.
  `src/` grows by the two generator methods and the substitution table they share with `run_pipeline`
  (`tekton_utils.py` is where both live); no new module, and no `subprocess` outside `shell.py`.
- Scripts, re-measured 2026-10-29 (**routing**): `find scripts -name '*.sh' | wc -l` → **11** and
  `find scripts -name '*.sh' -exec wc -l {} + | tail -1` → **683 LOC**. The one that went is
  `scripts/automation/components/install_routing.sh` (85 LOC), so the sentence two bullets above that
  reads "no script went with the ingress controller" was true at `badcf40` and is not true now: the
  routing group gained a Python owner, and the script it used to call is deleted. `uv run pytest -q` →
  **704 passed, 1 skipped** (~24 s); `uv run kmt --help` → **33** lines where the same grep counted
  **32** before, the new line being the `routing` group (`kmt routing install`, `kmt routing status`),
  and `users` still the only other group. The entry this closes is **11** below, and what it owes beyond
  the unit suite is the `routing` lane's own artifact, not a `full` lane.
- Landed since the block was last written: the mysql/redis/nginx automation deleted (owner decision
  2026-10-27), **FIN-11**'s Python uninstall including the namespace-drain wait, **FIN-30…FIN-33** team
  accounts in Python with `kmt users …` commands and 5 bash scripts deleted, namespaces-as-config's
  **deploy half** (`helmfile.yaml.gotmpl` reads `env … | default …`, exported by
  `HelmUtils._export_component_namespaces`), grafana's admin credentials moved to one source with a
  fail-early when unset, and the harness fixes a lane needs rather than one that invents: baseline
  measurement, tunnel session-leader kills, signature tables.
- The account asserts that used to be **absent** are now **green** end to end (`account-can-i-create`,
  `cap:create-team-account-read-only`, `account-read-only-account-resolvable`,
  `account-read-only-cannot-mutate`, `cap:delete-team-account`, `account-deleted`,
  `assert:account-token-secret-deleted`). Item 7 is closed.
- `docker_files/**` work landed (`ace958d`/`2f3b52f`), so FIN-50/FIN-51's blocker is removed but **neither
  has been verified** — the CI manifests still calling `scripts/*.sh` inside a pipeline pod is the largest
  unmeasured area left. Plan §1.3a carries per-ticket status and **§1.3b is the ordered remainder.**
- Config work is deliberately deferred: owner said 2026-10-28 that the `REPLACEME_*`/token keys in
  `templates/environment/static.encrypted.json.template` are handled later. Do not start on them; item 9
  records what was found so it does not have to be rediscovered.

### 1. `[DONE]` Make `kmt helmfile-apply` scriptable — the `[y/n]` prompt is the whole failure
- **Why:** `--helmfile-label-filter group=routing` gets all the way to helmfile, prints the
  `cert-manager` + `nginx-ingress` diff and then asks `Do you really want to apply? [y/n]:` on a
  non-terminal stdin. helmfile takes the EOF as a refusal and exits 1. Every scripted use of this
  command fails the same way, and the product gives no `--yes` the way `install`/`uninstall` do.
  (`nginx-ingress` is not in that diff any more — it left the routing group on 2026-10-28, item 11 — but
  the prompt it was measured on is this item's finding and stands.)
- **Evidence:** `stages/02-install--cap:helmfile-apply:routing.stdout.log` ends on the prompt;
  `src/kubementat/helm_utils.py` appends `--interactive` whenever `helmfile_apply(interactive=True)`,
  which is the default that `src/cli/commands/helm.py` passes. Still red on `d1cc5dc`, with a
  failure detail byte-identical to the pre-FIN-02 run: the argv conversion changed nothing here.
1. Decide the rule. Recommendation, mirroring `uninstall`: interactive stays the default *for a tty*,
   and any non-tty run applies without asking. Add `--yes`/`-y` as the explicit form, consistent with
   decision 7 and FIN-11.
2. Implement in `src/kubementat/helm_utils.py` + `src/cli/commands/helm.py`. The `shell=True` that used
   to be on that line is **gone** (FIN-02): helmfile runs as argv through `kubementat.shell.run`, which
   returns a `CommandResult`. So `--yes` is a decision about the flag list, not about how the command
   is executed — and a non-tty default can be expressed as "no `--interactive`" rather than as input
   juggling. If the decision ends up being "ask only on a tty", the tty check belongs in the CLI
   command, not in the library.
3. Add the flag to the suite's capability row (`helm.helmfile-apply` in
   `tests/e2e/harness/config.py`) so the row tests *applying*, not prompting.
4. Re-run the full lane and confirm `cap:helmfile-apply:routing` is green in both install and rerun.
- **Done when:** `kmt helmfile-apply <env> --helmfile-label-filter group=routing < /dev/null` exits 0 and
  the lane row is green.
- **Landed:** the tty decision moved to `kmt helmfile-apply` (`interactive = sys.stdin.isatty() and not yes`);
  `--interactive/--non-interactive` are gone and `--yes`/`-y` is the explicit form, `HelmUtils.helmfile_apply`
  keeps `interactive: bool = False` and appends the flag only when True, `install_kubementat` states its
  `interactive=False` explicitly, and the `helm.helmfile-apply` lane row passes `-y` so the row applies
  instead of prompting — verified by: `uv run pytest -q` → 152 passed, of which
  `uv run pytest -q tests/test_helmfile_apply_asks_only_on_a_terminal.py` → 10 passed (non-tty → no
  `--interactive`, `-y` → no `--interactive`, tty → `--interactive`), plus
  `PATH=<recording fake helmfile> uv run kmt helmfile-apply dev --helmfile-label-filter group=routing < /dev/null`
  → exit 0 with `--interactive` absent from argv (and the same command under `script` showing the flag appear,
  and disappear with `-y`). **Still needs a manual full-lane run** for the `cap:helmfile-apply:routing` row;
  no cluster is reachable from the implementing host, so the `exit 0` above is the code path plus a
  recording helmfile, not a real apply.
  *(2026-10-29, routing: that row no longer exists. `group=routing` is applied by `routing.install` through
  `GROUP_APPLY_CAPABILITIES` (`tests/e2e/harness/config.py:507-514`), so this item's last lane half is
  closed by the group acquiring a different applier and not by a run — `kmt helmfile-apply` itself is
  untouched. Which group gets applied how is now item 11's business.)*

### 2. `[DONE]` The tekton cleanup cronjob lives in a namespace nobody declared
- **Why:** `scripts/automation/tekton/setup_tekton_pipelinerun_cleanup_job.sh:23` hardcoded
  `CRONJOB_NAMESPACE="cluster-cronjobs"`, while the suite looked for the job in
  `"%s-pipelines" % TEAM` (`tests/e2e/harness/stages.py:873`). Two reds are the same bug:
  `assert:cleanup-cronjob-exists` and `assert:cleanup-cronjob-scheduled` (both report empty lists).
- **Also red for the same namespace:** `assert:no-extra-namespaces` reports `cluster-cronjobs` still
  present after uninstall — install creates it, uninstall does not remove it.
1. Read the namespace from config, not code: `CRONJOB_NAMESPACE` from
   `platform_config/{env}/{team}/static.json` (add the key to
   `templates/environment/team/static.json.template`) with the current value as the fallback. This is
   FIN-07's rule applied to a script the Python migration left behind.
2. Decide where it belongs: a per-team namespace that uninstall already sweeps, or one
   cluster-wide namespace that uninstall must explicitly remove. Per-team is cheaper to test.
3. Teach the uninstall path about whichever you choose (FIN-11's namespace list) so
   `kubectl get ns` after uninstall returns to baseline.
4. Have the suite derive the namespace from the same config key instead of `"%s-pipelines"` — right now
   the harness has its own guess about the same fact.
5. Full lane, stage 05: confirm `cleanup-cronjob-exists`, `cleanup-cronjob-scheduled` and
   `no-extra-namespaces` together.
- **Done when:** no namespace outside the install baseline survives uninstall and the two cronjob
  asserts pass without the harness guessing.
- **Landed:** `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE` is a key in `templates/environment/static.json.template`
  that `initialize` writes, read by the uninstall path (`constants.DEFAULT_NAMESPACES` →
  `Config.component_namespace`; it used to be
  `uninstall_kubementat.sh`, deleted by FIN-11), and by the suite's cleanup-job assert — which
  no longer guesses `"<team>-pipelines"`. The other 14 `*_DEPLOYMENT_NAMESPACE` keys and the 4
  `*_SERVICE_SELECTOR` keys came with the same accessor, `Config.component_namespace` /
  `component_service_selector`, and `constants.py` is the only place a default lives.
  Landed: component namespaces are configuration, not code — verified by:
  `grep -rn "= *'grafana'\|= *'prometheus'\|= *'openbao'\|= *'polaris'" src/kubementat` → empty, plus
  `uv run pytest -q` → 177 passed.
  **Landed through Python too now:** the script that still had the constant as its own is deleted, and
  `TektonUtils.setup_cleanup_job()` / `kmt tekton-setup-cleanup-job` reads the key through
  `Config.component_namespace("cluster-cronjobs")` instead of remembering an answer. Verified by:
  `uv run pytest -q` → **439 passed**, of which
  `tests/test_the_cleanup_job_is_created_in_the_namespace_the_config_names.py` → 7, including the case
  that renames `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE` and asserts the CronJob, the ServiceAccount and
  the namespace all move together; the parity case compares the whole CronJob payload (schedule, both
  history limits, `concurrencyPolicy`, the pod labels, the image, the prune script) against the heredocs
  read out of `git show 6d55f7d:scripts/automation/tekton/setup_tekton_pipelinerun_cleanup_job.sh`.
  `grep -rn "setup_tekton_pipelinerun_cleanup_job" src tests README.md` hits only the parity test's
  citation of that blob and the namespace guard that says it must stay deleted. The harness row is the
  command now: `task.cleanup-job` → `kmt tekton-setup-cleanup-job {env} --team {team}`, no `run-script`.
  **Closed by a lane 2026-10-29:** `20261029T223248Z-full-full-4e00cf5` reads
  `cap:task.cleanup-job`, `assert:cleanup-cronjob-exists` and `assert:cleanup-cronjob-scheduled` all green on a
  cluster that really ran the command, which is the half this entry owed. `no-extra-namespaces` is green too —
  the namespace this ticket started with is swept. One thing under it is still unread: that the CronJob's own
  pod pulls through the Secret the command creates (last-lap §4). Steps 2 and 3 of the list above took the per-team namespaces (`dev1`/`smoke`) out of scope —
  enumerating teams is FIN-11's, and the cronjob namespace is cluster-wide by choice. That open end closed
  with the Python uninstall (`Config.teams()` + `Automation.uninstall_kubementat`, `dc9d8f6`/`213bc9b`):
  every team's two namespaces are swept now, and `cluster-cronjobs` goes with the component set.

### 3. `[PARTLY DONE]` Backing services: MySQL / Redis / NGINX go through standard helm chart deploys
- **Decision:** no more specialised install automation. These three get deployed like any other app, by
  a standard helm chart deploy (see the longer notes under "Refactoring backing services" below — that
  section is the detail, this is the decision).
- **Done 2026-10-27 (the deletion half):** owner decision 2026-10-27 deleted the install automation
  outright instead of waiting for the replacement, because the pinned bitnami images return `NotFound`
  from docker.io and all three automations were red on a clean cluster
  (`docs/plans/lane-verification-3e8b3f6.md` §2.1). Gone: the three `scripts/tasks/install_*` scripts,
  `tekton_ci/pipelines/setup-pipeline-{mysql,redis}.yml`, their `examples/` copies, the
  nginx-reverse-proxy example pipeline and its run, the four `task.install-*` / `task.setup-mysql-databases`
  capability rows and the `mysql` / `redis` / `nginx_proxy` lane features. `setup_mysql_databases.sh`
  stayed, and **left on 2026-10-29** with `tekton_ci/tasks/run-automation-script-for-environment.yml` — the
  Task that was its only route to a cluster — under the owner's rule that a Task whose job is to execute a
  path you hand it is not a product verb (`0cf22b7`). Creating databases and users **from configuration**
  is retired, not migrated: FIN-52 removed the key (`8d9c318`) and this removed the reader, so nothing in
  this tree declares it or reads it. The two MySQL Tasks stay and do their own job. The 14 config keys
  that only those scripts read are gone from
  `templates/environment/team/static.json.template`.
  **The accepted cost: kubementat ships no working backing-service install until item 2 below lands.**
- **Still open (the replacement half):**
1. Pick a chart source for each of the three and give it somewhere to live: `values.encrypted.yaml` under
   `platform_config/ENV/TEAM/<APP>/`, deployed by `deploy-pipeline-helm-chart.yml` like any other app.
   The old pins are gone with the scripts (`NGINX_REVERSE_PROXY_HELM_CHART_VERSION`,
   `MYSQL_HELM_CHART_VERSION`, `REDIS_HELM_CHART_VERSION` no longer exist in
   `templates/environment/team/static.json.template`) — there is no key left to point at, so a
   replacement declares its own chart + version and does not inherit a bitnami pin. The same
   availability/licensing argument that killed bitnami applies to whatever is chosen.
2. Close the gaps this section already lists. They were written as gates *before* deleting anything;
   the 2026-10-27 decision deleted first, so they are now blockers *on* the replacement:
   `deploy-pipeline-helm-chart.yml` needs `path-to-values-file` and `helm-timeout`, and
   chart/repo/version/values must be declared in `platform_config/ENV/TEAM/<APP>/values.encrypted.yaml`
   instead of interpolated from env vars in bash.
3. ~~Remove in one change per service, with its script, its setup pipeline, its env-var keys and its
   capability row in `tests/e2e/harness/config.py`: `install_mysql_helm_chart.sh`,
   `install_redis_helm_chart.sh`, `install_nginx_reverse_proxy.sh`, `setup_mysql_databases.sh` stays as a
   task/trigger, not a deploy.~~ **Done 2026-10-27** — deleted, not moved behind a flag; see item 3
   above for exactly what left the tree. (`setup_mysql_databases.sh` outlived that change by two days and
   went with the script-execution ticket on 2026-10-29, so no member of this family survives.)
4. ~~Do not leave the harness rows pointing at deleted scripts: `tests/test_harness_capability_argv.py`
   fails when a capability names a script that is not in the checkout, which is the guard you want
   here.~~ **Satisfied 2026-10-27** — the rows are gone, and that guard is what proved it.
- **Done when:** ~~`scripts/tasks/install_{mysql,redis,nginx}_*.sh` are gone, the lane has no
  `task.install-*` rows, and the same services come up through the standard deploy pipeline.~~ — the first
  two clauses are **done 2026-10-27**; only "the same services come up through the standard deploy
  pipeline" is left, and that is items 1 and 2.

### 4. `[DONE]` The grafana API probe reads the wrong user
- **Measured 2026-10-29 (`4e00cf5`) — the entry is closed in code and the lane row is red, which is the
  condition this entry predicted and did not settle.** `assert:grafana-account-rows-declared` now **fails**
  ("`platform_config/dev/static.encrypted.json` declares no `GRAFANA_USERS` rows") and
  `cap:create-grafana-accounts` **exits 1**. The command is right to refuse, so this is not a product defect;
  it is last-lap row **20**, and it is an owner/config decision: seed a real row and assert the account
  actually lands in `/api/org/users`, or have the harness report "nothing declared" as a *skip that names the
  file*. It is no longer a vacuously-green row, so do not re-read the four-condition note below as though the
  guard were still doing its job — the guard is what is failing, correctly. (The earlier text said the row
  "would be green again but vacuous"; measured, it went red instead, and that is the better outcome.)
- **Why:** the suite probes `/api/org/users` as `admin` (`tests/e2e/harness/stages.py:863`) while the run
  creates `kmt-e2e-admin` (`GRAFANA_ADMIN_USER`). Whatever it returns, it is not testing the account the
  suite set up.
1. Use `config.GRAFANA_ADMIN_USER` in the probe.
2. Check `_service_endpoint("grafana", namespace="grafana")` at the same time: the namespace is hardcoded
   there too (FIN-07), which is why `assert:grafana-api-readable` reports `000` rather than a 401.
3. Confirm `assert:grafana-api-readable` and `cap:create-grafana-accounts` both move once the
   credentials are real — the second one may have been passing vacuously.
- **Done when:** the probe is a 2xx against the account the lane created.
- **Landed:** the probe is a real login at a reachable address. Both keys come from
  `platform_config/{env}/static.encrypted.json` — the file `helm_utils.helmfile_apply` exports them from and
  `create_grafana_read_only_account.sh` logs in with, rather than the hardcoded `admin` and the team file
  that declared neither key — and the endpoint is the service's ClusterIP. A second probe logs in as the
  exported `GRAFANA_ADMIN_USER`, so the account the lane *intends* is measured rather than assumed —
  verified by: `uv run pytest -q tests/test_the_grafana_probe_reads_the_run_own_configuration.py` → 7 passed,
  and `uv run pytest -q` → 262 passed.
- **Three corrections, each measured here rather than remembered:**
  1. Nothing creates `kmt-e2e-admin`. `initialize` writes no `GRAFANA_ADMIN_USER` (it is not one of
     `KUBEMENTAT_ENVIRONMENT_CONFIGURATION`, so it stays what the template ships, `grafadmin`) and
     `helm_utils.helmfile_apply` re-derives both keys from the config file before helmfile runs, overwriting
     what the lane exported. So step 1 as written would have probed a user that does not exist on purpose.
     The new `assert:exported-grafana-admin-user-reaches-the-install` is expected-red-because-product: what
     an operator exports never reaches the install.
  2. Step 2's `000` was not the namespace — that read was already right (FIN-07). The probe asked
     `<svc>.<ns>.svc.cluster.local`, which no node resolves: `000` is "could not connect", and it would have
     read `000` with the right user and password too. Fixed by probing the ClusterIP instead.
  3. Step 3: `cap:create-grafana-accounts` **is** vacuous, and measurably so. Its own base URL defaults to
     `127.0.0.1:31827`, which no lane opens (grafana declares no NodePort anywhere in the templates); the
     failed `curl` inside a command substitution does not abort `set -e`; `jq '.users[0]'` on the empty body
     it leaves behind exits **0** with no output; and `create_new_user_for_team` reads that empty string as
     "user already exists, skipping user creation!". Exit 0, nothing created. The row is therefore not
     evidence of an account, which is exactly why FR-56's API assert is the line that matters. Measured by:
     `printf '' | jq -r '.users[0]'; echo $?` → empty, `0`, and the `if [[ "$existing_user" != "null" ]]`
     branch in `scripts/user_management/grafana_helpers.sh` printing the skip. Making that row honest is
     product work (the script needs the tunneled URL, i.e. FIN-35's shape), not this entry's.
  4. **The client that fixes it landed 2026-10-28, and the row is not closed yet.** `src/kubementat/
     grafana_client.py` is FIN-34: `GrafanaClient`, which raises on a non-2xx, raises on an empty body,
     reads "already exists" from a 412 that says so rather than from no output, and refuses to default a
     base URL — one exception per loss above. Evidence: `uv run pytest -q` → **536 passed**, the 41 new
     cases in `tests/test_a_grafana_account_is_answered_for_and_not_assumed.py` (`pytest -q tests/
     test_a_grafana_account_is_answered_for_and_not_assumed.py` → 41), including the one case that kills
     this entry: `test_an_empty_body_from_the_search_is_a_failure_and_not_an_absent_user`.
     **What closes the row is FIN-35, not FIN-34**: no `kmt` command and no `Automation` method calls the
     client yet, `create_grafana_accounts.sh` is still what the harness runs for
     `cap:create-grafana-accounts`, and the script still gets its address from a positional argument with
     the same useless default. Until that row runs the command, it measures the script's own optimism and
     is worth nothing — which is the honest state, and the reason the plan's FIN-34 row says
     `verified-by-unit-suite / not wired to a CLI` rather than `landed`.
  5. **Closed by FIN-35 (2026-10-28): the row is honest, with one condition.** `kmt users
     create-grafana-accounts <env> [--team] [--grafana-url]` (and its single-account sibling) is the
     caller, `src/kubementat/grafana_accounts.py` is the flow, and the three scripts are deleted — so
     all four losses are closed where they lived: the address is resolved (`--grafana-url`, else the
     Service ClusterIP through `GRAFANA_SERVICE_SELECTOR`, else a failure that names both), the request
     is not inside a `$( )`, an empty body raises instead of answering, and "already exists" comes from
     a 412. Measured on both sides at once: `tests/test_parity_grafana_account_scripts.py
     ::test_an_unreachable_grafana_makes_the_script_green_and_the_command_red` runs the deleted blob
     against a `curl` that answers nothing (**exit 0**, "already exists, skipping") and the command
     against the same silence (**exit 1**). `uv run pytest -q` → **560 passed** (536 before, +24).
     **What still makes it vacuous, and it is not the code:** the row is only as honest as the config
     that drives it. `GRAFANA_USERS` in `platform_config/dev/static.encrypted.json` declares exactly one
     row today (`replaceme@example.com`, `type: read-only`); empty it and the command correctly creates
     nothing, correctly says so, and the capability row would be green again. That is why the lane now
     asserts `grafana-account-rows-declared` *before* the create and `grafana-account-created` (the
     declared emails, present in `/api/org/users`) *after* it — and why a lane run is still owed: (a)
     that a real Grafana answers 412 with a message containing "already exists", (b) that `Viewer` is
     honoured, which this command never sends, (c) that a Service lookup against a real cluster returns
     the ClusterIP the client is pointed at. Two product questions the code deliberately did not answer:
     whether "read only" should mean `orgRole: Viewer` — the scripts never set a role, so the command
     grants nothing and the client's `Viewer` half stays uncalled — and whether a `GRAFANA_USERS` row
     should carry a **team**, which is the only thing that could give `add_user_to_team` a caller.

**Re-measured 2026-10-28 (FIN-35):** `find scripts -name '*.sh' | wc -l` → **19 → 16**, and
`scripts/user_management/` is gone entirely (3 scripts here, 3 more with FIN-30…FIN-33 = the 7 §2.3 lists).
Everything `*.sh` outside `examples/` and `docker_files/` is **18**: the 16 product scripts above, plus
`tests/e2e/stages/01-provision.sh` (the harness's provisioner) and `tests/shims/_recorder.sh` (the parity
shims' recorder). Neither is product: the first is the lane, the second is the parity harness FIN-60's
deletions are judged against. `uv run pytest -q` → **560 passed** in ~18 s.

### 5. `[OPEN]` Tunnel asserts: the lifecycle half is paid, the signature half is a suspect
- **Measured 2026-10-29 (`4e00cf5`, last-lap row 19).** `tunnel-port-freed:*` and `tunnel-pod-tcp-connect` are
  green on every lane since the group-kill fix. `tunnel-signature:tunnel.tekton` read green on four
  consecutive lanes (`7269c9a` → `47b186f`) and is **red** on `4e00cf5`; `tunnel-signature:tunnel.grafana`
  alternates lane to lane. Both say `body=empty` with `bound-before-start=False` and the port free afterwards.
  **The old "a probe that raced the bind" note is a hypothesis that has now been used twice, not a finding.**
  Read the guest's own tunnel log in the artifact directory before touching probe code: empty body plus a free
  port means either "never bound" or "bound and answered nothing", and those want different fixes in
  `Automation.open_tunnel_*` and in the harness. This is the only red on the board that could be a regression
  the migration itself made.
- **Why:** 7 reds (`tunnel-signature:*`, `tunnel-port-freed:*`, `namespace-rename-tunnel-still-correct`)
  are the probe/tunnel lifecycle, not the tunnel. The brief for the last two fixes assigned them to the
  harness deliberately; they are recorded here so they stop being "the suite's problem".
1. Make the probe follow 302s (`curl -L`) before comparing signatures — grafana answers `/login` with a
  redirect and the current probe never follows it.
2. Kill the tunnel by process group, not by pid: `kubectl port-forward` survives the current kill, so
   `tunnel-port-freed` measures the wrong thing twice per run.
3. `namespace-rename-tunnel-still-correct` needs the tunnel to read `GRAFANA_DEPLOYMENT_NAMESPACE`
   instead of the hardcoded `namespace = 'grafana'` — `automation.py:1002`, and the same `# TODO: make
   this a config` appears for `tekton-pipelines` (1028), `polaris` (1052) and `openbao` (1076). That one
   *is* product, and it is FIN-07.
4. Re-run the full lane after 1+2; expect those six to be green and this last one to move with FIN-07.
- **Done when:** the six lifecycle asserts pass on a lane that changed nothing but a namespace name.
- **Landed (steps 1 and 2, 2026-10-28, harness side only):** the probe reads `curl -L`; the tunnel is
  started with `setsid` and stopped by process group (TERM, then KILL for the survivors, then the port is
  *polled* until free — no longer a pid kill followed by an assumption); every tunnel assert says how it
  stopped the tunnel, which socket is holding the port if one still is, and whether the port was already
  bound before the start (that leak is what made a re-tunnel read somebody else's answer).
  Verified by: `uv run pytest -q tests/test_the_tunnel_probe_measures_the_tunnel_it_started.py` → 6 passed,
  plus this host for the kill semantics it now depends on: after `setsid` the recorded pid is both sid and
  pgid leader (`ps -o sid=,pgid=`), `kill -TERM -<sid>` takes the whole subtree, and the pid-only kill it
  replaces leaves the child orphaned.
  **The lifecycle half is closed (`4e00cf5`): `tunnel-port-freed:tunnel.grafana`,
  `tunnel-port-freed:tunnel.tekton` and `tunnel-pod-tcp-connect` are green on this and every lane since the
  group-kill fix, which is the "done when" of steps 1 and 2. The entry stays `[OPEN]` for the signature half
  only — see last-lap row 19 / the paragraph below.**
- **What each assert read on the four lanes since (measured, not predicted):** both
  `tunnel-port-freed:*` and `tunnel-pod-tcp-connect` are green, and `tunnel-signature:tunnel.tekton` is
  green — the prediction was right that the signature table was the bug, not the tunnel: the dashboard
  serves an SPA document, so the row now asks for `tekton` and `id="root"`, verified against captured bodies
  (`~/.kmt-e2e/runs/20261027T184758Z-full-3e8b3f6/stages/05-features--tunnel-{grafana,tekton}.body.log`).
  The grafana row reads `grafanaBootData` / `reactRoot` for the same reason. The tunnel's session-leader
  problem (an async list `rm -f log && setsid … &` makes `$!` a subshell pid, so the group kill hit nothing)
  was the actual cause of ports staying bound; that is fixed and the asserts say how they stopped.
- **Still red, and why:** `namespace-rename-tunnel-still-correct` is no longer red for the reason predicted
  above — `helmfile.yaml.gotmpl` reads the namespace from the environment now, so the deploy half of
  FR-51 landed. It is red because **helm refuses to move a release that owns cluster-scoped objects into a
  second namespace**. That is item 8; read it before touching any tunnel code. The second grafana
  signature row is the same fact seen from the probe side. And `badcf40` added a red that
  `16c8196` did not have: the *plain* `tunnel-signature:tunnel.grafana` came back with `body=empty,
  bound-before-start=False` — one lane of evidence, a probe that raced the tunnel's bind rather than a
  credential or signature problem, and the first thing to re-confirm on the next lane before anyone
  goes looking for a product cause.
- **Stale detail corrected twice, and the second time is this ticket's:** the "four `open_*_tunnel.sh`
  scripts still carry literals" note stopped being true when FIN-60 deleted those four, and the note
  that replaced it ("the only ones left are `open_pod_tunnel.sh` and `open_mysql_tunnel.sh`") has now
  been closed — **both are gone**, so there is no `scripts/utilities/tunneling/` directory and no bash
  behind any `kmt tunnel-*`. `open_mysql_tunnel.sh` became `Automation.open_tunnel_mysql()` /
  `kmt tunnel-mysql`, which resolves the namespace from `APP_DEPLOYMENT_NAMESPACE` and the pod from
  `MYSQL_DEPLOYMENT_NAME` instead of taking both from whoever typed them; `open_pod_tunnel.sh` needed
  no command at all, because `kmt tunnel-pod` was already the same `kubectl port-forward` with the same
  four arguments, so it is deleted without a replacement and without a second generic tunnel command.
  The lane calls none of them, and the `--host`/address default is unchanged (`0.0.0.0`), so nothing in
  §1.3 of item 5's tunnel notes moves — including the session-leader fact below, which is the reason a
  tunnel must never be captured.
- **One thing FIN-02 changed underneath the harness:** `kmt tunnel-*` no longer runs `kubectl port-forward`
  through a shell, so the tree is `kmt` → `kubectl` with nothing exec-ing the last command. The pid the
  harness held was the wrapper's and the survivor was a direct child of the CLI — which is exactly the shape
  `_stop_background` now handles by group.

### 6. `[DONE]` The two structural tickets — landed 2026-10-27
- **The shape of the two tickets, for the record:** FIN-01 moved `lib/kubementat` → `src/kubementat` and
  `cli` → `src/cli`, dropped the `sys.path.append` hacks, pointed
  `[tool.setuptools.packages.find]` at `src/`, and shipped `tests/test_import_hygiene.py` with it — which
  closes D1 and D2. One catch worth carrying: moving `cli` added a level of depth, so `cli/kmt.py`'s
  `kubementat_main_dir` had to be recomputed or every script execution resolves `<repo>/src/scripts/`.
  FIN-02 replaced every command execution with one primitive (`src/kubementat/shell.py`), which closes D8
  and removes the 4 `os.chdir` calls and the 3 private `run_script` copies.
- **Why they were first:** they are the only Wave 0 tickets that change what the other tickets can say.
  FIN-01 unblocked D1/D2 and the way `kubementat_main_dir` is derived; FIN-02 unblocked the argv rule
  that FIN-03's parity harness is built on.
- **What landed:** `eb37c34` (FIN-01) + `4f78754` (FIN-02) + `d1cc5dc` (test naming).
  `import kubementat` works from a clean venv and `import lib.kubementat` fails; `grep -rn shell=True src`
  → 0 hits in code, `subprocess` is imported by `src/kubementat/shell.py` alone; no `os.chdir` anywhere
  and no private `run_script` copies left. `src/kubementat/shell.py::run(argv, *, cwd, env, capture,
  check, retries, retry_backoff, dry_run, input)` is the primitive — read its module docstring before
  adding an execution site, because it refuses a command string on purpose.
- **Verified on a real cluster:** the full lane's script-execution and helmfile paths behave as before,
  and `assert:weak-ok:install-nginx-no-traceback` moved from red to green because a failing script now
  reaches the operator as `Error: Command failed with exit code 1: <argv>` instead of a traceback.
- **What the two tickets did *not* close, and what to pick up next:**
  1. ~~**FIN-03** — the argv-recording shims and `bash`-vs-Python parity tests.~~ **Landed
     2026-10-27.** FIN-02 shipped shims for the converted call sites only
     (`tests/test_command_execution_policy.py`); this ticket made them repo-wide and added the
     other half. `tests/shims/` (one recorder behind eight symlinks) records the argv of
     `kubectl`/`helm`/`helmfile`/`tkn`/`git-crypt`/`mysql` plus a `yq` that only converts,
     `tests/conftest.py` gives every test a throwaway checkout with its own `platform_config`, and
     `tests/cluster_parity.py` reduces *both* sides — kubectl argv **and** generated-client calls —
     to one comparable operation. The 12 scripts on FIN-60's "delete immediately" row now have a
     real parity test each. *Landed: the shim harness, the fixture repo, the operation reducer, the
     six `test_parity_*.py` files and `tests/test_cli_surface.py` — verified by: `uv run pytest -q`
     → 232 passed, and `uv run pytest -q tests/test_parity_tunnels.py tests/test_parity_secrets.py
     tests/test_parity_tekton_runs.py tests/test_parity_kubernetes_reads.py
     tests/test_parity_generate_ssh_key.py` → 34 passed.* No script was deleted; a green parity test
     is the licence to delete, not the deletion.
  2. ~~**FIN-05** — `KUBEMENTAT_HOME` (D11), no `logging.basicConfig` at import (D10),
     `tekton_team_trigger_dir`.~~ **Landed 2026-10-27.** `kubementat_main_dir` reads
     `KUBEMENTAT_HOME` first, `platform_config_dir` reads `KUBEMENTAT_PLATFORM_CONFIG_DIR`,
     importing `kubementat` configures no logging (the CLI calls `basicConfig` in
     `cli.kmt:main`), and `Config.tekton_team_trigger_dir` is the per-team
     `platform_config/{env}/{team}/triggers/` directory `initialize` now scaffolds. It gates
     the CI-container work (FIN-50) only now that it exists. *Landed: the four config-path
     changes above — verified by: `grep -rn KUBEMENTAT_HOME src/` (finds the code, not just a
     comment) and `uv run pytest -q tests/test_config_path_resolution.py` → 15 passed.* No
     lane run is needed to see these: nothing here touches a cluster, and `kmt tekton-list
     dev dev1` still logs at INFO exactly as before.
  3. **FIN-04** — landed: `create_or_replace_role` takes the name it always logged, the duplicate
     `get_role_binding` is gone, and the cluster-role / delete-tolerant / pod-and-node / service-account-token
     primitives exist. None of them has a caller in `src/` yet — each arrives with the ticket that needs it.
     No live-cluster evidence: the development host has no reachable API server, and §8.4 permits the unit
     suite for a Wave 0 ticket.
  4. ~~**FIN-08** — delete the dead code: the empty `utils` module in `src/kubementat` and the
     half-migrated `create_team_account` Python stub under `scripts/automation/tekton`.~~
     **Landed 2026-10-27 — `[DONE]`** (the entry's own status tag is this list's `[DONE]` header).
     Neither file had a caller: nothing imported the zero-byte module, and nothing
     executed the stub — the only route left to a script is `kmt run-script`, which runs `.sh` files.
     The stub was also incapable of running: it called `create_service_account(namespace=, sa_name=)`
     and the library signature takes a third `secrets` argument, so its first call was a `TypeError`.
     The stale bytecode of the deleted module went with it (`**/__pycache__/` is already gitignored);
     no tracked `__pycache__` existed. *Landed: both files deleted, plus
     `tests/test_no_dead_code_ships_in_the_tree.py` (5 cases) — verified by: `uv run pytest -q` → 237
     passed, `uv run kmt --help` byte-identical to before the deletion (19 commands), and
     `grep -rn "kubementat.utils\|create_team_account.py" src tests scripts` → the only hits are the
     gitignored setuptools manifest `src/kubementat.egg-info/SOURCES.txt`.*
     **What this did *not* finish:** the team-account *feature* is still open — see "Now" item 7 and
     FIN-31. No `.sh` was touched by this deletion, and the `.sh` that item 7.2 calls a stale duplicate
     (`scripts/automation/tekton/create_team_account.sh`) is still there for exactly that reason: it is
     FIN-31's to fold, not FIN-08's. Nothing in a real-VM lane changes here — deleting two files no
     caller could reach moves no lane row.
- **House rule to keep:** no ticket ids in test file or case names. The guard exists; do not weaken it.


### 7. `[DONE]` Finish the team-account path
- **What closed it:** the flow moved to Python. `src/kubementat/user_management.py` (296 LOC) implements
  the helper's 14 functions, `kmt users create-team-account` / `create-team-account-read-only` /
  `delete-team-account` are the CLI (FIN-62's group, so `run-script` is not the delivery vehicle), the
  kubeconfig is built as a dict and `yaml.safe_dump`ed, `check_cluster_and_access` is called once, and the
  five bash implementations — including the stale duplicate that had its own copy of
  `check_cluster_and_access` — are deleted.
- **Verified by:** a full lane, **five** times (`021bdaa`, `16c8196`, `badcf40`, and `20261029T223248Z-full-full-4e00cf5`
  all green): `cap:create-team-account`,
  `account-can-i-create`, `cap:create-team-account-read-only`, `account-read-only-account-resolvable`,
  `account-read-only-cannot-mutate`, `cap:delete-team-account`, `account-deleted` and
  `assert:account-token-secret-deleted`. 64 unit tests across 5 files. Nothing in the harness passes
  `--yes` to `run-script` any more, and there is exactly one implementation of the flow.


### 8. `[DECIDED]` Renaming a component namespace does not move the release, and the lane says why
- **Answered 2026-10-29** (last-lap §3.1): kubementat is an opinionated framework; component namespaces are
  proper static defaults owned by the product, and **a live rename is not a supported operation** — moving one
  is a manual, release-scoped operation the operator performs. The harness stays as it is; the three lane rows
  that came from an unsupported rename stay red deliberately and are not a migration ticket. Making the
  defaults dynamic, and the harness work that asserts the refusal, are deferred out of this migration.
- **Why this is on the board:** `helmfile.yaml.gotmpl` now reads every release namespace from
  `env "<KEY>_DEPLOYMENT_NAMESPACE" | default "<the old literal>"`, and `HelmUtils` exports those keys from
  `Config.component_namespace` before helmfile runs. Every *reader* follows the rename. On the lane the
  tunnel resolves `kmt-e2e-renamed`, finds nothing, and grafana is still reachable in `grafana` — and the
  apply that was supposed to move it exits 1:
  `Unable to continue with install: ClusterRole "grafana-clusterrole" in namespace "" exists and cannot be
  imported into the current release: invalid ownership metadata; annotation validation error: key
  "meta.helm.sh/release-namespace" must equal "kmt-e2e-renamed": current value is "grafana"`
  (`20261028T061713Z-full-badcf40`, `stages/05-features--cap:helmfile-apply:standard-after-namespace-rename.stderr.log`,
  error at lines 80/84, the exported namespace table at line 12).
- **What that sentence is actually saying:** helm's ownership metadata is *cluster-scoped*. A release that
  owns cluster-scoped objects (`ClusterRole`, `ClusterRoleBinding`) cannot be re-installed into a second
  namespace while the old objects still carry the old release's namespace annotation. So renaming is not a
  config edit and an apply; something has to remove the old release's cluster-scoped objects first.
- **The decision to get** (the harness is right for whichever answer is picked, so do not re-litigate it in
  code before it is made):
  1. **Renaming is not a live operation.** Then kubementat should say so: the tunnel/assert pair should read
     "the release must be re-installed" rather than "grafana is not reachable", and the lane should not
     re-apply a standard group expecting a move. Delete the rename feature's re-apply and make the assert
     check the *message* instead.
  2. **Renaming is supported, with a procedure.** Then it needs `kmt`-side support: uninstall the release
     whose namespace changed (`helm uninstall` sweeps the cluster-scoped objects with the release), then
     apply. That is a real command or a documented two-step, not an editorial note.
1. Read the two lanes' artifacts before touching code: `16c8196` (apply's verdict discarded, so the failure
   was invisible) and `badcf40` (same state, verdict recorded — that is what surfaced the error above).
2. Decide, and record the decision in CHANGELOG either way, because it changes what "namespaces are
   configuration" promises an operator.
3. If option 2: implement release-scoped uninstall before the re-apply, and re-run the lane expecting
   `cap:helmfile-apply:standard-after-namespace-rename`, `namespace-rename-tunnel-still-correct` and the
   *after-renaming* `tunnel-signature:tunnel.grafana` to move together — they are one fact, three rows.
4. If option 1: rewrite the rename feature so it measures the refusal, not a silent no-op, and expect those
   three rows to be *deliberately* red or removed.
- **Done when:** the three reds are gone or are explicitly named as a design boundary in the lane's own
  report, and the CHANGELOG says which of the two rules kubementat is honouring.
- **Do not:** chase this through the tunnel probes. The probes are correct — they read the config, followed
  the 302, and said what the cluster said.

### 9. `[DEFERRED]` The `REPLACEME_*` keys in the environment config template
- **Owner decided 2026-10-28:** handled later, deliberately. **Do not start on this entry.** It is here so
  the findings do not have to be re-derived: `initialize` replaces `GRAFANA_ADMIN_PASSWORD` at
  `src/kubementat/automation.py:209` (`generate_password()` → `secrets.token_urlsafe(32)`), so that
  placeholder never survives an initialize. `GRAFANA_SERVICE_ACCOUNT_TOKEN` is *not* covered by any
  generation call and ships as `"REPLACEME_GRAFANA_SEVICE_ACCOUNT_TOKEN"` (the typo is in the template),
  while `scripts/user_management/create_grafana_read_only_account.sh:39` reads it and
  `grafana_helpers.sh:8` puts it in `Authorization: Bearer …`.
- `GRAFANA_ADMIN_USER` is a template literal (`grafadmin`), which is exactly what the source-of-truth
  decision says: a per-run password, a fixed user name, both living in `static.encrypted.json`. If the
  config rework makes the user configurable per environment, that is the moment to revisit this entry.

### 10. `[DECIDED]` Linkerd is not part of kubementat — the mesh is removed, and uninstall sweeps what a cluster still carries
- **Owner decided 2026-10-28:** kubementat is not using Linkerd. Every Linkerd step/task is out of the
  migration, and one step is in: remove it. Do not re-add a flag, a component, a chart, a script, a config
  key or a `constants` entry for Linkerd without asking — that is the whole content of this entry.
- What landed with the decision (`python_refactor_2`, this file's own history): `Automation.install_linkerd`
  / `uninstall_linkerd` and the mesh's constants and exceptions deleted from `src/kubementat/automation.py`;
  `--enable-linkerd/--disable-linkerd` gone from `kmt install` with **no alias and no deprecation warning**
  (plan §1.7a); `install_linkerd.sh`, `uninstall_linkerd.sh`, `generate_linkerd_certs.sh` and
  `open_linkerd_dashboard_tunnel.sh` deleted; `templates/environment/kubementat_components/linkerd/` and the
  `prometheus_values_with_linkerd_config.encrypted.yaml.template` deleted (the second carried no value that
  `monitoring/prometheus_values.encrypted.yaml.template` does not already carry — verified by diff, and
  nothing in the product referenced it); the `linkerd` helm repository and its commented chart block out of
  `helmfile.yaml.gotmpl`; `LINKERD_DEPLOYMENT_NAMESPACE`, `LINKERD_HA_ENABLED` and
  `LINKERD_VIZ_DEPLOYMENT_NAMESPACE` out of `static.json.template`; `linkerd` and `linkerd-viz` out of
  `constants.DEFAULT_NAMESPACES`; the `linkerd.io` group out of the Tekton CRD sweep and into its own step;
  the `linkerd` shim out of `tests/shims/`; the harness `KNOWN_BLOCKERS` row that blamed `linkerd (uninstall|viz)`
  gone, because it is no longer a documented gap.
- **What replaced it, and what it is worth:** `kmt uninstall` deletes the `linkerd.io` CustomResourceDefinitions
  first and then the `linkerd` and `linkerd-viz` namespaces, tolerating a mesh that was never there, without
  ever starting the `linkerd` binary. Evidence: `uv run pytest -q` → **432 passed**, and
  `grep -ril linkerd src tests scripts templates tekton_ci examples | grep -v __pycache__` hits only the
  removal step's own prose. **A lane owes** the cluster half: a cluster that really had the mesh must come off
  clean, and a mesh-free lane must stay green (the CRD-before-namespace order that keeps a namespace from
  sticking in `Terminating` is asserted against fake `kubectl` and cannot be proved here).
- **Deliberately left alone:** `docker_files/ci_images/ubuntu_ci_minimal/Dockerfile` still installs the
  `linkerd` CLI and its `test.sh` asserts it. That file belongs to the image workstream and is not this
  queue's to edit, so the change is *owed*, not made. It is dead weight in the CI image, not a product path.

### 11. `[DONE]` nginx-ingress came out, Traefik went in, and there is a command that installs it and one that asks first
- **Two owner decisions, and the second is what closes this entry.** 2026-10-28: nginx-ingress leaves the
  product, and "does Traefik (or anything else) replace it?" is deliberately left **open** — no chart, no
  key, no component entry, no flag, no placeholder. 2026-10-29 (`docs/plans/finish-python-migration-last-lap.md`
  §3.3, answering `docs/plans/traefik-integration.md` §9): **Traefik does**, it ships as the cluster's default
  `IngressClass`, the chart is pinned, its image joins the mirror list, and `examples/` stops speaking nginx.
  The "do not pre-add a successor" instruction that stood here is spent — what arrived is not a placeholder.
- **Landed and committed:** `2347583` ("Routing: Traefik comes back as the platform's Ingress controller")
  and `643ed6e` ("Traefik ships as the default class, on a pinned and mirrored chart"). The surface is `kmt
  routing install <environment> [--yes] [--update]`, which applies `group=routing` through a guard that reads
  the cluster first (`src/kubementat/automations/routing/{routing.py,traefik_presence.py}`), and `kmt routing
  status`, which prints what is there and whose it is. `scripts/automation/components/install_routing.sh`
  (85 LOC) is deleted. New: `templates/environment/kubementat_components/traefik/values.encrypted.yaml.template`
  and the `traefik` repository + release block with `version: "42.1.3"` (`helmfile.yaml.gotmpl:73-74,196-207`).
  Changed: `constants.py` gains the `traefik` namespace/selector/chart/controller entries,
  `templates/environment/static.json.template` gains `TRAEFIK_DEPLOYMENT_NAMESPACE`, `TRAEFIK_INGRESS_CLASS`
  and `TRAEFIK_SERVICE_SELECTOR`, the `ClusterIssuer` HTTP-01 solvers read the class through `Config` instead
  of the literal `nginx`, uninstall sweeps the chart's CRDs (`traefik.io` and `hub.traefik.io`, 25
  definitions, gated on no Traefik proxy being left), and `kmt install` still installs no controller —
  `install_kubementat` applies `group=standard`, and the absence is asserted in
  `tests/test_cli_surface.py:283-286`. `uv run pytest -q` → **704 passed, 1 skipped**.
- **Landed, with lane evidence.** `~/.kmt-e2e/runs/20261028T205357Z-routing-full-47b186f/result.json` (run
  2026-10-28 over the tree that became these two commits): 94 assertions recorded, 4 failing, **none of them
  routing** — they are `assert:no-extra-cluster-scoped:customresourcedefinitions`, `:clusterroles`,
  `:clusterrolebindings` and `cap:reinstall-after-uninstall`, which are red on the `full` lane at the same
  SHA too and are filed separately (`last-lap` §5). The 24 assertions that name routing are green: k3s's
  bundled controller disabled via `/etc/rancher/k3s/config.yaml` and gone before the first command, `kmt
  install` leaving no controller behind, Traefik `deployed` with exactly one class bound to
  `traefik.io/ingress-controller`, 80→443 answering 308, TLS terminating from a cert-manager-issued
  certificate, the solver bound to a class something serves, a second `routing install` being a skip that
  changes nothing, `--update` applying the definitions before the release, and uninstall leaving no namespace
  and no Traefik-shaped CRD. The routing lane is the gate for this area and only ever started by a human
  (`uv run python tests/e2e/suite.py --lane routing --scope full`) — it is not a CI job (owner decision,
  2026-10-28, `last-lap` §4).
- **Landed, and proven by no run. These are not verified claims:** (1) `ingressClass.isDefaultClass: true`
  postdates that artifact — the template said `false` on 2026-10-28 and the lane's TLS fixture names the
  class deliberately (`tests/e2e/harness/stages.py:2270-2277`), so nothing reads "an Ingress that names
  nothing is routed" yet. (2) The pin resolves in a lane the same way it did unpinned (that run pulled
  42.1.3 as *newest*), so the pin's guarantee is only argued, and `traefik:v3.7.16` in the mirror list has
  never been exercised. (3) HTTP-01 against real DNS/ACME is out of reach for the VM lane by construction:
  the run proves cert-manager is *connected* to Traefik with a lane-local self-signed Issuer and a `.invalid`
  host (`last-lap` §4's routing row). (4) INGRESS-REMOVE's own cluster half — a cluster that really had
  nginx-ingress coming off clean — is still owed and this lane does not pay it. (5) Nothing here has been
  run by hand against a real cluster; all of it is VM-lane evidence. (6) **Closed 2026-10-29 — the red this
  predicted never arrived, and the prediction was not a measurement.** The everyday `full` lane **has** run
  since the routing group got a Python applier (`20261029T223248Z-full-full-4e00cf5`), and `cap:install`,
  `cap:routing.install`, `cap:routing.install:routing` and `assert:helm-releases-present` are all green —
  routing's rerun is a clean `assert:idempotent:routing.install` with `delta=none`. The reason is that
  `_expect_release_set` reads the expected set from the groups that were actually *applied*, and `kmt install`
  applies `group=standard`; traefik's group is applied by `kmt routing install`, which asserts for itself. So
  the *declared and applicable* rule (`traefik-integration.md` §7.2b) is still unwritten and §7.2's four
  full-lane guard asserts (`routing-status-foreign`, `routing-install-skipped-existing-traefik`,
  `routing-left-k3s-traefik-alone`, `routing-did-not-upgrade-k3s-traefik`) still do not exist — measured, 0
  hits in `tests/e2e/harness/stages.py` — so **"k3s's Traefik is left alone" is still a fact only the routing
  lane asserts**. That is a missing assert, not a broken lane, and it is the part of this bullet that survives
  the closure.
- **History, from the 2026-10-28 decision (`python_refactor_2` @ `3ec7caa`) — true as written then, and the
  two sentences that read as open questions are closed by the bullets above.** The
  `ingress-nginx` helm repository and the `nginx-ingress` release went out of `helmfile.yaml.gotmpl`;
  `templates/environment/kubementat_components/routing/nginx_ingress_controller.encrypted.yaml.template`
  was deleted (66 LOC, and `cert-manager`'s template is not affected — cert-manager is what let's encrypt
  needs, not what nginx needed); `NGINX_INGRESS_DEPLOYMENT_NAMESPACE` went out of `static.json.template`;
  `nginx-ingress` went out of `constants.DEFAULT_NAMESPACES`, so the component set had no entry for a chart
  that no longer exists. What has since moved: `install_routing.sh` is **deleted** — the group it applied is
  applied by `kmt routing install` now — and the two let's-encrypt `ClusterIssuer`s that were "untouched,
  solver `http01.ingress.class: nginx`, class undecided" now solve through
  `http01.ingress.ingressClassName`, taken from `TRAEFIK_INGRESS_CLASS` (`routing.py:280` →
  `routing.py:496`). Both issuer names survive, because `create_ingress_yml.sh` and both EventListener
  templates reference them by string. That is the whole of FIN-17's routing half, and it is landed rather
  than withdrawn.
- **What replaced it, and what it is worth:** `kmt uninstall` grew `uninstall_nginx_ingress()`, which deletes
  the IngressClasses bound to `k8s.io/ingress-nginx` (cluster-scoped, so no namespace delete takes them), then
  the release the cluster reports living in `nginx-ingress`, then the namespace — tolerating a controller that
  was never there, and without starting anything the product stopped shipping. Evidence: `uv run pytest -q` →
  **433 passed** (one parametrised key case fewer, two controller cases in), and
  `grep -rn "nginx-ingress\|NGINX_INGRESS" src templates | grep -v __pycache__` hits only the removal step's
  own prose and its constants. **A lane owes** the cluster half: a cluster that really had the controller must
  come off with no `nginx-ingress` namespace, no release and no bound `IngressClass`, and a cluster that never
  had one must stay green — including the branch where the cluster cannot answer `kubectl get ingressclass`\.
- **Deliberately left alone, and reported rather than fixed:**
  `scripts/utilities/kubernetes/create_ingress_yml.sh` used to be in this list for stamping
  `kubernetes.io/ingress.class: nginx` and `nginx.ingress.kubernetes.io/*` annotations; it does not any
  more. It writes `spec.ingressClassName` read from `TRAEFIK_INGRESS_CLASS` (`:62`) and warns to stderr when
  no configuration was readable. The same went for both Tekton EventListener ingress templates and for
  `examples/`, which now speak Traefik. Two documents are still wrong and were not this ticket's to write,
  so they are reported and not edited: **`README.md:225-228`** ("installs cert-manager … and no ingress
  controller", with the deleted `install_routing.sh` named as the thing that still applies the group) and
  **`docs/README_TEKTON.md:355-373`** (the same claim, "what would replace them is still an open decision",
  above an example whose commented annotations are nginx's). Both are FIN-61/FIN-62's, the docs and
  CLI-surface row (`last-lap` §2 row 6), and a reader who finds them after this entry should read the
  entry, not them.
  ~~`templates/environment/kubementat_components/gitlab/values.encrypted.yaml.template` keeps
  `nginx-ingress: {enabled: false}` (`:106`), which is the GitLab chart's own bundled controller and is
  already off~~ — **that file is deleted (`8678526`), so the half of this sentence about GitLab's own
  bundled controller no longer has a subject.** What remains is still true: grafana's dashboard list
  keeps its `nginx-ingress` dashboard (`monitoring/grafana_values.encrypted.yaml.template:100-101`,
  gnetId 9614), which will simply have no data. Recorded as deleted and not silently removed, because
  this entry's whole point is what an nginx-ingress retirement touches, and one of the places it touched
  is a values file that is now itself gone.
  `examples/tekton_ci/**nginx-example*` and `templates/environment/team/nginx-example/**` are the demo app
  and were never the controller; the chart keeps its name after the 2026-10-29 retarget because that string
  is read from `src/cli/commands/tekton.py`, `templates/environment/team/nginx-example/`,
  `tekton_ci/tasks/run-tekton-pipeline.yml`, `examples/helm_charts/nginx-example/**` and the generators'
  fixtures — **not from `scripts/` any more**. Both names this sentence used to point at are deleted, one
  by FIN-43 (`fb82f7c`) and one by FIN-51b (`85f1ea8`), and `grep -rn nginx-example scripts/` answers
  nothing at all, so a chart-name decision that had two bash readers here in October has only Python and
  manifest readers now. `tests/e2e/harness/**`, which the follow-up commit's message names, does not
  actually spell it.
  `templates/environment/team/nginx-reverse-proxy/` is the deleted backing
  service and is **referenced by nothing that runs**: no script, no `src/`, no manifest, no test names it. The
  only things that still say its name are its own template file and the untracked
  `platform_config/dev/smoke/static.json`'s three `NGINX_REVERSE_PROXY_*` keys, which belong to another
  workstream's config tree. It is not the controller and it is not this change to delete.

### 12. `[DONE]` Removing one helm component was not a thing kubementat could do
- **Why:** `scripts/automation/components/uninstall_helm_deployment.sh` had **no caller in `src/` at all**.
  Its only mentions were documentation, so FIN-19 was never a port: kubementat could install a component,
  and `kmt uninstall` could tear the whole platform down, but there was no command that removed exactly one
  release. The second half of the ticket is the FR-61 fact `docs/plans/testsuite-requirements.md:289`
  records as a failure: lines 57-58 of that script *printed advice* —
  `kubectl -n <ns> delete pvc <PVC_NAME>` — and deleted nothing, so a claim outlived the release that made
  it, and the next install of the same component could mount the last run's data.
- **Landed:** `HelmUtils.uninstall_release(component)` and `kmt component uninstall <env> <component>
  --team <team>` — the command was **`helm-component-uninstall`** when this landed, and `d5f321b` renamed
  it onto the `kmt component` group outright (no alias, §1.7a). The implementation behind the name is the
  one described below and was not touched by the rename. The namespace comes from `Config.component_namespace(component)` — the same key install
  deploys into and `uninstall_kubementat` sweeps — and the release is addressed by **the name the cluster
  reports** (`helm list --all-namespaces --all`, the way `uninstall_nginx_ingress` does it, not a name built
  from a chart default that a release older than this repository's naming would not match). Then its claims
  go, read by `app.kubernetes.io/instance=<release>` and deleted by name, mirroring `_delete_claims_in` in
  the platform uninstall rather than inventing a second mechanism. Two absences are ordinary and log
  nothing alarming (release not present, release that made no claims); only a real failure raises, and it
  raises with the failed release's claims left intact.
- **The FR-61 advice-only failure is closed in code, and the lane still owes its half.** Verified by:
  `uv run pytest -q` → **452 passed** (~18 s, 439 before), of which
  `tests/test_a_helm_component_is_uninstalled_from_the_namespace_the_config_names.py` → 13, including the
  case that renames `POLARIS_DEPLOYMENT_NAMESPACE` and asserts the release, the claims and the namespace they
  are addressed in all move together; and the comparison against `git
  show b2d471a:scripts/automation/components/uninstall_helm_deployment.sh`, where the script's claim half is
  an `echo` line that deletes nothing. `uv run kmt --help` → **22** commands, one new, no alias. The command
  is exercised as its own process, so the surface and the `--team` plumbing are the real ones.
  `grep -rn uninstall_helm_deployment src tests docs README.md` hits only the blob citation and the rows that
  record the deletion.
- **What a lane owes:** (1) a real release going when the cluster named it — `--all`, a release renamed at
  install time, and a namespace still draining are proven against fake `helm`/`kubectl` only; (2) the claim
  sweep really going, which is where a retained PV behind a claim is the quiet failure; (3) a
  `no-extra-namespaces` + PVC assert after a **component** uninstall that is not a platform uninstall, a
  state the harness has never built; (4) the decision in item 8 — if the answer to "rename comes with a
  release-scoped uninstall" is yes, this command is the half that gets used, and that decision has to say
  whether it may delete claims.

### 13. `[OPEN]` `kmt uninstall` leaves behind the cluster-scoped objects it installed
- **Why:** the `full` lane at `4e00cf5` fails exactly three teardown asserts, and they are not unrelated
  infrastructure — they are what `kmt install` put on the cluster. cert-manager's CRDs
  (`cert-manager.io`, `acme.cert-manager.io`) come back because a chart's `crds/` directory is applied on
  install and removed by nobody on uninstall; that is the identical shape Traefik had, which is why
  `TRAEFIK_CRD_GROUPS` exists and `CRD_GROUPS_KUBEMENTAT_OWNS` (`automation.py:1611`) is still only
  `("tekton.dev",)`. The ClusterRoles and ClusterRoleBindings come from the Tekton / Triggers / Dashboard
  release YAMLs `TektonUtils.install()` applies, and deleting a namespace takes no cluster-scoped object with
  it. `assert:no-tekton-crds` and `:no-helm-deployer-clusterrole` pass, so the mechanism exists and is simply
  not applied to these two families.
- **The mechanism is the decision, and guessing names is the wrong one:** sweep by the labels the install path
  stamps, or by the objects the same manifests declare, **not** a literal name list — a name list is how an
  uninstall deletes something the operator owns. Mirror `_delete_crds_in_groups`: gate it on the release that
  installed them being gone, tolerate their absence, and never touch a foreign owner.
1. Read the residue out of `~/.kmt-e2e/runs/20261029T223248Z-full-full-4e00cf5/result.json` (19 ClusterRoles,
   18 ClusterRoleBindings, 6 cert-manager CRDs) rather than from a name list you write down.
2. Add `CERT_MANAGER_CRD_GROUPS` beside `TRAEFIK_CRD_GROUPS`, gated the same way (`helm list` says no
   cert-manager release left), with the absent-case tolerance the Traefik sweep has.
3. Decide and implement the cluster-scoped RBAC sweep for the Tekton family. State which question it answers
   ("did kubementat install this?") in the module, because the answer is what makes it safe.
4. Full lane: the three asserts go green, `assert:no-tekton-crds` and `assert:no-helm-deployer-clusterrole`
   stay green, and reinstall still works.
- **Done when:** a `full` lane's teardown answers **zero** residue for CRDs and cluster-scoped RBAC, and a unit
  test proves a foreign ClusterRole that kubementat did not install survives the sweep.
- **Note:** the last version of `docs/plans/finish-python-migration-last-lap.md` filed these three reds as
  "not this migration's". That was wrong, and the wrongness is the entry: it put a real defect out of reach of
  anyone reading the plan. `cap:reinstall-after-uninstall` now passes, so the set is three, not four.

---

### 14. `[OPEN]` OpenBao installs, reports `deployed`, and never becomes ready
- **Found:** 2026-10-31, by the assert added for this purpose (`assert:workloads-running:extra`). It
  is not new. `group=extra` was in no lane, so nothing installed the secret-management release --
  `vault` before the rename, `openbao` after -- and every claim about it was made from a chart render
  on a host, where nothing can fail to start.
- **Evidence, from a full lane (`20261031T103624Z-full-full-f259983`):** `helm-releases-present` says
  openbao is `deployed`, the pod is `Running`, and `kubectl logs openbao-0` repeats
  `core: security barrier not initialized` / `core: seal configuration missing, not initialized`
  every five seconds after `OpenBao server started!` (v2.7.2, pebbledb, listener 8200 `tls: disabled`).
  Standalone mode starts uninitialized, the readiness probe is `bao status`, and `bao status` fails
  until `operator init` has run and the barrier has been unsealed. kubementat does neither.
- **What is true meanwhile:** the install path works (`helmfile apply -l group=extra` succeeds, the
  alias resolves, the injector deploys), `kmt uninstall` sweeps the `openbao` namespace, and stage 04
  applies the group twice idempotently (`exit=0`, resource-set delta none). So packaging is sound and
  the gap is purely lifecycle.
- **The decision, which is a product decision and not a bug fix:** either kubementat owns init and
  unseal, or `extra` is documented as not-installed-by-this-tool. What the first option commits to is
  key custody -- where the unseal keys live, what happens when they are lost, what re-initializing a
  storage backend that already holds secrets means. If nothing in this platform consumes OpenBao yet
  (the release sits in a group nothing installs, which is how this hid for so long), then the honest
  answer is the second option and the lane stays quiet until someone needs it.
  - Deliberately not chosen: `server.dev.enabled: true`. It would make the assert green in one line,
    by turning the only secret-management component in this product into a dev server with in-memory
    storage and its root key in the container log, and the lane would then be testing the workaround.
- **Re-enabling the lane is a one-word change, by design.** `install_groups` for `full` has
  `"extra"` in a comment beside it, `WORKLOAD_ASSERTED_GROUPS` still names `extra` so the assert is
  loaded and dormant, and its five unit tests are shape-pinned so they run whether or not the lane
  does. Do not delete the machinery while the component is unresolved -- an unasserted group is a
  group nobody re-enables.
- **Done when:** a full lane installs `extra` and `assert:workloads-running:extra` goes green on its
  own, or documentation an operator reads before installing says plainly that `extra` is not
  installed and initialized by kubementat.

---

## Prioritized TODOs

- Main Goal 1: finish python rewrite
  - **Structural (landed 2026-10-27):** FIN-01 `lib/`+`cli/` → `src/` with no `sys.path` hacks
    (`eb37c34`); FIN-02 one command-execution primitive, no `shell=True`, no `os.chdir`, script-execution
    helpers deduplicated (`4f78754`). Both are verified by the unit suite and by the full lane, which
    gained zero new failures. **FIN-04** (`KubernetesUtils`: D4's `NameError`, D5's shadowed
    `get_role_binding`, and the cluster-role / 404-tolerant-delete / pod-and-node / service-account-token
    primitives — `05214d4`) landed with unit-suite evidence only: nothing in `src/` calls the new helpers
    yet, so a lane run cannot see them, and this dev host reaches no API server. That is permitted for a
    Wave 0 ticket (§8.4), but it means FIN-11/14/36 get the first real proof when they wire them up.
    Remaining structural work: **the FIN-08 tail only** — `src/cli/kmt` is still tracked (53 LOC, the
    pre-FIN-01 CLI entry file, imported by nothing: `[project.scripts]` resolves `cli.kmt:main` to
    `src/cli/kmt.py`). **Measured 2026-10-29: this half is already done — `git ls-files src/cli/kmt`
    is empty and `git log --oneline -1 -- src/cli/kmt` is `3ec7caa` ("the pre-FIN-01 CLI entry file
    goes"), so nothing is tracked by that name; only `src/cli/kmt.py` is. Do not re-open it.**
    **FIN-03** landed alongside FIN-05 and is what makes the
    36 "not migrated" rows verifiable before deletion: every script on FIN-60's delete-immediately
    row has a test that runs the bash and compares what the two paths asked the cluster to do.
    Nothing is deleted by it — the deletions are FIN-60's, and FIN-61/62 still close Wave 6.
    **FIN-60 executed it 2026-10-28:** 8 scripts / 391 LOC deleted, 4 of the row's scripts held back
    because their own ledger conditions are unmet (plan §7 names each one and the ticket it waits on).
    *Landed: the deletions, the README sentence that named `list_tekton_resources.sh`, and the two
    guards that fail when `src/` names a script the ledger already deleted — verified by:
    `uv run pytest -q` → 249 passed, and `find scripts -name '*.sh' | wc -l` → 44.*
  - remove yq usage
    - **DONE 2026-10-29 (`d82f901`, "rows 8, 9 and 10 in one pass").** `yq` is out of
      `KUBEMENTAT_DEPENDENCIES` and `grep -rl '\byq\b' scripts | wc -l` → **0**. The last caller went with
      `run_pipeline.sh`. The 2/4 numbers this bullet used to carry are older than FIN-60's deletions; the
      command above is the answer now.
  - remove jq usage
    - **OPEN — one line, and it is last-lap row 21.** `grep -rl '\bjq\b' scripts | wc -l` → **1**
      (`scripts/utilities/kubernetes/retrieve_node_addresses.sh`, a 2-line `kubectl get nodes -o=json | jq`), and
      that file is an orphan: no `src/`, no `tests/e2e`, no manifest execs it, and its Python counterpart
      (`KubernetesUtils.get_nodes()`) already exists. So this is not "wait for Waves 1–4" any more — the waves
      are done and 2 scripts remain. Delete both wrappers with a stays-deleted guard, then drop `jq` from
      `KUBEMENTAT_DEPENDENCIES`. The 18/27/35 numbers this bullet carried were never what the command answers.
  - docker image
    - **[DONE on its own half; the pod-evidence half is open]** `docker_files/ci_images/ubuntu_ci_minimal` is
      migrated: python3, node and `uv` are installed (`uv` at `:131`, `f9a86f4`), and the Tasks that used to
      source a script inside the pod run `uv run --locked --no-dev kmt …` instead. Two things still owed and
      neither is code: **start a pod once** to turn "kmt is importable in a step" from a unit-test hypothesis
      into an observation, and take the `linkerd` CLI out of the image (item 10's owed edit — the image installs a
      binary the product no longer has a path for, and the image belongs to that workstream, not to the
      migration queue). It also still installs `yq`, which nothing in this tree calls any more.
  - **What is left to implement, in order.** A fresh agent should take these top-down. Effort numbers are
    the plan's own; dependencies are plan §6; one writer per worktree applies, and every ticket below owes
    a smoke-lane run that nobody has produced yet.
    1. ~~**FIN-50 + FIN-51** (Wave 5, 14 h)~~ — **the two deletions this row was holding are closed**:
       `run_pipeline.sh` (the last FIN-60 script whose parity test still ran live bash) went with FIN-51b
       (`85f1ea8`) and `scripts/tasks/**` went with FIN-51c (`0cf22b7`). The row is not open as a whole —
       the CI manifests calling `kmt` did land — but **the lane evidence it owes has not**: no pod has been
       started to prove `kmt` resolves inside a step, and that is a human with a lane, not a ticket.
    2. **FIN-30 + FIN-31 + FIN-32 + FIN-33** — **LANDED, lane-green 2026-10-29.** Team accounts in Python:
       `src/kubementat/user_management.py`, the `kmt users …` group, the kubeconfig built as a dict and
       `yaml.safe_dump`ed, five bash implementations deleted. The line this bullet carried — "the five account
       asserts in item 7 do not execute at all today" — is **false now**: `20261029T223248Z-full-full-4e00cf5`
       runs all eight of them and they are green (`cap:create-team-account`,
       `cap:create-team-account-read-only`, `cap:delete-team-account`, `account-can-i-create`,
       `account-read-only-account-resolvable`, `account-read-only-cannot-mutate`, `account-deleted`,
       `assert:account-token-secret-deleted`). FIN-04's primitives have callers now. Nothing in this queue is
       open here.
    3. **FIN-12 + FIN-13 landed — FIN-14 withdrawn** (Wave 1; FIN-11 left this list, `213bc9b`) — `kmt uninstall`
       is Python and the bash is deleted. **FR-61 is not "only the lane can confirm it" any more — the lane
       confirmed most of it and found what it does not cover**: `assert:no-extra-namespaces`,
       `assert:no-kubementat-claims`, `assert:no-kubementat-releases`, `assert:no-tekton-crds`,
       `assert:no-claims-left-pending` and `assert:no-helm-deployer-clusterrole` are green at `4e00cf5`, while
       the three cluster-scoped residue asserts stay red. That residue **is** FR-61's remaining half and it is
       item **13** above / last-lap row **18**, not a lane debt.
       **FIN-12 — `TektonUtils.install()`, `implemented-unverified`.** `kmt install` installs Tekton in
       Python now: four `kubectl apply` calls built from `TEKTON_RELEASE_BASE_URL` and the
       `TEKTON_VERSION_*` pins, addressed at `Config.component_namespace("tekton")`, plus
       `helm-deployer-cluster-role` written through `KubernetesUtils.create_or_replace_cluster_role`.
       The script is deleted, no `src/` string names it, and the two informational `kubectl` reads it
       ended with are gone (they printed, they decided nothing —
       `preflight_installed_tekton_api_versions` is what asserts the install worked).
       Landed: the install, the script deletion, the README sentences that told a human to run it, and
       the same commit's fix for **D13**, which FIN-13 was blocked on —
       `apply_resources` takes `.yaml`, walks subdirectories, skips empty documents, raises
       `ResourceWithoutNameError` instead of a `KeyError`, and refuses a missing directory.
       verified by: `uv run pytest -q` → **417 passed** (~18 s), of which
       `tests/test_parity_tekton_installs_the_pinned_releases.py` (7, compares the Python path against a
       record of the deleted script) and
       `tests/test_the_tekton_install_reads_its_configuration_first.py` (7: happy path, the 409 upgrade
       branch, a refusal that is not 409, missing pin, refused manifest) and
       `tests/test_applying_a_directory_applies_what_is_actually_in_it.py` (15, including a check of the
       real `tekton_ci/` roots) — plus `uv run kmt --help` → 20 commands, no duplicates, none of them new.
       **Landed 2026-10-29 (`4e00cf5`) — and so is the lane half this row was holding.** The four pinned URLs
       really do land Pipelines 1.17.1 / Triggers 0.37.2 / Dashboard 0.73.0 on a clean cluster: the install
       stage's own output carries `v1.17.1` (3 hits), `v0.37.2` (6) and `0.73.0` (6), `cap:install` is green, and
       the client-written `helm-deployer-cluster-role` was accepted where the heredoc was —
       `assert:no-helm-deployer-clusterrole` proves it by asking for it back at uninstall. None of it is a doubt
       about the code any more; it was the half of §8.4 no unit suite could pay, and a lane paid it
       (`20261029T223248Z-full-full-4e00cf5`, `stages/02-install--cap:install.stdout.log`).
       The next item after that was FIN-13, and **FIN-13 has since landed** — see 3b below.
    3b. **FIN-13 — `TektonUtils.setup_triggers()`, and decision 2.** **Landed 2026-10-28 (`e354bcc`)**, as
       `implemented-unverified`.
       Python now: seven RBAC objects through the `KubernetesUtils` create-or-replace primitives (1
       ClusterRole, 1 ClusterRoleBinding named per team, 2 Roles, 2 RoleBindings, 2 ServiceAccounts), the
       two webhook Secrets written from the team's `static.encrypted.json` **only for the provider that has
       a token**, and the team's generated manifests applied through `Automation.apply_resources` from
       `Config.tekton_team_trigger_dir` — no `kubectl` anywhere in the flow.
       The script is deleted (269 LOC, 35 → 34 files under `scripts/`), no `src/` string names it, the
       `tekton-setup-triggers` command calls Python, `initialize` stopped creating
       `tekton_ci/triggers/{TEAM}/`, `generate_trigger_config_from_templates.sh` writes into
       `platform_config/{env}/{team}/triggers/{APP}/` so generation and reading agree in one commit, and
       `.gitignore` no longer assumes the old location (`tekton_ci/triggers/**` ignored, `templates/` not).
       Three deliberate differences, each pinned: `jq -r` answered `null` for an absent webhook key and
       that passed the script's `!= ""` guard, so bash wrote a Secret holding `base64("null")` — an
       EventListener validating against it rejects every webhook quietly; a missing `PIPELINE_NAMESPACE`
       now raises `PipelineNamespaceNotConfiguredError` **before** the first write instead of sending eight
       objects to a namespace called `null`; and the install call site's `retry_on_failure=True` is gone,
       because every operation is create-or-replace so a repeat run is the retry.
       One thing the port needed: a team's `triggers/` directory is an EventListener, a TriggerBinding, a
       TriggerTemplate **and** an Ingress, so `apply_resources` takes its `kind`/`group`/`version` from each
       document and keeps the caller's as the fallback, and `plural_for_kind` replaced
       `f"{kind.lower()}s"` (which addressed an Ingress at `ingresss`). Core-group documents
       (`apiVersion: v1`) are refused with the alternative named: the custom-objects client cannot address
       `/api/v1/`.
       verified by: `uv run pytest -q` → **433 passed** (~18 s), of which
       `tests/test_the_trigger_setup_asks_the_cluster_for_the_same_rbac.py` (4: the eight RBAC heredocs,
       reduced through the same reducer the live comparisons use; both webhook Secrets' shape; the
       one-provider case; idempotence) and
       `tests/test_the_trigger_setup_writes_rbac_the_way_the_manifest_did.py` (11: order, namespace read
       from config, the 409 replace branch for two kinds, missing namespace, configured/unconfigured
       Secret, both label shapes, absent trigger directory, reading `platform_config` and not the
       checkout, per-document API groups, an unnameable document, and that the command execs nothing).
       `uv run kmt --help` → **20 commands**, unchanged and none new; `find scripts -name '*.sh' | wc -l`
       → **34**.
       **Landed 2026-10-28 (`e354bcc`); the lane paid the half that mattered 2026-10-29**
       (`20261029T223248Z-full-full-4e00cf5`): a real API server accepts all seven objects exactly as written —
       `cap:tekton.setup-triggers` is green twice with `assert:idempotent:tekton.setup-triggers` clean, so the
       `policy`/`podsecuritypolicies` and `extensions`/`ingresses` rules the script carried did not break the
       apply on k3s 1.36, and the generated manifests apply end to end (`cap:tekton.setup-pipelines` green,
       `assert:pipelinerun-succeeded` green). Two edges still unread, and neither is a doubt about the code: a
       real EventListener pod reading a `ClusterTriggerBinding` through the cluster-scoped binding, and a
       generated Ingress applied through the custom-objects path coming out identical to `kubectl apply -f`.
       **Still owed by whoever closes item 13:** the two cluster-scoped objects are not taken away by deleting a
       namespace, and `uninstall_pipelines` does not delete them today — that is the same hole item 13 names.
    4. **FIN-17…FIN-25** — **LANDED.** Routing (now `kmt routing install`, item 11), the cleanup CronJob in
       Python (`kmt tekton-setup-cleanup-job`, lane-green at `4e00cf5`), `HelmUtils.uninstall_release` +
       `kmt component uninstall`, and the three generators (`kmt tekton-generate-deploy-pipeline`,
       `-triggers`, `-pipeline-run`). The namespace-literal sentence below this list is obsolete: component
       namespaces come from `Config.component_namespace` and
       `tests/test_component_namespaces_come_from_configuration.py` says so.
    5. **FIN-23** — **LANDED.** `kmt tekton-generate-triggers` is the generator; the four `sed` passes and
       `generate_trigger_config_from_templates.sh` are gone, generation and reading both use
       `platform_config/{env}/{team}/triggers/`, and the byte-identity claim is pinned by
       `tests/test_generated_deploy_pipelines_and_trigger_configs_are_written_where_they_are_read.py`.
       `tekton_ci/triggers/` holds `templates/` only.
    6. **FIN-40…FIN-46** — **LANDED, including the last one.** All 14 `scripts/utilities/**` went, and
       `kmt run-script` is deleted (`e6b3afd`) — defect D7 is dead, `kmt --help` prints 36 commands and none of
       them takes a script path. What is left of the family is 2 orphan wrappers and that is row **21** above,
       not Wave 4.
    7. **FIN-09 / D12** — **PARTLY LANDED 2026-10-29.** `yq` is out of `KUBEMENTAT_DEPENDENCIES` and 0 scripts
       call it. `jq` stays for one 2-line orphan (`retrieve_node_addresses.sh`) — see "remove jq usage" above
       and row **21** in last-lap §2.1. One commit closes it.
    8. **FIN-61, FIN-62, FIN-63** — **OPEN, and it is row 22.** Docs and READMEs, the Click groups
       (`kmt tekton …`, `kmt tunnel …`, `kmt helm …`; `kmt users`, `kmt component` and `kmt routing` already
       exist), CHANGELOG. **Renames, no aliases**, so they land with the commands. **No version bump** — the
       owner bumps manually at the end, and no test asserts the version. The `fin62="…"` markers are the
       authoritative count: **11** measured today, and the lane prints the same 11 as
       `needs_grouped_cli_form`.
    9. **Housekeeping** — `src/cli/kmt` deleted (`3ec7caa`). **FIN-10 is OPEN and is row 23**: no `.github/`,
       no `.tekton/`, no `tekton_ci/` pipeline mentions pytest, so there is no CI that could run the suite. The
       owner deferred it, not decided against it — §2.1 puts it fifth because a CI running a surface that is
       still being renamed (row 22) is how the first green run gets blamed for the rename. The dashboard
       decision is parked by last-lap §3.2: after this migration.
  - **Blocked / out of scope for this migration** — moving `platform_config` into its own repository
    (decision 4: do not design for it yet), Flux/ArgoCD, and the backing-services rework (item 3 under "Now"
    — product work wearing a migration's clothes).
    **Cluster-context and multi-cluster support came out of this list on 2026-11-02**: it was never part of
    the migration, and v2 did it (`eca4c31`) — see the Features entry below and
    [`docs/specifications/cluster-config-v2.md`](docs/specifications/cluster-config-v2.md). What this line is
    about is that the migration was never the thing that would make it arrive.

- Refactoring
  - [DONE] introduce an option for setting the platform_config directory
    - Landed: `KUBEMENTAT_PLATFORM_CONFIG_DIR`, read in `Config.__init__` (explicit
      argument beats it, it beats the `kubementat_main_dir/platform_config` default) —
      verified by: `uv run pytest -q tests/test_config_path_resolution.py`
      (15 passed). One mechanism for both asks: `KUBEMENTAT_HOME` moves the whole
      checkout-relative path set, this variable moves the config root inside it.
  - [DONE] Update tekton to the latest version and migrate v1beta1 -> v1
    - Landed: the whole `tekton.dev` surface is v1 **on the current upstream triplet Pipelines
      1.17.1 / Triggers 0.37.2 / Dashboard 0.73.0**. `Config.TEKTON_API_VERSION = "v1"` with no env
      override, every manifest in `tekton_ci/` (38 occurrences) and `examples/tekton_ci/` (20) is
      `tekton.dev/v1`, the v1 field moves are in (`taskRunTemplate.serviceAccountName`,
      `timeouts.pipeline`, `computeResources`), the `PipelineResource`/`resources.inputs` example was
      rewritten onto workspaces, `scripts/automation/tekton/generate_pipeline_run.sh` stamps v1, and
      `setup_pipelines`/`TektonUtils` ask the API server once before applying anything
      (`TektonControllerWithoutConfiguredApiError`). The throwaway transformer lived in
      `tools/tekton_v1_migrate.py` and is deleted, per the plan. The three pins and the release URL
      moved together: the installer builds all four manifest URLs from one
      `TEKTON_RELEASE_BASE_URL=https://infra.tekton.dev/tekton-releases` (the old bucket still serves
      `v1.4.0` and 404s anything from 1.15 on — that string is a Python constant since FIN-12 deleted the
      script that held it), it applies Triggers' `interceptors.yaml` beside the
      release, and `kmt install` no longer trusts `kubectl apply` —
      `preflight_installed_tekton_api_versions` asserts the cluster really serves `tekton.dev/v1` and
      `triggers.tekton.dev/v1beta1`. Triggers stays on `triggers.tekton.dev/v1beta1`, and that is not
      an oversight: 0.37.2's own release.yaml declares `v1beta1` served and stored on every trigger
      CRD and never `v1`.
      — verified by: `grep -rn "storage.googleapis.com/tekton-releases" scripts src templates` (empty),
      `curl -s -o /dev/null -w "%{http_code}\n"` on the four URLs the new pins build
      (`302` each, `200` once followed: pipeline release.yaml, dashboard release-full.yaml, triggers
      release.yaml + interceptors.yaml), `uv run pytest -q tests/test_tekton_install_asserts_the_served_api_versions.py`
      (10 passed), `uv run pytest -q` (247 passed). The apiVersion side is verified by
      `grep -rn "apiVersion: tekton.dev/v1beta1" tekton_ci examples` (empty) and
      `uv run pytest -q tests/test_tekton_manifest_api_versions.py` (11 passed).
    - how the triplet was picked (the "target:" line that used to live here, now answered): the
      Dashboard gates it. 0.73.0's release notes say *"This Dashboard release supports Pipelines
      v1.15.x LTS, v1.17.x, and Triggers v0.37.x LTS"*, and 1.17.1 + 0.37.2 is that pair. Pipelines
      1.17.1 is the current release of its line (1.15.x is the same cycle's LTS), Triggers 0.37.2 is
      the newest 0.37 patch, Dashboard 0.73.0 is the newest release. Re-verified against upstream
      before landing, not from memory.
    - the migration checklist below is closed, all of it: `apiVersion` is v1 on pipeline-runs,
      pipelines and tasks, and the breaking changes from
      https://tekton.dev/docs/pipelines/migrating-v1beta1-to-v1/ are applied —
      `timeout` -> `timeouts`, `input.resources` removed in favour of workspaces,
      `serviceAccountName` -> `spec.taskRunTemplate.serviceAccountName`. `taskRef.bundle` /
      `pipelineRef.bundle`, `ClusterTask` and `taskPodTemplate` do not appear in this tree, so
      nothing needed changing for them.
    - a real-VM lane must still prove (nothing about this is verified by a fake cluster):
      that `uv run kmt tekton-setup-pipelines dev dev1` applies the converted Tasks/Pipelines to a
      real Pipelines 1.17.1 (`api_version_is_served` has met only fakes), that
      `examples/tekton_ci/pipeline-runs/hello-world-pipeline-run.yml` reaches `Succeeded` end to end
      on v1, that a helm deploy pipeline run crosses namespaces with the nested `taskRunTemplate`
      account, that `kmt tekton-setup-triggers` still works with the v1 `PipelineRun` embedded in a
      v1beta1 TriggerTemplate — and that the trigger controller starts at all now that
      `interceptors.yaml` is applied, since a missing `config-triggers-core-interceptors` kills it
      silently. The generated `pipeline_run` from `generate_pipeline_run.sh` is also unverified
      against a cluster.
    - smoke test: hello-world-pipeline-run + one local and one remote helm deploy pipeline

  - refactor: git-crypt process as preparation for externalizing the platform_config into a separate git repository
  - refactor: allow using a separate platform_config repository

- Refactoring platform config structure
  - Tekton
    - Pipelines
      - Allow definition of custom pipelines
    - Pipeline runs
      - Custom Pipeline-runs should be moved to the platform_config directory
      - Pipeline runs should be loaded from the standards and the teams directory
    - Tasks
      - Allow definition of custom tasks in the platform_config directory
      - Load standard and custom tasks from the standards and the teams directory
    - Triggers
      - Allow definition of custom triggers in the platform_config directory
      - Load standard and custom triggers from the standards and the teams directory

- Refactoring backing services (MySQL / Redis / NGINX)
  - decision 2026-10-23: no dedicated install automation for any of these three. They go through the
    standard helm chart deploy path; see "Now" item 3 above for the ordering and the deletion list.
    NGINX is in scope for the same reason as MySQL/Redis (bitnami chart source, pinned version).
  - order: 1) replace the bitnami charts first, 2) then refactor the parameters for the standard deploy pipeline
    - **reversed 2026-10-27:** the deletion landed and the replacement did not, because the images the
      automation pins answer `NotFound` today (`lane-verification-3e8b3f6.md` §2.1). Accept the order as
      deleted-first, replaced-later — and note that step 1 of this order never happened before the
      deletion, which is why there is no `--set` path left to keep wired (see the note below).
    - why: both are pulled from charts.bitnami.com and pinned via MYSQL_HELM_CHART_VERSION /
      REDIS_HELM_CHART_VERSION in templates/environment/team/static.json.template ->
      availability/licensing risk for a "no vendor lock in" project
    - mysql candidates: mariadb, percona mysql-operator, oracle mysql-operator chart, own chart
    - redis candidates: valkey (OSI approved, redis protocol compatible), or an own/lightweight chart
    - update the chart source + versions in templates/environment/team/static.json.template
      - this template no longer holds them: the MYSQL_/REDIS_/NGINX_REVERSE_PROXY_ chart keys were
        deleted with the scripts on 2026-10-27, so a replacement declares its chart + version in
        `platform_config/ENVIRONMENT/TEAM/<APP>/values.encrypted.yaml` instead
    - retest values: persistence/storageClass, auth, replicas, sentinel and metrics keys are not
      interchangeable between bitnami and the alternatives
  - refactor the mysql/redis setup to follow the standard helm chart deployment path
    - goal: no dedicated backing-service pipelines or scripts, run it via
      tekton_ci/pipelines/deploy-pipeline-helm-chart.yml like any other app deployment
    - gaps to close first:
      - deploy-pipeline-helm-chart.yml does not expose path-to-values-file and helm-timeout,
        although deploy-remote-helm-chart.yml already supports both
      - chart/repo/version/values are currently interpolated from env vars in bash instead of being
        declared in platform_config/ENVIRONMENT/TEAM/<APP>/values.encrypted.yaml
    - note: after step 1 the new chart values must still be wired through the old env var --set path,
      so the values keys change twice; accept this as the agreed tradeoff of the chosen order
      - **moot since 2026-10-27:** step 1 happened after the deletion, not before it. The old env-var
        `--set` path is gone with the scripts, so the values keys change **once** — into
        `values.encrypted.yaml`, which is where they belong
    - remove: ~~setup-pipeline-mysql.yml, setup-pipeline-redis.yml,
      scripts/tasks/install_mysql_helm_chart.sh, scripts/tasks/install_redis_helm_chart.sh,
      scripts/tasks/install_nginx_reverse_proxy.sh~~ — **done 2026-10-27**, deleted together with the
      two `examples/` copies and the nginx-reverse-proxy example pipeline and run
    - ~~keep the db/user creation part (setup_mysql_databases.sh) out of the deployment step,
      it is a task/trigger, not a helm install~~ — **closed 2026-10-29 by deletion**: the script and the
      Task that ran it are both gone (`0cf22b7`) and the config key that drove it went first (`8d9c318`),
      so there is no db/user-creation step left to keep out of anything. If a MySQL ever comes back through
      a standard helm chart, the rule that survives this is the one about shape and not the file: a
      task/trigger that runs `mysql` in its own step is fine, a task that takes a path to execute is not.
    - check cleanup: docs/plans/*, examples/tekton_ci/pipelines/setup-pipeline-*.yml and the testsuite
## Open TODOs
- platform config - VERY IMPORTANT CHANGE: separate all platform_config completely out of the automation repository, so that the DevOp using the kubementat automations will only need to manage the platform_config git repositories for his installations and use the kmt tooling. This will require a bunch of changes
  - this will also remove git-crypt from this repo as this will only be used for platform_config repos so that credentials are encrypted
  - maybe we can also offer different secret management options (like extensions/plugins) -> git-crypt would then be the first implementation and default standard

- argoCD / Flux or other alternative for deployment via Configuration as Code
  - goal: get rid of all deployment pipelines
  - focus tekton on build and task execution aside
  - https://fluxcd.io/flux/get-started

- README and DOC improvements
  - Add a section to the readme - Why should i use kubementat?
    - NO VENDOR LOCK IN
    - FREE AND OPEN SOURCE
    - Convention over configuration
    - CI/CD and base deployment automation included
    - easy usage via the kmt cli
    - runs from a local or raspberry pi setup up to a Kubernetes cluster in the cloud
  - Add a section to the readme - What are the goals of Kubementat?
  - update all readme files in docs

## Features TODOs
- really implement support for separate environments aka kubernetes clusters
  - **designed 2026-11-01:** [`docs/specifications/cluster-config-v2.md`](docs/specifications/cluster-config-v2.md)
    — the plan this entry deferred. The two bullets below stay true as scope, and one of them is corrected by
    the spec: the mechanism is **not** switching the authentication before executing commands, which is a
    per-process global and would make the library-for-many-clusters API the spec is written for impossible.
    It is a per-command `ClusterTarget` that builds its own clients, so "which cluster" is decided before any
    code that could touch one runs.
  - this needs a refactor of the usage of kubectl and the kubernetes python client library so that the authentication is switched to the according cluster context before executing commands
  - this affects every command call which sends or retrieves data from a kubernetes cluster
  - what the spec settled that this entry left open: environment ↔ cluster is **1:1 and enforced**; the
    current-context fallback is **gone** (`CLUSTER_KUBE_CONTEXT` is required); identity is the server address
    compared across the environments in `platform_config`; a collision is a hard block with no override; and
    `kubectl` stays a dependency, because `logs -f` / `exec -i -t` / `port-forward` and the Tekton release
    manifests stay kubectl calls — they just carry the target.
  - **this entry is implemented**, through `eca4c31`, and one sentence in it needed the commit it belongs to.
    The spec's §14 says `create-secret-from-json` running in a Tekton pod is "already broken and nobody has
    noticed" — true of `d245e4b`, where `load_kube_config()` raised a `ConfigException` against a kubeconfig
    that pod never had. At HEAD the same failure is a **named refusal**, not a `ConfigException`; the reason
    it is still called out is that the *consequence* was right (that pod could not reach the cluster) and the
    fix is `from_service_account`, the last item of the plan. Both halves of the claim are kept, each with its
    commit, because the spec's §14 also carries a library fact that survives both: `load_kube_config()` in the
    pinned `kubernetes==33.1.0` has no in-cluster fallback, measured, and `create-secret-from-json` is why.
- create-cluster-admin-account script integrated into the kmt cli
- gatekeeper - https://open-policy-agent.github.io/gatekeeper/website/docs/howto/
- alerting
  - default alerts on kubernetes node level
  - examples for custom alerts
  - research grafana alerts
  - dashboard for alertmanager ??
    - https://github.com/prymitive/karma
- PRO FEATURE - predictive alerting / anomaly detection
- PRO FEATURE - AUTHENTICATION UPDATE
  - LDAP integration
    - https://github.com/dexidp/dex
- PRO FEATURE - OAuth Proxy
  - https://github.com/oauth2-proxy/oauth2-proxy
- PRO FEATURE - Cluster Backup
  - https://velero.io/
- PRO FEATURE - S3 compliant Object Storage
  - https://docs.min.io/docs/deploy-minio-on-kubernetes.html
- provide defaults for raspberry pi setup with k3s installation
- replace the loki-stack log aggregation setup
  - **[DONE] 2026-10-31.** `grafana/loki-stack` is out and two releases are in: `grafana-community/loki`
    **20.2.0** (Loki **3.7.9**, monolithic, filesystem, 14d retention) and `grafana/alloy` **1.15.0**
    (Alloy **v1.21.3**, the DaemonSet collector), both into the `loki` namespace. See the release blocks
    in `helmfile.yaml.gotmpl` and `CHANGELOG.md`.
  - **What the migration notes got wrong, and what it cost.** "Plan for dual-run: install the new loki
    alongside loki-stack and join it to the existing ring" was declined by the owner, and it is worth
    saying why it was wrong rather than just different. Loki 2.9 wrote boltdb-shipper chunks and this
    Loki reads TSDB v13, so the old PVC holds bytes this binary cannot open -- a joined ring would have
    two members where one of them has nothing to answer. The 14 days in there are unreadable either
    way, so they were given up rather than defended, and the PVC is deleted on upgrade.
  - **Retention is where the numbers had to be re-derived, not carried.** `table_manager` and
    `chunk_store_config.max_look_back_period` were the two keys the old file trusted for 336h, and both
    are dead here: this chart emits `table_manager` only from the top-level `tableManager` block (a
    separate Deployment this install does not want), and `chunk_store_config` only when `chunksCache`
    is on. `helm template` answers `<ABSENT>` for both. What replaced them, and renders as 336h and
    `retention_enabled: true`, is `loki.limits_config.retention_period` and
    `loki.compactor.retention_enabled`, with the compactor running inside `-target=all`.
  - **What still needs a lane.** That a query returns 14 days and nothing older, and that the
    `alloy` DaemonSet is what put those logs there. Neither is a unit test.
  - status: helmfile deploys grafana/loki-stack (values from logging/loki_stack_values.encrypted.yaml, 14d retention)
    -- **this line is the entry's own history, not the tree's state.** See the `[DONE]` block above it,
    which is the status as of 2026-10-31. The house rule that entries are not reworded is why the old
    sentence is left standing.
  - why now: the loki-stack chart is officially deprecated with no further updates, and its log shipper
    promtail reached end of life on 2026-03-02, so this path has no upstream support left
  - options (pick one, keep grafana as the query/visualization frontend via a Loki data source)
    - modern loki setup: grafana-community loki chart (monolithic / simple scalable / microservices),
      retention + storage config per https://grafana.com/docs/loki/latest/setup/upgrade/upgrade-to-community/
    - alternative aggregator: Vector or Fluent Bit -> Loki, if we want to drop the promtail/alloy dependency
    - if we keep the prometheus/grafana stack consistent: grafana alloy as the collector, since it replaces
      promtail and can be configured with promtail configs via its convert command
  - migration notes / risks
    - both charts have incompatible values schemas -> loki_stack_values.encrypted.yaml cannot be reused, needs a rewrite
    - plan for dual-run: install the new loki alongside loki-stack and join it to the existing ring before
      tearing down the old release, otherwise we lose query access to historical logs
    - retention must stay >= current 336h/14d behaviour, and the storage class handling (persistence vs object
      storage backend) needs an explicit decision for the raspberry pi / on-prem use case
    - wire the new loki into grafana as a data source during install, otherwise the existing dashboards go dark
    - grafana dashboards/queries that assumed loki-stack label conventions need re-verification

- autoscaling
  - kubernetes nodes
  - apps
    - goldilocks (vertical pod scaling and configuration)


## RESEARCH TODOs
- serverless FaaS
  - https://blog.doubleslash.de/serverless-functions-auf-kubernetes-welche-open-source-loesung-ist-die-richtige/
  - https://fission.io/
  - https://knative.dev/docs/

- letsencrypt and traefik - https://doc.traefik.io/traefik/user-guides/crd-acme/
  - **answered 2026-10-29, not by this URL's pattern:** kubementat ships cert-manager with the standard
    `Ingress` provider doing HTTP-01 (`providers.kubernetesIngress.enabled: true`), and the
    `ClusterIssuer` solvers name `ingressClassName` from `TRAEFIK_INGRESS_CLASS`. The crd-acme pattern this
    link describes puts ACME inside Traefik itself and would need the `kubernetesCRD` path plus an
    `acme.json` — which is exactly what `persistence.enabled: false` in
    `templates/environment/kubementat_components/traefik/values.encrypted.yaml.template` refuses. What that
    link still owes this product is the same thing the lane cannot do: an HTTP-01 challenge solved against
    real DNS. See TODOs item 11.
