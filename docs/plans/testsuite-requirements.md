# Kubementat Test-Suite Requirements (Wave T / FIN-80…FIN-89)

**Status:** requirements only — no implementation, no production-code changes.
**Implements:** Wave T of `docs/plans/finish-python-migration.md` (§ Wave T, §1.3, §1.4, §1.7, §9).
**Consumes:** `virt-runner` (`~/dev/os_projects/virt-runner`) — its `create/destroy/ssh/list`, `--json`
envelope, `--mount`, `--script` semantics are the harness's only VM interface.
**Empirical base:** [`virt-runner-setup-research.md`](virt-runner-setup-research.md) (2026-10-20) — a real
research run on this host that measured the primitive, k3s, the tooling and the timings. **Every requirement
the research touched is annotated below with `confirmed`, `partially confirmed`, or `refuted as written`**;
refuted ones have been rewritten, because a requirement that fails on a healthy cluster is a bug in the
refuted ones have been rewritten, because a requirement that fails on a healthy cluster is a bug in the
requirement. Two of its findings were product blockers: **FIN-88** (the dead `twuni` repo) is now **decided —
delete the entry**, no replacement for now; **FIN-89** (the retired kubernetes-dashboard chart repo) is
**still undecided** and needs a decision memo before anyone may implement it (see §8 Q9).

**Owner decisions this doc inherits (not re-opened):**

| # | Decision | Consequence |
|---|---|---|
| D1 | No backwards compatibility (§1.7a) | No test may exist only to exercise a shim/alias/fallback. |
| D2 | Runs always start **manually** — no CI job, no schedule, no nightly | No lane depends on a CI environment variable, a gitlab webhook, or an env-gated runner. Nothing runs while the box is idle. |
| D3 | VM target is the **local machine** only, no pool, no fleet | Parallelism = a single host lock. No admission logic. |
| D4 | Real `setup-pipeline-mysql` execution is **not** tested | mysql/redis behaviour is driven through `kmt` in the guest (the "lazy alternative"). **Update 2026-10-27:** the backing-service install automation this excluded is deleted (owner decision, `lane-verification-3e8b3f6.md` §2.1) — the manifest named here is gone, so D4 now excludes nothing. |
| D5 | **No new helmfile groups** | Smoke reuses the existing `dashboard` group via `--helmfile-installation-group`. FIN-81's `group: core` label is dropped. **The mechanism is sound; the release under it is retired — see FR-33 and FIN-89.** |
| D6 | Full lane **re-runs `kmt install`** and asserts nothing broke | Idempotency is a lane stage, not a separate lane. |
| D7 | Isolated VM, never a real secret | Git/registry/webhook credentials are dummy values everywhere. |
| D8 | Verification is **manual**, and there is **no merge gate** | A lane's run directory is cited on the ticket; the harness records the git SHA it tested. No CI, no hook, no runner reads the result. |
| D9 | `kmt uninstall` removes **the whole kubementat stack** — every kubementat-owned namespace **and** every PVC it created. It prompts by default; only `--force` runs it without a last confirmation. | FR-61 = what "complete" means, FR-63 = the flag/confirmation contract. |
| D10 | Guest is **`--distro ubuntu --release resolute` (Ubuntu 26.04)**, pinned explicitly per run | FR-24. Not virt-runner's profile default by accident — a profile change must not silently change which OS the suite proves. |

---

## 1. Scope and purpose

The suite answers exactly one question that argv-shim parity tests (`FIN-03`) cannot: **does kubementat
install, run, and remove itself on a real cluster?** It replaces `tests/test_installation.sh`,
`tests/uninstall.sh`, `tests/reset_configuration.sh` (FIN-85) and is the verification mechanism for Waves
1–7. **It is not a gate**: it runs when a human decides to run it (D8), which is why its runtime and its
one-command entry point are treated as requirements here and not as polish.

In scope: the loop `create VM → provision single-node k3s → install kubementat → exercise main features →
re-run (full) → uninstall → assert absence → destroy VM`.
Out of scope: unit/parity tests, CLI-surface tests, import-hygiene tests — all `pytest`, all `FIN-03`.
This document does not restate them.

### 1.1 Bug classes → the requirement that catches them

| Class | Requirement |
|---|---|
| 1. Wrong storage-class assumption → PVCs Pending forever | FR-21 |
| 2. Crash on the **second** run against an existing cluster (D4) | FR-30, FR-31 |
| 3. Uninstall leaves namespaces/CRDs/clusterroles behind | FR-40, FR-41 |
| 4. Tunnels bind a port but select the wrong pod / break on a moved namespace | FR-50, FR-51 |
| 5. `v1` resources that apply cleanly but never run | FR-60 |
| 6. helm/helmfile drift between templates, `values.encrypted.yaml`, chart versions | FR-23 |
| 7. Idempotency of every mutating command | FR-30 |
| 8. Config-resolution regressions (missing team config D6, hardcoded team D9) | FR-52 |

Traceability runs the other way in §4 (matrix) and §5 (exit criteria).

---

## 2. Terminology and the four non-negotiable invariants

- **Run** — one execution of `suite.py --lane {smoke,full}` in one VM.
- **Stage** — a host-invoked unit of work with a name, a command line, a hard timeout, and an exit code
  (`01-provision`, `02-install`, `03-run`, `04-rerun`, `05-features`, `99-teardown`).
- **Assertion** — a single observable fact checked by a command, with a pass/fail, not a narrative.
- **Capability** — the stable name of a behaviour (`tunnel.grafana`, `tekton.setup-pipelines`). **The test
  matrix and the harness's command table are keyed by capability, never by CLI name**, because FIN-62
  renames `kmt tunnel-grafana` → `kmt tunnel grafana` with no alias. A rename is then one row of a mapping
  table, not a rewrite of the suite.
- **Artifact directory** — `~/.kmt-e2e/runs/<UTC-time>-<lane>-<sha7>/` written by the harness.
- **Guest command** — the harness's only execution primitive: one hardened `ssh` invocation built from the lease IP
  that `vm.ssh_command` (from `virt-runner create --json`) contains — hardened, because the raw
  `vm.ssh_command` aborts on a reused DHCP lease (FR-12, research §3.1 / F5). One primitive, per-command exit code,
  stdout and stderr. (`virt-runner ssh` is interactive-only by design — `cmd_ssh.py` takes no command
  argument — and `--script` pipes the script over stdin, so it can carry no arguments or environment, which is
  why `--script` is reserved for the single provisioning stage.)

**Invariants (each is itself a requirement, because the old suite violated all four):**

- **INV-1 — Never touch the developer's checkout.** The tree is delivered to the guest read-only in spirit
  and *copied* before `kmt initialize`. Verified: after a full run, `git status --porcelain` on the host
  checkout is empty, and no `.git/git-crypt`, `gpg_*`, `git_crypt_symmetric.key`, `deployer_ssh_key*` or
  `platform_config/dev/**` file exists that was not there before the run.
  *(Mechanism note: virt-runner `--mount` is read-write and UIDs pass through, so the guest copy is
  mandatory, not advisory. Confirmed: host `git status --porcelain` was identical before/after a run that
  copied 24 untracked entries plus the 5 secret-ish files.)*
  **The copy is the working tree as-is, dirty state included** — the uncommitted change under test is the
  point of the loop, so the harness must not `git stash`, `git archive`, or refuse to run on a dirty checkout.
  **One sanctioned mutation inside the guest copy** *(research §3.2 / F10)*: `kmt initialize` refuses to run when
  `platform_config/dev/static.encrypted.json` or `platform_config/dev/smoke/static.json` exist, and a working
  dev checkout has them as untracked leftovers. The harness deletes exactly those four marker files
  (`dev/static.json`, `dev/static.encrypted.json`, `dev/smoke/static.json`, `dev/smoke/static.encrypted.json`)
  before `initialize`, logs that it did so, and touches nothing else. This is the only exception to INV-1 and
  must be visible in `result.json`.
- **INV-2 — Destroy is unconditional.** `virt-runner destroy` runs in a `finally` for every VM the harness
  created, unless `--keep-vm` was explicitly passed. Verified: force-fail a stage on purpose → the run
  still exits non-zero **and** `virt-runner list` no longer contains the VM.
  *(Confirmed with a correction the harness must handle: destroy is **not** idempotent — a second destroy of
  the same VM exits 1 with `vm-not-defined`, so the `finally` treats that exact code as success and every
  other error as loud. Also, a `create` that fails at the `script` stage **leaves the VM running**, so the
  destroy path must handle a VM that exists but whose create reported failure.)*
- **INV-3 — Only stdout is parsed, and it is exactly one JSON document.** No stderr parsing. Verified: a
  run whose stage emits stray JSON-like lines on stderr still produces a valid `result.json`.
- **INV-4 — Never destroy a VM you did not create.** The harness keeps its own ownership record of the VM
  names it created in the current run and destroys only those. Verified: with a foreign VM present, the
  pre-clean step leaves it untouched.

---

## 3. Functional requirements

Format: **FR-n** — requirement. *Verify:* how a reviewer proves it without asking.

### 3.1 Harness lifecycle (FIN-80, FIN-85)

- **FR-1** — One entry point, stdlib + `subprocess` only: `python tests/e2e/suite.py --lane smoke [--keep-vm] [--vm-ram N] [--vm-disk N] [--vm-vcpu N]`. No new Python dependency. *Verify:* `python -c "import suite"` (from `tests/e2e/`) fails on nothing imported that is not in the stdlib or already in `pyproject.toml`; `grep -n "^[^#]*import " tests/e2e/*.py` lists stdlib only.
- **FR-2** — The suite is runnable by a human with no CI environment. No env var from a CI system is required; every input is a flag, a default, or read from the repo. *Verify:* `env -u CI -u CI_PIPELINE_ID -u BUILD_ID ... python tests/e2e/suite.py --lane smoke --dry-run` reaches the preflight stage.
- **FR-3** — No stage may prompt, read stdin, or gate on `kubectl config current-context`. *Verify:* `grep -rn "read -p\|current-context" tests/` returns nothing, and a full run under `</dev/null` completes.
- **FR-4** — VM names are unique per run: `kmt-e2e-<lane>-<utc-epoch>-<pid>`. *Verify:* two runs with different pids produce different names; a run with the same name twice fails preflight with `vm-already-defined` surfaced as a readable line, not a traceback.
- **FR-5** — Before starting, the harness destroys leftovers **it owns** (from `~/.kmt-e2e/owned.json`) that match `kmt-e2e-*`, and leaves everything else alone. *Verify:* create `kmt-e2e-orphan` and `someone-elses-vm` beforehand → both gone? No: only `kmt-e2e-orphan` is gone.
- **FR-6** — Every virt-runner call goes through `--json` and the envelope is parsed; on error the harness reports `error.code`, `error.stage` and the tool name. *Verify:* a run against a host with `vm-pool` inactive exits with a line containing `pool-not-active`, and no Python stack trace.
- **FR-7** — Exit code is 0 only if every assertion passed; otherwise 1, with a per-assertion summary printed last. *Verify:* inject a failing assertion (an impossible predicate, not a fake VM) → exit 1, summary names the assertion.
- **FR-8** — Each stage has an explicit timeout; a stage that exceeds it is reported as `timeout:<stage>` with the elapsed budget, not as a generic hang. *Verify:* set `PROVISION_TIMEOUT=1s` → `timeout:01-provision`.
- **FR-9** — `--keep-vm` suppresses destroy *and* prints the exact `virt-runner ssh <name>` command plus the stage that failed. *Verify:* a failing stage with `--keep-vm` leaves the VM listed by `virt-runner list`.
- **FR-10** — All 3 legacy `tests/*.sh` scripts are removed by the end of Wave T; nothing in `docs/` instructs the reader to run them. *Verify:* `ls tests/*.sh` empty and `grep -rn "test_installation.sh\|reset_configuration.sh" docs/ README.md` empty.
- **FR-11** — The documented human entry point is the suite itself: `uv run python tests/e2e/suite.py --lane smoke`
  (and `--lane full`). **No `Makefile` is introduced** — the repo has none, and two aliases for one command
  are not a reason to add a build system. *Verify:* the command runs from the repo root and is the command
  printed in `docs/README_DEVELOPMENT_FLOW.md`; `ls Makefile` is absent (or, if one ever exists, `make -n e2e-smoke`
  must resolve to exactly this command and nothing else).
- **FR-12** — Every command executed in the guest goes through the single **Guest command** primitive, which
  composes the hardened ssh line below — **not** the raw `vm.ssh_command`, which aborts on a reused DHCP lease
  with `REMOTE HOST IDENTIFICATION HAS CHANGED` (observed during research):
  ```bash
  ssh -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$run_dir/known_hosts" -o ConnectTimeout=10 \
      "$user@$ip" '<command>'
  ```
  per-command exit code, stdout, stderr (verified: guest `exit 42` → host exit 42; streams separate). Multi-line
  stages go in as `ssh … 'bash -s' < stage.sh`. `dns_name` is unreliable (`<name>.default` only); the lease IP
  is the handle. `virt-runner ssh` is never used programmatically. *Verify:* `grep -rn "virt-runner ssh" tests/e2e/`
  empty; the primitive exists once, contains `UserKnownHostsFile` + `BatchMode=yes`, and stages do not build
  their own ssh lines. *(research §3.1 / F5)*
- **FR-13** — `--script` is used for exactly one stage, `01-provision.sh` (one-shot, parameter-free). All later stages run per-command through FR-12, because a `--script` receives no arguments and no environment. *Verify:* `grep -c "\-\-script" tests/e2e/suite.py` equals 1.
- **FR-14** — Command names are resolved through a single capability→argv table. Until FIN-62 lands, rows carry the current flat names plus a `NEEDS-FIN-62` marker, so a rename touches the table and no stage. *Verify:* `grep -rn "NEEDS-FIN-62" tests/e2e/` returns every row that still needs the grouped name; no stage contains a literal `kmt ` command string.

### 3.2 Provisioning (FIN-81)

- **FR-20** — Provisioning installs single-node k3s and the prerequisite set, then makes the kubeconfig usable.
  *(Confirmed with two corrections — `yq` and `python3-pip` were missing from this list and are required by
  `KUBEMENTAT_DEPENDENCIES`; `jq` is already in the cloud image and must not be installed.)* Required set:
  `helm`, `helmfile` (**pin 1.8.2** — the `download-helmfile.sh` helper 404s on 2.x), `helm-diff`, `tkn`, `uv`
  (lands in `~/.local/bin`, **not on the non-interactive ssh PATH** — symlink to `/usr/local/bin` or export PATH),
  `git` (preinstalled), `git-crypt` (apt), `yq`, `python3-pip`. k3s needs **no** host/guest preparation
  (`INSTALL_K3S_*` flags, `apt upgrade`, kernel modules) — measured 9.6 s to `Ready`.
  **kubeconfig:** `/etc/rancher/k3s/k3s.yaml` is `0600 root:root`, and `KubernetesUtils.__init__` calls
  `load_kube_config()`, so *even `kmt initialize` fails* without the fix. `chmod 644` is reset by every k3s
  restart — write `write-kubeconfig-mode: 644` to `/etc/rancher/k3s/config.yaml` and restart. *Verify:*
  `kubectl get nodes -o json` reports exactly one `Ready` node; `kmt --help` runs as `ubuntu` without sudo
  and without `KUBECONFIG`; `stat -c '%a' /etc/rancher/k3s/k3s.yaml` is `644` **after** a `systemctl restart k3s`.
- **FR-21** — **No hardcoded storage class.** The harness reads the cluster's default StorageClass once and
  passes it through; the suite must not contain the string `hostpath`.
  *(Rewritten — the original form was **refuted**: k3s `local-path` is `WaitForFirstConsumer`, so a probe PVC
  alone stays `Pending … waiting for first consumer` **forever**, even on a healthy cluster. Bound happens
  ~9 s after a consumer pod is scheduled, never on its own.)* So: discover the SC from
  `storageclass.kubernetes.io/is-default-class`, create `kmt-e2e-probe`, and **schedule a consumer pod that
  mounts it** — the assertion is the pod reaching `Running` (which proves the claim bound), not the PVC's own
  phase in isolation. Also: `grep hostpath tests/` is not sufficient protection —
  `platform_config/dev/smoke/static.json` hardcodes `MYSQL_VOLUME_STORAGE_CLASS: hostpath` and a fresh
  `initialize` overwrites it from the passed `KUBERNETES_DEFAULT_STORAGE_CLASS`, so the requirement is about
  the *passed value*, not just the harness's text. *Verify:* consumer pod `Running` within 120 s;
  `result.json` records the discovered SC name (expected `local-path`); `grep -rn "hostpath" tests/` empty.
- **FR-22** — Fail fast on capacity. *(Message confirmed against a real node.)* On an `Evicted` pod, an
  unschedulable `Pending` pod, or a PVC stuck `Pending`, abort within 120 s with "increase --vm-ram/--vm-disk",
  matching the node's own text: `0/1 nodes are available: 1 Insufficient memory.` plus the `FailedScheduling`
  event. Not a generic timeout. *Verify:* a run with a deliberately tiny `--vm-ram` produces that message;
  eviction (not just scheduling) is exercised once a lane stage can provoke it. *(research §7 gap 2 / F17 — eviction
  messages were not reproduced, only scheduling text.)*
  **Preflight must check `df`**, not `virsh pool-info`: the pool reported 92 GiB free while `df` showed 49 G
  (sparse qcow2 accounting). Budget ≥ 20 G host-free per smoke VM. *(research F2)*
- **FR-23** — Every PVC that kubementat creates during a run must reach `Bound`. Because the SC is
  `WaitForFirstConsumer`, read this as: no claim is left `Pending` **after its consumer has been scheduled** —
  a claim that is still `Pending` while its workload is `Running` is the failure. *Verify:* at end of
  `02-install`, `kubectl get pvc -A --field-selector=status.phase!=Bound` must be empty for every claim whose
  workload exists; claims deliberately waiting for a consumer are reported separately, not as failures. This
  is the strong assertion for bug class 1 **and** a cheap detector for chart/values drift.
- **FR-24** — The guest image is **pinned and cached**: the harness passes `--distro ubuntu --release resolute`
  (Ubuntu 26.04) explicitly instead of relying on virt-runner's profile default, records the resolved image
  path in `result.json`, and must not force a re-download per run. *(Confirmed; warm create is 16–30 s, well
  inside the 60 s target.)* **Caveat the harness must not paper over:** on a cache *hit*, virt-runner reports
  `verification: "skipped", verified: false` — cached images are trusted, never re-verified, so cache
  integrity rests entirely on the first download. Record `image.cache_hit`/`verified` in `result.json` so a
  stale or corrupt cache is visible in the artifact. *Verify:* the flags appear in the create argv (grep the
  harness for `--release resolute`); a second run reports create time < 60 s; changing the pin without
  updating `result.json` fails a test. *(research §7 gap 1 / F15, §4 footnote 1 — cold-cache timing deliberately untested.)*

### 3.3 Install + run (FIN-82)

- **FR-30** — The suite's fixed identity is **environment `dev`, team `smoke`** in both lanes (`kmt initialize dev smoke`); a per-lane team name is deliberately not used, since runs are serial on one VM. `kmt initialize → install → tekton-setup-pipelines → tekton-setup-triggers → setup-docker-registry-access → setup-secrets → tekton-run-pipeline <hello-world>` executes in that order, with the documented dummy git values, and each command exits 0 on the **first** attempt. *Verify:* stage exit codes + PipelineRun status; `grep -rn "dev1" tests/e2e/` empty.
  **Two findings the harness must carry into the product:** (a) `kmt install` currently **prompts** —
  `input("Do you really wish to install kubementat ... (yes/no): ")` — so piping `yes` works but violates FR-3's
  promise in spirit; the clean fix is a `--yes`/`--force` flag on `install` (FIN-11 already carries this contract
  for `uninstall`, so `install` should get the same shape). (b) The helmfile template `requiredEnv`s
  `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`, which `initialize` does **not** write, so the harness must export
  both dummies (add to the FR-35 dummy list). Also note the harness must delete the 4 `initialize` marker files
  first (INV-1 exception), or `initialize` fails deterministically. **This requirement cannot pass until FIN-88
  is fixed** — install dies in `helm repo add` before anything deploys.
- **FR-31** — **Re-run assertion (the D4 detector).** Every mutating capability is executed exactly twice, in the same VM and same cluster state, and the second run must (a) exit 0 and (b) not change the observable resource set: `kubectl get <kind> -A --no-headers | sort | sha256sum` identical between the two passes, for namespaces, roles, rolebindings, serviceaccounts, secrets, pipelines, tasks, eventlisteners, triggers, triggerbindings. *Verify:* the assertion is implemented as one function applied to the capability list, not 15 copies; deliberately reintroducing an "already exists" crash (e.g. a name error in the create path) makes it fail.
- **FR-32** — The hello-world `PipelineRun` reaches `Succeeded`, verified by polling the PipelineRun's status condition via `kubectl get pipelinerun … -o jsonpath`, never by scraping `tkn pipelinerun logs` or a fixed `sleep`. *Verify:* the poll is a bounded loop with a predicate; no `sleep` between kubectl calls (NFR-6).
- **FR-33** — Smoke installs the cheapest meaningful helmfile set by passing `--helmfile-installation-group dashboard`, so exactly one non-essential release is deployed per smoke run. **No new helmfile group is introduced** (D5). *Verify:* the flag appears in the smoke stage's argv; `helm list -A` after install contains exactly the tekton-adjacent releases plus the dashboard release.
  The group is fine, the chart in it is not: `kubernetes/dashboard` is archived (2026-01-21) and its repo 404s,
  so this slot must point at a maintained dashboard — Headlamp being the direction of travel, decided by the
  owner, with the exact chart/version confirmed by the spike inside FIN-89 (§8 Q9). Until that lands, the
  harness must not "temporarily" point this stage at `group=standard`: that doubles runtime and breaks the
  < 25 min target. The selected group and release list must be recorded in `result.json` so a substitution is
  visible after the fact.
- **FR-34** — Full lane installs with `group=standard`, then applies `routing` and `dashboard` (existing groups), then continues. *Verify:* stage argv records the three group filters.
- **FR-35** — Nothing in the suite passes a real credential. `AUTOMATION_GIT_URL`, `AUTOMATION_GIT_SERVER_*`, registry URLs, webhook secrets are dummies generated by the run. *Verify:* `grep -rn "github.com\|gitlab.com\|julianweberdev" tests/` empty.

### 3.4 Feature coverage (full lane, FIN-84)

Each row below is one stage. Assertions are deliberately few and strong.

- **FR-50 (tunnels)** — For each tunnel capability that targets an installed component (`grafana`, `tekton`
  dashboard, `mysql` in the full lane; `polaris`/`vault` are negative cases, see §6), the harness starts
  the tunnel with a bounded timeout, then from inside the guest curls the bound local port. The pass
  condition is **the response body contains that component's signature string** (e.g. Grafana's login page
  markup), not merely HTTP 200 — a 200 from the wrong pod must fail. *Verify:* the signature table is one
  dict in the harness; pointing a tunnel's selector at the wrong pod produces a body mismatch, which is
  the exact failure mode class 4 describes.
- **FR-51 (namespace-driven breakage)** — One tunnel is exercised after the harness renames the component's
  deployment namespace in the guest's `static.json`. It must still reach the right pod. This is the E2E
  proof of FIN-07's payoff and the only test that catches a hardcoded namespace in a live cluster.
  *Verify:* the stage exists in the full lane; it fails if a hardcoded namespace string is reintroduced
  into the code path.
- **FR-52 (config resolution)** — Two negative tests: (a) with the team's `static.json` removed, the
  command fails with an error that names the missing file (no `UnboundLocalError`, no traceback) — the D6
  assertion; (b) with a sentinel value written only into team `dev2`'s config, `kmt … --team dev1` does not
  read it — the D9 assertion. *Verify:* both stages assert on the error string / absence of the sentinel,
  not on exit code alone.
- **FR-53 (mysql/redis)** — ~~`install_mysql_helm_chart` / `setup_mysql_databases` / `install_redis`
  behaviour is driven **directly through `kmt` in the guest** (D4 decision: not via the CI pipeline pod). Pass
  condition: a mysql pod reaches `Ready`, a database exists that a one-off query can list, and a redis pod
  is `Ready`. Not "the command exited 0". *Verify:* three observable facts in `result.json`~~.
  **FR-53 is un-implementable as written and its three lane asserts (`mysql-pod-ready`,
  `mysql-database-listable`, `redis-pod-ready`) were removed 2026-10-27**, not because the measurement
  changed but because the thing measured is gone: kubementat ships no working backing-service install
  until the standard-helm-chart path lands. The *shape* of the requirement survives — assert on three
  observable facts, never on an exit code — and the replacement ticket inherits it. `setup_mysql_databases.sh`
  itself went with the Task that ran it on 2026-10-29 (`0cf22b7`) and no longer exists to be in or out of
  any FR's scope. The shape this requirement argued for is unchanged, though: assert on observable facts
  and not on an exit code.
- **FR-54 (dashboard + login)** — `kmt login-kubernetes-dashboard` prints a token that authenticates: the
  harness uses it against the API server and asserts a `2xx` for a namespace listing. A printed-but-invalid
  token is a failure.
  **Note for FIN-89:** this assertion is deliberately chart-agnostic — the token is a ServiceAccount token and
  the check is against the API server, not against a dashboard URL — so swapping the retired
  `kubernetes/dashboard` chart for a maintained one (Headlamp direction, §1.8 of the implementation plan) must
  not weaken it. The chart-specific part is the `svc/kubernetes-dashboard-kong-proxy` port-forward inside the
  product script, which is product code (FIN-89), not a suite concern.
- **FR-55 (user management)** — Create team account → the emitted kubeconfig block resolves a service
  account whose `kubectl auth can-i` answer is the documented one; create read-only account → the
  corresponding `can-i` must be `no` for a mutating verb; delete account → the namespace/SA/token secret
  are gone. *Verify:* the three `can-i` checks are the assertions; the kubeconfig text is parsed, not
  eyeballed.
- **FR-56 (grafana)** — Create grafana accounts → they exist via the Grafana API using credentials from the
  config the run generated. *Verify:* one API read per created user.
- **FR-57 (cleanup job)** — The PipelineRun cleanup CronJob exists and is scheduled; after triggering it
  manually, succeeded runs older than the retention filter are gone. *Verify:* count before/after.

### 3.5 Teardown and residue (FIN-83)

- **FR-60** — After `kmt uninstall` (run **twice**, the second time against an already-partially-clean
  cluster), the cluster must be a subset of the post-provision baseline:
  - `kubectl get ns -o jsonpath` ⊆ baseline namespaces (nothing extra),
  - `kubectl get crd -o json` contains **zero** `*.tekton.dev` and zero `*.triggers.tekton.dev` CRDs,
  - `kubectl get clusterrole` contains no `helm-deployer-cluster-role`,
  - `kubectl get pvc -A` shows no kubementat-created claim,
  - `helm list -A` shows no kubementat release.
  *Verify:* the before/after namespace snapshots are stored as JSON in the artifact dir and the diff is
  computed by the harness, not by a human. This is what proves FIN-11.
- **FR-61 ("remove the whole building")** — Per D9, `kmt uninstall` must remove **all** kubementat-owned
  namespaces and **all** PVCs belonging to the stack. Deleting the namespaces is sufficient to make the PVC
  *objects* go away (Kubernetes deletes namespaced objects along with the namespace), so the assertion stays:
  `kubectl get pvc -A` shows zero kubementat claims, `kubectl get ns` ⊆ post-provision baseline.
  The legacy pattern must not survive: `tests/uninstall.sh` hand-deleted three PVCs by hardcoded name/namespace
  (one line, `kubectl -n storage-prometheus-alertmanager-0 | true`, is not a working command at all), and
  `uninstall_helm_deployment.sh:57-58` only *prints advice* telling the operator to delete the PVC themselves.
  Advice-only is a requirement failure, not a documented limitation. **That legacy pattern is closed in code**:
  `uninstall_helm_deployment.sh` is deleted, and its replacement (`HelmUtils.uninstall_release()` behind
  `kmt component uninstall`, FIN-19 / `finish-python-migration.md` §1.3b row 6b — the command was
  `kmt helm-component-uninstall` when this row was written and `d5f321b` renamed it onto a group, with no
  alias, so the mechanism named here is unchanged) deletes the claims the
  release made, by the `app.kubernetes.io/instance` label, in the same command. What is still owed is the
  assertion below, on a real cluster — the mechanism is proven against fake `kubectl` only.
  *Verify:* FR-60's residue assertion is the check. If a correct harness goes red on it, that is an authorised
  **product** finding, not something to silence with a harness-side `kubectl delete pvc`.
- **FR-62 (single-component removal)** — `kmt component uninstall <name>` deliberately leaves the namespace
  standing, so that is where a claim ticket actually survives. Exactly two outcomes are allowed: it also deletes
  the claim its component created, **or** its own printed output states that removing stored data is the
  operator's action. Silently keeping the disk usage is the excluded outcome.
  *Verify:* install one component, uninstall it, then assert either zero claims in that namespace or the exact
  advice line. No third behaviour may pass.
- **FR-63 (confirmation contract)** — Today `uninstall_kubementat.sh:29-35` has a `read -p` loop and *no* flag to
  skip it, so the command cannot run unattended at all — which is why the old suite needed a human.
  `kmt uninstall <env>` without `--force` must: print the cluster it is about to wipe (context name and node
  list, read from the cluster rather than guessed from kubeconfig), state that the deletion is irreversible,
  read one confirmation from stdin, and delete nothing unless the answer is yes. With `--force` it deletes
  immediately and **never reads stdin**. If stdin is not a terminal and `--force` is absent, it must fail closed
  with a message naming `--force` — never hang, never delete.
  *Verify:* (a) `kmt uninstall dev --force` → exit 0 and FR-60's residue list empty; (b) `echo n | kmt uninstall dev`
  → non-zero exit and `kubectl get ns` unchanged; (c) `kmt uninstall dev < /dev/null` → non-zero exit naming
  `--force`, under 5 s, having deleted nothing.
  *Dependency:* FR-63 is a **product** requirement owned by FIN-11, not harness work — the harness only asserts it.
- **FR-64** — The harness always passes `--force`, so no E2E stage can block on a prompt (FR-3's promise). The
  prompt path runs exactly once, as FR-63(b)'s negative test.
  *Verify:* `grep -n "kmt uninstall" tests/e2e/` shows `--force` on every invocation except that one stage.
- **FR-65** — Uninstall never requires a git repo or a mounted tree: after teardown the guest directory is
  deletable with `rm -rf` and no further command works. *Verify:* teardown stage runs after `rm -rf` of the
  checkout is attempted.

### 3.6 Tekton `v1` assertions (Wave 7 support)

- **FR-60v** — The suite asserts behaviour, not text: for at least one `Task` and one `PipelineRun`, the
  resource must be accepted (`kubectl apply` exit 0) **and** actually execute to a terminal `Succeeded`.
  A static grep of the manifests is FIN-71's job and is explicitly *not* a substitute here.
  *Verify:* the assertion is on the PipelineRun condition, so a `v1` manifest that applies but whose
  controller silently drops the run fails the lane.
- **FR-60w** — The suite must contain **no** `v1beta1` string for the `tekton.dev` group anywhere in its
  own commands or fixtures (Triggers templates stay on their own API and are not exercised for version).
  *Verify:* `grep -rn "tekton.dev/v1beta1" tests/` empty.

---

## 4. Test matrix

Keyed by capability. Command names are shown in their **current** flat form; FIN-62 renames land in the
mapping table, and only that table changes. Lane legend: `S` = smoke (run manually per ticket), `F` = full
(run manually when someone chooses to, never scheduled),
`S+F` = both.

### 4.1 The 19 `kmt` commands

| # | Capability | Command | Lane | Strong assertion | Cleanup check |
|---|---|---|---|---|---|
| 1 | `config.initialize` | `kmt initialize <env> <team>` | S+F | Exits 0 and the initialized tree is self-consistent: a follow-up `kmt install` needs no further manual edits. Key files exist *inside the guest copy only* | INV-1: host `git status` clean |
| 2 | `install` | `kmt install <env> <team> [--helmfile-installation-group G]` | S+F | Exit 0, zero PVCs Pending (FR-23), `helm list -A` contains the expected releases, one node `Ready` | FR-60 residue set |
| 3 | `uninstall` | `kmt uninstall <env> --force` | S+F | FR-60 residue set (namespaces, CRDs, clusterrole, **all PVCs**) plus FR-63's three confirmation cases | the check *is* the cleanup check |
| 4 | `secrets.setup` | `kmt setup-secrets <env> <team>` | S+F | The named Secret exists in the team namespace with the keys `static.encrypted.json` declares, and `kubectl get secret -o jsonpath` round-trips the value | Secret gone at teardown |
| 5 | `registry.access` | `kmt setup-docker-registry-access <env> <team>` | S+F | The imagePullSecret exists and is attached to the team's pipeline `ServiceAccount` (`kubectl get sa -o jsonpath` non-empty) | SA/secret gone |
| 6 | `dashboard.login` | `kmt login-kubernetes-dashboard <env>` | S+F | FR-54: printed token authenticates against the API server | token secret deleted |
| 7 | `tunnel.pod` | `kmt tunnel-pod <env> <ns> <pod> <ports>` | F | Port bound in guest + TCP connect succeeds to the forwarded port | process reaped, port free |
| 8 | `tunnel.grafana` | `kmt tunnel-grafana <env>` | F | FR-50 signature: response body is Grafana's, not another 200 | port free |
| 9 | `tunnel.tekton` | `kmt tunnel-tekton <env>` | F | FR-50 signature | port free |
| 10 | `tunnel.vault-ui` | `kmt tunnel-vault-ui <env>` | S | **Negative:** exits with a clear "component not installed"-class error within 30 s and binds nothing, instead of hanging | port free |
| 11 | `tunnel.polaris` | *(absent — D3)* | S | CLI-surface test (FIN-03) asserts the command exists; E2E asserts only the negative case above. Excluded from UI coverage (§6) | — |
| 12 | `tekton.run-pipeline` | `kmt tekton-run-pipeline <env> <team> <run.yml>` | S+F | PipelineRun `Succeeded` via bounded poll (FR-32) | run deleted |
| 13 | `tekton.list` | `kmt tekton-list <env> <team>` | S | Exit 0 and stdout lists at least the hello-world pipeline; output parses (no traceback, no `None`) | read-only |
| 14 | `tekton.cleanup-pipeline-runs` | `kmt tekton-cleanup-pipeline-runs <env> <team> --filter succeeded` | F | Succeeded runs older than the filter are gone; a *failed* run that should be kept is still present | n/a |
| 15 | `tekton.setup-pipelines` | `kmt tekton-setup-pipelines <env> <team>` | S+F | Pipelines + Tasks exist in the team namespace and **one of them runs** (FR-60v) | CRDs/resources gone |
| 16 | `tekton.setup-triggers` | `kmt tekton-setup-triggers <env> <team>` | S+F | EventListener/TriggerBinding/TriggerTemplate exist, the RBAC quartet exists (1 ClusterRole + 1 CRB + 2 Roles + 2 RoleBindings + 2 SAs), and the EventListener pod is `Ready` | same set gone |
| 17 | `tekton.uninstall-pipelines` | `kmt tekton-uninstall-pipelines <env> <team> -y` | F | Team pipeline namespace gone, CRDs gone, and running it twice exits 0 | the check *is* the cleanup |
| 18 | `helm.helmfile-apply` | `kmt helmfile-apply <env> --helmfile-label-filter …` | F | Release present after apply, and applying the same filter twice changes nothing (`helm history` revision count stable) | release removed |
| 19 | `ci.run-container` | `kmt run-ci-container <env> <ns>` | excluded | see §6 — not exercised |
| 20 | `script.run-script` | *(to be deleted, FIN-46/FIN-60)* | excluded | see §6 |

That is 19 registered commands today; rows 11/19/20 are deliberately negative or excluded.

### 4.2 The 55 scripts as behaviours

Every script must end the migration either covered below or listed as an explicit removal. "Weak-OK"
means the assertion is *exit 0 + stdout parses + no traceback* — deliberately weak, because these are
read-only diagnostics and the cost of a strong assertion is a maintenance tax with no bug-catching value.

| Family | Scripts | Behaviour verified | Lane | Assertion |
|---|---|---|---|---|
| components install/uninstall | `install_tekton`, `install_routing`, `install_kubernetes_dashboard`, `install_kubementat`, `uninstall_kubernetes_dashboard`, `uninstall_helm_deployment`, `helmfile_apply` (7 — `uninstall_helm_deployment` is not a script any more: its removed half is `kmt helm-component-uninstall` (FIN-19), so this row's "removed → absent" step goes through that command and not through `run-script`. `install_linkerd`, `uninstall_linkerd` and `generate_linkerd_certs` are deleted with the mesh, 2026-10-28, so there is nothing here to install or uninstall) | installed → present → removed → absent | S (no mesh is installed any more, so no Linkerd half) | present/absent via `kubectl get`; FR-23 for PVCs |
  *(2026-10-29: `helmfile_apply` is **deleted**, so this row has one fewer member. It was a script no code execed: the helmfile apply that `kmt install`, `kmt routing install` and `kmt helmfile-apply` run is assembled in Python by `HelmUtils.helmfile_apply` (`src/kubementat/helm_utils.py:75`), and the deletion is held by `tests/test_the_helmfile_argv_is_built_in_python_and_the_script_stays_deleted.py`. Drive this row through those commands, not through `run-script`.)*
| tekton generate/apply | `generate_deploy_pipeline_from_template`, `generate_pipeline_run`, `generate_trigger_config_from_templates`, `setup_triggers`, `uninstall_pipelines`, `configure_secrets`, `configure_docker_registry_access`, `run_pipeline`, `list_tekton_resources`, `setup_tekton_pipelinerun_cleanup_job`, `create_team_account.py` (11 → **8** — **update 2026-10-28: 2 of the named generators are deleted and 3 of the family is already commands.** `generate_deploy_pipeline_from_template` (FIN-22) is `kmt tekton-generate-deploy-pipeline` and `generate_trigger_config_from_templates` (FIN-23) is `kmt tekton-generate-triggers`, so the generate half of this row is driven through the CLI now; `setup_triggers` (FIN-13), `uninstall_pipelines` (FIN-11), `configure_secrets` / `configure_docker_registry_access` (FIN-38/FIN-10) and `setup_tekton_pipelinerun_cleanup_job` (FIN-21) are commands too, and `create_team_account.py` is `kmt users create-team-account`. What is left as a script: `generate_pipeline_run`, `run_pipeline` (the `tekton_ci/tasks/run-tekton-pipeline.yml` half, which needs FIN-50) and `list_tekton_resources` — which is `kmt tekton-list` and is listed here as a command) | generated YAML applies **and runs** | S+F | FR-32/FR-60v + FR-57; generated files land under `platform_config/{env}/{team}/triggers/` (triggers) and `platform_config/{env}/{team}/tekton/pipelines/` (pipelines — **moved 2026-10-28**, out of `tekton_ci/pipelines/`). **What no unit suite can give this row, and a lane has not yet**: the four generated trigger manifests and the generated pipeline being *accepted and started* by a real cluster — the 20 FIN cases in `tests/test_generated_deploy_pipelines_and_trigger_configs_are_written_where_they_are_read.py` compare manifests against bash's own bytes and against the config that filled them, never against an API server; and the generator's output being applied out of the *new* pipeline location with its `templates/` directory not applied. |
| secret_management | `create_secret_from_json`, `create_k8s_secret_for_ssh_key`, `generate_ssh_key`, `retrieve_token_for_service_account`, `base64_library` (5 → 3 — **update 2026-10-28: 2 of the 5 are deleted.** `retrieve_token_for_service_account` is `kmt service-account-token` (FIN-36), so this row's token half is read through that command and not through `run-script`, and `create_k8s_secret_for_ssh_key` is deleted without a command of its own: its act is `KubernetesUtils.create_or_replace_secret()` driven by `kmt setup-secrets` over the keys `SSH_DEPLOY_KEYS` declares, so the row is now driven through that command for a configured key and has no member for the one-key-file case. `base64_library` stays as the library `create_secret_from_json` and `generate_ssh_key` still `source`, and both survivors' tickets are in the migration plan's §7) | secret created, decodes back to the input | S+F | `kubectl get secret -o jsonpath` → base64-decode → byte-equal to input |
  *(2026-10-29: `generate_ssh_key` is **deleted** — 3 → 2. `CryptoUtils.generate_ssh_key_pair` behind `kmt initialize` generates the deployer keypair, and the only live call site guards the call with `os.path.exists`, which is the check the script had. `base64_library` still stays, `source`d by `create_secret_from_json` alone; guard: `tests/test_the_deployer_ssh_keypair_is_written_by_the_library_and_the_script_stays_deleted.py`.)*
| tasks | `install_mysql_helm_chart`, `install_redis_helm_chart`, `setup_mysql_databases`, `install_nginx_reverse_proxy` (4) | backing services reachable | F | FR-53, driven through `kmt` in-guest (D4). **Update 2026-10-27: 3 of the 4 are deleted** (the three `install_*`), so this family had one member (`setup_mysql_databases`) and no lane row, and FR-53 had nothing to drive. **Update 2026-10-29: 4 of 4** — the last one went with `tekton_ci/tasks/run-automation-script-for-environment.yml` (`0cf22b7`), so the family is empty. See the FR-53 strike-through. |
| user_management | `create_team_account`, `create_team_account_read_only`, `delete_team_account`, `create_grafana_accounts`, `create_grafana_read_only_account`, `user_management_helpers`, `grafana_helpers` (7) | accounts + permissions | F | FR-55 + FR-56 |
| utilities/kubernetes | `list_pvc_usage`, `create_ingress_yml`, `retrieve_node_addresses`, `view_cluster_resource_versions` (7 → 4 — `cluster_capacity`, `analyze_deployment` and `retrieve_deployment_docker_image_versions` are not scripts any more: FIN-41/42 deleted them in favour of `kmt cluster-capacity`, `kmt analyze-deployment` and `kmt pod-images`, so this row's members are read through those commands and not through `run-script`) | diagnostics | F | Weak-OK; `create-ingress` additionally: generated YAML applies and produces a `Ready` ingress |
| utilities/helm | `get_latest_version_for_helm_chart`, `ssh_into_helm_deployment`, `tail_logs_for_helm_deployment` (3 → 0 — **none of them is a script any more**: FIN-44/45 deleted all three in favour of `kmt deploy-ssh`, `kmt deploy-logs` and `kmt helm-version`, so this row's members are read through those commands and not through `run-script`. The row's *assertion* half is still the right half — a version string that is not empty is the whole point of `helm-version`, and the command that replaced the pipeline refuses to answer at all rather than answering blank) | chart/deployment introspection | F | Weak-OK + "returns a version string that is not `null`/empty" |
| utilities/tunneling | `open_grafana_tunnel`, `open_tekton_dashboard_tunnel`, `open_polaris_tunnel`, `open_vault_ui_tunnel`, `open_pod_tunnel`, `open_mysql_tunnel` (6 — `open_linkerd_dashboard_tunnel` is deleted with the mesh) | FR-50/FR-51 | F | body-signature, not HTTP-200 |
| root | `uninstall_kubementat.sh` (1) | FR-60 | S+F | residue diff |
| out of scope | `tests/*.sh` (3) — deleted, not migrated (FIN-85/§2.4) | — | — | — |

10+11+5+4+7+7+3+7+1 = 55.

---

## 5. Harness exit criteria — what "the suite itself works" means (FIN-80…FIN-89)

Each ticket is done when its **negative** test also passes; a harness that has never been observed to fail
on purpose is not trusted.

| ID | Done when | Negative test that must pass |
|---|---|---|
| **FIN-80** skeleton | A run with `--lane smoke` performs create→stages→assert→destroy, prints per-assertion results, exits 0/1 correctly, writes `result.json` | Force-fail stage 2 → exit 1 **and** VM destroyed (INV-2) |
| **FIN-81** provision | k3s `Ready` in a fresh guest, prerequisites present, SC discovered from the cluster, probe PVC `Bound` | Boot with `--vm-ram` too small for the probe → the "increase --vm-ram/--vm-disk" message (FR-22). *Measured achievable: 9.6 s to `Ready`, no prep needed — so a slow provision is a harness bug, not environment noise.* |
| **FIN-82** install+run | `initialize→install→…→hello-world Succeeded` green from a pristine VM, ≥3 consecutive runs | Point `AUTOMATION_GIT_URL` at a nonexistent repo and assert the run still passes (it must, smoke uses dummy values). **Blocked on FIN-88 landing and on the FIN-89 decision** — no `kmt install` can finish without the first, and the lane's cheap release is unresolved until the second. That is the honest status of this row, not a harness failure |
| **FIN-83** teardown | FR-60's residue list is empty after uninstall (namespaces, CRDs, clusterrole, **all PVCs**), asserted from stored snapshots, and FR-63's three confirmation cases pass | Break uninstall on purpose (e.g. skip a namespace deletion) → the residue diff must turn red |
| **FIN-84** features | All full-lane stages present, full lane < 120 min | A tunnel pointed at the wrong pod → body-signature failure, not a timeout |
| **FIN-85** unattended | No `read -p`, no `current-context` gate, `tests/*.sh` deleted, run with `</dev/null` | Run under a cron-like `env -i` (minus PATH) → must not require anything interactive |
| **FIN-86** entry point | `uv run python tests/e2e/suite.py --lane smoke` and `--lane full` work from the repo root; KVM prerequisites documented incl. the libvirt/kvm re-login gotcha. **No `Makefile` is introduced** (the repo has none) and no CI/hook is wired to a lane | Run on a host without `virt-runner setup` → readable preflight failure naming the fix |
| **FIN-87** artifact | Artifact dir contains `result.json` (git SHA, lane, per-stage exit codes, assertion list, SC name, timings), `stages/*.log`, `snapshots/ns-*.json`, `virt-runner/*.json` | A failed run's directory must be self-sufficient to diagnose the bug *without re-running it* |

The suite is not "done" — it is **operational** when: 3 consecutive green smoke runs, 1 green full run (run
by whoever thought it was worth it, per D8), and 1 deliberately-broken build detected as red by the smoke
lane.

---

## 6. Non-functional requirements

### 6.1 Runtime budget (excludes queue time, which is reported separately)

These numbers mix **measured** and **estimated** — the measured ones come from the research run, the rest are
targets to be replaced by the first throwaway timed run (§5's operational criterion). No stage budget may be
met by a sum of sleeps.

| Stage | Smoke target | Full target | Status |
|---|---|---|---|
| VM create (warm cache) | 0:40 | 1:00 | **measured 16–30 s** |
| provision k3s + prereqs | 3:00 | 3:30 | **measured ≈ 2:00** (k3s 9.6 s · kubeconfig fix 8 s · tooling ≈63 s · `uv sync` 2.9 s · tree copy 1.4 s) |
| install | 5:00 (`group=dashboard` — pending FIN-89) | 18:00 (`standard`+`routing`+`dashboard`) | **unmeasurable until FIN-88 lands and FIN-89 is decided** |
| hello-world pipeline | 3:00 | 3:00 | estimated |
| features | — | 25:00 (mysql/redis 8, tunnels 6, users/grafana 5, other 6) | estimated |
| re-run `kmt install` + verify (D6) | — | 15:00 | estimated |
| uninstall + residue | 4:00 | 8:00 | estimated |
| destroy + artifacts | 0:30 | 0:30 | **measured 0.3 s** destroy |
| **Total / budget** | **~16 / < 25 min** | **~74 / < 120 min** | |

`create + provision + initialize` is measured at **≈ 2:00**, so the lane has ~1 minute of slack against its
3:40 sub-budget before install even starts — the install/feature numbers are where the lane actually lives or
dies, and they are the last thing that cannot be timed today.

Any stage exceeding 1.5× its target is recorded as `slow`, and the run still passes. A budget that is
merely "met by the sum of sleeps" is worthless — hence the stage-level telemetry in `result.json`.

### 6.2 Parallelism and queueing

- One lock, no pool (D3): `flock /tmp/kmt-e2e.lock` for the whole run. Two runs queue; the second prints
  `queued: waiting for <name>, Ns elapsed` and continues waiting up to a 4 h cap, then fails with
  `queue-timeout` rather than fighting libvirt.
- A run holds the lock only for the VM's lifetime; artifact writing happens after release.
- Documented ceiling (this is the accepted limitation): because nothing is scheduled (D8, decision 4), the
  only queueing is human-vs-human or human-vs-themselves — a `full` run you start blocks a later smoke run on
  the same machine for ~1 h. Mitigation is the `--lane` choice and the lock, not a scheduler.

### 6.3 VM cost

| | vCPU | RAM | Disk |
|---|---|---|---|
| smoke | 4 | 6 GiB | 25 GiB |
| full | 8 | 16 GiB | 50 GiB |

Preflight must refuse to start if free host RAM is below the lane's requirement, with a readable message —
not a `vm-create-failed`. Use `df`/`free`, **not** `virsh pool-info` (the pool lied about free space by
~43 GiB; research F2).

**Measured ceilings the lane must respect** (research §3): a 6 GiB guest's node allocates `cpu=4`,
`memory=5842 MiB`, `pods=110` — so a `standard`-group install of prometheus+grafana+loki is at the RAM
ceiling for the smoke shape and comfortably needs the full shape. Measured **real** host writes per idle
smoke VM were ≈ 16 GiB and `destroy` reclaims them exactly (92 → 137 GiB available after destroying two), so
keep ≥ 20 G free per smoke VM and destroy promptly.

### 6.4 Artifacts and retention

- Per run: `result.json`, per-stage logs (stderr only, per INV-3), namespace snapshots, the virt-runner JSON
  envelopes. **No `junit.xml`, no HTML report** — nothing consumes them while verification is manual (D8).
- Retention: failure runs kept until the developer deletes them; successful runs pruned to the last 10.
  No upload, no remote store.
- `result.json` must carry the git SHA, so a cited run cannot silently be stale.

### 6.5 Debuggability

On failure the harness appends, in the artifact dir and never on stdout: `kubectl get pods -A -o wide`,
`describe` for every non-`Running` pod, `kubectl get events --sort-by=.lastTimestamp | tail -50`,
`helm list -A`, and for pipeline failures `kubectl get pipelinerun … -o yaml`. Secrets in captured output
are redacted by a fixed key list. `--keep-vm` is the only supported interactive debug path.

### 6.6 Repeatability / flakiness policy

- Every wait is a bounded poll with a stated predicate and deadline. **No `sleep` for synchronisation.**
- Assertions are classified `infra` or `product` in the code. Only these may retry, exactly once per run:
  image download/verification, `lease-timeout`, `ssh-timeout`, pod scheduling after a node reboot. Every
  retry is recorded in `result.json`; a lane that passes only after a retry is flagged `flaky=yes`.
- Product assertions (exit codes, residue, pipeline `Succeeded`, body signatures) never retry. A retry
  there is a bug in the suite and the lane's output must not be cited as a ticket's evidence.
- Two consecutive red runs of the *same* lane on the *same* SHA means: stop the lane, file the finding, and
  decide which side is wrong. Suites do not get "tolerated flakes"; they get a named owner.
- Stale-image-cache guard: `rm -rf ~/vm-images/<distro>/<release>/` in the documented recipe, run whenever the
  pinned `resolute` image is re-fetched or a provisioning regression is suspected. **Nothing rotates it for
  you** — there is no scheduler (decision 4), so this is part of the human procedure, not a cron.

---

## 7. Exclusions — what the suite must not try to prove

| Excluded | Why |
|---|---|
| `setup-pipeline-mysql` / any pipeline that runs `scripts/*.sh` inside a CI pod | D4 decision: requires a reachable git server + real deploy keys inside an isolated VM (D7). mysql/redis behaviour is covered through `kmt` in the guest instead. The CI manifest rewrite is proven by FIN-51's own acceptance + a static assertion, not here. **Update 2026-10-27:** `setup-pipeline-mysql.yml` is deleted with the backing-service automation, so the first half of this row no longer describes anything in the tree. |
| Linkerd install/uninstall | There is nothing to install: kubementat dropped the mesh on 2026-10-28 (owner decision), so no lane may install it and `--enable-linkerd` no longer exists. **What the exclusion now owes is the other direction** — a lane on a cluster that really does carry an old mesh has to prove `kmt uninstall` takes `linkerd.io` and both `linkerd` namespaces off it, and a mesh-free lane must stay green over a cluster that never had one. Neither is payable without such a cluster, so both are recorded rather than claimed. |
| Polaris and Vault UI content | Neither is installed by any lane's helmfile group. Only the negative tunnel case runs. |
| Git-driven deploys (a real webhook triggering a real clone) | No real secrets (D7). |
| Multi-node, HA, upgrades, rollbacks | §1.7a: no upgrade path exists to preserve. |
| Chart-version currency and security scanning | A version registry problem, not a kubementat behaviour problem. |
| Observability data quality (are dashboards/metrics *correct*) | Would need a golden-file system; cost ≫ value. |
| Non-k3s clusters | The suite's value is that it is always the same environment. |
| Host-OS/arch matrix, IPv6, air-gapped installs | One local machine (D3), one distro. |
| Performance/load/soak | Out of lane budget by construction. |
| Anything that exists to prove a compatibility shim works | Banned by D1. |

---

## 8. Open questions and recommended defaults

| # | Question | Recommended default | Trade-off |
|---|---|---|---|
| 1 | *(reduced)* What VM shape does the full lane get? | 8 vCPU / 16 GiB / 50 GiB (§6.3). | Unchanged by decision 4; it no longer matters "when the box is otherwise idle" because nothing runs when it is idle. The shape only applies when someone starts a `full` run. |
| 2 | *(resolved — D9)* Does `kmt uninstall` delete the PVCs? | Yes: kubementat deletes every claim it created, in every mode. The harness never deletes PVCs itself. | If the PV reclaim policy is ever `Retain`, no code can finish the job (FR-61 note); that must be documented rather than worked around. |
| 3 | Should linkerd get an E2E stage now that FIN-14 is withdrawn? | No. There is no install path to stage and no flag to pass. The one stage worth writing is a *residue* assert on a cluster that still carries a leftover mesh, and it is on the exclusion list until somebody brings such a cluster. | Same cost argument as before, now moot rather than traded away: the product ships no mesh, so a mesh stage would be a test of a feature that does not exist. |
| 4 | What exactly is the "wrong namespace" mutation in FR-51? | Change `GRAFANA_DEPLOYMENT_NAMESPACE` in the guest's `static.json` to a generated value and re-tunnel. | If namespaces become config-driven everywhere, this test's value goes up and its brittleness goes down. |
| 5 | *(resolved)* Who starts the lanes, and when? | Nobody automatically (D2 + decision 4). You run `uv run python tests/e2e/suite.py --lane smoke` per ticket and `--lane full` when you judge it worth an hour. | There is no nightly to rot, and no runner to maintain. The honest cost: regressions sit between runs, so smoke frequency is the only thing keeping this honest. |
| 6 | Do `kmt run-script` / `run-ci-container` get an E2E row before deletion? | No. Both are slated for deletion/migration (FIN-46, FIN-16); write their row when the replacement capability exists. | Two of 19 commands are temporarily unproven by E2E. Cheap to add later. |
| 7 | Where do the harness's stage scripts live? | `tests/e2e/stages/NN-*.sh`, but **executed through the Guest command primitive (FR-12), not delivered via `--script`**, except `01-provision.sh` which is the sole `--script` user (FR-13). | Bash in the stages contradicts "no bash products"; it is test-only and stays. Revisit if stages grow logic that belongs in the harness. |
| 8 | *(decided)* How does FIN-88 fix the dead `twuni` repo? | **Delete it.** Remove the `twuni` entry and the `registry` release that uses `twuni/docker-registry` from both helmfile templates. A different docker-registry solution arrives later, as its own work. | Cost: kubementat no longer deploys an in-cluster registry, so `setup-docker-registry-access` configures a Secret for a registry that isn't installed — acceptable while the replacement is planned, and it must be stated in the CHANGELOG rather than left to be discovered. |
| 9 | **FIN-89 — direction decided ("we keep a dashboard"), chart source pending.** Which maintained dashboard replaces the archived `kubernetes/dashboard` chart in the existing `group: dashboard` slot? | **Headlamp** (`kubernetes-sigs`, https chart repo, ServiceAccount-token login — matches our existing login command and FR-54's assertion). KubeGlass is the alternative but ships via `oci://` and its maintenance/license posture is unverified here. | Headlamp is cheap (one Deployment, no PVC), needs no new helmfile group (D5 holds), and keeps the lane's assertion honest. It does rename what our dashboard command does, so it is a product change, not only a test change — see FIN-89's blast radius. |

**Decision on FIN-89 is "keep a dashboard" — the retired chart is replaced, not deleted.**

| What we know | Source |
|---|---|
| `kubernetes/dashboard` was **archived 21 Jan 2026**; its helm repo (`kubernetes.github.io/dashboard`) 404s, and installs keep running but receive **no fixes, including security fixes**. | [archived repo](https://github.com/kubernetes-retired/dashboard), [sig-ui notice](https://groups.google.com/g/kubernetes-sig-ui/c/vpYIRDMysek/m/wd2iedUKDwAJ) |
| SIG-UI's own recommended alternative is **Headlamp**: `helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/` then `helm install … headlamp/headlamp`. It is a `kubernetes-sigs` project, uses an ordinary https chart repo (identical mechanism to every other repo in our helmfile), requires K8s ≥ 1.21, and logs in **with a ServiceAccount bearer token under RBAC** — the same model `kmt login-kubernetes-dashboard` already implements. | [headlamp.dev](https://headlamp.dev/docs/latest/installation/in-cluster/), [chart README](https://github.com/kubernetes-sigs/headlamp/tree/main/charts/headlamp) |
| **KubeGlass** is the other candidate: a single Go binary, chart published as an **OCI artifact** (`helm install kubeglass oci://ghcr.io/kubeglass/charts/kubeglass`), with a published migration guide off the archived dashboard. | [kubeglass.com install](https://kubeglass.com/docs/getting-started/installation/) |

**Why Headlamp fits this codebase better:** the login path is already ours. Our dashboard command mints a
ServiceAccount token and the E2E assertion (FR-54) already checks that a printed token authenticates against
the API server — Headlamp consumes exactly that. KubeGlass's own chart would mean a new auth story, and its
chart arrives as an `oci://` artifact, which is a *different* mechanism from every https repo in
`helmfile.yaml.gotmpl` and needs verifying against helmfile before anyone commits to it. KubeGlass's
license/maintenance posture was not verified here and should be, if it is preferred.
- What each costs the suite: (a) cheapest today, but the same failure recurs upstream; (b) one tarball to
  bump forever; (c) needs a product decision about what `group: dashboard` is allowed to install.

**One decision above (#9) is on the suite's critical path**; FIN-88 (#8) is settled and can be implemented
immediately. Everything else in this table can be answered during implementation. The research's remaining
open items (cold-cache create time untested, pod-eviction message text unreproduced, `group=standard` install
never completed, tunnel/feature behaviour unmeasured) are tracked in the implementation plan's risk table,
not here — they are measurements the suite itself will make once an install can finish.
