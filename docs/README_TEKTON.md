# Tekton Pipelines
[Tekton](https://tekton.dev/) serves as the main CI tool for all automations running on the [kubernetes cluster](https://kubernetes.io/).
The information below describes the initial setup and usage of the automated tasks for building and running applications on the k8s cluster.

# Install prerequisites
- see pre-requisites in [README.md](../README.md)
- Optional: Install tekton VSCode Plugin
  - https://marketplace.visualstudio.com/items?itemName=redhat.vscode-tekton-pipelines
- Optional: Install kubernetes tools for VSCode Plugin
  - https://marketplace.visualstudio.com/items?itemName=ms-kubernetes-tools.vscode-kubernetes-tools

# Tekton versions and API versions

The versions kubementat installs are the `TEKTON_VERSION_*` keys of the environment's own
`static.json` — read from there when the environment declares them, and answered by
`constants.DEFAULT_STATIC_ANSWERS` inside the installed `kmt` when it does not (R-12.1). No command
writes those three keys into a directory; a fresh `static.json` does not contain them. The API version
it *speaks* is a constant in
`src/kubementat/core/config/config.py` (`TEKTON_API_GROUP` / `TEKTON_API_VERSION`). Both halves belong in the same
tree: writing v1 manifests against a 0.x controller, or reading v1beta1 objects from a v1-only
controller, fails the same way — a 404 per resource.

| Component | Pinned version | API group / version kubementat writes | Config key |
|---|---|---|---|
| Tekton Pipelines | **1.17.1** | `tekton.dev/v1` | `TEKTON_VERSION_PIPELINE` |
| Tekton Triggers | 0.37.2 | `triggers.tekton.dev/v1beta1` | `TEKTON_VERSION_TRIGGERS` |
| Tekton Dashboard | 0.73.0 | — (a UI, no resources kubementat applies) | `TEKTON_VERSION_DASHBOARD` |

The Dashboard is what gates the set: its 0.73.0 release notes read "This Dashboard release supports
Pipelines v1.15.x LTS, v1.17.x, and Triggers v0.37.x LTS", and 1.17.1 + 0.37.2 is the pair inside that
statement. Pipelines 1.17.x is that cycle's current release and 1.15.x is the same cycle's LTS, so a
cluster held back to the LTS still gets a Dashboard that declares it.

`tekton.dev/v1` has been the serving version since Pipelines **1.0**, so the v1 flip needed no
controller bump of its own — but upstream still *serves* `tekton.dev/v1beta1` across all 1.x releases,
so a v1-only tree runs unchanged on both 1.15.x and 1.17.x. Triggers is deliberately left on
`v1beta1`: the 0.37 release.yaml declares `v1beta1` served and stored on every trigger CRD and never
`v1`, so the six generator templates this kmt ships (the directories under
`Config.tekton_trigger_template_dir`) keep that apiVersion, and only the `PipelineRun` they embed
says `tekton.dev/v1`.

Check what your cluster serves:
```
kubectl api-versions | grep tekton.dev
# tekton.dev/v1                                   <- what kubementat needs
# triggers.tekton.dev/v1beta1                     <- what the trigger resources need
```

## Where the release manifests come from

`install()` (`src/kubementat/infrastructure/tekton/install.py`, run by `uv run kmt install`) builds
every URL it applies from one constant, `TEKTON_RELEASE_BASE_URL` (currently
`https://infra.tekton.dev/tekton-releases`), and never from a second place. Upstream moved where
releases are served and the bucket this address used to name holds only what it happened to keep,
which is why the base URL and the three pins land in one commit: pinning a version the old bucket no
longer serves is an install that dies on the first `kubectl apply`.

Three things the install applies that are not obvious, because a missing one does not look like a
missing release:

- `triggers/previous/v<VER>/interceptors.yaml` is applied alongside the Triggers release. The trigger
  controller reads `config-triggers-core-interceptors` from it, so without it the controller starts and
  crashes instead of failing the install.
- `dashboard/previous/v<VER>/release-full.yaml` (not `release.yaml`) — that is the one carrying the
  extensions kubementat's tunnel endpoint expects.
- After the three applies, the install re-reads the API versions from the cluster itself
  (`preflight_installed_tekton_api_versions`) rather than trusting the `apply` exit code. A controller
  that does not serve `tekton.dev/v1` or `triggers.tekton.dev/v1beta1` fails the install with the
  version it was asked to install. No fallback, no override.

# Migrating from `tekton.dev/v1beta1`

The v1 flip is **hard**: there is no compatibility mode, no feature flag, and no environment variable
that puts kubementat back on the beta API. `Config.TEKTON_API_VERSION` is the one place the version is
read, and it is `v1`. A tree that mixes the two does not work "mostly" — it applies part of its
manifests and 404s on the rest.

| `tekton.dev/v1beta1` | `tekton.dev/v1` | What happens if you leave the old shape in place |
|---|---|---|
| `PipelineRun.spec.serviceAccountName` | `PipelineRun.spec.taskRunTemplate.serviceAccountName` | v1 does not read the old key. The run executes with the namespace's default service account, so a task that deploys into another namespace fails on RBAC. |
| `PipelineRun.spec.timeout` | `PipelineRun.spec.timeouts.pipeline` | v1 does not read the old key. The deadline falls back to the controller default (`defaultTimeoutMinutes`, 60 minutes unless the operator changed it), so a hung run outlives what its author intended. |
| `Task.spec.steps[].resources` | `Task.spec.steps[].computeResources` | v1 does not read the old key, so the step runs with no requests and no limits. |
| `Task.spec.resources` (`inputs:` / `outputs:`) | **removed** — a `workspaces` entry plus `params` replaces it | The field is not read, so whatever the block was wired to (a repository on disk, a file the step expects to find) simply never arrives. |
| kind `PipelineResource` | **removed** — a workspace carries the clone, and the `git-clone` Task this kmt ships fills it | The apply fails: there is no CRD behind the kind any more. |

Three shapes that look like migration targets and are **not**:

- `triggers.tekton.dev/v1beta1` on the Triggers resources — the pinned Triggers controller is 0.37.2
  and does not serve v1. Rewriting that string produces a manifest no controller here serves.
- `volumeClaimTemplate.spec.resources.requests` — a Kubernetes `PersistentVolumeClaim` field. Renaming
  it does not break the apply; it silently creates a claim that asks for no storage, and the pipeline
  waits for a volume that never arrives.
- `EventListener.spec.resources` and the container `resources` inside its embedded pod template, plus
  that pod's `serviceAccountName: tekton-triggers-sa` — all Triggers/Pod fields, not Pipelines ones.

`tests/test_tekton_manifest_api_versions.py` asserts all of this over every manifest in
`tekton_ci/` and `examples/tekton_ci/`, so a bad `resources:` edit fails a test rather than a pipeline.

## What to do

1. Confirm the controller: `kubectl api-versions | grep tekton.dev` must show `tekton.dev/v1`. If it
   does not, upgrade first (`uv run kmt install <ENVIRONMENT> <TEAM>`, or raise
   `TEKTON_VERSION_PIPELINE` in `platform_config/<environment>/static.json` — 1.0 is the oldest version
   that serves v1, and `kmt install` asserts this for you instead of letting the apply pass silently).
2. Re-apply everything: `uv run kmt tekton setup-pipelines <ENVIRONMENT> <TEAM>`. This is also the
   command that fails closed on a too-old controller — it asks the API server once, before applying
   anything, and raises `TektonControllerWithoutConfiguredApiError` instead of reporting a 404 per
   Task and Pipeline.
3. Convert anything that lives outside the repo the same way: team manifests under
   `platform_config/{env}/{team}/tekton/…`, generated pipeline runs, and manifests you keep in other
   repositories. There is no automatic path, and the shapes above are ignored rather than rejected.

Rolling back is a git revert followed by `uv run kmt tekton setup-pipelines` again — there is no knob
inside kubementat, and that is intentional.

# Preparations (Only for new environments)

The checkout carries no `platform_config/` directory: configuration is a separate repository, which is
what §10 of the spec is for. The two `.gitkeep` files that used to hold the directory open were residue
from when configuration lived inside the product's own tree, and they were also what made
`kmt config init` refuse a checkout it had shipped itself.

So a working tree is a directory you point kubementat at, created by
`kmt config init <dir> --environment <env> --team <team>`. Everything that used to live under
`platform_config/dev/` is written there instead, and an environment that already exists is copied from
whichever configuration repository owns it — not from this checkout, which has nothing to copy.

## SSH Deployer Key
If you want to setup a new environment with a new deployer key you need to generate the according key via:
```
# generate ssh key
ssh-keygen -t rsa -b 4096 -C "service-deployer" -f service-deployer.key

## base 64 encode ssh key

### ATTENTION: base64 behaves differently on some Operating systems
# OS X Variant
cat service-deployer.key | base64

# Arch linux variant
cat service-deployer.key | base64 -w 0
```

## Git-crypt deployer gpg key
If you need to add a new deployer gpg key
see docs/README_GIT_CRYPT.md

# Initial setup and installation instructions
## Automated Tekton Installation
Installing kubementat installs Tekton: the three pinned releases plus the `helm-deployer-cluster-role`
that lets a helm deploy deploy. There is no separate step for it, and no script to run first.

```
# in the kubementat root directory
# general: uv run kmt install <ENVIRONMENT> <TEAM>

# e.g. for ENVIRONMENT=dev and TEAM=dev1
uv run kmt install dev dev1
```

Use `--yes` for a run that has no terminal to ask in (`kmt install dev dev1 --yes`).

## Automated Tekton pipeline and task setup
The `kmt tekton setup-pipelines` command will install all needed pipeline and task resources for the given environment and team.
You can execute the script via:

```
# in the kubementat root directory
# general: uv run kmt tekton setup-pipelines <ENVIRONMENT> <TEAM> 

# e.g. for ENVIRONMENT=dev and TEAM=dev1
uv run kmt tekton setup-pipelines dev dev1

# or for ENVIRONMENT=dev and TEAM=dev2
uv run kmt tekton setup-pipelines dev dev2
```

## Automated tekton trigger setup
Tekton triggers are a feature of tekton that can be used to spin up endpoints for receiving gitlab/github/... webhook requests. This allows automatic starting of pipelines on git events like push, tag, ...

The command creates the service accounts, roles and role-bindings the trigger controller needs,
writes the `gitlab-trigger-webhook-secret` / `github-trigger-webhook-secret` Secrets from the team's
`static.encrypted.json` (only for the providers that have a token configured) and applies the
trigger resources found under `platform_config/<ENVIRONMENT>/<TEAM>/tekton/triggers/`.

Run it via:
```
# general: uv run kmt tekton setup-triggers <ENVIRONMENT> <TEAM>

# e.g. for ENVIRONMENT=dev and TEAM=dev1
uv run kmt tekton setup-triggers dev dev1
```

It runs as part of `kmt install`, so it is rarely needed by hand.

> **Where generated triggers live:** `platform_config/<ENVIRONMENT>/<TEAM>/tekton/triggers/` (moved
> under `tekton/` in T-60; a tree that still has the old `<TEAM>/triggers/` directory is refused by
> name, at generation and at setup, rather than silently applied nowhere). The
> generator templates they are built from are product data and ship inside the package
> (`Config.tekton_trigger_template_dir`) — a trigger configuration is team configuration, so it
> belongs beside the team's other config, while its templates belong to nobody's checkout.

## Adding new tekton triggers
This will setup a skeleton trigger template and other needed resources as a starting point for further customization. The generated files land in `platform_config/<ENVIRONMENT>/<TEAM>/tekton/triggers/<APP_NAME>/` (the command prints every file it wrote, and the exact directory it used), where `kmt tekton setup-triggers` reads them from. This was `scripts/automation/tekton/generate_trigger_config_from_templates.sh` until 2026-10-28; the script is deleted and the command is the only way to run it.

```
uv run kmt tekton-generate-triggers dev dev1 nginx-example \
    --pipeline-name build-pipeline-nginx-example --trigger-type github
```

`--trigger-type` chooses which templates are read (`github` or `gitlab`), and everything else it needs is read from `platform_config`: `BASE_DOMAIN` from the environment's `static.json`, `HELM_DEPLOYER_SERVICE_ACCOUNT_NAME` from the team's. **A key it cannot read is an error, not a manifest** — the command names the key and the file to fix it in, and writes nothing. The four generated files are the Ingress, the EventListener, the TriggerBinding and the TriggerTemplate; the command prints the webhook URL the generated Ingress answers for, and the `static.encrypted.json` key the secret has to come from.

## Generating a deploy pipeline for a new app
`kmt tekton-generate-deploy-pipeline <ENVIRONMENT> <TEAM> <APP_NAME>` generates a helm-deploy pipeline from the deploy-pipeline template this kmt ships (`Config.tekton_pipeline_template_dir`) into `platform_config/<ENVIRONMENT>/<TEAM>/tekton/pipelines/deploy-pipeline-<APP_NAME>.yml`, which is the directory `kmt tekton setup-pipelines` applies. This was `scripts/automation/tekton/generate_deploy_pipeline_from_template.sh` until 2026-10-28 — deleted, and note that the **output moved**: it used to be written beside the template in `tekton_ci/pipelines/`.

```
uv run kmt tekton-generate-deploy-pipeline dev dev1 nginx-example \
    --git-url ssh://github.com/julweber/nginx-example.git \
    --git-project-name nginx-example --git-server-host github.com \
    --git-server-port 22 --git-server-ssh-user git
```

The five git coordinates belong to the app rather than to the platform, so no config key holds them: pass them or the command stops and says which ones are missing. Review the generated file either way — generating cannot know what the app's database setup and seeding need, and those parts are left as the template wrote them.


# Usage instructions

## Access tekton dashboard via kmt local tunneling
As the tekton dashboard is not accessible via ingress per default we need to open a tunnel via the kmt cli:
```
ENVIRONMENT="dev"
uv run kmt tunnel tekton $ENVIRONMENT
```

## Run a pipeline
For running a pipeline (= starting a Tekton pipelinerun) you can use the `kmt tekton run-pipeline`
command (`uv run kmt tekton run-pipeline --help`, run from the repository root — there is no separate
`cli/` directory to `pushd` into).

```
# For listing available pipeline runs you can call the function without providing a pipeline run definition yaml file
# General: uv run kmt tekton run-pipeline <ENVIRONMENT> <TEAM>


# if you want to run the same pipeline in parallel
uv run kmt tekton run-pipeline dev dev1 global/hello-world-pipeline-run.yml --parallel
```

## List all tekton resources within an environment and team
The `kmt tekton list` command lists pipelines, tasks, taskruns and pipeline-runs for the given
environment and team. This can be very handy to get an overview of what is happening within the CI
pipelines. (It replaced `scripts/automation/tekton/list_tekton_resources.sh`, which did the same
with four `tkn … list` calls.)

```
uv run kmt tekton list <ENVIRONMENT> <TEAM>
# e.g. for environment=dev and team=dev1
uv run kmt tekton list dev dev1
```

## Cleanup pipeline runs
Pipeline runs will stay within the k8s pipeline namespace after execution. This will also leave the pipeline run's workspaces intact, which uses kubernetes volumes as storage. So it is not recommended to keep all old pipeline runs as the cluster storage space will fill up if not cleaned up.

```
# general
uv run kmt tekton cleanup-pipeline-runs <ENVIRONMENT> <TEAM>

# e.g. for environment: dev , team: dev1

# For cleaning up all successful pipeline runs for an environment and team:
uv run kmt tekton cleanup-pipeline-runs dev dev1

# For cleaning up all pipeline runs (including failed ones) for an environment and team:
uv run kmt tekton cleanup-pipeline-runs dev dev1 --filter all
```

# Developer guide

## Directory structure
This section describes the general directory structure for the tekton related resources.
- pipeline-runs: global pipeline-run configurations
  - team specific pipeline-runs are located in ´platform_config/{ENVIRONMENT}/{TEAM}/tekton/pipeline-runs`
- pipelines: contains the tekton pipeline definition yaml files chaining tasks together to a pipeline
  - team specific pipelines are located in ´platform_config/{ENVIRONMENT}/{TEAM}/tekton/pipelines`
- tasks: contains global tekton task definition yaml files
  - team specific tasks are located in ´platform_config/{ENVIRONMENT}/{TEAM}/tekton/tasks`

### Which manifest wins: the three apply layers

`kmt tekton setup-pipelines` applies manifests from three places, in this order, and the last one
applied is the one the cluster keeps:

1. **product** — what this `kmt` ships, from inside the package: `assets/tekton_ci/tasks/` and
   `assets/tekton_ci/pipelines/` (`Config.tekton_task_asset_dir`, `Config.tekton_pipeline_asset_dir`).
2. **environment** — `platform_config/{ENVIRONMENT}/tekton/{tasks,pipelines}/`
   (`Config.tekton_env_task_dir`, `Config.tekton_env_pipeline_dir`). This is where one environment
   tunes a shipped Task without every team under it carrying its own copy.
3. **team** — `platform_config/{ENVIRONMENT}/{TEAM}/tekton/{tasks,pipelines}/`, generated output.

Two layers that declare the same `metadata.name` are not a collision: the same namespace, kind and
name is written twice, and the second write patches the first. That is the whole rule, so there is
no table of names and owners to keep in step. A layer with no directory is not an error — the log
says `no Task directory at <path>` once per directory and the layer below it stands. What is applied
is what is shipped and what the layers declare; nothing that stopped being shipped gets deleted, so
removing an object is `kmt uninstall`'s job or the operator's, never setup's.

`triggers/` is team-side (`Config.tekton_team_trigger_dir`, under `tekton/` since T-60); the
environment layer has no `triggers/` directory because nothing reads one.

## Making code changes
This section describes the general processes for extending the existing automations:

- In case you are changing configuration in platform_config you also need to  commit the changes to the master branch of the kubementat automation repository. Like this the pipeline runs can read the up-to-date configuration and use it accordingly
- In case you are adding or changing tasks or pipelines in the "tasks" or "pipelines" sub-directories you need to re-run the "uv run kmt tekton setup-pipelines <ENVIRONMENT> <TEAM>" script for your environments and teams as described in the section "Automated Tekton pipeline and task setup". This ensures that the task and pipeline definitions are updated on the k8s cluster and are running the latest versions of task definitions and your pipelines.

## Where a pipeline gets its configuration (§10's source B)

A pipeline used to clone one repository and read both halves out of it: `git-clone-automation` fetched
`automation-git-url` — your fork of this project — and every step that needed
`platform_config` reached into that same checkout, `deploy-local-helm-chart.yml`'s `path-to-configuration`
defaulting to `$(workspaces.workspace.path)/kubementat`. So the configuration was read from wherever the
code happened to be, and it worked only while the two were one repository.

That is no longer the shape. The deploy pipeline now has **two clone steps in the same workspace, landing
in two directories**:

```
/workspace/kubementat      code   — cloned from automation-git-url, landed under automation-git-project-name
/workspace/configuration   config — cloned from the same URL, landed under configuration-git-project-name
```

`--config-dir` on every `kmt` step names the second one, and `path-to-configuration` on
`deploy-local-helm-chart` defaults to it, so a step reads configuration from a clone that declares itself
as configuration instead of from a checkout that might be anything.

Two things follow from the cheaper shape. **`automation-git-url` now describes the configuration
repository** (§10 R-10.2: the key stopped meaning "where the product lives", and no `CONFIG_GIT_URL` was
added to fill the gap), so for anyone whose configuration still lives in their fork, the two clones fetch
the same URL twice and nothing needs changing. And **two params in one pipeline have to agree on one
path**, which Tekton does not check: the clone step binds a `git-project-name` and the reader binds a
path, so if you change one and not the other the clone succeeds, the step succeeds, and the failure is the
file that is not there. `configuration-git-project-name` exists so there is exactly one place to change,
and a guard test (`tests/test_the_configuration_root_in_ci_is_a_clone_and_not_the_product_checkout.py`)
reads the agreement off the shipped templates and examples so that a silent drift is a failed suite and
not a pipeline that runs for twenty minutes before dying.

The code clone stays, and not for decoration: the CI image ships `uv` and does not install kubementat, so
`uv run kmt` still resolves the entry point from a checkout. §12's wheel lane would retire the code clone
without touching this file, and that lane is not part of this change.

### Opening the clone: the unlock step and the Secret it reads (§10 R-10.3)

The configuration clone arrives **locked**. `git_crypt_symmetric.key` is gitignored (`config init` writes it
as its eighth step and gitignores it in the same breath), so git-crypt's symmetric key is the one thing a
repository cannot carry, and a step that reads `platform_config` right after the clone sees ciphertext and
has no way to say so.

A step of its own opens it, immediately after `git-clone-configuration` and before anything that reads
configuration:

```
git-clone-configuration  →  git-crypt-unlock-configuration  →  every step that reads configuration
```

It is a step and not part of the clone, because "did the unlock happen" has one answer in one place, and
because a clone step that also unlocked would report success on a repository with no keystore, which is the
failure `tests/test_no_clone_step_unlocks_a_repository_it_never_encrypted.py` exists to keep out.

**Where the key comes from.** A pod has no filesystem to find a key on, so it arrives through the pod's own
credentials: `kubementat` creates a Secret named `git-crypt-configuration-key` in the pipeline namespace
during `kmt install`, holding that file's bytes base64-encoded once, and the step reads it by
`secretKeyRef` and decodes once. Both directions are measured against git-crypt 0.7.0 rather than asserted
(`tests/test_exactly_one_base64_layer_sits_between_the_key_and_the_unlock_step.py` builds a real encrypted
repository, unlocks a real clone with the shipped encoding, and confirms that encoding it twice does not
unlock).

**What `kmt install` creates, and when.** Only for a directory whose `.gitattributes` declares
`filter=git-crypt`. That marker is chosen because it is the git-crypt fact that *travels*: the keystore
lives under `.git/`, which is not cloned, and the key file is gitignored, so either would answer "no" to
the exact tree that needs opening. A configuration that never used git-crypt gets no Secret and `setup_pipelines`
says so in the log rather than failing an install over a file that was never part of that tree. The step
reads the same marker from the clone it just fetched, which is why the two decisions cannot disagree about
the same repository.

**The three failures, and which is which.** All measured, because git-crypt's own messages are too similar
tone to tell these apart:

| What happened | What you see |
|---|---|
| Encrypted clone, no key | `this configuration declares git-crypt in its .gitattributes, but no key arrived` — run `kmt install` against the directory that owns the clone. Do **not** copy a key into the tree. |
| Secret exists, key is empty | Same refusal: an empty value and a missing Secret look identical to the step, and they are the same fix. |
| Key from a different repository | git-crypt says `encrypted file has been tampered with!`, leaves the tree configured-but-undecrypted, and a retry with that same key reproduces it. Check the configuration `kmt install` was given before suspecting the clone. |

Already-unlocked is a fourth case and it is silent: git-crypt exits 0 and prints nothing, so a green step is
evidence the tree is readable from that point onward and nothing more.

## Adding new tekton triggers
`uv run kmt tekton-generate-triggers <ENVIRONMENT> <TEAM> <APP_NAME> --pipeline-name <NAME> --trigger-type github|gitlab` — see the section of the same name above under "Installing / configuring tekton", which is where the whole flow is written up.

## Adding pipeline run configuration yml files easily
We created a script for generating pipeline-run yml files from existing pipelines for an environment and team. The script will create the according file in pipeline-runs/TEAM.

You can use the script as follows:
```
# General
./generate_pipeline_run.sh <ENVIRONMENT> <TEAM> <PIPELINE_YML_FILE>
# e.g.:
./generate_pipeline_run.sh dev dev2 ../pipelines/build-pipeline-ci-images.yml
```

# Tekton triggers

## Test tekton triggers
```
kubectl logs -n "tekton-pipelines" -l eventlistener=nginx-example-event-listener -f
```

# Uninstall instructions

## Uninstall tekton pipelines and tasks
```
# General usage: uv run tkm tekton-uninstall-pipelines <ENVIRONMENT> <TEAM>

# for uninstalling resources in environment=dev and team=dev1
uv run tkm tekton-uninstall-pipelines dev dev1

# for uninstalling resources in environment=dev and team=dev2
uv run tkm tekton-uninstall-pipelines dev dev2
```

# Notes

## Manual Tekton Uninstall
The URLs are the same ones `install()` in
`src/kubementat/infrastructure/tekton/install.py` applies, so the pinned versions come from the same
place — do not hand-write them here:
```
# uninstall tekton dashboard
BASE_URL=https://infra.tekton.dev/tekton-releases
ENVIRONMENT=dev   # the environment whose pins these URLs carry
# the same keys `install()` reads, from the environment's own static.json; the shipped defaults they
# start from are the template inside the installed kmt
PIPELINE_VERSION=$(jq -r .TEKTON_VERSION_PIPELINE "platform_config/$ENVIRONMENT/static.json")
DASHBOARD_VERSION=$(jq -r .TEKTON_VERSION_DASHBOARD "platform_config/$ENVIRONMENT/static.json")
kubectl delete -n tekton-pipelines -f "${BASE_URL}/dashboard/previous/v${DASHBOARD_VERSION}/release-full.yaml"

# uninstall tekton crds
kubectl delete -f "${BASE_URL}/pipeline/previous/v${PIPELINE_VERSION}/release.yaml"

# (optional) delete ingress
# kubectl delete ingress/tekton-dashboard-ingress -n tekton-pipelines
```

# Optional: manual installation instructions
## Manual Installation of tekton Custom Resources (Not recommended)
The path the product runs is `uv run kmt install <ENVIRONMENT> <TEAM>`; this is only for
reading the commands it actually issues. Same base URL, same pins:
```
BASE_URL=https://infra.tekton.dev/tekton-releases
ENVIRONMENT=dev   # the environment whose pins these URLs carry
PIPELINE_VERSION=$(jq -r .TEKTON_VERSION_PIPELINE "platform_config/$ENVIRONMENT/static.json")
DASHBOARD_VERSION=$(jq -r .TEKTON_VERSION_DASHBOARD "platform_config/$ENVIRONMENT/static.json")

# install crds to k8s
kubectl apply -f "${BASE_URL}/pipeline/previous/v${PIPELINE_VERSION}/release.yaml"

# monitor installation
kubectl get pods --namespace tekton-pipelines --watch

# install tekton dashboard
kubectl apply -n tekton-pipelines -f "${BASE_URL}/dashboard/previous/v${DASHBOARD_VERSION}/release-full.yaml"

# monitor installation
kubectl get pods --namespace tekton-pipelines --watch
```

## (optional) expose tekton dashboard

> **Correction, 2026-10-30.** The sentence below this line used to say that kubementat installs no
> ingress controller. It was wrong since 2026-10-28 (`2347583`): **kubementat installs Traefik**, and
> that is the routing surface as shipped. What a reader can check:
>
> - **What installs.** `kmt routing install <environment>` applies the `routing` group — the
>   `traefik/traefik` chart (pinned `42.1.3` in
>   `kubementat/core/assets/values/helmfile.yaml.gotmpl`) and the `jetstack/cert-manager`
>   release that fronts it. Routing is **not** part of `kmt install`: it is applied here, or not at all.
> - **Where it lands.** Release `traefik` in the namespace `TRAEFIK_DEPLOYMENT_NAMESPACE` (default
>   `traefik`), labelled `component_name=traefik,group=routing`, and applied with the values file
>   `helmfile.yaml.gotmpl` names as `traefik/values.encrypted.yaml` — that file is git-crypt'd, so what a
>   reader can see in the tree is its template,
>   `kubementat/core/assets/values/traefik/values.encrypted.yaml`. Cert-manager
>   behaves the same way one key over: `CERT_MANAGER_DEPLOYMENT_NAMESPACE`, `component_name=cert-manager`.
> - **Which class it serves.** `TRAEFIK_INGRESS_CLASS` (default `traefik`) names the `IngressClass` the
>   controller creates, and the same key is what the two `ClusterIssuer` HTTP-01 solvers and every
>   manifest `kmt create-ingress` writes name. A class no controller serves is accepted by the API and
>   served by nobody, which is why one key and not two answers this.
> - **How to read it, not guess it.** `uv run kmt routing status <environment>` reads the cluster and
>   reports which Traefik it is running, whose it is, and what `kmt routing install` would do with it.
>   On a cluster that already runs a Traefik kubementat did not install, `install` names that controller
>   and leaves it alone, and `--update` is the flag that says overwrite it.
>
> The example under this note is unchanged and is what the comment above the code block says it is: an
> Ingress written for a controller that brought its own `nginx` class and
> `nginx.ingress.kubernetes.io/*` annotations. On a kubementat cluster neither of those routes, because
> the class here is `traefik` and kubementat ships no nginx annotation compatibility; the supported way
> to get that manifest is `uv run kmt create-ingress <environment> <domain> <service> <port>`, which
> writes the class `TRAEFIK_INGRESS_CLASS` names and no nginx annotations at all.

```
# DASHBOARD_URL=dashboard.domain.tld

kubectl apply -n tekton-pipelines -f - <<EOF
apiVersion: extensions/v1beta1
kind: Ingress
metadata:
  name: tekton-dashboard-ingress
  namespace: tekton-pipelines
  annotations:
    ingress.kubernetes.io/rewrite-target: /
#    kubernetes.io/tls-acme: "true"
#    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
spec:
  rules:
  - http:
      paths:
      - path: /tekton
        backend:
          serviceName: tekton-dashboard
          servicePort: 9097
#  - host: $DASHBOARD_URL
#    http:
#      paths:
#      - backend:
#          serviceName: tekton-dashboard
#          servicePort: 9097
  tls:
   - secretName: tekton-dashboard-cert
     hosts:
       - 62.116.156.12
EOF
```

# Examples for manual tekton usage

### apply hello world task definition
```
# the example tasks live under examples/tekton_ci/tasks/
kubectl apply -f examples/tekton_ci/tasks/task_examples.yml
# `input-example-task` reads its repository from a workspace, which is what replaced the
# `resources: inputs:` block in v1 - the file itself shows the shape.

### list tasks
tkn tasks list

task_name="echo-hello-world-task"
# task_name="input-example-task"

tkn task describe $task_name

### run hello world task
kubectl apply -f hello_world_task_run.yml

### run input task example task
kubectl apply -f input_example_task_run.yml

### view details for task run
tkn taskrun list
tkn taskrun describe "${task_name}-run"
tkn taskrun logs "${task_name}-run"

### delete a task run
tkn taskrun delete "${task_name}-run"
```

## pipelines
```
tkn pipeline list

### set pipeline
kubectl apply -f hello_world_pipeline.yml
tkn pipeline describe hello-world-pipeline

### run pipeline
kubectl apply -f hello_world_pipeline_run.yml
tkn pipelinerun list
tkn pipelinerun logs hello-world-pipeline-run -f

tkn pipelinerun describe hello-world-pipeline-run

### delete pipeline run
tkn pipelinerun delete hello-world-pipeline-run
```

# Links
- tkn - Tekton CLI: https://github.com/tektoncd/cli
- Installation: https://github.com/tektoncd/pipeline/blob/master/docs/install.md
- Tutorial: https://github.com/tektoncd/pipeline/blob/master/docs/tutorial.md
- Tekton Examples: https://github.com/tektoncd/pipeline/tree/master/examples
- Tekton task catalog: https://github.com/tektoncd/catalog
- kaniko - build docker images in k8s - https://github.com/GoogleContainerTools/kaniko
- Tekton tutorial VMWare
  - https://tanzu.vmware.com/developer/guides/ci-cd/tekton-gs-p1/
  - https://tanzu.vmware.com/developer/guides/ci-cd/tekton-gs-p2/
- Tekton triggers tutorial: https://dlorenc.medium.com/tekton-triggers-3aba132c6344
- Tekton k8s deployment tutorial: https://github.com/IBM/deploy-app-using-tekton-on-kubernetes/tree/master/tekton-pipeline