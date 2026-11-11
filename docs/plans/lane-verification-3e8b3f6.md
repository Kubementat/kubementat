# Lane verification — full lane on `3e8b3f6` (2026-10-27)

**Artifact:** `~/.kmt-e2e/runs/20261027T184758Z-full-3e8b3f6/result.json`
**Result:** 89 assertions, **13 failed** (`d1cc5dc` was 88 / 16 red). Guest: ubuntu resolute,
k3s, 8 vCPU / 16 GiB / 50 GiB. `flaky=True` (some asserts needed a retry).
**This file is the evidence base for the ordered work below. Read it before touching code.**

## 1. Verified — the claims that landed actually work

| Assert | Before | Now | Ticket |
|---|---|---|---|
| `cap:helmfile-apply:routing` | red | **green** | TODOs item 1 (`-y`, non-tty applies) |
| `assert:cleanup-cronjob-exists` | red | **green** | FIN-07 namespace from config |
| `assert:cleanup-cronjob-scheduled` | red | **green** | FIN-07 |
| `assert:grafana-api-readable` | red | **green** | TODOs item 4 (ClusterIP + real login) |
| `assert:no-extra-namespaces` | red | green **vacuously** | see §3.4 |

Install, rerun, all `assert:idempotent:*`, `confirmation-rejected-changes-nothing`,
`confirmation-fails-closed`, `uninstall-advertises-yes-flag`, `checkout-untouched`: green.

## 2. The 13 reds and their root causes (measured this session)

### 2.1 Backing services — 3 reds. DECISION MADE: delete the automation.
- `cap:task.install-mysql` — `exit=124`, ssh timed out after 900 s. `mysql-0` in `ImagePullBackOff`:
  `docker.io/bitnami/mysql:8.0.30-debian-11-r15: not found` (and same for `bitnami/mysqld-exporter`).
  **The Bitnami Docker Hub catalog is gone.** This is not a values bug and it is not a timeout.
- `cap:task.install-redis` — `exit=1`, `bitnami/redis` chart 14.3.0, same shape.
- `weak-ok:install-nginx` — `exit=1`, chart 8.8.1.
- Owner decision 2026-10-27: **remove `install_mysql_helm_chart.sh`, `install_redis_helm_chart.sh`,
  `install_nginx_reverse_proxy.sh` and their pipelines now**; a replacement chart solution comes later.
  `setup_mysql_databases.sh` stays (task/trigger, not a deploy).
- Effect: 3 reds and 4 lane rows disappear. Accept that kubementat ships no working backing-service
  install in the meantime — that is the explicit trade.

### 2.2 Tunnels — 7 reds.
- `assert:tunnel-port-freed:tunnel.grafana` / `:tunnel.tekton` / the after-rename one — 3 reds.
  Detail: `port 3001 free after stopping pid nothing alone (it leads no session of its own):
  LISTEN ... users:(("kubectl",pid=41455,fd=8))`. The recorded pid is **not** a session leader in the
  guest, so the process-group kill kills nothing. TODOs item 5 claims `setsid` fixed this; on a real VM
  it does not hold. The port is then *polled*, so the same survivor is blamed twice per run.
  **Root cause, found in the code (`stages.py:137`):**
  `command="rm -f %s && setsid nohup bash %s > %s 2>&1 < /dev/null & echo $!"` — `A && B &` makes bash run
  the whole `rm && setsid nohup bash wrapper` as one **async list**, so `$!` is the *subshell's* pid, which
  is not a session leader. `_session_id` (`stages.py:211`) then finds no sid, emits
  "nothing alone (it leads no session of its own)", falls back to the pid-only kill, and the
  `kubectl port-forward` grandchild keeps the port bound. The docstring at `stages.py:123` has the right
  intent; the `rm &&` prefix silently defeats it. Fix = bind `&` to `setsid` alone **and** record the
  session leader from inside the session (`echo $$ > <pidfile>`), because `$!` is the leader only when
  `setsid` execs rather than forks.
- `assert:tunnel-signature:tunnel.grafana` (×2) — `missing=['api/login','signin']`. `curl -L` works
  (the body is real Grafana HTML), so the **signature table** names tokens the served body does not
  contain. Same class as the tekton prediction, now measured.
- `assert:tunnel-signature:tunnel.tekton` — `missing=['PipelineRun']`. The dashboard serves an SPA
  `index.html`; the token lives only in the JS bundle. Signature table is the bug, not the tunnel.
- `assert:namespace-rename-tunnel-still-correct` — expected-red-because-product. The tunnel resolves
  `GRAFANA_DEPLOYMENT_NAMESPACE` (FIN-07 closed the Python side), but the release namespace is hardcoded
  in `templates/environment/kubementat_components/helmfile.yaml.gotmpl:113` (`namespace: grafana`).
  A correct tunnel finds nothing after a rename. Needs the *deploy* half of namespaces-as-config.

### 2.3 `cap:create-team-account` — 1 red, plus 5 dark asserts.
Still `exit=1`. `feature_user_accounts` returns the moment this row fails, so
`account-can-i-create`, `cap:create-team-account-read-only`, `account-read-only-cannot-mutate`,
`cap:delete-team-account`, `account-deleted` are **absent from `result.json`, not green**.
Also: `scripts/automation/tekton/create_team_account.sh` defines its **own** `check_cluster_and_access`,
so the `--yes` fix in `390bd61` never reaches it. FIN-30/31/32/33.

### 2.4 `assert:exported-grafana-admin-user-reaches-the-install` — 1 red, designed to be red.
`initialize` writes no `GRAFANA_ADMIN_USER` (not in `KUBEMENTAT_ENVIRONMENT_CONFIGURATION`), so
`helm_utils.helmfile_apply` re-derives it from the config file and the operator's export never lands.
Installed admin is `grafadmin`; the probe got 401 for `kmt-e2e-admin`.

### 2.5 `assert:no-kubementat-releases` — 1 red. **NEW product defect.**
Survivors: `['cert-manager', 'nginx-ingress']`. Root cause: `uninstall_kubementat.sh` reads **14**
`*_NAMESPACE` keys via config; FIN-07 shipped 15 in `templates/environment/static.json.template` and
`CERT_MANAGER_DEPLOYMENT_NAMESPACE` / `NGINX_INGRESS_DEPLOYMENT_NAMESPACE` have **no reader**. Both the
releases *and* their namespaces survive — `snapshots/ns-after-uninstall.json` still lists both. FR-61 is
broken for the routing group. Belongs to FIN-11 (Python uninstall derives the set from `Config`).
**Closed in code by `213bc9b`** — the two keys no longer need a reader because the sweep is the component
set itself. Still red until a lane proves it.

### 2.6 A measuring instrument that cannot fail — harness, not product.
`stages.py:1062`: `baseline = post_install_namespaces`. So `assert:no-extra-namespaces` compares
uninstall residue against the namespaces kubementat just created — its snapshot reads
`["cert-manager","default","kube-node-lease","kube-public","kube-system","nginx-ingress"]` while it
prints "namespaces beyond baseline: none". It passed on `d1cc5dc` only via `cluster-cronjobs` appearing
during stage 05, and passes now for the wrong reason. Fix: baseline = `baseline_namespaces`
(`stages.py:344`, post-provision).

## 3. Ordered work. One step per PR, one writer in the tree.

**Rules for every step (from §3 of `finish-python-migration.md`):**
- No new `subprocess` call site outside `src/kubementat/shell.py`; argv lists, never `shell=True`,
  never `os.chdir`.
- No new `sys.path.append`.
- Breaking changes are expected: no compatibility shims, no deprecated aliases, no fallback config reads.
- No ticket ids (`FIN-NN`) in test file or test case names — `tests/test_test_name_conventions.py`
  fails the build if one creeps in.
- `uv run pytest -q` must stay green (262 at the time of writing).
- Commit only the files you touched. **Never `git add -A`** — the tree carries another agent's
  uncommitted `docker_files/**` work and untracked `platform_config/dev/**`; leave those alone.
- Evidence: name the command that proves the change, in the commit message.

### Step 0 — make the harness measure what it claims
1. `stages.py:1062` — `no-extra-namespaces` baseline is the post-provision set, not post-install.
   The assert must be able to fail for anything `kmt install` creates.
2. The tunnel stop: the pid the harness records must be the guest-side session leader, so
   `kill -TERM -<sid>` takes `kubectl port-forward` with it. Prove it in the guest, not on the host.
3. The two signature tables: pick tokens that occur in the body each redirect chain actually serves
   (grafana: whatever the login page really contains; tekton: do not assert on an SPA `index.html` at
   all — assert on something verifiable, e.g. a header or the dashboard's own static asset).
**Done when:** a lane that changes nothing but a namespace name reads green on the lifecycle asserts,
and `no-extra-namespaces` is red on a run that leaves a namespace behind (prove with a deliberate
negative: assert it fires when one namespace is not removed).

### Step 1 — delete the backing-service automation — **landed 2026-10-27**
Per §2.1. Delete the three scripts, their setup pipelines (`tekton_ci/pipelines/setup-pipeline-{mysql,redis}.yml`
and the `examples/` copies), their capability rows and feature ids, and the keys that only they read.
`tests/test_harness_capability_argv.py` is the guard that a row must not name a deleted script.
**Done when:** `scripts/tasks/install_{mysql,redis,nginx}_*.sh` are gone, the lane has no `task.install-*`
rows, and `uv run kmt --help` still lists 19 commands.

### Step 2 — FIN-11, `kmt uninstall` in Python — **the code half landed 2026-10-27**
Highest risk command; the only new red that is a product defect. Derive every namespace from `Config`
keys that exist, delete the routing releases and their namespaces, remove PVCs (FR-61 — the bash only
prints advice), keep `--yes` failing closed off-tty. Closes §2.5.
**Done when:** `no-kubementat-releases`, `no-extra-namespaces`, `no-kubementat-claims` are green on a
full lane and the 204-line bash is deleted.

The bash is deleted (`213bc9b`) and the implementation is `Automation.uninstall_kubementat()`:
namespaces from `constants.DEFAULT_NAMESPACES` through `Config.component_namespace` plus both
namespaces of every team `platform_config/{env}` configures (`Config.teams()`, `dc9d8f6`); releases
uninstalled per namespace after reading `helm list`; claims deleted by name; Linkerd's own
uninstallers only when its namespaces exist; CRDs listed from the cluster by group;
`helm-deployer-cluster-role` via the 404-tolerant primitive. §2.5's root cause — two keys with no
reader — cannot come back, because nothing spells a namespace out any more.

**What the lane still owes** (282 unit tests pass; none of them has ever spoken to a cluster, so these
five asserts are claims about the code's *intent* until a full lane says otherwise):

| Assert | What the code now does | What is unmeasured |
|---|---|---|
| `no-kubementat-releases` | `helm list` read, then `helm uninstall` per release in an owned namespace | that helm answers before the namespace delete wins the race, on a real cluster |
| `no-extra-namespaces` | every declared namespace deleted where the cluster has it | whether k3s' own CRDs/add-ons reappear after teardown, and that no *other* namespace is left |
| `no-kubementat-claims` | `kubectl get pvc -n NS` read, then `kubectl delete pvc` by name | that the claims the harness recorded at install time are inside namespaces we own — a claim bound in a namespace kubementat does not own is still residue |
| `no-tekton-crds` | every `*.tekton.dev` CRD on the cluster deleted | that nothing kubementat still needs is in that set (it should not be, that is the point) |
| `no-helm-deployer-clusterrole` | `delete_cluster_role`, 404-tolerant | nothing — this one is as close to proven as argv gets |

Plus: the second uninstall pass (`cap:uninstall-second-pass`) must stay quiet, and
`confirmation-rejected-changes-nothing` still runs through a pty, which is the only case no unit test
can reach (see `test_the_uninstall_command_is_python_and_still_asks.py` for what it covers instead).

### Step 3 — FIN-30…33, team accounts in Python
Build the kubeconfig as a dict + `yaml.safe_dump`, token via FIN-36, first real callers of FIN-04's
primitives. Fold the duplicate `check_cluster_and_access` in the same change.
**Done when:** the five dark asserts execute and are green, and there is exactly one implementation.

### Step 4 — tunnels + the deploy half of namespaces-as-config
`helmfile.yaml.gotmpl` release namespaces from `*_DEPLOYMENT_NAMESPACE`, so
`namespace-rename-tunnel-still-correct` can go green.

### Not scheduled, and deliberately so
- `os.environ` secret handling (`automation.py:111-115`, `helm_utils.py:32-33` — including assigning
  `None` to `os.environ`), `exit(1)` in library code, `create_service_account`'s deprecated `secrets`
  field. No ticket owns these; they belong to the deferred platform_config externalization. Raise a
  ticket, do not fix them silently inside another step.
- FIN-50/51 CI work: the image work is now done (`docker_files/ci_images/ubuntu_ci_minimal/**`,
  uncommitted at the time of writing), so the blocker is gone. Needs its own verification.
