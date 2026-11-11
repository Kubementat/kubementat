# Finish the Python Migration — Last Lap

**What this file is.** The single reference for what is left of the Python rewrite. It merges the three
lists that were tracking the same work from three angles:

| source | what it held | status |
|---|---|---|
| `docs/plans/finish-python-migration.md` | the ledger, the waves, the exit criteria | history + §1.3b table, now superseded |
| `TODOs.md` | numbered `Now` items with *done when* lines | entries 1–12, mixed states |
| `.agent-work/orchestrator-queue-2026-10-28.md` | the dispatch queue as it ran 2026-10-28 | all rows resolved or moved here |

Everything not marked **landed / done** in those three files is here, once. Read this file, and you know
what is work, what is a decision, and what is evidence nobody has produced yet. Where this file and the
code disagree, the code wins — §8 gives the commands that re-measure every number quoted below.

**Measured against.** `python_refactor_2`, HEAD `4e00cf5`; working tree carries 13 entries of
`docs/research/**` and one research file that moved, none of it product code.
A `full`/`full` lane has run at this SHA (`~/.kmt-e2e/runs/20261029T223248Z-full-full-4e00cf5/result.json`),
and §2.1 is what came out of it.

---

## 0. The state in one paragraph

The migration is nearly over. The default `kmt install` path, `kmt uninstall`, the team-account commands,
the Tekton install/triggers/CronJob/generators, the diagnostics reads, the tunnels and the Grafana accounts
are all Python, and **2 `.sh` files remain** in `scripts/` — both of them three- or four-line `kubectl`
one-liners under `scripts/utilities/kubernetes/`, neither on a path any Python command reaches. Everything
that used to be a hat for the CI-container problem is gone: `run_pipeline.sh` with FIN-51b (`85f1ea8`),
`create_secret_from_json.sh` with FIN-51a (`51a9a1d`), `setup_mysql_databases.sh` with FIN-51c (`0cf22b7`).
The unit suite is **918 passed, 1 skipped** on this checkout (a clean checkout: 874 passed / 45 skipped, the
45 being parity tests that need the repository history), and the lanes run. What is actually left is short and
is §2.1's ordered list, not this paragraph: one real product defect the `full` lane just named (row 18 —
uninstall leaves behind the cert-manager CRDs and the Tekton cluster-scoped RBAC it installed), one candidate
tunnel regression (row 19), one commit that empties `scripts/` and removes the last declared `jq`, the
docs/CLI-surface ticket (11 ungrouped commands measured), the CI job that would keep every green claim honest,
and three small hardening items. What is *not* left is a migration decision: §3's owner decisions are all
answered, and the 17 rows that landed are listed in §2.2 so nobody re-opens one.

**Re-measured 2026-10-29 (`4e00cf5`) after a `full`/`full` lane** — 106 assertions, 10 red, 17 min, artifact
`~/.kmt-e2e/runs/20261029T223248Z-full-full-4e00cf5/result.json`. Read that run before this file's prose: it
moved three items from "owed" to "paid" (the cleanup CronJob asserts, the account asserts, the reinstall
cycle) and moved one from "flaky, not ours" to "probably ours" (the tunnel signatures).

---

## 1. In the tree, uncommitted — finish this first

> **Resolved 2026-10-29:** the Traefik/routing work below is committed on `python_refactor_2` as `2347583`
> ("Routing: Traefik comes back as the platform's Ingress controller") with the four §3.3 defaults follow-ups
> as `643ed6e`, and **T-10 has landed** (CHANGELOG, README_PLATFORM_CONFIG, TODOs item 11, FIN-17's row).
> Nothing in §1 is owed any more; the counts below were corrected against the lane artifact the same day.

The Traefik/routing work (`docs/plans/traefik-integration.md`, tickets T-1…T-9) is **implemented and
lane-proven but not committed**, and its docs ticket (T-10) was deliberately held back so that file writes
would not be blamed as lane failures by the `checkout-untouched` assert.

```
git status --short            # 35+ entries: M + ??
```

What it contains, and what proves it:

- `src/kubementat/automations/routing/{routing.py,traefik_presence.py}` — the presence guard and
  `install_routing()`; `kmt routing install [--yes] [--update]` and `kmt routing status`.
- `templates/environment/kubementat_components/traefik/values.encrypted.yaml.template` (new dir) and the
  `helmfile.yaml.gotmpl` repo + release block (`group=routing`, `component_name: traefik`).
- nginx annotations deleted from `scripts/utilities/kubernetes/create_ingress_yml.sh` and both Tekton
  templates; ClusterIssuer solvers now read `ingressClassName` from `Config` instead of the literal `nginx`.
- `scripts/automation/components/install_routing.sh` **deleted**.
- Uninstall grew a gated Traefik CRD sweep (both `traefik.io` and `hub.traefik.io`; the group question in
  research §9 is answered by `helm show crds` on `traefik/traefik` 42.1.3, not by memory).
- The new `routing` lane in `tests/e2e/harness/{config,stages}.py`: k3s's bundled Traefik disabled through
  `/etc/rancher/k3s/config.yaml`, `kmt routing install`, five post-install asserts, a TLS fixture that proves
  Traefik really terminates, a redirect probe, and the guard that `kmt install` does not install a controller.

Evidence: `~/.kmt-e2e/runs/20261028T205357Z-routing-full-47b186f/result.json` — **24/24 routing asserts
pass** (the file names 24 assertions whose name or detail says routing, not 25; 94 assertions total, 4 of them
red, all four the §5 set). TLS terminates with the issued certificate, 80→443 is a 308, k3s's controller is disabled and
stopped, cert-manager is connected to the class, a second install is a skip and installs nothing,
`--update` really applies the CRDs). The 4 failures in that artifact are the same 4 that the `full` lane at
the same SHA fails (§5) — none of them is routing.

**Do before anything else in §2:** commit it, then land T-10. T-10 is `CHANGELOG.md`,
`docs/README_PLATFORM_CONFIG.md`, `TODOs.md`, and the `docs/plans/**` row for FIN-17: what landed, what each
lane proves, what is still owed. The CHANGELOG's `# Unreleased` currently starts at the two Tekton
generators and says nothing about a controller being back in the product, which is a user-visible change and
the first thing an operator will read.

---

## 2. The open work, in the order an implementer should take it

**Measured against 2026-10-29 (`4e00cf5`)**, after a `full`/`full` lane (`~/.kmt-e2e/runs/20261029T223248Z-full-full-4e00cf5/result.json`:
106 assertions, 10 red) and `uv run pytest -q` → **918 passed, 1 skipped**. Re-measure with §8; the numbers
in the old rows below are older than the tickets that moved them.

**The order, and why this order.**

1. **Fix what the lane measured, in that order.** A product defect the lane just surfaced outranks every
   unfinished ticket, because every other claim is read against a teardown baseline that currently lies.
   Row **18** is that defect — three reds that the old §5 filed as "not this migration's" and are not.
   Row **19** is next because it is the only candidate *regression* left in the migration itself: a tunnel
   that read green on four lanes and red on two.
2. **Then the mechanical last step of the migration.** Row **21** is one commit that makes `find scripts -name '*.sh'`
   answer **0** and takes `jq` out of the declared dependencies. Until it lands, the sentence "there is no bash left"
   is almost true, and an almost-true claim is the kind a later agent writes a test for that cannot fail.
3. **Then the surface and the docs together.** Row **22** renames commands and *has no aliases*, so it lands
   with the commands and cannot be sequenced after them; and it is the last thing that has to be right before
   the CHANGELOG describes 0.5.0. **CI (row 23) goes after it** so the first real CI run tests a settled
   surface rather than a renaming one — but row 23 is genuinely independent, so it is the best candidate for a
   second worktree (§7 rule 1).
4. **Then hardening that needs no lane.** Rows **24** and **17** are small, have no dependency, no cluster, and
   no lane. They belong at the end not because they matter less but because nothing they do changes what any
   other row can assert.
5. **Then the owner-level items.** Row **20** is a config decision, not code.

Nothing else here is work. The rows below are the whole of it.

### 2.1 Open rows, in order

| Order | Row / ticket | What it actually is | Measured state | Effort | Done when |
|---|---|---|---|---|---|
| **1** | **18 — uninstall sweeps the cluster-scoped objects kubementat installed** (was §5's "not this migration's") | The lane's three surviving teardown reds are residue of things `kmt install` put there: `cert-manager.io` and `acme.cert-manager.io` CRDs, and the Tekton / Triggers / Dashboard **ClusterRoles** and **ClusterRoleBindings**. `CRD_GROUPS_KUBEMENTAT_OWNS` is `("tekton.dev",)` (`automation.py:1611`) — so `assert:no-tekton-crds` passes and cert-manager's definitions are never asked about, exactly like Traefik's were before the `TRAEFIK_CRD_GROUPS` sweep. The cluster-scoped RBAC is a different shape: it comes from the release YAMLs `TektonUtils.install()` `kubectl apply`es, and deleting a namespace takes none of it. **Decide the mechanism, do not guess names**: by `app.kubernetes.io/managed-by`/the labels the install path stamps, or by the objects those same manifests declare — a literal name list is how this deletes something the operator owns. | 3 reds, reproducible: `no-extra-cluster-scoped:{customresourcedefinitions,clusterroles,clusterrolebindings}`. `reinstall-after-uninstall` now **passes**, so §5's set is 3, not 4 | 3 h | the three asserts pass on a `full` lane, `assert:no-tekton-crds` and `:no-helm-deployer-clusterrole` stay green, a test asserts an untouched foreign ClusterRole survives, and the uninstall path never deletes a CRD whose group it did not install |
| **2** | **19 — the two tunnel signature rows** | `tunnel-signature:tunnel.grafana` (plain) alternates PASS/FAIL lane to lane with `bound-before-start=False`, `tunnel-port-freed` green and `body=empty`; `tunnel-signature:tunnel.tekton` read green on four consecutive lanes (`7269c9a`→`47b186f`) and is **red** on `4e00cf5`. The old note blamed "a probe that raced the tunnel's bind" and that explanation is now *doubled*, not tested. Before anything else: read the guest's own tunnel log in the artifact directory, not the probe's, because `body=empty` with a free port means either "never bound" or "bound and answered nothing" and the two want different fixes. | Both rows red on `4e00cf5`; grafana alternates across `16c8196`/`badcf40`/`47b186f`/`4e00cf5`; `tunnel-port-freed:*` and `tunnel-pod-tcp-connect` green everywhere | 2 h + a lane | either the row is green twice in a row and the flake is named in the harness's own words, or the cause is a product one and the fix lands in `Automation.open_tunnel_*` / the CLI — and the assert detail says which of the two the reader is looking at |
| **3** | **21 — FIN-09 / D12 finished: `jq` out, `scripts/` empty** | `yq` is already out of `KUBEMENTAT_DEPENDENCIES` and 0 scripts call it. What is left is the tail the old row 8 described as "one file that calls `jq`": both surviving `scripts/**` files are 2-line `kubectl` wrappers that **no code execs** (`retrieve_node_addresses.sh` — `jq`; `view_cluster_resource_versions.sh`), and both already have parity tests. Delete both, turn `tests/test_parity_kubernetes_reads.py` into "these files stay deleted and `kmt pod-images`/`get_nodes()`/`get_api_resources()` answer for them", and drop `jq` from `KUBEMENTAT_DEPENDENCIES` (`automation.py:206`). | `find scripts -name '*.sh' \| wc -l` → **2** (4 LOC); `grep -rl '\bjq\b' scripts` → 1; `yq` → 0 | 1 h | `find scripts -name '*.sh' \| wc -l` → **0**, `KUBEMENTAT_DEPENDENCIES` names neither tool, `tests/test_the_declared_dependencies_are_what_the_tree_needs.py` still derives the list from the tree, and the stays-deleted guard fails if either file returns |
| **4** | **22 (row 6) — FIN-61 + FIN-62 + FIN-63**: docs, READMEs, the Click groups, CHANGELOG | The 11 ungrouped commands are measured, not estimated: `tunnel.pod`, `tunnel.grafana`, `tunnel.tekton`, `tunnel.vault-ui`, `tekton.run-pipeline`, `tekton.list`, `tekton.cleanup-pipeline-runs`, `tekton.setup-pipelines`, `tekton.setup-triggers`, `tekton.uninstall-pipelines`, `helm.helmfile-apply` — the lane reports the same list in `needs_grouped_cli_form`. **Renames, no aliases** (§1.7a). No version bump — the owner bumps by hand at the end. The docs half is not cosmetic: `docs/README_TEKTON.md:355` still tells a reader *"kubementat installs no ingress controller any more"*, which has been false since Traefik shipped, and a reader who finds it after the CHANGELOG will believe the document that is wrong. | `grep -rn 'fin62="' tests/e2e/harness/config.py \| wc -l` → **11**; `kmt --help` → 36 lines | 11 h | 0 markers, `kmt --help` shows the groups and no old flat names, the lane's `needs_grouped_cli_form` is empty, every README states the routing surface as shipped, `pyproject.toml` stays `0.4.0`, and no test asserts a version string |
| **5** | **23 (row 7) — FIN-10: CI runs the unit suite** | Still nothing. There is no `.github/`, and no `tekton_ci/` pipeline mentions pytest, so every "verified by `uv run pytest -q`" in the three planning files is a claim that lives in a markdown file. Row 14's bug (a test that is green because of a file `initialize` left behind) is exactly what this catches, and row 15 is fixed so the run is reproducible. **Parallelisable** — a second worktree, no file overlap. | 0 CI configs that run tests | 3 h | a step fails when the suite fails, on a commit; and the job's interpreter comes from `.python-version` so the verdict does not depend on the runner |
| **6** | **24 — `helm list`: the same question, asked two ways** | `helm_utils` spells all six statuses (`HELM_LIST_RELEASES_ARGV`), while `automation.py:2303` and `:2396` still read `helm list` by **default**. On helm 4 that is the same set; on helm 3 they report different release sets for one namespace, and **the lane runs helm 3.22.1** while the tree declares a pin of helm 4.3.1. This is a decision, not a bug: make both call sites read the same constant, or write down why the default is right there. | 2 call sites, 1 constant; guest helm = v3.22.1 (`tool_versions` in the lane artifact) | 30 min | one constant answers `helm list` in every path, and the guard that execs the real `helm` still proves no flag in the argv is unknown |
| **7** | **17 — the exec guard cannot see a path typed as a string** | Read the code: `_starts_with_a_named_program` answers `True` for **any** `ast.Constant` str, so `["/srv/scripts/thing.sh", *args]` passes the guard that is the reason `kmt run-script` stayed retired. Nothing in `src/` does this today, so this is a hole in a guard and not a product defect. | unchanged; the hole is documented in the test's own comment and not fixed | 1 h | a planted literal path as `argv[0]` fails the guard, the plant is removed, and the four registered-surface tests stay green — they are the proven half and must not be traded |
| **8** | **20 — `GRAFANA_USERS` declares nothing, so the grafana rows cannot be green** | The lane now *proves* the vacuity item 4 of `TODOs.md` only described: `assert:grafana-account-rows-declared` fails ("declares no `GRAFANA_USERS` rows") and `cap:create-grafana-accounts` exits 1. The code is right to refuse; the row is red because the configuration the harness writes declares nothing. Either seed one real row in the lane's config or have the harness report "nothing declared" as a **skip that names the file** — the current state is a red that reads like a product failure. | both rows red on `4e00cf5` | 1 h + an owner decision | the lane goes green with a declared account (and then asserts it really exists in `/api/org/users`), or goes `skipped` with a reason that names the config file. Do not "fix" it by making the command create something nobody declared |

**Also open, as evidence rather than tickets** — see §4: row 1's half is *a pod must be started once*, not a
code change; and LINKERD-REMOVE / INGRESS-REMOVE owe a cluster that had those things, which this lane cannot
give them.

### 2.2 Rows landed since the last reading of this file — do not re-open

The full original wording of every row below is in `git log -p docs/plans/finish-python-migration-last-lap.md`;
the ledger of which script each one replaced is §7 of the old plan. What is kept here is what proved it, so a
fresh agent cannot mistake a landed row for work.

| Row | Landed | What proved it, measured |
|---|---|---|
| 1 | FIN-50/51 code | `grep '\.sh' tekton_ci examples` → 0; the tasks run `uv run --locked --no-dev kmt …`. **Its pod-evidence half is still owed (§4).** |
| 2 | FIN-24 | `TektonUtils.generate_pipeline_run()` + `kmt tekton-generate-pipeline-run`; no `read -p`, no context gate anywhere |
| 3, 3a | FIN-40 / FIN-43; Traefik defaults | `kmt pvc-usage`, `create-ingress` as a generator; `version: "42.1.3"` on the traefik release; examples off nginx |
| 4 | FIN-06 / D7 | `kmt --help` has no `run-script`; only prose mentions it in `src/` |
| 5 | FIN-25 | `kmt component install/uninstall`; namespace-only components refused with the reason |
| 9 | housekeeping | `helmfile_apply.sh`, `generate_ssh_key.sh`, `base64_library.sh` deleted with guards |
| 10 | cross-references | both other files carry the pointer in their first 15 lines |
| 11, 11b | T-10 docs; `initialize` no-op | `_initialization_write_targets` is one table read by guard and write |
| 12 | git components retired | `gitea`/`gitbucket` 0 hits; `component: git` 0 |
| 13, 14, 15 | component message; checkout-dependent test; `.python-version` | the named guards pass; the pin file is tracked and *not* ignored — measured: a clean checkout resolves 3.9.23 / click 8.1.8, same as this machine |
| 16 | toolchain pin | `constants.PINNED_BINARY_VERSIONS` (helm 4.3.1, helmfile 1.7.0); warn-and-continue guard passes |
| — | FIN-11, FIN-12/13, FIN-17…FIN-23, FIN-30…FIN-36, FIN-41/42, FIN-44/45/46, FIN-60, LINKERD-REMOVE, INGRESS-REMOVE | the `full` lane at `4e00cf5`: install ×2, pipeline run, idempotent reruns, all 8 team-account asserts, cleanup cronjob exists+scheduled, secrets round-trip, uninstall sweeps, reinstall cycle — all green |
## 3. Owner decisions — answered 2026-10-29

Each answer below is final for this migration. An agent that re-litigates one of them is writing the wrong
file; the lanes will not tell you until 15 minutes later.

1. **Renaming a component namespace** (TODOs item 8 / old plan §1.3b row 2b) — **DECIDED: kubementat is an
   opinionated framework on top of Kubernetes. Component namespaces are proper static defaults owned by the
   product, and a live rename is not a supported operation.**
   kubementat ships proper defaults for every component and keeps them static where they are static. The
   cluster-scoped Helm ownership problem (`meta.helm.sh/release-namespace must equal "kmt-e2e-renamed":
   current value is "grafana"`, `20261028T061713Z-full-badcf40`) is not a bug to fix but a boundary to state:
   moving a component's namespace is a manual, release-scoped operation the operator performs, not a verb the
   product offers. Making those defaults more flexible is deferred out of this migration, and so is the
   harness work that would assert the refusal — postponed with the rest of §3, do not schedule it. The three
   lane rows that came from an unsupported rename stay red on purpose and are not a migration ticket.

2. **The dashboard** (old plan §1.8 B2, FIN-89, Headlamp recommendation) — **DECIDED: not in this migration.
   The dashboard replacement is scheduled *after* the Python refactoring is finished.** Nothing installs a
   dashboard today, `login_kubernetes_dashboard.sh:56`'s `svc/kubernetes-dashboard-kong-proxy` port-forward
   question dies with that script, and no agent adds a repo or release to either `helmfile.yaml.gotmpl` copy
   for it. Park it; do not re-open it as migration work.

3. **The four Traefik template questions** (`docs/plans/traefik-integration.md` §9) — **ALL FOUR ANSWERED,
   and they are work, not prose. See row 3a.**
   - `isDefaultClass` → **ship `true`.** Traefik *is* the default when it is installed; that is the point of
     bringing a controller back. The IngressClass is the cluster-wide answer to an Ingress that names none.
   - **Pin the chart.** `helmfile.yaml.gotmpl` must carry a real `version:` on the `traefik` release. The
     commented `# version: "42.1.3"` is not a pin, and "resolves newest against a `kubeVersion >=1.30.0-0`
     floor" is not an answer.
   - **Mirror it.** `traefik/traefik` and its image go into the platform registry the way every other
     kubementat image does.
   - **The nginx examples are not a museum.** Retarget `examples/**` that still speak nginx to Traefik.

**One more, small and unanswered:** FIN-35 landed, but two of its edges were reported and never answered —
whether `create-team-account-read-only` grants a role at all, and nothing joins a team to a cluster
membership. Both are product questions, not migration work, and neither is scheduled.

---

## 4. Evidence: what the lanes have paid, and what they still owe

This is the half of §8.4 no unit suite can pay, and the reason "implemented-unverified" is the honest state of
most of what landed. It is not a doubt about the code — it is the cluster half.

**PAID by `~/.kmt-e2e/runs/20261029T223248Z-full-full-4e00cf5` — these rows are closed, not work:**

- **FIN-12** — the four pinned URLs really do land Pipelines 1.17.1 / Triggers 0.37.2 / Dashboard 0.73.0 on a
  clean cluster: `cap:install` green, `assert:helm-releases-present` green, and `tekton-setup-pipelines` and
  `tekton-setup-triggers` both pass and are idempotent. The ClusterRole kubementat writes through
  `KubernetesUtils` was accepted where the heredoc was, which `assert:no-helm-deployer-clusterrole` proves by
  asking for it back again.
- **FIN-21** — `cap:task.cleanup-job`, `assert:cleanup-cronjob-exists` and `assert:cleanup-cronjob-scheduled`
  all green. One thing under it stays unread: whether the CronJob's own pod pulls through the Secret the
  command creates.
- **The tunnel lifecycle half** (item 5, row 19) — `tunnel-port-freed:*` and `tunnel-pod-tcp-connect` green on
  every lane since the group-kill fix.
- **Everything the migration already replaced that this file used to call unverified**: the install path, the
  rerun idempotency of install / routing / pipelines / triggers / registry access, all eight team-account
  asserts, the secrets round-trip, the uninstall namespace sweeps, and the reinstall-after-uninstall cycle.

**Still owed:**

| who | what a lane must show | why a unit test cannot |
|---|---|---|
| FIN-19 | a release renamed at install time, a namespace the cluster is still draining, and a claim with a retained PV behind it. (`--all` is moot since `e57fed3` named the statuses.) | all asserted against fake `helm`/`kubectl` |
| FIN-22/23 | that `kmt tekton-setup-pipelines` applies the file generated into `platform_config/...` and **not** the `templates/` copy. *Applying* is paid; *which file* is not asserted. | compared to bash's bytes, not to a cluster |
| FIN-36 | a token minted on a real service account printing byte-for-byte what the script printed | no lane stage runs `kmt service-account-token` |
| FIN-41/42 | `cluster-capacity` against `kubectl describe node`'s Requests table (`500m`, `150000000n`, `128Mi`); a cluster **without** metrics-server answering without a traceback | no lane stage runs `kmt cluster-capacity` / `kmt pod-images` |
| FIN-44/45 | `deploy-ssh` where the image has no `/bin/bash`; neither session hanging when attached; `deploy-logs` on a multi-container pod; a real `helm search repo` index | no lane stage runs them, and `capture=False` is not a terminal observed |
| row 19 (signatures) | see §2.1 — this is a suspect, not a debt: `tunnel-signature:tunnel.tekton` went red after four green lanes | the harness reads a body, not the tunnel's own stderr, and that log has not been read |
| LINKERD-REMOVE | a cluster that really had the mesh: `kmt uninstall --yes` leaves no `linkerd.io` definition and no `linkerd`/`linkerd-viz` namespace | CRD-before-namespace order is asserted against fake `kubectl`; a real namespace stuck in `Terminating` is the unproven half |
| INGRESS-REMOVE | a cluster that really had nginx-ingress: no namespace, no release, no `IngressClass` bound to `k8s.io/ingress-nginx` | same shape as above |
| routing | paid on `47b186f` (24/24). Two debts since `643ed6e`: **(i)** that an Ingress naming no class really is routed now `isDefaultClass: true` — no lane assert reads that path (`stages.py:2270-2277` names the class deliberately); **(ii)** HTTP-01 against real DNS/ACME, which the isolated VM lane structurally cannot do | the lane-local `ClusterIssuer` proves the cert-manager↔Traefik connection and nothing more |
| row 1 (CI image) | `kmt` importable inside a pipeline step | no lane step runs `uv run kmt` — neither Task is exercised, so the succeeding hello-world PipelineRun is not evidence of this |
| row 18 | a `full` lane's teardown answering **zero** residue for CRDs and cluster-scoped RBAC | the whole uninstall sweep is asserted against fake `kubectl` |
| row 20 | that a declared `GRAFANA_USERS` row really lands in `/api/org/users`, that a 412 really says "already exists", and that a Service lookup returns the ClusterIP the client is pointed at | the lane cannot get that far while the config declares nothing |

**Owner, 2026-10-29: a `routing --scope full` lane runs once, after the other tickets land.**

**Routing lane policy** (owner decision, 2026-10-28): the `routing` lane is the verification gate for routing
work and starts **only when routing components change**. It is not a CI gate; a human types the command.

---

## 5. What the lane's failures are — and the one this file got wrong

**Corrected 2026-10-29, against `~/.kmt-e2e/runs/20261029T223248Z-full-full-4e00cf5/result.json`.** The old
version of this section filed four teardown asserts as "not this migration's — they want their own". Three of
them do not. On the new lane:

```
PASS  cap:reinstall-after-uninstall
FAIL  assert:no-extra-cluster-scoped:customresourcedefinitions   certificaterequests/certificates/challenges/clusterissuers/issuers/orders
FAIL  assert:no-extra-cluster-scoped:clusterroles                tekton-aggregate-edit … tekton-triggers-core-interceptors (19)
FAIL  assert:no-extra-cluster-scoped:clusterrolebindings         tekton-cleaner-clusterrole-binding … tekton-triggers-webhook-admin (18)
```

`reinstall-after-uninstall` is **green**, so the set is 3 and not 4. And the residue is not unrelated
infrastructure: it is what `kmt install` put there. cert-manager's CRDs are left by the cert-manager release
helm uninstalls (a chart's `crds/` directory is applied on install and removed by nobody on uninstall — the
same shape Traefik had, which is why `TRAEFIK_CRD_GROUPS` exists), and the ClusterRoles/Bindings come from the
Tekton/Triggers/Dashboard release YAMLs that `TektonUtils.install()` applies; deleting a namespace takes no
cluster-scoped object with it. `CRD_GROUPS_KUBEMENTAT_OWNS` is `("tekton.dev",)` at `automation.py:1611`, which
is why `assert:no-tekton-crds` passes while cert-manager is never asked about.

**So this is uninstall completeness — FR-61 — and it is row 18.** Calling it "not this migration's" sent two
agents past a real defect. The only honest thing that statement got right is that it is not *routing* work;
diff the assertion sets of `20261028T143555Z-full-47b186f` and the routing run at the same SHA and you will see
they were red then too, which is the evidence that this has been broken longer than anyone has been looking.

Independently: `helm_utils.py:269` passed `--all` to `helm list`. helm 3 accepts it, **helm 4.3.1 (this
host) raises**, and discovery's `HELM_LIST_ARGV` had already dropped the flag. That was described as "a
one-line ticket that nobody has filed" — **it has now been filed and landed (`e57fed3`), and it was not
one line.** `--all` could not simply be deleted: on helm 3 it pulled in every status, and helm 3's
documented default is *deployed or failed only*, so a deletion would have silently dropped
`pending`/`superseded`/`uninstalled`/`uninstalling` while the docstring still promised them. The fix
(`HELM_LIST_RELEASES_ARGV`) names all six statuses, which is portable, and a guard now execs the real
`helm` to assert no flag in that argv is unknown. **The premise I had been carrying was wrong about
`failed`** — it was already covered by helm 3's default — which is worth recording because it is the
kind of confident, unexecuted claim that turns into a silent regression.

**One consequence of that fix, still open and not a defect on the shipped version:** `automation.py:2303`
and `:2396` still read `helm list` by *default*, while `helm_utils` now *spells the statuses* via
`HELM_LIST_RELEASES_ARGV`. On helm 4 the two agree (helm 4 defaults to every status). On helm 3 they report
different release sets for the same namespace — one call site says a component has no release where the other
says it has a pending one. Harmless in theory, and worth naming because **the lane's guest runs helm 3.22.1**
(`tool_versions` in `20261029T223248Z-full-full-4e00cf5/result.json`) while the tree declares a pin of helm
4.3.1. That is row 24, and it wants a decision rather than a ride in on a bugfix.

---

## 6. Deliberately not scheduled

- `platform_config` externalisation (decision 4: do not design for it, do not make it harder) and
  git-crypt leaving this repo with it.
- Multi-cluster / cluster-context work.
- Flux / ArgoCD, and the "get rid of the deployment pipelines" goal that comes with it.
- A chart's image pointed at the platform registry by something the release honours (`image.registry` for
  every chart, not just Traefik). The mirror list is what ships: `traefik:v3.7.16` is pre-positioned, but a
  chart pulls what it is told to pull and no `src/` code repoints a chart. **Owner, 2026-10-29: acceptable as
  it stands, revisit as a feature later.**
- The dashboard replacement — **decided 2026-10-29: after this migration** (§3 item 2), the backing-services
  rework (TODOs item 3), the loki-stack → community-Loki swap (**landed 2026-10-31**, see the log-aggregation
  entry in `CHANGELOG.md`), alerting, autoscaling, and the PRO FEATURE
  list. None of them is this migration.
- Making the static component namespaces dynamic, and the harness work that asserts a refused rename
  (§3 item 1). Deferred out of this migration by that same decision.
- Full-lane E2E runs as a scheduled thing. A human starts a lane when a human needs one.

---

## 7. Rules every agent gets (carried forward)

1. **One writer per worktree.** Never edit `platform_config/` or `docker_files/**` without checking who owns them.
2. **No `git commit`, `checkout`, or `stash`** unless the operator asked. Stage is not a commit.
3. **Bash only through `kubementat.shell.run(argv, capture=True, check=False)`** — argv lists, never strings,
   never `shell=True`, never `os.chdir`.
4. **Test names describe behaviour**, never ticket ids; ids may appear in docstrings only
   (`tests/test_test_name_conventions.py` enforces).
5. **A claim needs the evidence that proves it.** Unit suite = `uv run pytest -q`; cluster claim = a
   `result.json` that names the SHA. "A lane would show this" is not evidence; a lane run is.
6. **A `scripts/**` deletion is a ledger event**: name the ticket that replaces it, keep a parity or
   stays-deleted guard, and update §7 of the old plan's ledger.
7. **≤3 concurrent agents**, file-disjoint scopes, generous timeouts — most inference endpoints here are local.

---

## 8. Re-measure everything this file claims

```bash
uv run pytest -q                                   # measured **918 passed, 1 skipped** (2026-10-29, `4e00cf5`).
                                                   # The 895 this file quoted is older than rows 16/17.
                                                   # The 1 skip is `test_harness_capability_argv.py`'s
                                                   # parametrised "no run-script" case, whose parameter set
                                                   # is empty because the command is gone — that is the
                                                   # correct shape of a closed door, not a hole.
uv run python tests/e2e/suite.py --lane full --scope full   # 106 asserts / 10 red / ~17 min; the reds and
                                                   # their cause are §5 and row 18/19.
git worktree add --detach /tmp/kmt-clean HEAD && (cd /tmp/kmt-clean && uv run --locked pytest -q)
                                                   # measured 874 passed / 45 skipped. Read the 45 as the
                                                   # parity tests that need `git show <sha>:scripts/...`;
                                                   # a `git archive` extraction has no history and adds 2
                                                   # failures that belong to `git check-ignore`, not to the
                                                   # tree. A *worktree* is the clean-checkout test, not an
                                                   # archive.
cat .python-version                                # measured `3.9`, and `git check-ignore` says no (exit 1).
                                                   # A clean checkout resolves 3.9.23 / click 8.1.8, identical
                                                   # to this machine — row 15's whole point.
find scripts -name '*.sh' | wc -l                  # measured 2  -> row 21 takes this to 0
find scripts -name '*.sh' -exec wc -l {} + | tail -1   # measured 4 total LOC
uv run kmt --help | grep -cE "^  [a-z-]+ "         # measured **36**; 11 commands still print flat (§2.1 row 22)
grep -rl '\bjq\b' scripts | wc -l                  # measured 1 (`retrieve_node_addresses.sh`)
grep -rl '\byq\b' scripts | wc -l                  # measured 0
ls ~/.kmt-e2e/runs/ | tail -5                      # which lane evidence actually exists
grep -rn 'fin62="' tests/e2e/harness/config.py | wc -l   # measured 11 — FIN-62's remaining count, and the lane
                                                   # prints the same 11 in `needs_grouped_cli_form`
grep -n 'CRD_GROUPS_KUBEMENTAT_OWNS' src/kubementat/automation.py    # ("tekton.dev",) today -> row 18 adds
find templates -type f | wc -l                     # measured **19** (22 before the git providers, `8678526`)
find templates -name '*values*.template' | wc -l   # measured **11** (14 before them)
ls -d templates/environment/kubementat_components/*/ | wc -l  # measured **6** (9 before)
git status --short                                 # 13 entries: research docs, not product code
```

**Two §8 lines were deleted rather than corrected, because both had no subject left.**
`grep -rn "run-script" src/` answers nothing but a rewritten sentence (`src/cli/helpers.py:10`), so the
line that used it to count the command's remaining surface is gone. And no grep in this file's §8 listed
the `helm` repositories or `component: git`, which is how a retired component survived a section that
promises to re-measure everything — so `find templates …` now carries three counts where §8 had none.

The `jq`/`yq` pair is the one row whose reading changed the character of a ticket: FIN-09 / D12 was "strip
them out of `KUBEMENTAT_DEPENDENCIES` once the scripts that call them are gone", and the scripts answer is now
**one file that calls `jq` and none that calls `yq`**. Both surviving scripts are 2-line `kubectl` wrappers no
code execs, so row 21 is "delete the wrappers, then the dependency" and not the six-script sweep the earlier
numbers implied. Re-measure before quoting; this block's own history is counts that were true of an older tree.
