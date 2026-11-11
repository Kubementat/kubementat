# virt-runner / guest-VM research — measured facts for the E2E suite

Research run: 2026-10-20, single host (`kubementat` dev box), `virt-runner` 0.5.0. All VMs were named
`kmt-research-*` and destroyed (`virt-runner list` count 0 after the run). No production code was changed. The
host checkout was verified untouched (`git status --porcelain` identical before/after).

> ## How to use this document
>
> **Everything here is a finding, not an instruction.** No recommendation, judgement, or action item survives
> below the line marked *(advice — removed)*. What to do about a finding is decided in exactly two places:
> `finish-python-migration.md` (the plan) and `testsuite-requirements.md` (the requirements). If this file and
> either of those disagree, **they win and this file is stale** — that is a bug in this file, not a hint.
>
> Each finding carries a confidence tag:
>
> | Tag | Means |
> |---|---|
> | **measured** | Reproduced with a command shown here. Trust it. |
> | **observed once** | Saw it happen; don't design a dependency on its frequency. |
> | **inferred** | Reasoned from a reading of code/source, not executed. Verify before building on it. |
>
> §8 lists the four things that changed after the run because of owner decisions. **Read §8 before §7.**

---

## 1. Index of findings

| # | Finding | Confidence | What it touches |
|---|---|---|---|
| F1 | The host is already capable of running the suite; `virt-runner setup --check` is green and needed no changes | measured | Onboarding, FIN-86 docs |
| F2 | `pool-info Available` overstates free space vs `df` by ~2× (92 GiB vs 49 G) | measured | Harness preflight |
| F3 | Destroy is **not** idempotent: 2nd destroy → exit 1 `vm-not-defined` | measured | Harness teardown |
| F4 | A failed `--script` **leaves the VM running** (`script-failed`, `stage=script`) | measured | Harness teardown |
| F5 | Raw `vm.ssh_command` breaks on reused DHCP leases (`REMOTE HOST IDENTIFICATION HAS CHANGED`) | observed once | Harness command primitive |
| F6 | k3s installs on a bare Ubuntu 26.04 guest in **9.6 s with zero preparation** | measured | Provision stage, budget |
| F7 | `/etc/rancher/k3s/k3s.yaml` is `0600 root` and `chmod` is reverted on restart; `write-kubeconfig-mode: 644` + restart is the durable fix | measured | Provision stage |
| F8 | kubementat's dependency check requires **`yq` and `pip`**; `jq` is preinstalled in the image | measured | Provision stage, FR-20 |
| F9 | k3s `local-path` is `WaitForFirstConsumer`: a probe PVC stays `Pending` **forever** until a consumer pod is scheduled | measured | Storage assertion, FR-21 |
| F10 | `kmt initialize` refuses to run if the 4 `platform_config/dev/**static{.encrypted,}.json` files exist | measured | Copy handling, INV-1 |
| F11 | `kmt install` **prompts** on stdin (`yes/no` via `input()`) | measured | Harness, FR-3/FR-30 |
| F12 | The helmfile template `requiredEnv`s `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`, which `initialize` does not write | measured | Harness env, FR-30 |
| F13 | The `twuni` helm repo is unreachable (guest **and** host); helmfile adds all repos before filtering, so *every* `kmt install` dies in `repo add` | measured | Product, FIN-88 |
| F14 | The kubernetes-dashboard chart repo returns **404**; the project is archived upstream | measured | Product, FIN-89 |
| F15 | Cold image-cache create time: **not measured** (cache never deleted) | — | Known gap, §8 |
| F16 | On a cache *hit* virt-runner reports `verification: "skipped", verified: false` | measured | Artifact/`result.json` |
| F17 | Pod *eviction* messages not exercised — only `Insufficient memory` scheduling text reproduced | — | FR-22, known gap |

---

## 2. Host facts

Verified against this machine. Values here are a snapshot of a specific box, not invariants — which is why the
harness measures them at runtime instead of embedding them (preflight: §6.3 of `testsuite-requirements.md`).

| Check | Result |
|---|---|
| `virt-runner --version` | 0.5.0 (~0.1 s) |
| `virt-runner setup --check` | exit 0, every line `+` ("nothing will be installed or changed"), lists 15 packages it *would* upgrade. Audit-only, no sudo used in this run |
| `virsh -c qemu:///system pool-info vm-pool` | `State: running`, capacity 855 GiB, dir `/var/lib/libvirt/images/vms` |
| `virsh -c qemu:///system net-list --all` | `default active yes yes` |
| `df -h ~` during run | `856G 763G 49G 94%` |
| `free -g` during run | 125 G total / 61 G available |

**F2 — capacity accounting is a trap.** During the same run, `pool-info` reported **92 GiB** available while
`df` reported **49 G**. Sparse qcow2 allocation is the stated reason. **Consequence for the harness:** a
preflight that asks the pool can pass on a full disk. *(The pool-vs-`df` question is now a requirement; see
FR-22's preflight clause.)*

**Disk write reality:** a 25 GiB smoke VM wrote **16 GiB of real host data** while idle/used, and `destroy`
reclaimed it exactly (`Available` 92→137 GiB after destroying 2 VMs). *(The ≥20 G headroom rule is a
requirement, §6.3.)*

**Only host action that could ever be needed** (not needed here, and not observed): if `setup --check` reports
something missing, `sudo virt-runner setup` then **log out and back in** for group membership.

---

## 3. Guest facts (measured)

- **Distro:** Ubuntu 26.04 LTS "Resolute Raccoon", kernel `7.0.0-28-generic`, user `ubuntu` (uid 1000),
  passwordless sudo, no swap, cgroup v2 (`cgroup2fs`).
- **k3s:** `v1.36.5+k3s1`, single server, containerd `2.3.4-k3s1.36`. Traefik + svclb install by default.
- **Node capacity at 6 GiB VM:** allocatable `cpu=4`, `memory=5842 MiB`, `pods=110`.
- **StorageClass:** `local-path`, provisioner `rancher.io/local-path`, `reclaim=Delete`,
  **`volumeBindingMode: WaitForFirstConsumer`**, annotated
  `storageclass.kubernetes.io/is-default-class: "true"`.
- **Unschedulable signal (FR-22's matchable text):**
  `0/1 nodes are available: 1 Insufficient memory.` plus a `FailedScheduling` event.
- **Tools present in the guest after §4.5:** helm 3.22.1 · helmfile 1.8.2 · helm-diff 3.15.15 · tkn 0.46.1 ·
  uv 0.13.4 · git 2.53.0 · git-crypt (apt) · jq 1.8.1 (base image) · yq v4.54.1 · pip 25.1.1.
- **`kmt --help` (19 commands, all flat, pre-FIN-62):** helmfile-apply, initialize, install,
  login-kubernetes-dashboard, run-ci-container, run-script, setup-docker-registry-access, setup-secrets,
  tekton-cleanup-pipeline-runs, tekton-list, tekton-run-pipeline, tekton-setup-pipelines,
  tekton-setup-triggers, tekton-uninstall-pipelines, tunnel-grafana, tunnel-pod, tunnel-tekton,
  tunnel-vault-ui, uninstall.

### 3.1 The ssh line that works *(F5)*

`create --json` returns `vm.ssh_command` = `ssh -i ~/.ssh/virt_runner_key ubuntu@<ip>` — **raw, no options**.
Used raw, a reused DHCP lease aborted a command mid-run with `REMOTE HOST IDENTIFICATION HAS CHANGED`; a plain
`ssh` can also prompt. This line was used for every command in this research:

```bash
ssh -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$run_dir/known_hosts" -o ConnectTimeout=10 \
    "$user@$ip" 'command with "quotes" | pipes' >stdout.txt 2>stderr.txt
```

Verified: guest `exit 42` → host exit 42; stdout/stderr separate. Multi-line scripts go in over stdin
(`ssh … 'bash -s' < script.sh`). `dns_name` is only `<name>.default`; the lease IP is the reliable handle.
*(The requirement that governs this is FR-12.)*

### 3.2 What the guest copy contains *(F10, INV-1)*

`cp -a /mnt/kubementat /var/tmp/kmt-e2e` — 68 MB in **1.4 s** over virtiofs. The copy carried `.git/`, the
dirty state (24 untracked entries, `git status` in the copy matched the host), the 5 secret-ish files
(`gpg_private.key`, `gpg_public_key.gpg`, `git_crypt_symmetric.key`, `deployer_ssh_key{,.pub}`), and the
git-crypt state (18 tracked files encrypted; keys live in `.git/git-crypt/keys`, so the guest can unlock).

**The blocker found here:** `kmt initialize` exits non-zero when any of
`platform_config/dev/static.json`, `platform_config/dev/static.encrypted.json`,
`platform_config/dev/smoke/static.json`, `platform_config/dev/smoke/static.encrypted.json` exists — and a
working dev checkout has them as **untracked leftovers of a previous initialize**. Copying dirty state as-is
(invocation of INV-1) therefore makes `initialize` fail deterministically.
*What the harness does about it is a sanctioned mutation defined in INV-1's exception, not an open question.*

### 3.3 k3s needs no preparation *(F6)*

```bash
curl -sfL https://get.k3s.io | sudo sh -     # 9.6–9.9 s, node Ready within ~1 poll
```

No `INSTALL_K3S_*` flags for single-node. No swap (it's off), cgroup v2 is default, `k3s.service` self-loads
`overlay`/`br_netfilter` via `ExecStartPre=-/sbin/modprobe`, `nf_conntrack`/`ip_tables` are loaded by
kube-proxy, `ip_forward` flips to 1. **The `apt update && apt upgrade` in the old `tests/README.md` recipe is
unnecessary** — that is a fact about the recipe, not a recommendation.

### 3.4 The kubeconfig gotcha *(F7)*

`k3s kubectl` always prefers `/etc/rancher/k3s/k3s.yaml` (mode `0600 root:root`) when `KUBECONFIG` is unset,
ahead of `~/.kube/config`; a symlink/copy does not help. `chmod 644` works but k3s **resets it to 600 on every
service restart** (measured). Two ways that were verified to work:

```bash
printf 'write-kubeconfig-mode: 644\n' | sudo tee /etc/rancher/k3s/config.yaml
sudo systemctl restart k3s          # ~8 s, file stays -rw-r--r--
# or, per command: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

Why it matters beyond `kubectl`: `KubernetesUtils.__init__` calls `load_kube_config()`, so **even
`kmt initialize` fails** (`ConfigException: Invalid kube-config file`) if neither measure is in place.
*(Inferred from reading `kubernetes_utils.py` and confirmed by the failure; not confirmed that this is the only
import-time consumer.)*

### 3.5 Tooling install notes

| Tool | How | Got | Notes measured |
|---|---|---|---|
| helm | `get-helm-3` script | 3.22.1 | script self-elevates; also works into `~/.local` |
| helmfile | release tarball | **1.8.2** | `download-helmfile.sh` 404s looking for 2.x |
| helm-diff | `helm plugin install` | 3.15.15 | lands in `~/.local/share/helm`, no sudo |
| tkn | release tarball | 0.46.1 | installs to `/usr/local/bin` |
| uv | `astral.sh/uv/install.sh` | 0.13.4 | lands in `~/.local/bin` — **not on the non-interactive ssh PATH** |
| git-crypt | `apt-get install -y git-crypt` | apt | 4.2 s |
| yq | mikefarah binary | v4.54.1 | **required by kubementat's dependency check** |
| pip | `apt-get install -y python3-pip` | 25.1.1 | **required by the same check** |
| jq | — | 1.8.1 | already in the cloud image; kubementat installs nothing |

*(F8: `yq`/`pip` were absent from the original FR-20 list; it now includes them.)*

### 3.6 What `kmt install` actually did

Sequence observed, in order: prompt → dependency check → permission check → **Tekton install** → `helmfile apply`.
At `helmfile apply` it failed three times in a row, for three distinct reasons:

1. `requiredEnv GRAFANA_ADMIN_USER is not set` *(F12 — the helmfile template needs both `GRAFANA_ADMIN_*`
   vars and `initialize` does not write them)*
2. `helm repo add twuni` → unreachable *(F13)*
3. after removing that repo entry **in the guest copy only**: 404 on the kubernetes-dashboard chart repo *(F14)*

So `kmt install` never reached a deployment in this run. Note two secondary facts from the ordering: **Tekton
resources were applied before the failure**, and the prompt is a blocking `input()` *(F11 — the harness cannot
be blocked, and FR-3 forbids reading stdin)*.

---

## 4. Measured timings

| Stage | Cold | Warm |
|---|---|---|
| VM create (default profile, warm cache) | not measured *(F15)* | **16.0 s** (6G/4v/25G) · 18.8 s (+mount) · 29.6 s (+`--script`, 2 G VM) |
| VM create `--no-boot` | — | 4.5 s |
| VM destroy | — | **0.3 s** |
| `cp -a` of tree over virtiofs (68 MB) | — | 1.4 s |
| k3s install | — | **9.6–9.9 s** |
| node `Ready` (1 poll) | — | ≤ ~10 s |
| kubeconfig fix (`config.yaml` + restart) | — | ~8 s |
| probe PVC `Bound` — **with a consumer pod** | — | **9 s** |
| probe PVC `Bound` — **without** a consumer pod | — | **never** *(F9)* |
| tooling (§3.5, incl. 8 s `apt update`) | — | ≈ 63 s total |
| `uv sync --all-extras` (fresh guest) | — | 2.9 s |
| `kmt initialize dev smoke` | — | 1.2 s |
| `kmt install … --helmfile-installation-group dashboard` | — | ~3 min, then fails (§3.6) |

**create + provision + initialize ≈ 2:00** per run, against a 3:40 lane sub-budget. Install/feature timings
are not measurable until the product blockers in §8 are resolved.

Cold-cache create was deliberately not tested: deleting the cached 860 MB image would slow every subsequent
run to answer a question the suite does not currently depend on. **F16 is the residual risk: on a cache *hit*
virt-runner reports `verification: "skipped", verified: false` — cached images are trusted, never
re-verified.**

---

## 5. What changed in the requirements after this run

These are not corrections to the requirements; they are the *reason* four requirements were rewritten. If you
are reading a rewritten FR text, it already accounts for these.

| Original claim | What the measurement showed | Now governed by |
|---|---|---|
| FR-21: "create a probe PVC, assert `Bound` within 120 s" | `local-path` is `WaitForFirstConsumer`; that assertion fails forever on a healthy cluster. It binds ~9 s **after a consumer pod** is scheduled | FR-21 (rewritten: pod must be scheduled) |
| FR-20: prerequisite list | Missing `yq` and `pip`, both required by kubementat's own dependency check; `jq` is already in the image | FR-20 (rewritten) |
| INV-2: "unconditional destroy" | Destroy errors `vm-not-defined`/exit 1 on a VM that is already gone, and a failed `create` can leave the VM running | INV-2 (+ the tolerance rules in §6 of the requirements) |
| FR-12: "append the command to `vm.ssh_command`" | That raw line breaks on DHCP reuse and can prompt | FR-12 (rewritten: hardened line) |
| INV-1: "copy the tree as-is" | True, but the copy then contains 4 stale `initialize` markers that make `initialize` fail | INV-1 + its single sanctioned exception |
| FR-24: image caching | Works; cache hits are never re-verified | FR-24 (+ the `result.json` field) |

**One more that did not make it into a requirement, so record it here:**
`platform_config/dev/smoke/static.json` in the repo template hardcodes `MYSQL_VOLUME_STORAGE_CLASS: hostpath`.
A fresh `initialize` overwrites it from the passed `KUBERNETES_DEFAULT_STORAGE_CLASS`; a leftover config does
not. So `grep hostpath tests/` is a necessary but insufficient assertion — what matters is the value passed in.

---

## 6. Harness-relevant behaviours of `virt-runner` (all measured)

| Behaviour | Evidence |
|---|---|
| `--json` stdout is exactly one JSON document; stage output goes to stderr | envelope parsed cleanly every time |
| `create --script` runs after boot as the cloud user; envelope carries `scripts[].exit_code` | used for `01-provision.sh` |
| On script failure: `error.code=script-failed`, `stage=script`, **VM kept running** | F4 — a create that fails is not a destroyed VM |
| Destroy of a nonexistent VM: exit 1, `vm-not-defined` | F3 |
| `image.cache_hit=true` on warm create, with `verified=false` | F16 |
| `--ram/--vcpu/--disk`, `--mount`, `--no-boot` all behaved as documented | §4 |
| Image cache path: `~/vm-images/<distro>/<release>/`; invalidation `rm -rf` that dir | 5, 860 MB, cached 2026-08-26 |

**A foreign VM appeared during this run.** `virt-runner list` showed a `mas-vmtest-*` VM that I did not create
and that had disappeared by the end of the run without my involvement. Whether that was a colleague or
something automated, I could not tell. **This is the evidence that "only destroy VMs you created" is not
theoretical** — the constraint lives in the plan's Wave T harness constraints, and this incident is why.

---

## 7. Open gaps — what this research did *not* establish

These are gaps in the evidence, not to-do items.

1. **Cold image-cache create time** *(F15)*. Unknown. If someone measures it, record it here and in §4.
2. **Pod eviction message text** *(F17)*. Only `Insufficient memory` scheduling text was reproduced; the
   eviction path (FR-22's other half) was never provoked.
3. **`kmt install` for `group=standard`**. Never completed — blocked by F13 at the time. Install/feature
   timings in the budget tables remain estimates for that reason.
4. **Tunnel and feature behaviour** (FR-50/51). Out of scope for this run; nothing was measured.
5. **Whether the k3s default services (Traefik, svclb) interfere** with kubementat's ingress/routing. Not
   tested — nothing was installed past the failure point in §3.6.
6. **`initialize` writes `static.encrypted.json` in plaintext on disk.** The filename is misleading; git-crypt
   only acts on tracked files. *(Inferred from the file's mode/content, not from a code read.)* It raises an
   open question the suite should not resolve silently: what is that file supposed to contain, and does the
   name promise protection that isn't there?

---

## 8. Owner decisions that supersede earlier parts of this doc

Nothing in §§1–7 is a recommendation, so nothing here "corrects" a finding. What follows is: findings that
were **open questions** when the research was written, and what was decided about them afterwards.

| Finding | Now decided | Where |
|---|---|---|
| F13 — dead `twuni` repo | **Delete** the repo entry and its `registry` release. No vendoring, no replacement for now | FIN-88, plan §1.8 |
| F14 — retired dashboard chart | **Keep a dashboard**; swap the chart for a maintained one (`kubernetes/dashboard` was archived 2026-01-21 — installs get no security fixes). Headlamp is the candidate of record | FIN-89, plan §1.8 |
| F10 — stale `initialize` markers | One **sanctioned mutation**: the harness deletes exactly those 4 files in the guest copy and logs it | INV-1 exception |
| F11/F12 — interactive prompt + `GRAFANA_ADMIN_*` | Harness must not read stdin; the prompt is a product gap to fix, dummies go in the documented env list | FR-3, FR-30 |

**The two pieces of advice that were removed from this document, and why.** A previous revision recommended
(a) vendoring the `docker-registry` chart — rejected: it fixes the suite by deleting the thing under test, and
the owner wants a different registry solution later; and (b) keeping `group: dashboard` but repointing the
chart or swapping to metrics-server/headlamp — partly right (the swap direction was chosen) but the framing
"rework D5" was wrong: no new helmfile group is permitted, and that decision is the owner's, not the
harness's. If you were pointed here by a stale reference, the answer is in the plan document, not this table.
