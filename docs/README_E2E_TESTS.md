# End-to-end tests (virt-runner)

The E2E suite answers the question that argv-shim parity tests cannot: **does
kubementat install, run, and remove itself on a real cluster?** It replaces the old
interactive `tests/*.sh` scripts and is the verification mechanism for the Python
migration.

It is not a gate. There is no CI job, no schedule, no nightly (D2/D8): you run it when
a ticket needs evidence, and the run directory is what you cite.

## Run it

From the repo root (no CI environment variables are read):

```bash
uv run python tests/e2e/suite.py                          # default: --lane smoke --scope install
uv run python tests/e2e/suite.py --scope full             # every stage, reinstall cycle included
uv run python tests/e2e/suite.py --lane full --scope full # the big VM, both installed groups, everything
uv run python tests/e2e/suite.py --lane tekton --scope full # the build pipeline's own lane: clone, build, push

uv run python tests/e2e/suite.py --keep-vm                # leave the VM up to debug
uv run python tests/e2e/suite.py --dry-run                # preflight only
uv run python tests/e2e/suite.py --list-capabilities      # the capability table
```

Exit code is `0` only when every assertion passed; the per-assertion summary is printed
last. One lock (`/tmp/kmt-e2e.lock`) serialises runs on the machine — a second run
queues and prints its wait, with a 4h cap.

Two independent choices shape a run:

- `--lane` is the VM shape and the helmfile groups installed (table below).
- `--scope` is how much of the lifecycle is walked:

| scope | stages | what it answers |
|---|---|---|
| `install` (default) | `01-provision`, `02-install`, `99-teardown` | does kubementat initialize, install every declared release, set up tekton, registry access and secrets correctly, and come off the cluster without residue? |
| `full` | all six | the above, plus a pipeline run, rerun idempotency (names and Secret/ConfigMap content), the lane's features, the uninstall confirmation probes, a second uninstall pass, and install → uninstall again after the first uninstall |

The VM is destroyed at the end of either scope unless `--keep-vm` is given.

Artifacts land in `~/.kmt-e2e/runs/<UTC>-<lane>-<scope>-<sha7>/`: `result.json`, redacted
`stages/*.log`, `snapshots/ns-*.json`, and the `virt-runner` JSON envelopes. Failures
are kept until you delete them; successes are pruned to the last 10. A failed run's
directory is meant to be sufficient to diagnose the bug without re-running it.

## First run on a machine

The harness preflights the host and tells you exactly what is wrong. To fix it:

```bash
virt-runner --version          # uv tool install virt-runner
virt-runner setup --check      # audit only, changes nothing
sudo virt-runner setup         # KVM + libvirt + pool + network + groups
# LOG OUT AND BACK IN — group membership needs a new login, or virsh keeps needing sudo
```

The suite reads host capacity from `df`/`free`, never from `virsh pool-info`: the pool
has been observed to report ~2x the space `df` agrees is free (sparse qcow2).

## The VM

`--distro ubuntu --release resolute` (Ubuntu 26.04), pinned explicitly, so a profile
change cannot quietly change which OS the suite proves. Image cache:
`~/vm-images/ubuntu/resolute/`.

| lane | vcpu | RAM | disk | install groups | target |
|---|---|---|---|---|---|
| smoke | 4 | 6 GiB | 25 GiB | `standard` | ~16 min |
| full | 8 | 16 GiB | 50 GiB | `standard` + `routing` | ~75 min |
| tekton | 4 | 6 GiB | 25 GiB | `standard` | ~25 min |

The tekton lane is not another slice of the install claims: it runs the product's `docker-build-pipeline`
end to end in stage 05 against fixtures the lane owns — a bare git repository served by the VM's own
sshd, a `registry:2` pod at the node's NodePort, a `FROM scratch` fixture Dockerfile — and asserts only
the two outcomes that matter: the run reaches `Succeeded`, and the image tagged for that run exists in
the registry. Which tool builds inside the build step is the product's to decide; replacing it in
`src/` should not touch a line of this lane. It needs `--scope full` because the feature stage is where
its whole subject lives.

The full lane knows about a third group, `extra`, and does not install it. `group=extra` is the
secret-management release, and installing it exposed that OpenBao comes up uninitialized and never
becomes ready — see the note in `tests/e2e/harness/config.py` and the entry in `TODOs.md`. The assert
that found it (`assert:workloads-running:extra`, which checks pods rather than release records) stays
in the harness dormant, so re-enabling the group is a one-word change and not a feature.

Preflight refuses to start if the host cannot cover the lane's RAM/disk. Measured: a
25 GiB smoke VM writes ~16 GiB of real host data and `destroy` reclaims it, so keep
>= 20 G free per VM.

When the pinned image is re-fetched, or when a provisioning regression is suspected,
clear the cache — nothing rotates it for you:

```bash
rm -rf ~/vm-images/ubuntu/resolute/
```

Cached images are trusted, never re-verified (`verification: "skipped"`,
`verified: false` on a cache hit). `result.json` records `image.cache_hit` and
`image.verified` so a stale cache is visible in the artifact.

## Structure

```
tests/e2e/suite.py           entry point, locking, preflight, stage budgets, result.json
tests/e2e/harness/config.py  lane shapes, budgets, capability table, dummies, known blockers
tests/e2e/harness/vmm.py     every virt-runner call (--json, ownership record, destroy)
tests/e2e/harness/guest.py   the single hardened ssh primitive
tests/e2e/harness/stages.py  the stages and the feature assertions
tests/e2e/harness/asserts.py kubectl vocabulary, waits, residue/idempotency helpers
tests/e2e/harness/artifacts.py  run dir, redaction, retention
tests/e2e/stages/01-provision.sh  the one scripted stage
```

Stages: `01-provision`, `02-install`, `03-run`, `04-rerun`, `05-features`,
`99-teardown`. Destroying the VM is not a stage — it happens in a `finally` for every VM
the harness created, and only for those.

Commands come from the capability table keyed by *capability*, never by CLI name, so
FIN-62's rename (`kmt tunnel-grafana` -> `kmt tunnel grafana`) touches one table and no
stage. `python tests/e2e/suite.py --list-capabilities` prints it, including the rows
still marked `NEEDS-FIN-62`.

## Timings

Measured on the smoke lane, warm image cache, one run:

| stage | seconds | what dominates it |
|---|---|---|
| 01-provision | 182 | VM create + k3s + tooling + tree copy + `uv sync` |
| 02-install | 109 | `config init` + the `standard` group (5 releases) + secrets + registry access |
| 03-run | 21 | apply the hello-world pipeline and poll it to `Succeeded` |
| 04-rerun | 46 | every mutating capability a second time, hashed before/after |
| 05-features | 2 | the openbao tunnel negative, `tekton-list` |
| 99-teardown | 135 | two uninstalls plus the residue sweep |
| **total** | **~490 (8.2 min)** | |

The lane shape says `~16 min`; the measured number is lower because nothing sleeps
waiting for a condition. The budgets in `config.LANES` stay generous, because the first
run on a machine also downloads the ~860 MB image.

A stage over 1.5x its budget is recorded `slow` and still passes. Every wait is a
bounded poll with a predicate; there is no fixed `sleep` standing in for a condition.
Individual budgets are overridable without touching code:

```bash
PROVISION_TIMEOUT=3s python3 tests/e2e/suite.py --lane smoke   # FR-8 probe
E2E_TIMEOUT_03_RUN=60 python3 tests/e2e/suite.py --lane smoke
```

## What the harness itself changes in the guest

Two things, both recorded in `result.json` so they cannot be denied later:

`config_adjustments` — after `config init`, the suite points the CI image reference in
the config it just generated at a real public image (`docker.io/library/ubuntu`). An
isolated VM has no registry of its own, and no lane is allowed to build one (D4: the
pipeline that would build `ubuntu-ci-minimal` needs a real git server). What is proven
by this is *that pipeline runs reach `Succeeded`*; registry *configuration* is proven
separately by the `imagePullSecret` assertions.

The same list records the lane's own cluster declaration: `config init` writes
`CLUSTER_KUBE_CONTEXT: null` and says what that means (spec §4.4 — a new environment
exists and addresses nothing), and stage 02 fills that key in from the one context the
kubeconfig resolves to, before the first command that touches a cluster. The rig never
exports `$KUBECONFIG` and never asks `kubectl` which cluster is current (both of which
would address the run from outside `platform_config`, which is the hole §4.2 closed); an
assert states which context was chosen and why, and another states that the guest
command environment is not carrying the variable.

`sanctioned_mutations` — the files the suite is allowed to write in the guest. Nothing
else in the guest tree is touched, and the host checkout is never written at all
(`checkout-untouched` compares the tree before and after).

The `tekton` lane's feature stage additionally touches the guest and the cluster within
its own stage and cleans up before returning: it hosts a
`kmt-e2e-dockerbuild` namespace with a push-capable `registry:2`, seeds the node's container
store with the CI image under the exact reference the run names (so no kubelet pull ever
touches a plain-HTTP registry), appends a run-generated fixture key to `~/.ssh/authorized_keys`,
and serves a bare git repository from `/var/tmp/kmt-e2e-dockerbuild`. All four are removed by
the feature's `finally`, so `99-teardown`'s residue sweep sees the same baseline the other
lanes see.

## Which cluster, answered against a real cluster

The cluster-config work is unit-tested against fake filesystems, so the lane carries the
asserts a fake cannot make. They run as the `cluster_addressing` feature of the `full`
lane (`harness/config.py::LANES["full"].features`, stage 05), plus two that belong to the
install itself:

| assert | the fact |
|---|---|
| `install-names-the-cluster-it-installed-into` | §9.5's report line is in the output of the command that writes, and names the address the rig declared |
| `releases-landed-where-the-environment-says` | a release sits in the namespace the environment's `static.json` names — the address and the namespace are two halves of one claim |
| `environments-list-answers-what-environments-and-what-cluster` | TD-2: the listing enumerates the same tree the 1:1 check enumerates (TD-2) |
| `undeclared-cluster-refuses-and-names-the-file` | §4.3 with the key set back to `null`: environment, file, key, value found, no traceback — and the key restored afterwards |
| `two-environments-one-cluster-are-refused`, `refusal-gives-the-address-and-the-command-that-shows-it` | RC-3: a second environment that names a second context reaching the same server, refused, both environments named, both addresses named |
| `refused-collision-install-wrote-nothing` | the most valuable one: after the refusal the helm release set is unchanged and the namespace that install would have created does not exist |
| `unresolvable-environment-is-reported-and-skipped`, `unreadable-sibling-did-not-stop-the-command` | §8.4 report-and-skip: a sibling whose context is not in any kubeconfig is named in the report and the command still ran |
| `cluster-fixtures-are-removed` | the fixture environments are out of `platform_config` again, read from `kmt environments list` |

The fixtures are real environments in §8.2's sense — a directory at depth 1 with a
`static.json` of its own — so the check enumerates them like any other environment, and
they are removed before the feature returns (a fixture left behind is a live claim on the
cluster `dev` addresses, and every later command would refuse). The collision kubeconfig is
a copy of the resolved one with one `kubectl config add-context` entry added; the
machine's own `~/.kube/config` is never edited.

`result.json` carries `not_covered`, and the stage prints it, so the limitation the rig
cannot work around reaches a reader who reads only the JSON. See *Not proven here*.

## What is expected to fail today

Nothing, on the smoke lane: the last run is **66 assertions, 0 failed**
(`20261022T225043Z`). The suite still reports product blockers instead of hiding them,
so if a future run is red, `result.json` names the ticket.

- **nothing** — both halves of FIN-11 are **closed**: `kmt install ENV TEAM --yes`/`-y` and
  `kmt uninstall ENV --yes`/`-y` skip the confirmation, and without the flag each one fails
  closed with an actionable message when it cannot prompt. The lane runs both with `--yes`
  and no stdin, so the flag itself is under test.

Resolved by removing things rather than fixing them: the dead `twuni` repo (FIN-88) and
the `kubernetes-dashboard` release (FIN-89) are gone from the tree. The dashboard
component was removed outright rather than swapped: the upstream `kubernetes/dashboard`
chart was retired on 21 Jan 2026 and `kmt login-kubernetes-dashboard` depended on a
read-only ServiceAccount that nothing in the install path created. A replacement
dashboard is a future decision, not a lane blocker.

Also fixed here: `get_pod_name_for_deployment` used to raise `IndexError` when the
component is not installed. It now checks the `items` array (and a failing `kubectl`) and
reports `"Is the component installed? There is nothing to tunnel to."` with exit 1, which
is what `assert:tunnel-negative:openbao-ui-fails-cleanly` proves. The same guard covers the
other three tunnels. `kmt tunnel-polaris` was registered again here too — it had been
shadowed by a duplicate `def tunnel_polaris` — and is retired as of `a7a8330`, so this
document should be read as history for that one command and not as the current surface.

### What the idempotency check does *not* measure

`assert:idempotent:<capability>` diffs resource names before and after a second execution,
snapshot across the whole cluster, and hashes the payload of every Secret and ConfigMap
(`config.IDEMPOTENCY_CONTENT_KINDS`) so a rerun that regenerates a credential under the same name
is red. Deployments, CronJobs and cluster-scoped kinds are still compared by name only. Kubernetes-owned namespaces are excluded
(`config.IDEMPOTENCY_EXCLUDED_NAMESPACES`) because k3s's own helm-controller installs its
bundled addons whenever it feels like: a run showed `kube-system/chart-values-traefik`,
`sh.helm.release.v1.traefik.v1` and the `helm-traefik` ServiceAccounts appearing *during*
`install`, which reads as a delta created by a capability that never touches that
namespace. Nothing kubementat manages is in scope of the exclusion, so no coverage is lost.

## Debugging a red run

```bash
result.json                       # status, stages, assertions, storage class, git SHA
stages/02-install--cap:install.stderr.log
stages/03-run--pipelinerun-<run>.yaml   # the condition of the run that was polled
stages/05-features--cap:tekton.list.stdout.log
snapshots/ns-before-uninstall.json
virt-runner/create.json
diagnostics/dump-00-...           # pods, pvc, helm list, events
```

Anything long-running in the guest is detached into `/var/tmp/kmt-e2e-<name>.log` and
that file's last line is `KMT_E2E_EXIT=<n>`, so a tunnel that died can be told apart
from a tunnel that never started. `--keep-vm` leaves the machine up and prints
`virt-runner ssh <name>` when a stage failed; destroy it yourself afterwards, since the
ownership record has already been cleared:

```bash
virt-runner destroy <name>
```

`VM_JSON_TRACE=1` re-sends virt-runner's progress to stderr.

## Not proven here

Polaris, OpenBao UI content, real git-driven deploys, multi-node/HA/upgrades,
load, chart-version currency, and anything whose only purpose is to prove a
compatibility shim works. See `docs/plans/testsuite-requirements.md` §6-§7.

The cluster config is tested against **one** k3s, because the rig is one virt-runner
machine with one k3s installation. What that costs is recorded in `result.json` under
`not_covered` and printed by the stage that cannot answer it, rather than being left to a
reader's memory:

> not covered: TD-1's other half — `kmt install dev` and `kmt install prod` back to back
> into two different clusters. This rig is one virt-runner machine with one k3s
> installation, so it can prove that a command addresses the cluster its own environment
> declares, and that two environments claiming that one cluster are refused and write
> nothing; it cannot prove a second cluster, and no assertion in this lane claims that it
> does.

The collision half of TD-1 *is* covered, and that asymmetry is deliberate: the invariant
§8.1 protects is that two environments do not address the same cluster, and both sides of
that check are address claims read from local kubeconfigs. It needs no second cluster.
