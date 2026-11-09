# Plan: replace kaniko with BuildKit — a state-of-the-art image build in the pipeline

```
  ┌───────────────────────────────────────────────────────────────────────────┐
  │  ONE RULE, AND ONE THING NOT TO DO                                        │
  │                                                                           │
  │  The task keeps its name and its two-step shape; everything after step    │
  │  one is rebuilt to what a 2026 image build looks like: rootless BuildKit,  │
  │  registry cache, provenance, a published digest.                          │
  │                                                                           │
  │  What it does not do: keep kaniko's flag surface for comfort. Compatibility│
  │  with a dead upstream is not a product requirement, and the plan is       │
  │  written the other way round.                                             │
  └───────────────────────────────────────────────────────────────────────────┘
```

**Why now.** `gcr.io/kaniko-project/executor:debug`, the default every shipped pipeline carries,
comes from a repository Google archived on 2025-06-03; the image is frozen and receives no
security fixes, and the Tekton catalog deprecated its kaniko tasks in the same breath. BuildKit
solves nothing kaniko cannot *except* the things this plan lists: a content-addressable solver
cache instead of per-instruction layer caching, attestation, and a digest it reports.

Be straight about the reason, because an implementer will be challenged on it: kaniko was forked
and is still maintained (`github.com/osscontainertools/kaniko`, published to
`ghcr.io/osscontainertools/kaniko`, also Chainguard's fork), and this repository's own e2e harness
already builds with `ghcr.io/osscontainertools/kaniko:v1.29.0-debug`
(`tests/e2e/harness/config.py`). So the honest "why now" is *capabilities*, plus a shipped default
that is frozen. Not "the lane is unpatched".

**Decisions this spec is written against.**

1. The builder is `moby/buildkit:v0.34.2-rootless`, pinned everywhere it appears. Released
   2026-11-03, multi-arch (amd64/arm64/arm/ppc64le/s390x/riscv64), maintained upstream. Pin the
   mirror row by digest as well: the tag's manifest list is
   `sha256:c8252268760e1baba80780d63d738ef9f260fb7c7c0973c78494cb9ddd99eda1`. A mutable tag is a
   weak thing to pin while selling content-addressable trust.
2. The parameter surface is spelled in buildctl's terms, not kaniko's. `extra-build-args`
   becomes `buildctl-args` — the same idea, honestly named — and anything kaniko-shaped is the
   team's problem to migrate, announced in the CHANGELOG and verified by a census, not translated
   by a shim. The same honesty applies to the flags this plan itself writes: they are `buildctl`
   flags, not `buildx` flags, and section 1 spells them as `buildctl` documents them.
3. Caching ships by default, because a build process without one is only half of
   state-of-the-art. Registry cache in, `mode=max` out, one cache tag per build, opt-out with
   `cache=off`.
4. Provenance ships by default. What it is, and what it is not, is in "Provenance and the digest".
5. The pushed image's digest becomes a task result. kaniko can emit one with `--digest-file`;
   this task never used it. BuildKit reports `containerimage.digest` through `--metadata-file`,
   and a platform that publishes the digest it deployed is publishing something a fleet can verify.
6. The registry protocol is decided by the script with a probe, not by the operator — section
   "Registry protocol".
7. **Rootless where the environment permits it, and never by parameter.** The task ships
   rootless-only; that is the supported posture. Where a node or namespace cannot carry it, the
   fix is on the environment side or it is a follow-on ticket, and the reason is mechanical:
   Tekton does not substitute variables into a step's `securityContext`, so `builder-mode` is not
   a thing this task can grow. See "Rootless inside the container".
8. **Kubernetes 1.30 is the floor for the docker-build step.** It is the version that introduced
   `securityContext.appArmorProfile`, which the rootless step declares, so it is a product
   requirement and not a detail. It belongs in the CHANGELOG and in `README_TEKTON.md`.

---

## 1. What the new step two looks like

Two steps; step one (`configure-docker-registry-access`, Secret → `.docker/config.json`)
survives untouched, because buildctl reads that exact file via `DOCKER_CONFIG` (the image's own
`examples/kubernetes/job.rootless.yaml` says the same in a comment). Step two:

```sh
#!/bin/sh
set -eu
# ... tag resolution unchanged from the current script ...

# The build context is the clone's directory; the Dockerfile lives inside it. The task parameter
# for the Dockerfile is relative ("Dockerfile") by default, so the dockerfile directory must be
# resolved against the context or buildctl is handed the step's cwd instead.
CONTEXT="<absolute context dir>"
DOCKERFILE_DIR="$(cd "$CONTEXT/$(dirname "$DOCKERFILE")" && pwd)"
FILENAME="$(basename "$DOCKERFILE")"

export DOCKER_CONFIG="$(workspaces.workspace.path)/.docker"
export BUILDKITD_FLAGS="--oci-worker-no-process-sandbox"

# Start the daemon the way the image ships it, wait for a worker, then build through it.
# See "Rootless inside the container" for why rootlesskit is not optional.
rootlesskit buildkitd --root "$BUILDKIT_ROOT" --addr="unix://$XDG_RUNTIME_DIR/buildkit/buildkitd.sock" \
    $BUILDKITD_FLAGS &
DAEMON=$!
# The daemon needs a moment; without this wait the first buildctl answers "connection refused".
try=0
until buildctl --addr "unix://$XDG_RUNTIME_DIR/buildkit/buildkitd.sock" debug workers >/dev/null 2>&1; do
    try=$((try + 1))
    [ "$try" -le 30 ] || { echo "buildkitd did not come up" >&2; exit 1; }
    sleep 1
done

# The probe from "Registry protocol" runs here, before the build, and writes buildkitd.toml.
buildctl --addr "unix://$XDG_RUNTIME_DIR/buildkit/buildkitd.sock" build \
  --progress plain \
  --frontend dockerfile.v0 \
  --local context="$CONTEXT" \
  --local dockerfile="$DOCKERFILE_DIR" \
  --opt filename="$FILENAME" \
  $CACHE_ARGS \
  --metadata-file "$BUILDKIT_ROOT/meta.json" \
  $BUILDCTL_ARGS \
  --output type=image,name="$DESTINATION",push=true
kill "$DAEMON" 2>/dev/null || true
```

Then read the digest out of `meta.json` and write it to the step's results file.

Three things about that block are load-bearing, and a reader who skims them will get them wrong:

* **`--opt attest:provenance=`** goes in the build line with the `--output` of section "Provenance
  and the digest"; it is deliberately not spelled `provenance=true` on the output, because that is
  buildx's syntax and buildctl does not accept it. `docs/attestations/slsa-provenance.md` documents
  buildctl's form.
* **The daemon is not `buildkitd`, it is `rootlesskit buildkitd`.** Running `buildkitd` directly as
  uid 1000 does not start anything; it exits. See "Rootless inside the container".
* **No bash, no jq, no curl.** Step two runs in the BuildKit rootless image, which is Alpine with
  busybox `ash`: the current `#!/busybox/sh` shebang names a binary that image does not have, and
  the plan's earlier drafts used `[[ ]]`, `curl`, and `jq` — none of which exist there. Keep the
  script POSIX `sh`; parse JSON with `sed`/`case`, and make the registry probe with busybox `wget`.
* The shell the step runs is `/bin/sh` and the step's working directory is the workspace mount.
  The daemon's own root must be somewhere uid 1000 owns and that survives a read-only container
  filesystem, which is why section "Rootless inside the container" asks for a workspace bound as
  an empty workspace rather than pointing `--root` at the clone's PVC.

The buildctl flags, as they exist in v0.34.2 (checked against `cmd/buildctl/build.go`, so the
implementer is not asked to guess):

| kaniko (old) | BuildKit (new) |
|---|---|
| `--context=/dir` | `--local context=/dir` |
| `--dockerfile=FILE` | `--local dockerfile=DIR --opt filename=FILE` |
| `--destination=url:tag` | `--output type=image,name=url:tag,push=true` |
| `$EXTRA_BUILD_ARGS` | `$BUILDCTL_ARGS` (renamed, no translation) |
| `--push-retry=N` | a shell retry loop around the solve, see "push-retries" |
| `--cache` / `--cache-repo` (layer cache, off by default) | `--import-cache` / `--export-cache type=registry,mode=max`, on by default |
| `--digest-file` (this task never used it) | `--metadata-file` → task result |
| nothing | `--opt attest:provenance=` |

Two flags a draft of this plan got wrong, spelled out so they do not come back: the cache flags
are **`--import-cache` and `--export-cache`**; `--cache-from/--cache-to` are buildx flags and do
not exist on `buildctl`. And provenance is a frontend opt, **`--opt attest:provenance=`**, not an
attribute of `--output`.

## 2. What the build gains, and what running rootless asks for

### 2.1 Provenance and the digest, both published

`--opt attest:provenance=` attaches an SLSA-format provenance attestation (in-toto wrapped, v1 by
default) to the pushed image: build inputs, environment, source revision, step-to-layer mapping.

Do not oversell it, because the first reviewer will. The attestation is **inline and unsigned**;
BuildKit's own `builder.id` is empty in this shape. It records what BuildKit saw, faithfully; it
does not prove to a third party that the build happened the way it says. Getting a signed lineage
is its own ticket with a signing identity in it, not this one.

The digest comes from `meta.json`, which buildctl writes from the exporter response, and lands in
the task's `results:` as `image-digest`. The lane never reads results (its contract is outcomes),
so these additions are invisible to the green check — as they must be.

### 2.2 Cache: registry cache, `mode=max`, on by default

Say the comparison accurately, because kaniko has cache flags too: kaniko caches *layers* created
by `COPY`/`RUN`, keyed on the instruction and everything before it up to the `FROM` digest, and it
is off unless `--cache` is given. BuildKit caches *content-addressed build steps*, which is what
makes a rebuild skip work rather than skip layers.

The cache lives in the same registry the image does, at a sibling repository, imported with
`--import-cache type=registry,ref=<ref>` and exported with
`--export-cache type=registry,ref=<ref>,mode=max` so multi-stage intermediates are cached too.
The cache travels with the registry: every node, every run, and the next pipeline pulls it without
a warm node. `cache=off` opts out with one parameter; a cache that must be re-warmed by hand is
not a cache.

The cache key is one repository plus one tag, spelled consistently (earlier drafts spelled it two
ways): `ref = <push-image-url>-buildkit-cache:<this-build-tag>`. Boring and cheap; the day a run
proves it needs finer keys is the day a ticket adds them. One consequence to state: the registry
must accept creating a new repository per image name, which a plain pull-through registry does and
some read-only endpoints do not.

### 2.3 `push-retries`, whose meaning changes on purpose

BuildKit solves and pushes in one operation, so a retry is a re-solve — against a warm daemon and
a warm registry cache that is the cheap half of what kaniko was retrying. The parameter keeps its
name and intent, and the CHANGELOG says in one sentence that a retry may now rebuild. This is less
of a change than it sounds: the build task already runs under `retries: 1` in every pipeline that
uses it, so a rebuild on failure is already today's behaviour, and a re-solve is what those retries
do now.

### 2.4 Rootless inside the container

The requirement: the image build happens in the same TaskRun pod, in the same step, as a non-root
user, and nothing in the pod is privileged. No sidecar daemon, no `privileged: true`, no
`securityContext.runAsUser: 0`.

**How rootless actually works in this image, and the trap.** The rootless variant of the image is
rootless because its entrypoint is `rootlesskit buildkitd`, and `rootlesskit` is what creates the
user namespace in which uid 1000 becomes "root". `buildkitd` checks this itself and refuses
anything else:

> `rootless mode requires to be executed as the mapped root in a user namespace; you may use RootlessKit for setting up the namespace`

So a step that starts `buildkitd` as uid 1000 does not build anything; it exits with that sentence.
Note *who* runs it: a Tekton step that supplies a `script` does not run the image entrypoint at all,
so the image's `rootlesskit buildkitd` wrapper is a thing the script has to reproduce deliberately —
not something that happens by itself when the container starts.
Rootlesskit with no flags creates only a user namespace (its `--copy-up` flag has no default, and
`--net` defaults to `host`), which is why the socket it listens on is reachable from the step's own
process and why the build's network stays the pod's. The image ships
`buildctl-daemonless.sh` which is exactly this lifecycle (start, poll, build, reap) and the
upstream `examples/kubernetes/job.rootless.yaml` is this plan's shape of problem; copying them is
allowed and encouraged.

**What the step must declare.** All of this belongs in `build-and-push-docker-image.yml` step two,
so that rootless is a property of kubementat's task and not of whichever cluster is under it.
Current state of that step is `securityContext: { runAsUser: 0 }`, i.e. root, and that is the line
the swap actually has to change.

```yaml
    - name: build-and-push
      image: "$(params.builder-image)"
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        runAsNonRoot: true
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
        seccompProfile:
          type: Unconfined     # rootlesskit needs clone/unshare; upstream's examples set this
        appArmorProfile:
          type: Unconfined     # Kubernetes 1.30 is this step's floor; below it the node's own profile decides
```

Why `Unconfined` and why not to lean on the cluster for it: a pod whose `seccompProfile` is unset
gets whatever the kubelet decides, and the kubelet applies the runtime default only if it was
started with `--seccomp-default`, which k3s is not by default. That is an accident of this
installation, not a guarantee: turn that flag on during a hardening pass and the step breaks. Same
for AppArmor. Declare it. Two caveats that must survive into the CHANGELOG:
* A namespace under **PodSecurity `restricted`** rejects `seccompProfile: Unconfined` outright
  (restricted requires `RuntimeDefault` or a localhost profile). That is the case this plan names
  "rootless is not permitted here", and the environment fixes it: a `pod-security.kubernetes.io/exempt-*`
  exemption on the CI namespace, or the namespace is not `restricted`. Say so in the docs; do not
  discover it in a red run.
* `appArmorProfile` needs Kubernetes 1.30, which is this step's stated floor. Below it, the field
  is dropped at apply and the node's own AppArmor profile decides — the measurement below is the
  answer for that node.

**Node prerequisites the step cannot declare**, listed for the spike rather than argued:
`user.max_user_namespaces` non-zero (the buildkit docs point at
`examples/kubernetes/sysctl-userns.privileged.yaml` for the shape of the fix), no
AppArmor `kernel.apparmor_restrict_unprivileged_userns=1` (Ubuntu 24.04 turns that on), and
`/etc/subuid`+`/etc/subgid` present, which the image bakes (`user:100000:65536`). Overlayfs under
rootless needs kernel >= 5.11; below that the fallback is the `fuse-overlayfs` snapshotter, and
below 4.18 `native`. `fuse-overlayfs` needs `/dev/fuse`, which a plain pod does not hand out, so the
ladder on an old kernel is overlayfs, then `native`, and if neither performs the node is simply one
of the environments where rootless is not available.

**How it is proved, in the step, in one line each.** The spike writes these; the task logs them and
does not fail on them:

```sh
id -u                                                   # must be 1000, not 0
grep -m1 Seccomp /proc/self/status                      # 0 = disabled, 2 = filtered
cat /proc/self/attr/current                             # want: unconfined
cat /proc/self/uid_map | head -1                        # first field non-zero = mapped, not real root
ps -o comm= -p 1                                        # the daemon's view: rootlesskit, then buildkitd
buildctl debug workers --format '{{.Information}}' | head -1   # which snapshotter is in use
```

Plus one thing outside the pod: `kubectl get pod -o jsonpath` on the TaskRun's pod, asserting no
container requests `privileged`. Rootless that cannot be pointed at is a claim, not a property.

**Rootless where possible, and what "not possible" means in practice.** The task ships one
posture: rootless, declared in the step. Two facts decide what happens when that fails.

* The environment owns the fix in the common cases: `user.max_user_namespaces`, the Ubuntu
  `apparmor_restrict_unprivileged_userns` switch, and the `restricted`-namespace exemption are
  cluster-side settings, and the spike's measurements name which one a given node is missing.
  kubementat documents them; it does not start shipping privileged pods to work around them.
* The task cannot hedge. Tekton substitutes variables into a step's `image`, `command`, `args`,
  `script`, `env` and `workingDir` — **not** into `securityContext` — so a
  `builder-mode=rootless|rootful` parameter is not implementable as written and would be a lie in
  the parameter list. The rootless fields above are therefore hard requirements of the step, not
  defaults a team can flip.

If a real deployment turns up where the environment cannot be fixed, the rootful fallback is a
second, gated step in the same task — the same buildctl invocation, the rootful image, a privileged
`securityContext`, and a step-level `when` keyed off the rootless step's result (step-level `when`
exists upstream since pipeline PR 7746 and is evaluated by the entrypoint). That is a ticket with
its own evidence behind it, and it is deliberately not part of this plan: shipping a privileged
step by default to cover an environment nobody has measured is how this plan loses the argument it
was written to win.

## 3. The surfaces and the tickets

**T0 — spike, one afternoon.** Rootless BuildKit in a TaskRun on the lane's k3s, from the pinned
image. No opinions, seven answers: does `rootlesskit buildkitd` start unprivileged with the
`securityContext` above; which snapshotter does the kernel give; does buildctl find auth through
`DOCKER_CONFIG` and push; is the socket reachable from the step's own process; what do the
measurements in "Rootless inside the container" report; **which environment setting, if any, this
node is missing** (`user.max_user_namespaces`, AppArmor, PSA `restricted`) so the fix is assigned
to a cluster rather than invented in a task; and what the daemon's stdout looks like when a build
fails. This is where the plan's "rootless where possible" gets its definition of "possible".

**T1 — the task.** `src/kubementat/core/assets/tekton_ci/tasks/build-and-push-docker-image.yml`
rewritten:
* `builder-image` default: `moby/buildkit:v0.34.2-rootless`.
* step two: `securityContext` exactly as in "Rootless inside the container"; POSIX `sh`;
  `buildctl-args` and `cache` parameters replacing `extra-build-args`; `results: [image-digest]`.
* step one: untouched, still writes `$(workspaces.workspace.path)/.docker/config.json`; step two
  exports `DOCKER_CONFIG` to point at it.
* a second workspace, bound `emptyWorkspace: true`, mounted for the daemon's root and its
  `meta.json`; the clone's 100Mi PVC stays sized for the clone. The task cannot declare a volume,
  so an empty workspace is the mechanism.
* the retry loop around the solve, and the readiness wait, in the script, because nothing else in
  the step does it for them.

The `securityContext` block is a requirement of this task, not a knob: Tekton will not substitute a
parameter into it, so nothing upstream of the task can make the step rootful or rootless by
configuration.

**T2 — every consumer of the task, in one change.** This is not optional and not a follow-up. The
task and its pipelines are a single contract: a pipeline that passes a parameter the task no longer
defines fails validation before it schedules, and a pipeline that keeps its own kaniko default
hands a kaniko image to a buildctl script. Every file below references the task by name and must
land in the same commit.

| File | What it is | What changes |
|---|---|---|
| `src/.../tekton_ci/pipelines/docker-build-pipeline.yml` | the app-build pipeline | `builder-image` default (line ~61) to the pinned BuildKit image; `extra-build-args` → `buildctl-args` (param + the `taskRef` params); `cache` added and forwarded |
| `src/.../tekton_ci/pipelines/build-pipeline-ci-images.yml` | builds the CI images | same three changes (default line ~60, param ~62, taskRef ~105) |
| `src/.../tekton_ci/pipelines/build-pipeline-public-ci-images.yml` | public CI images | default (line ~31) and param forwarding; it does not pass `extra-build-args` today, so it needs `buildctl-args`/`cache` added if the operators are to be able to set them |
| `examples/tekton_ci/pipelines/build-pipeline-flutter-example.yml` | shipped example | **deleted**, with its run file `examples/tekton_ci/pipeline-runs/build-pipeline-flutter-example-run.yml`. It is broken today: it passes `docker-registry-base-url`, `tekton-ci-image-name`, `tekton-ci-image-tag` to `build-and-push-docker-image`, parameters that task does not define, so it cannot run at any version of this task. The migration is not a chance to keep a known-bad shape in sync |

The example removal has a tail, and skipping it leaves sentences that read as true while the tree
says otherwise:

* `tests/test_a_step_only_passes_params_the_task_it_calls_declares.py` pins the example's two
  undeclared-parameter steps in `PREEXISTING_UNDECLARED_PARAMS`. That dict is a measurement of the
  tree, so deleting the files deletes the entries — leaving them is how the file's own claim that
  it measures stops being true.
* `tests/test_the_configuration_root_in_ci_is_a_clone_and_not_the_product_checkout.py` names the
  example's *absence from a set* as half of one assertion, in a comment. Rewrite that sentence or
  the comment is stale while the code passes.
* `tests/test_tekton_manifest_api_versions.py` counts manifests across both trees and asserts a
  floor of 50; the tree is at 54 today, so removing two files leaves 52. The floor is not the
  blocker and must not be lowered to make the deletion land.

`build/lib/**` also contains copies of all of these. It is generated output. The census excludes it,
the build excludes it, and nobody should find it in a grep result and be told it is a migration.

A note on the mechanism, because it explains why one commit is required: the pipelines own their
own parameter blocks, and each also declares its own default for `builder-image`. The task's default
only ever applies to a pipeline that does not pass the parameter — which is what the example does.
So "we changed the task" migrates nothing that a pipeline overrides, and renaming a parameter
without renaming it at every call site is a hard validation failure, not a silent fallback.

**T2 also owns the mirrors.** `src/kubementat/core/assets/templates/environment/mirrored_docker_images.json.template`
loses the `kaniko` row and gains `buildkit` (source `moby/buildkit:v0.34.2-rootless`, destination
mirrored, digest-pinned) — an airgapped cluster pulls one image and its own cache.

**T3 — the census, which replaces migration.** `git grep` every team's configuration, every shipped
pipeline, every example, and every doc for kaniko-shaped values in what is now `buildctl-args`
(`--skip-tls-verify-registry`, `--insecure`, `--single-snapshotter`, kaniko's own cache flags), and
for anyone still instantiating `extra-build-args` in a run. The finding is the deliverable: teams
translate their own values, the CHANGELOG announces it, and no code path pretends to understand
dead syntax.

**T4 — the lane, which is the test.** `DOCKERBUILD_BUILDER_IMAGE` in `tests/e2e/harness/config.py`
moves from the maintained kaniko-fork image to the pinned BuildKit image — the only harness line the
swap is allowed to touch. The lane's fixture overrides exactly one pipeline parameter
(`builder-image`, `tests/e2e/fixtures/tekton/docker-build/run.yml.tmpl`), which is what makes it
the swap's proof rather than a second implementation. A green `--lane tekton --scope full` is the
swap's proof, and it reads nothing in `src/` about BuildKit. If the cache or the probe is wrong, the
lane red-lands on `assert:dockerbuild-run-succeeded` with the TaskRun's own words, not on five new
asserts nobody asked for.

5. `README_TEKTON.md`: kaniko's link becomes BuildKit's, and the Kubernetes 1.30 floor for the
docker-build step is stated there with the `restricted`-namespace caveat. `CHANGELOG.md`: the archived
upstream and the maintained-fork reality; pinned rootless image, digest-pinned in the mirror;
`extra-build-args` → `buildctl-args` with kaniko flags unsupported; registry cache on by default
with `cache=off`; inline unsigned provenance and what that does and does not buy; the
`image-digest` result; the registry-probe behaviour; the step's `securityContext`, the 1.30 floor,
and that rootless is unavailable under a `restricted` PodSecurity namespace without an exemption;
and that the flutter example is gone. Every line
a team needs to re-configure themselves, in one place.

**Done when:** unit suite green; `--lane tekton --scope full` green on BuildKit; the measurements in
"Rootless inside the container" are in the run's artifacts and say non-root, mapped, unconfined, no
privileged container; `git grep -i kaniko src/` is empty; `git grep -il flutter examples/` is empty;
every file in the T2 table is changed or deleted (say so per file, or it did not happen), including
the two test files the example removal touches; the Kubernetes 1.30 floor and the `restricted`
namespace caveat are in `README_TEKTON.md` and the CHANGELOG; a second run of the same lane is
visibly faster off the registry cache — measured, not asserted, and expect that to be near-zero on a
fixture this small; CHANGELOG complete.

## 4. Registry protocol: a probe, not a new parameter

BuildKit's resolver does what the daemon's config says and otherwise fails with `server gave HTTP
response to HTTPS client` — a real and documented trap, and `http` plus `insecure` on one host is
what makes it worse (moby/buildkit PR 3382, issue 4458). So the script probes before the daemon
builds:

1. `https://<push-host>/v2/`. An answer means TLS works; write nothing.
2. Otherwise `http://<push-host>/v2/`. An answer means the registry is plain HTTP: write the
   `[registry."<host>:<port>"] http = true` stanza and nothing else.
3. Neither answers: fail, with both probe outputs. A registry that is simply unreachable must not
   be quietly downgraded to plain HTTP, because that turns a cluster problem into a confusing
   registry-side 404 and the run's own failure was the useful evidence.

Never both `http` and `insecure`, never a flag the operator must know the name of. The one GET per
step buys what kaniko's resolver did silently. The day a registry genuinely needs `insecure`
(self-signed HTTPS, not plain HTTP) is a small ticket for a `registry-ca` parameter — an addition,
not a regression. The lane is the probe's happy path: its fixture registry answers HTTP.

## 5. Out of scope, stated so nobody builds it quietly

* **Multi-arch image builds.** Real, but it means QEMU binfmt deployment and cross-compiled
  layers; single-arch k3s clusters do not need it. The pinned builder is multi-arch, so the day it
  is wanted the ticket is additive.
* **`buildx` as the CLI.** buildx is a frontend around a daemon plus builder-instance bookkeeping
  (`docker-container`, `kubernetes` drivers). Inside a Tekton step, against a daemon started in that
  same step, `buildctl` is the answer, and nothing here is weaker for the choice — except that the
  plan must then write buildctl's own flags, which it now does.
* **A central, shared, long-lived BuildKit.** Different product decision, its own daemon, cache
  policy, supply chain, and research before its own plan.
* **Pod-level user namespaces** (`spec.hostUsers: false`, GA in Kubernetes 1.33) as an alternative
  to rootlesskit inside the step. It is a real option, upstream ships manifests for it, and its
  manifests are "not really privileged" — which is the whole of this plan's argument against it.
  Choosing it would be a decision, not a fallback, and it is not this plan's decision.
* **Tekton's own user-isolation feature flags.** They change how step users and workspace
  ownership work, and rootlesskit inside a step that Tekton is already faking users for is a
  debugging conversation nobody needs while the swap is landing.
* **SBOM beyond provenance.** provenance is enabled now; `sbom` is a parameter-sized follow-on once
  someone names the tool that reads it.
* **Signing the provenance or the image.** Not this ticket, and this plan does not claim it.
* **A rootful fallback step inside the task.** It is implementable (same buildctl, rootful image,
  privileged `securityContext`, gated by a step-level `when` on the rootless step's result), and it
  is a ticket that needs a measured environment asking for it. Shipping one by default to hedge is
  the quiet version of the decision this plan argues against.

## 6. What could go wrong, stated before it does

* **The kernel or the policy refuses rootless.** The measurements in "Rootless inside the container"
  say which: `Seccomp: 2` under a runtime default profile, AppArmor restricting unprivileged
  user namespaces, `user.max_user_namespaces=0`, or an overlayfs mount failing on an old kernel.
  Each is answered by the fallback ladder in that section, and the chosen fallback is written down
  in the CHANGELOG rather than left in the spike log.
* **A namespace enforces PodSecurity `restricted`.** `seccompProfile: Unconfined` is rejected at
  admission, so the step never starts and the failure is a rejection, not a build error. The answer
  is an exemption on that namespace or the cluster's posture changing — not a kubementat parameter,
  and not a privileged step shipped to everybody else.
* **The daemon starts and the socket is unreachable.** Symptom is `connect: no such file or
  directory` or `connection refused`, and the answer is that the daemon was started without its
  namespace, or that `XDG_RUNTIME_DIR` is not what the image set it to. The readiness loop turns
  this into a legible error either way.
* **The workspace is not readable by uid 1000.** The clone step runs as root, so anything it leaves
  mode 600 is unreadable in the mapped namespace, and buildctl's context checksum fails on it. If a
  run shows `permission denied` inside the context, the fix is at the clone step's umask or an
  explicit `chmod -R a+rX` on the context, not a `--user` change here.
* **An HTTPS registry that fails the probe** (broken proxy chain) would have been silently pushed
  over plain HTTP; the third branch of section 4 fails the run instead. That is rarer than today's
  no-probe status quo, and loud.
* **First cold pull of the pinned image** lands in the build window of a fresh node; the lane budget
  already accounts for it, and T2's mirror is the airgap answer.
* **Cache tag litter.** One extra repository per image name, one tag per tag, bounded by the same
  retention policy teams already set for their images.
* **A half-migrated installation.** A pipeline that kept its kaniko default, or a run that still
  passes `extra-build-args`, fails in the first minutes rather than producing a wrong image. That
  is why T2 is one change across all four files and the census is the deliverable.
