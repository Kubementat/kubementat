# Concourse CI (opt-in)

[Concourse](https://concourse-ci.org/) arrives in kubementat as an opt-in platform component —
the second member of the `extra` group, beside openbao. The release is one chart
(`concourse/concourse` 20.3.3, Concourse 8.3.3) that installs the web (ATC + TSA), a StatefulSet
worker, and a bundled postgres. It is **disabled by default**: a tree that says nothing installs
and runs nothing here. The Tekton surface (`kmt tekton`, see [README_TEKTON.md](README_TEKTON.md))
stays the platform's default CI; this page says what runs where and why both stay.

Everything factual here was proven on a cluster: the Phase-1 prototype (2026-11-17, chart
20.3.3 on kind), the component work, and the `concourse` e2e lane, which passed 72/72 assertions
including one real pipeline run (`uv run python tests/e2e/suite.py --lane concourse --scope
install`, see [README_E2E_TESTS.md](README_E2E_TESTS.md)). The commands below name real keys and
real failure modes, all observed.

## What ships, and the three knobs

| what | where | note |
|---|---|---|
| the release block | `src/kubementat/core/assets/values/helmfile.yaml.gotmpl` | labels `component: ci`, `component_name: concourse`, `group: extra` |
| shipped values | `src/kubementat/core/assets/values/ci/concourse/values.yaml` | the shape that worked, with every chart-default key stripped |
| namespace entry | `src/kubementat/core/constants.py` (`DEFAULT_NAMESPACES`) | load-bearing twice: knob export + uninstall sweep |

The config keys derive from the component name (`keys.py`, no code spells them out):
`CONCOURSE_ENABLED` (default `false`), `CONCOURSE_VERSION` (default `20.3.3`) and
`CONCOURSE_DEPLOYMENT_NAMESPACE` (default `concourse`). The chart pin carries its own argument:
chart 20.3.3 **is** Concourse 8.3.3, and the shipped values pin `imageTag: "8.3.3"` beside it —
an upgrade bumps both, never one (see the runbook).

Values layering is the platform's, stated once in
[README_PLATFORM_CONFIG.md](README_PLATFORM_CONFIG.md): shipped default →
`<root>/<env>/values/concourse.yaml` → `<root>/<env>/values.encrypted/concourse.yaml` → the team
layers. Key material reaches the release from the encrypted layer only.

## Coexistence with Tekton

**The decision this implements is coexist, not replace.** Tekton stays what it is; Concourse
arrives beside it, installed by nobody unless asked.

What runs where, as a rule and not a slogan:

| work | runs on | because |
|---|---|---|
| building and deploying this repository's own examples/automations, the `kmt tekton` verbs, the generated Tasks/Pipelines and triggers | **Tekton** | it is wired end-to-end through `kmt tekton` and ships in every default install |
| pipelines authored in Concourse's own YAML, container-first workloads, `fly`-driven promotion flows | **Concourse** | that is the surface Concourse is for; kubementat delivers the runtime, the pipelines live in Concourse |
| both, same cluster | allowed | Concourse is `extra`-group opt-in, installs nothing wound-off, so no default lane or install changes shape |

Why both stay, honestly:

1. The Tekton surface is not a UI — it is working commands (`kmt tekton generate-deploy-pipeline`,
   `generate-triggers`, `run-pipeline`) and installed controllers. Retiring them is a product
   decision, not an integration side effect.
2. There is no shared auth or secret store between the two, so a replacement would rewrite both
   pipeline trees **and** the credential plumbing. Nothing in the integration plan of 2026-11-17
   settled that scope; until it does, each system owns the pipelines it owns.
3. Concourse gives something Tekton does not: pipelines as first-class, versioned YAML, applied
   with `fly set-pipeline` by whoever owns the pipeline.

The practical failure to avoid is not "two CIs exist" — it is one application deployed by two
sources of truth. Pick per pipeline, not per cluster.

## Enabling it

**Order matters, and the first step is not kubementat.** Key material must exist before the first
apply (the chart's encryption gate says so; see *Keys*), and the namespace must exist with its
PodSecurity labels before the workers can be admitted.

1. **Generate keys** (next section) and write the encrypted layer
   `<root>/<env>/values.encrypted/concourse.yaml`.
2. **Label the namespace.** kubementat's `createNamespace: true` creates a namespace it cannot
   label, and no Namespace manifest ships, so this is an operator step (the whole rationale is in
   the shipped values file):

   ```
   kubectl create namespace concourse                      # if absent
   kubectl label namespace concourse \
     pod-security.kubernetes.io/enforce=privileged \
     pod-security.kubernetes.io/warn=privileged
   ```

   Worker pods carry `securityContext.privileged: true`. A namespace labeled `enforce=privileged`
   admits them; a `restricted` one rejects them with `FailedCreate ... violates PodSecurity`, and
   the e2e lane observed exactly that verdict both ways. On a cluster whose own default already
   admits privileged pods, this is insurance, not a precondition.
3. **Wind the knob on:**

   ```
   uv run kmt config set <environment> CONCOURSE_ENABLED=true
   ```

4. **Install by its label:**

   ```
   uv run kmt component install <environment> concourse
   ```

   which is the declarative `helmfile apply -l component_name=concourse` path, identical to
   openbao's.
5. **Verify** — the lane's three facts, in this order:
   `concourse-web` Available, the pods scheduled (the worker without PSA labels never gets past
   `FailedCreate`), and the health API green:

   ```
   curl http://<web-service>:8080/api/v1/health
   {"status":"ok","database":{"status":"healthy"},"workers":{"status":"healthy","total":1,"running":1}, ...}
   ```

**The `fly` CLI does not ship inside the web image** (verified with chart 20.3.3 / image 8.3.3:
no `fly` in the container). Download the release's own `fly` matching `imageTag` —
`fly-<version>-linux-amd64.tgz` from the Concourse release page — and pin it to the image, the
same two-pins rule. The same rule applies to `fly sync` after an upgrade (runbook).

Two `fly` shapes the lane settled on live servers, worth knowing before your first red minute:
`fly set-pipeline` does not reliably consume a piped `y` through a non-TTY exec — `-n` is the
documented flag and behaves. And `fly builds` prints no header row: the data row *is* line one,
column 2 is the build reference (not the status), the status word is past tense (`succeeded`),
and `-j` needs `<pipeline>/<job>` (`-j kmt-e2e/hello`).

## Keys — provisioning them once, the right place

The chart ships **demo private keys and a `test:test` user as values defaults**, and their public
halves are published with the chart. A release that renders the chart's own defaults is a release
whose keys everybody owns. The shipped values therefore overwrite every key default with the
empty value and fix `concourse.web.encryption.enabled: true`, so enabling without real keys fails
early with a message naming the missing `encryptionKey` — instead of quietly deploying somebody
else's. This is deliberate: the failure is the point.

Generate everything **outside helm**, the recipe the prototype proved:

```sh
# RSA/SSH keypairs, generated inside the concourse image so the binary matches the release:
docker run --rm --entrypoint sh -v "$PWD/keys":/keys concourse/concourse:8.3.3 -c \
  '/usr/local/concourse/bin/concourse generate-key -t ssh -f /keys/tsa_host_key && \
   /usr/local/concourse/bin/concourse generate-key -t rsa -f /keys/session_signing_key && \
   /usr/local/concourse/bin/concourse generate-key -t ssh -f /keys/worker_key'

# at-rest encryption key — the chart wants exactly 16/24/32 chars, so 32-char hex:
openssl rand -hex 16

# the admin user's password hash (bcrypt cost 12):
docker run --rm --entrypoint sh python:3.12-slim -c \
  "pip install -q bcrypt && python3 -c \"import bcrypt; print(bcrypt.hashpw(b'<PASSWORD>', bcrypt.gensalt(rounds=12)).decode())\""
```

and land them in `<root>/<env>/values.encrypted/concourse.yaml` — the git-crypt layer
([README_GIT_CRYPT.md](README_GIT_CRYPT.md)), which the e2e lane uses the same way (per-run keys,
bcrypt `admin`, same file):

```yaml
concourse:
  web:
    auth:
      mainTeam:
        localUser: admin   # the chart default `test` has a published password; do not leave it
secrets:
  create: true
  hostKey: |               # tsa_host_key (PEM)
    ...
  hostKeyPub: ssh-rsa AAAA...   # tsa_host_key.pub, single line
  sessionSigningKey: |     # session_signing_key (PEM RSA)
    ...
  workerKey: |             # worker_key (PEM)
    ...
  workerKeyPub: ssh-rsa AAAA... # worker_key.pub, single line
  localUsers: "admin:<bcrypt-hash>"
  encryptionKey: "<32-char-hex>"
```

Three facts about this block, all observed:

- A worker **inside** the release registers through the `workerKeyPub` the web mounts from this
  same Secret and needs no extra entry. A worker **outside** it (the hybrid fleet) registers only
  if listed by team: `secrets.teamAuthorizedKeys: [{team: main, key: "<worker_key.pub>"}]`.
- The plain layer must never carry any of this; a plain layer with a private key in it undoes the
  whole empty-value sanitizing (the rule is asserted by a test, not a comment).
- Losing `encryptionKey` makes an existing deployment's stored credentials and pipeline
  configuration unreadable. It is part of the backup artifact — see the backup section.

**The other door, not walked:** the integration plan also allows pre-created cluster Secrets plus
`secrets.create: false` (writable via
`uv run kmt kubernetes create-secret-from-json <environment> <namespace> <secret-name> <file.json>`,
whose data entries are the JSON file's keys as strings). No cluster evidence from this work backs
the exact Secret name/key mapping that path needs on chart 20.3.3 — the encrypted layer is the
proven route, and this is named as the alternative, not recommended.

## External database recipe

Bundled postgres is dev-grade on purpose: the database is this release's single point of failure
and a backup story does not begin inside the same chart. The production recipe is one plain-layer
statement plus credentials in the encrypted layer:

`<root>/<env>/values/concourse.yaml`:

```yaml
postgresql:
  enabled: false
concourse:
  web:
    postgres:
      host: "<external-host>"
      port: 5432
      database: atc
      sslmode: require        # verify against `helm show values concourse/concourse --version 20.3.3`
```

and in `values.encrypted/concourse.yaml` the credentials (`secrets.postgresqlUser`,
`secrets.postgresqlPassword`, and the chart's TLS/role keys if the provider uses them) — the
chart wires them to `web` with the same mechanism as the key material. The one rule that survives
any provider: this is a documented example, not a shipped default; a tree that says nothing
keeps the bundled dev database, and the values file's comment says so in the same words.

## Backup recipe

The database is `atc` (the chart's default name). `pg_dump` it from somewhere that already runs
on a schedule — kubementat's `cluster-cronjobs` namespace exists for exactly this
(namespace-only component, already swept by `kmt uninstall`). Example CronJob — copy, fill the
host/secret, apply, check the first dump lands:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: concourse-pgdump
  namespace: cluster-cronjobs
spec:
  schedule: "17 3 * * *"          # one dump a day, off the :00 marks
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
          - name: pgdump
            image: postgres:17    # the bundled image is 17; match the server, not your taste
            command:
            - sh
            - -c
            - 'pg_dump "host=$PGHOST user=$PGUSER dbname=atc sslmode=require" -Fc
               -f /backups/atc-$(date -u +%Y%m%d).dump'
            env:
            - name: PGHOST
              value: "<db-host>"
            - name: PGUSER
              value: "<dump-user>"
            envFrom:
            - secretRef:
                name: concourse-pgdump-credentials   # PGPASSWORD=<...>
            volumeMounts:
            - name: backups
              mountPath: /backups
          volumes:
          - name: backups
            persistentVolumeClaim:
              claimName: cluster-cronjobs-backups   # whatever this cluster calls its backup claim
```

and restore is:

```sh
pg_restore --clean --if-exists -d atc atc-<date>.dump
```

**The warning the runbook exists for:** `secrets.encryptionKey` is what makes the dumped
credential and pipeline-config rows readable. **A dump without the matching key set is a
well-formatted pile of unreadable bytes** — dump and keys ship and rotate as one artifact, or
the restore fails at runtime with a wall of decryption errors and no obvious cause. (Postgres
major bumps of the bundled image also restore-from-dump rather than in-place upgrade; same page,
fewer surprises.)

## Monitoring

The metrics endpoint is the web pods' `:9391` (`concourse.metrics.bindPort`), and
`/api/v1/health` is the authoritative readiness answer — the e2e lane treats it as one.

The documented addition to kubementat's prometheus follows the file's own precedent
(`monitoring/prometheus_values.yaml` already uses `extraScrapeConfigs`; it is a *string*, so the
`|` is required):

```yaml
extraScrapeConfigs: |
  # ... keep whatever is already in this block ...
  - job_name: 'concourse'
    static_configs:
      - targets: ['concourse-web.concourse.svc.cluster.local:9391']
```

**This is shipped as docs-only, deliberately:** `prometheus_values.yaml` is read by every install
of every environment, and a scrape target for a component most trees do not install would put a
permanently-down job into all of them. Add the block to the *environment* values layer only where
`CONCOURSE_ENABLED=true`. Alert at minimum on worker health — the failure that needs to page is
`workers: 0 healthy` while pipelines queue, and the stalled-worker runbook step below is what
fixes it.

## Runbook

### Upgrade

Concourse upgrades are **not zero-downtime**: the web pods migrate the database serially under a
DB lock, and older web pods are confused mid-roll. The order the plan extracted from upstream,
and the commands that implement it here:

1. Bump **both pins together** — `CONCOURSE_VERSION` (static.json or env layer) **and**
   `imageTag` in the values layer. One without the other runs an unjudged binary under a chart
   somebody else validated.
2. `uv run kmt component install <environment> concourse` — the same declarative apply as always.
   Let the web rollout finish before touching workers, and do not scale workers concurrently with
   a rollout; the upgrade and the scale path collide.
3. `fly sync` — aligns the `fly` web assets so the next CLI command speaks the server's version.
   Remember the CLI itself is pinned separately (the image ships no `fly`); a version-skewed
   client is its own failure mode.
4. Major jumps: verify the path against the Concourse upgrade notes before automating it — from
   very old releases the jump must transit v3.6→v4; chart-version pinning keeps most upgrades
   chart-level, which is the point of the pin.

### Scaling

- **Web:** `concourse.web.replicas`, plain Deployment. The chart offers no web HPA; a template
  that does not exist is a config key that should not be offered.
- **Workers:** `concourse.worker.replicas` is the honest knob. The chart's HPA
  (`concourse.worker.autoscaling.*`) exists, but Concourse exposes **CPU and container counts,
  never queue depth** — autoscaling here is garnish, not the scale story, and the shipped values
  keep it empty with this caveat attached.
- **Retire a worker gracefully:** `kubectl delete pod <worker>` lets it drain, bounded by the
  worker's termination grace period.

### Stalled worker

Symptom: `workers: 0 healthy` sticks, and nothing schedules. A pod replacement alone does not fix
it — the stale registration must go before the replacement can join:

```sh
fly land-worker -d <worker-name>   # if it is still reachable and drainable
fly prune-worker -d <worker-name>  # what the runbook does for the stalled case
```

then verify with `fly workers` and the health API. Rehearse this before you need it during a
queue.

### Teardown

What each command actually removes, settled on real clusters (the lane asserts all of it):

1. `uv run kmt component uninstall <environment> concourse` — removes the release. Two things
   stay, by design, not by accident: **the PVC claims**
   (`concourse-work-dir-concourse-worker-0`, `data-concourse-postgresql-0`) survive
   `helm uninstall` because `volumeClaimTemplate` claims are never release-owned.
2. `uv run kmt uninstall <environment>` — the ordinary sweep. Because `concourse` is in
   `DEFAULT_NAMESPACES`, this is where the claims actually die; the lane's `concourse-claims-swept`
   assertion says exactly that.
3. **Team namespaces outlive both.** Concourse creates `concourse-<team>` namespaces at runtime
   (`concourse-main` for team `main`); no manifest owns them and no kubementat label names them,
   so neither the uninstall nor the sweep touches them, and an enabled-then-uninstalled release
   leaves `concourse-main` Active. Delete what your own pipelines caused:

   ```sh
   kubectl delete namespace concourse-main
   ```

   Whether the product should sweep team namespaces on its own is an open question this tree
   names rather than answers; until it is answered, deleting them is the operator's last step.
4. If the keys were written for this tree only, decide deliberately: the encrypted layer is
   either kept for the next install or destroyed with the release — a restored cluster that
   re-installs under the old keys is a different (and worse) leak than a deleted file.

### What is rehearsed and what is not, stated plainly

The install/uninstall half of this runbook is proven by the e2e lane. Upgrade order, restore-with-
keys, and a killed worker were **not** drilled on a staging cluster here — those belong to the
Phase-3 exit-criterion drills and remain the goal's remaining work.

## Where the truth lives

- release block + rationale: `src/kubementat/core/assets/values/helmfile.yaml.gotmpl` (concourse block)
- shipped values: `src/kubementat/core/assets/values/ci/concourse/values.yaml`
- the lane that proves it: `docs/README_E2E_TESTS.md` + `tests/e2e/` (`concourse` lane)
- the CI it coexists with: `docs/README_TEKTON.md`
- layering and git-crypt: `docs/README_PLATFORM_CONFIG.md`, `docs/README_GIT_CRYPT.md`
