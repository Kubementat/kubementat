# Faster integration testing for kubementat

Date: 2026-10-28. Scope: `tests/e2e/` (virt-runner VM suite) and what sits around it.

## 1. What the suite costs today

These numbers come from `~/.kmt-e2e/runs/*/result.json`, not from the README.

| run | 01-provision | 02-install | 03-run | 04-rerun | 05-features | 99-teardown | total |
|---|---|---|---|---|---|---|---|
| `20261028T143555Z-full-47b186f` | 99 | 188 | 42 | 67 | 37 | 123 | ~555 s |
| `20261028T142202Z-full-44b27fc` | 211 | 188 | 130 | 66 | 38 | 116 | ~750 s |
| `20261028T153022Z-smoke-install` | 111 | 136 | – | – | – | 54 | ~300 s |
| Oct 23 full runs | 94–249 | 104–124 | 19 | 44–49 | **~1188** | ~139 | ~1600 s |

Where the time goes, from the latest full run (`stages/suite--suite.log.log`):

- **01-provision, 99–211 s.** This is almost all network. Every run does `apt-get update`, then installs git-crypt, yq and pip, then downloads helm, helmfile, the helm-diff plugin, tkn, uv and k3s. The swing between 99 s and 211 s is download variance. The product's own code runs for a few seconds of it.
- **`kmt install` (standard group), ~155 s.** Helm repo updates, chart downloads and container image pulls (prometheus, grafana, vault, tekton and so on), all into a cluster that starts empty every time.
- **99-teardown, ~120 s.** Two uninstalls, plus a reinstall and a third uninstall.
- The ~1188 s `05-features` in the Oct 23 runs came from tunnel waits hitting their ceilings. That has since been fixed: the stage now takes 37 s.

On its own, one full run at ~9 minutes is acceptable. **The real cost is iteration.** Between Oct 27 and Oct 28 there were about 10 `full` runs, and each went red on the same 3–4 assertions (`tunnel-signature:tunnel.grafana`, `namespace-rename-tunnel-still-correct`, `create-grafana-accounts`). Every retry of a feature that runs for 30 s paid about 6 minutes of provision and install before reaching it. Making the full run 30 % faster helps less than letting that retry take one minute.

The assertions themselves are good: real cluster, bounded polls, residue and idempotency checks, a failure directory you can diagnose from. Nothing below proposes replacing them. Every option cuts the fixed cost around them, or adds a cheaper layer underneath.

## 2. Options

### A. Snapshot after install, then resume from it (biggest win for iteration)

Snapshot the VM once `02-install` passes, and let a run start from that snapshot.

- `suite.py --snapshot-after 02-install` saves an external qcow2 snapshot (`virsh snapshot-create-as --disk-only`, or an overlay on a copy of the disk) and writes the in-memory `self.state` to `state.json` next to it: baselines, storage class, `helm_baseline_ids`, `post_install_resource_sets`.
- `suite.py --resume <snapshot> --stages 05-features --features grafana_accounts` reverts to the snapshot, reloads `state.json` and runs only what was asked for.
- With this, re-testing the grafana tunnel costs a revert plus the feature: roughly 30–60 s instead of roughly 9 minutes.
- **What stops this from being free:**
  - virt-runner has no snapshot command (`create/destroy/list/modify/setup/ssh` only), so either `virsh` is called directly (a break from "the suite speaks only to virt-runner") or virt-runner gains `snapshot`/`revert`.
  - The DHCP lease and the ssh host key must survive the revert.
  - k3s must tolerate the clock jump after a revert. It usually does, but leases and certificates can need a moment.
- **Rule that keeps it honest:** `result.json` records `resumed_from: <snapshot id, sha>`. A resumed run is never evidence to cite on a ticket. Only a run from scratch is.

### B. Golden base image (cuts provision from 100–210 s to about 20–30 s)

Bake one qcow2 that already contains apt packages, helm, helmfile, helm-diff, tkn, uv and k3s, with the k3s airgap image tarball in `/var/lib/rancher/k3s/agent/images/` (with `.cache.json`, so restarts skip the re-import).

- k3s documents this preload path in its [air-gap install docs](https://docs.k3s.io/installation/airgap). It imports tarballs from that directory at startup.
- `virt-runner create --image` takes "Direct URL of a raw cloud image". Whether a `file://` URL to a local baked image works needs a one-line test. If it does not, this belongs in virt-runner as a feature, and it is small.
- The VM must not run k3s already at bake time. Install the binary and the images, but leave k3s unstarted, so every run still gets a fresh cluster and the post-provision baselines stay meaningful.
- **Coverage lost:** `01-provision.sh` running from a bare image. That script is harness code, not product code, so the loss is small. A weekly `--cold` run from the bare cloud image keeps it proven. The image is keyed by a hash of `01-provision.sh` plus the pinned tool versions, so changing the script rebakes it automatically.

### C. Host-side image and chart caches (cuts install time and network flakes)

- **Container images:** run a pull-through `registry:2` on the host, one per upstream (docker.io, quay.io, ghcr.io, registry.k8s.io). Point the guest at it through `/etc/rancher/k3s/registries.yaml` mirrors, as described in the [k3s private registry docs](https://docs.k3s.io/installation/private-registry). From the second run on, image pulls happen at LAN speed. This complements B: the golden image covers k3s's own images, and the mirror covers the charts' images.
- **Helm charts:** keep a chart cache on the host and share it read-only over virtiofs (a second `--mount`). Set `HELM_CACHE_HOME` and `HELM_REPOSITORY_CACHE` in the guest env to point at it. Repo index updates still go out, but chart tarballs do not.
- Add a `cache_hits` field to `result.json`, the same way `image.cache_hit` is recorded today, so a stale cache is visible in the artifact.
- Expected effect: the ~155 s `kmt install` drops toward the time helm and Kubernetes actually need to roll out, likely 60–90 s. This is unmeasured and needs one run to pin it.

### D. A container-cluster lane (k3d) for a fast inner loop

The harness already has one seam: every guest command goes through `Guest.run`. A `LocalGuest` could run the same commands in a toolbox container (or on the host, in a scratch copy of the tree) against a k3d cluster.

- k3d creates a single-node k3s cluster in seconds rather than minutes. Published comparisons put it ahead of kind for startup ([sanj.dev](https://sanj.dev/post/k3d-vs-kind-local-kubernetes/), [dev.to](https://dev.to/mtrnm/k3d-for-local-kubernetes-how-we-cut-cluster-startup-from-3-minutes-to-under-10-seconds-3ba2)). Docker is already on this host. k3d is not installed.
- It is the same distribution (k3s) as the VM lane, so the bundled-Traefik and local-path behaviour stays the same.
- The routing lane's "disable bundled Traefik" step becomes `--k3s-arg --disable=traefik@server:0` at cluster creation, not an edit to `/etc/rancher/k3s/config.yaml` plus a restart. That is a different code path, so the routing lane's disable-and-wait assertions stay VM-only.
- **What it does not prove:**
  - The host-isolation invariants (INV-1 tree copy, `checkout-untouched`) behave differently.
  - `systemctl`-based steps do not apply.
  - NodePort and LB reachability goes through k3d's port mapping.
  - The cluster shares the host's Docker daemon.
- **Verdict:** this is worth it only if A (snapshots) turns out to be awkward. A gives most of the iteration benefit without a second execution backend to maintain.

### E. Cheap mechanical wins in the harness

- **ssh connection reuse.** `guest.py` opens a new ssh connection for every call (`BatchMode`, `ConnectTimeout` only). Assertions issue hundreds of `kubectl` calls. Add `ControlMaster=auto`, `ControlPath=<run dir>/ssh-%C` and `ControlPersist=60` to `SSH_OPTIONS`. It is a one-line change. The saving is unmeasured; likely tens of seconds per full run.
- **Batch the reads.** Some `resource_sets`, `cluster_scoped_names` and `_missing_tools` loops make one ssh round trip per kind or tool. Running one remote script that returns JSON for all of them takes a single round trip.
- **Feature selection.** Add `--features a,b` to override `lane.features`. This is useful even without A, for example a smoke-shaped VM that runs one full-lane feature.
- **Parallel rerun capabilities.** Out of scope: `04-rerun` must stay sequential, because its before and after hashes are only meaningful one capability at a time.

### F. Shift checks left, out of the VM

Some of the red runs were configuration bugs, which a rendered manifest can show without a cluster:

- **namespace rename, `helmfile-apply:standard-after-namespace-rename`:** run `helmfile template` against a fixture `platform_config` with a renamed namespace, then assert that every rendered object for that release lands in the new namespace.
- **"values never write a service type or storage class":** this test already exists in that form (`test_the_routing_values_file_never_writes_a_service_type_or_a_storage_class.py`). Extend the pattern.
- **Schema checks:** run kubeconform on rendered output, so an API-version or schema error fails in pytest in seconds instead of in `02-install`.

These need `helmfile` and `helm` on the host, plus network access for chart pulls, or the chart cache from C. Mark them `@pytest.mark.render` so the default `pytest -q` stays offline.

### G. Declarative test frameworks: not recommended

[Kyverno Chainsaw](https://kyverno.io/docs/subprojects/kyverno-chainsaw) is now the standard declarative Kubernetes e2e tool. [KUTTL](https://github.com/kyverno/chainsaw) is unmaintained and Chainsaw offers a migration path from it. It is a good fit for asserting the state of a controller's resources. kubementat's assertions, though, are about a CLI's behaviour: exit codes, prompts, tunnels, residue against a baseline, idempotency hashes. The Python harness already expresses these better. Migrating would cost a lot and buy no speed. The only place it might help is the routing ingress asserts, and those are not the bottleneck.

## 3. Recommended layering

| tier | what | time | when |
|---|---|---|---|
| 0 | `pytest -q` (parity, shims, CLI surface) + new `render` tests (F) | seconds | every change |
| 1 | `--resume <post-install snapshot> --features X` (A + E) | ~1 min | iterating on a red feature |
| 2 | `--lane full --scope full` from the golden image, with caches (B + C) | ~5 min (est.) | ticket evidence |
| 3 | `--cold`: bare cloud image, no caches | ~10 min | weekly, or when `01-provision.sh` or the tool pins change |

Order to build it in:

1. **E**, ssh reuse plus `--features`. Hours of work, no coverage risk.
2. **A**, snapshot and resume. This directly fixes the 10-runs-for-one-assertion pattern.
3. **C**, the image mirror and chart cache. Also removes a whole class of network flakes.
4. **B**, the golden image. The largest saving on provision, but it needs a rebake rule and a cold-run discipline.
5. **F**, render tests, added as configuration bugs show up.
6. **D**, only if A cannot be made reliable.

Expected outcome: ticket-evidence runs drop from ~9 minutes to about 5. The more important change is that a red-feature iteration drops from ~9 minutes to about 1. No assertion is weakened.

## 4. Open questions to settle with one experiment each

- Does `virt-runner create --image file:///...` accept a local baked qcow2/raw image?
- Does k3s come back cleanly after a libvirt snapshot revert, specifically node Ready and leases, without a `systemctl restart k3s`?
- How large is the actual image set for the `standard` group? `crictl images` after install gives it, and it sizes the mirror disk.
- How many ssh calls does a full run make? Count `guest._invoke` calls once, before claiming a saving for E.

Sources:
- [k3s air-gap install](https://docs.k3s.io/installation/airgap)
- [k3s private registry configuration](https://docs.k3s.io/installation/private-registry)
- [k3s: strategies for large images](https://docs.k3s.io/blog/2025/11/11/strategies-for-large-images)
- [Kyverno Chainsaw docs](https://kyverno.io/docs/subprojects/kyverno-chainsaw)
- [kyverno/chainsaw](https://github.com/kyverno/chainsaw)
- [k3d vs kind (sanj.dev)](https://sanj.dev/post/k3d-vs-kind-local-kubernetes/)
- [k3d startup under 10 s (dev.to)](https://dev.to/mtrnm/k3d-for-local-kubernetes-how-we-cut-cluster-startup-from-3-minutes-to-under-10-seconds-3ba2)
