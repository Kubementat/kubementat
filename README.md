# Kubementat

Kubementat allows developers to quickly get started with their kubernetes environment by providing an opinionated framework for kubernetes automation.It contains code for automating installation and setup of kubernetes environments and running pipelines via `tekton CI`.
In addition it installs a complete Open Source standard toolset: monitoring (prometheus, grafana, and the
blackbox and helm exporters beside them), log aggregation and analysis (loki and alloy), the routing pair
(traefik with cert-manager in front of it), a secret store (openbao) and a compliance scan (polaris).
It installs no service mesh. Linkerd is swept from a cluster that really has it and installed by nothing,
and nginx-ingress left on 2026-10-28.
It also delivers examples on how to create tasks & pipelines for building and deploying apps on k8s.

__The general goal of this project is to allow developers/operators to get up and running; aka productive; with their kubernetes cluster as easy and comfortable as possible.__

If you are interested in the naming of this project, you can have a look [here](https://dune.fandom.com/wiki/Mentat) :)

## Why should you use Kubementat?
- NO VENDOR LOCK IN
- FREE and OPEN SOURCE
- Convention over configuration
- CI/CD and base deployment automation included
- easy to use via the kmt cli
- runs from a local or raspberry pi setup up to a Kubernetes cluster in the Cloud

## Included platform tools

The releases a run applies are declared in the helmfile that ships inside `kmt`, and their versions come
from the release index; both are configuration, so both are answered over by the tree.

- [Tekton Pipelines](https://tekton.dev/), with Triggers, the Dashboard and the resolvers
- Helm and Helmfile, which is how every release arrives
- [Traefik](https://traefik.io/) and [cert-manager](https://cert-manager.io/), applied by `kmt routing install`
- [Prometheus](https://prometheus.io/), [Grafana](https://grafana.com/grafana/),
  [Loki](https://grafana.com/oss/loki/) and [Alloy](https://grafana.com/docs/alloy/)
- [OpenBao](https://openbao.org/) and [Polaris](https://www.fairwinds.com/polaris)

`cluster-cronjobs`, `goldilocks` and `vpa` are namespaces kubementat creates, not releases it applies, and
`kmt component install` refuses them for exactly that reason.

## Sub-directories

Where things are. `kmt` reads no path this checkout does not have, so this is not a tour of a file tree;
it is what the product's own code addresses.

- __src/cli__: the `kmt` command-line tool — the entry point every verb in this product is defined in, and
  the only caller of the library. There is no top-level `cli/`; the package is `src/cli`.
- __src/kubementat__: the Python library — automation, configuration, cluster resolution, everything the
  CLI delegates to. It is imported and never exec'd, and it raises its refusals without knowing how they
  will be printed.
- __src/kubementat/core/assets__: the **shipped data**: `values/` (the helmfile
  manifest and the default values files it names) and `tekton_ci/` without `pipeline-runs/`. There is no
  `templates/` here any more: 1.x's seed copies shipped under it, no verb ever opened one, and the two that
  had a reader are test fixtures now (`tests/fixtures/configuration_shape/`). It is read
  from inside the package, so an installed `kmt` needs no checkout to find it. Editing one of these files
  changes what the product does; no command ever writes here.
- __docs__: the documentation, the specifications under `docs/specifications`, the plans and censuses
  under `docs/plans`, and the per-component READMEs.
- __docker_files__: the Dockerfiles the CI images are built from.
- __examples__: example helm charts, Kubernetes manifests and tekton resources.
  - `examples/helm_charts/nginx-example`: the example chart the nginx-example deployment installs.
- __tekton_ci__: `pipeline-runs/` only — the run manifests this checkout holds as **output**. The Tasks
  and Pipelines the cluster is given, and the generator templates they are built from, are product data
  and ship inside the installed `kmt` under `src/kubementat/core/assets/tekton_ci/`.
- __tests__: the unit suite (`test_*.py`, no cluster needed), the shims and fixtures it runs against, and
  `tests/e2e/` for the VM-based lanes. A test name says what the test proves — `tests/README.md` explains
  the convention and `tests/test_test_name_conventions.py` holds that line.
- __tools__: repository scripts. None of them is product code, and no command execs any of them.

**There is no `scripts/` directory.** Every verb that once shelled out to one is Python in
`src/kubementat/` now: the user management, the secrets, the tunnels, the CI container, the component
applies. The last shipped script was deleted rather than left standing beside a Python implementation of
the same work, which is why `kmt install`'s dependency check carries a rule about shipped scripts that
currently has no referent — and will catch a re-added one.

**There is no `helm_charts/` directory.** The root directory of that name held a `.gitkeep` and nothing
else; it is deleted, not emptied. Every chart that installs anything lives under `examples/helm_charts/`
(a worked example of how to ship your own) or inside the configuration directory you prepared, and no code
path under `src/` resolves a chart path against this checkout.

## Which document answers which question

`docs/` holds 47 Markdown files, and this file links to exactly one of them. That is the whole reason for
the table: a reader who finishes the quick start above still has to know where to go next, and most of
those 47 files are dated records of work that is already finished. So — ten documents are **live**, and
the rest are **records**, read for why a decision was made and never as an instruction:

| What you want to know | Read |
|---|---|
| what a configuration directory is, which key belongs in which layer, and in what order a root is named | [docs/README_PLATFORM_CONFIG.md](docs/README_PLATFORM_CONFIG.md) |
| how git-crypt is laid out inside that directory, and which of its commands a failure is about | [docs/README_GIT_CRYPT.md](docs/README_GIT_CRYPT.md) |
| Tekton: the Tasks and Pipelines that ship, triggers, cleanup jobs, where generated pipeline runs go | [docs/README_TEKTON.md](docs/README_TEKTON.md) |
| where the monitoring stack is, and what it answers | [docs/README_MONITORING_AND_LOGGING.md](docs/README_MONITORING_AND_LOGGING.md) |
| what other tools kubementat ships and what they are for | [docs/README_ADDITIONAL_TOOLS.md](docs/README_ADDITIONAL_TOOLS.md) |
| how work in this repository is done (branches, commits, the lanes) | [docs/README_DEVELOPMENT_FLOW.md](docs/README_DEVELOPMENT_FLOW.md) |
| how to run an E2E lane, what an artifact directory holds, how a red lane is judged | [docs/README_E2E_TESTS.md](docs/README_E2E_TESTS.md) |
| what is where in the code, and what is known to be broken | [docs/index-overview.md](docs/index-overview.md) |
| how the deployer GPG keypair is made, and what `--gpg-key` will accept | [docs/gpg_key_generation.md](docs/gpg_key_generation.md) |
| the class diagram and the generator that renders it | [docs/diagram-generator/README.md](docs/diagram-generator/README.md) |

The other 37 files are records, and their directories say what kind: `docs/plans/` (10) is the execution
documents, `docs/research/` (10) is the evidence behind them, `docs/specifications/` (2) is one design
per document, `docs/finished-tickets/` (5) is the small hygiene tickets, `docs/lessons-learned/` (2) is
what a failure taught, `docs/implementation-summaries/` (2) is the account of a finished change,
`docs/census-*.md` (3) is a split that happened, and the three files at `docs/` root that are not
`README_*` (`next-action-steps.md`, `plan-kubernetes-split.md`, `plan-tekton-split.md`) are the same kind
of thing with a shorter name. A file that carries a date in its name is a record. None of them is
current instructions, and reading one as if it were is how someone ends up running a verb that was
deleted two months ago.

## Git-Crypt

**This repository holds no encrypted files, so there is nothing to unlock here.** The root
`.gitattributes` is deleted, `platform_config/` is not in the checkout, and what git-crypt protects is the
**configuration directory** you prepare with `kmt config init` — a tree of your own, not a subdirectory of
this one. git-crypt is initialised there and only there, by that verb, in an order that declares
`*.encrypted.*` before the first encrypted file is written, registers the deployer's key as a
collaborator, and exports the symmetric key at mode 0600.

Two things follow, because the old habit of running `git-crypt unlock` here is now aimed at the wrong
tree:

- **Reading is not the same act.** kubementat reads the configuration file it is given and never runs
  `git-crypt unlock`. A file that comes back as ciphertext is refused by name, and unlocking it belongs
  to whoever owns that directory — an operator at the prompt, or the pipeline's checkout step.
- **The symmetric key is the environment's whole credential.** `init` writes it beside the other key
  material, gitignored, and says so when it does: nothing can regenerate it and nothing can rotate it,
  so losing it loses every credential that environment holds.

The commands `config init` wraps, so a reader can tell which one a failure is actually about, are written
out in [docs/README_GIT_CRYPT.md](docs/README_GIT_CRYPT.md).

## Local Environment Prerequisites

### Start a prebaked docker image with the full tooling installed
From the kubementat root you can run:
```
# PREFERED WAY:
# Run image via Docker and mount this directory
docker run --name ubuntu-ci -it --rm \
--mount type=bind,source="$(pwd)",target=/src \
--mount type=bind,source="$HOME/.kube",target=/root/.kube \
'docker.io/julianweberdev/ubuntu-ci-minimal:latest'

# Then on the container: cd /src - the sources are mounted into the container in /src

# Alternative: Kubernetes - but then you need to directly commit all changes to your fork of the kubementat repo
# In addition you also need to transfer all generated key files manually using this approach
kubectl run ubuntu-ci -i --tty --image="docker.io/julianweberdev/ubuntu-ci-minimal:latest" --command /bin/bash
```

### Or: The hard manual way

These are what `kmt install` checks before it touches a cluster, so a missing one is a sentence naming the
binary and not a failure three minutes later:

```
# install python3 & pip                                  (what the kmt cli runs on)
# install git
# install gpg            <- the deployer keypair `config init` writes is made through it
# install kubectl
# install helm
# install helmfile
# install helm diff plugin -> helm plugin install https://github.com/databus23/helm-diff --verify=false
#                        helm 4 verifies plugin sources and helm-diff ships no provenance, so the plain command fails
# install tkn cli
```

`git-crypt` is wanted as well, by one verb only: `kmt config init` initialises it inside the configuration
directory. Reading that directory afterwards needs no git-crypt, because kubementat reads files and never
unlocks them.

`jq` and `yq` are not on the list, and they are not missing from it. Both were needed by shipped shell
scripts, the last of which this repository has deleted, and the same answers come out of the Kubernetes
class objects and `yaml.safe_load` now. A tool no part of the product runs is not a dependency, and
asking the check to verify one is asking it to verify something that does not exist.

## Installation / Getting Started
For installing kubementat on your kubernetes cluster (either running in the Cloud, a raspberry pi, on-prem...)
we need to take some initial configuration steps.
- Prepare the configuration directory that says what the environments are
- Initialize and configure the git repository you will use for running and developing with kubementat
- Configure the docker registry you will use
- Configure kubernetes specific settings

### Clone the repository
```
git clone https://github.com/Kubementat/kubementat
cd kubementat

# if you have not set your git configuration yet:
git config --global user.email "smith@matrix.com"
git config --global user.name "Agent Smith"
```

### Install the python requirements via uv
The kmt cli serves as a central tool for managing your kubementat processes. Two installs, and they differ
only in what the checkout is for:

- **from a checkout**, which is what you want if you are changing kubementat: `uv sync --all-extras`, then
  everything runs as `uv run kmt …`.
- **as a tool**, which is what you want if you are only operating a cluster: `uv tool install kubementat`
  or `pipx install kubementat`, then the command is just `kmt …`.

A checkout is not needed for the second one, and that is not a convenience — since v2 the configuration
directory is a tree of its own, so nothing about your environments depends on where kubementat happens to be
installed from. The rest of this document writes `uv run kmt`; drop the prefix if you installed the tool.

```
uv sync --all-extras

# view kmt cli help
uv run kmt --help
```

The verbs are grouped, not flat: `kmt config …`, `kmt environments …`, `kmt kubernetes …`, `kmt helm …`,
`kmt tekton …`, `kmt component …`, `kmt routing …`, `kmt tunnel …` and `kmt users …`, beside the commands that
act on a whole cluster (`install`, `uninstall`, `setup-secrets`, `setup-docker-registry-access`,
`run-ci-container`). `uv run kmt --help` prints the surface, and every verb answers `--help` with the
refusals it makes.

## The configuration directory

The stack is configured by an **external configuration directory**: a directory of your choosing that is a
git repository in its own right, prepared by `kmt config init`. It is not a subdirectory of this checkout,
nothing is copied into it, and no command reads its configuration out of the tree kubementat was installed
from.

It is **named, never located**, by three things and no fourth: `--config-dir PATH`, which outranks
`$KUBEMENTAT_PLATFORM_CONFIG_DIR`, the spelling a container or a scheduled run already carries; and
below both, the one tree `kmt config init` prepared for this user, which that verb records when it
lays the tree down. With none of the three, the command says so and stops. That is not pedantry — a
root found by proximity is a wheel installing into whichever tree it happened to land in. The
recorded root is what lets the flag be typed once: the run that *made* the tree said which tree it
was, so every command after it can ask the same question and get one answer. Two prepared trees on
one machine is the case that still refuses, because choosing between them by whichever was written
first is the silent-wrong-tree bug this rule exists to keep out. The record is a plain JSON array at
`~/.config/kubementat/configuration_roots.json`, it holds paths and nothing else, and
`KUBEMENTAT_CONFIGURED_ROOTS` says where it is when the home directory is not the right place — the
dial is for a container or a scratch tree, and setting it to nothing is a refusal and not a fallback.

```
uv run kmt config init --environment dev --team dev1 --gpg-email you@example.com --config-dir ~/kmt-config
```

This is the only command on the whole path below that carries the flag, because it is where a tree that
does not yet exist is named for the first time. It leaves a tree like this:

```
~/kmt-config/
├── kubementat.yaml                       the stamp: which product this tree was prepared for, and which
│                                         schema its answers mean. kubementat reads this before it
│                                         trusts anything else in the tree.
├── .gitattributes                        *.encrypted.* filter=git-crypt diff=git-crypt
├── .gitignore                            the key files below, so none of it reaches git
├── deployer_ssh_key{,.pub}               the deploy keypair; the public half goes to the git host
├── gpg_private.key / gpg_public_key.gpg  the git-crypt collaborator's pair
├── git_crypt_symmetric.key               mode 0600, cannot be regenerated, cannot be rotated
├── dev/
│   ├── static.json                       the environment layer: what this cluster is
│   ├── static.encrypted.json             its credentials, encrypted by the filter above
│   ├── kubeconfig.encrypted.yaml         optional, and it outranks $KUBECONFIG. See below.
│   ├── tekton/{tasks,pipelines}          resources this environment owns
│   └── dev1/
│       ├── static.json                   the team layer, which answers over the environment layer
│       ├── static.encrypted.json
│       └── tekton/{tasks,pipelines,pipeline-runs,triggers}
└── prod/…                                another environment, one cluster each
```

Every key is answered by exactly one of three things — the team's file, the environment's, or kubementat's
shipped default, in that order — and `kmt config show <environment>` is the only command that prints the
result: each key, which of the three answered it, and whether a default did. A key you never answered is
not missing. `kmt config validate` reads the whole tree and returns one verdict instead of failing on the
first fault, and that is the verb to run before anything else touches a cluster.

Resources load in this order when `kmt tekton setup-pipelines` runs:

1. kubementat's own defaults, which ship inside the installed `kmt`
2. `<config-dir>/<environment>/tekton/`
3. `<config-dir>/<environment>/<team>/tekton/`

A resource of the same name in a later line wins over an earlier one, so the team's Task replaces the
environment's, which replaces the shipped one.

Everything else about a tree is below: which cluster an environment is, where its kubeconfig lives, which
git remote the pipelines pull from. The specification of all of it is `docs/README_PLATFORM_CONFIG.md`.

### Git Repository
- This project is intended as a template to build your own customizations on top.
- You need to either fork this repository to your own public github account or clone and push to your own private git repository.
  - The location is answered by the configuration directory, not by the environment:
    - `AUTOMATION_GIT_URL` - e.g. 'git@github.com:Kubementat/kubementat.git'
      - for your own registry: 'git@github.com:YOUR_USERNAME/kubementat_YOUR_ENVIRONMENT.git'
    - `AUTOMATION_GIT_SERVER_HOST` - e.g. 'github.com'
    - `AUTOMATION_GIT_SERVER_PORT` - e.g. '22'
    - `AUTOMATION_GIT_SERVER_SSH_USER` - e.g. 'git'
  Answer them with `kmt config set <environment> KEY=VALUE`, and check what the tree answers with
  `kmt config show <environment>`. Your configuration and script
  adjustments will then be pulled
  from this location for executing your CI tasks (search for "automation-git-url" within the pipeline
  yaml files to learn more about the specifics)

### Docker Registry
- We are providing a prebuilt standard docker image here:
- If you want to use the system and adjust it to your needs, answer your own registry in the
  environment's static layer:
  - `DOCKER_REGISTRY_BASE_URL` - e.g. 'docker.io/julianweberdev'
  This one has no shipped default, so nothing answers it until you do, and the commands that read it
  say which key they wanted rather than inventing a value. `kmt setup-docker-registry-access` is what
  turns an answered registry into the pull Secrets the cluster needs.

### Kubernetes Settings
- Storage for pipeline workspaces is answered by `TEKTON_KUBERNETES_STORAGE_CLASS`, whose shipped
  default is `local-path`. The name a cluster actually uses is a fact about that cluster, so changing
  it is a `config set` and not an install-time flag.

### Generate initial configuration

`kmt config init` is the only verb that lays down an environment. The spelling that names the root is the
one above, and this is what it writes — it is idempotent, so a second run writes no byte, commits
nothing, and names the step it skipped. It also records the directory it prepared, which is why no
command after this one repeats the flag:

```
uv run kmt config init --environment dev --team dev1 --gpg-email you@example.com --config-dir ~/kmt-config
```

That writes `kubementat.yaml`, the environment's two static layers and the team's two, the deployer
keypair, and git-crypt made live in that directory — its attributes declared before the first
encrypted file is written, the collaborator registered, and the symmetric key exported at mode 0600.
It commits its own work once. It copies nothing out of the checkout, so the directory does not depend
on this repository staying where it is.

What the tree answers comes from that directory and from kubementat's own shipped defaults. It does
**not** come from your shell environment: exporting a key's name does not answer that key. Every variable
kubementat reads *points* and none of them configures — `KUBEMENTAT_HOME` and
`KUBEMENTAT_PLATFORM_CONFIG_DIR` for the two roots, `KUBECONFIG` and `ENVIRONMENT` for the machine and the
environment, and `CLUSTER_KUBE_CONTEXT` / `CLUSTER_KUBECONFIG_PATH`, which are the layer above what the
environment declares and exist so a CI task can state a cluster from outside. So answer the keys your setup
genuinely needs with the verb that writes them:

```
uv run kmt config init --environment dev \
    --set BASE_DOMAIN=example.com \
    --set DOCKER_REGISTRY_BASE_URL=registry.example.com \
    --encrypted-set CLUSTER_MANAGER_EMAIL=you@example.com
```

`--set` writes the plain layer, `--encrypted-set` the git-crypt-protected one; the value is JSON if
it parses, so `false` is a boolean and `example.test` is a string. A key you do not answer is not
missing — it is answered by a shipped default, which `kmt config show` tags for you. The flag appears
in the command above and not here because `--config-dir` is needed only where the tree is new: this
verb names the root the first time and records it for every command that comes after.

The symmetric key that directory now holds cannot be regenerated and nothing can rotate it; losing
it means losing every credential in that environment. `init` says so when it writes the file.

### Tell kubementat which cluster `dev` is

A new environment answers `CLUSTER_KUBE_CONTEXT` as null, and `init` prints a sentence saying so
rather than filling it in. This is deliberate: copying the context `kubectl` happens to be pointed at
into your configuration would be a claim the tool did not earn, so **install refuses until you name
one**. Nothing is resolved from `kubectl`'s current-context.

```bash
# What contexts does this machine's kubeconfig offer?
kubectl config get-contexts

# Name the one this environment means:
uv run kmt config set dev CLUSTER_KUBE_CONTEXT=dev-cluster

# Confirm what kubementat resolves, before installing anything:
uv run kmt environments list
```

Note the symmetry, because it used to be asymmetrical: every `kmt config` verb takes the directory the
same way every command that acts on a cluster does — `--config-dir`, or
`$KUBEMENTAT_PLATFORM_CONFIG_DIR`, or the tree `config init` recorded — and none of them refuses to say
which one it read. The verbs here once took the directory as a positional, which meant a machine that
had configured its root once could install against it and could not configure it. Nothing is located by
proximity either: with nothing named and nothing recorded, a command says so and stops.

You can write that key into `~/kmt-config/dev/static.json` by hand and get the same answer, but the
verb is the safer half: it keeps every other key in the file where it is, and it refuses rather than
silently creating a layer that does not exist yet.

Two environments naming two contexts that point at the same cluster are refused on the spot; fix one
of them.

### Put the kubeconfig inside the configuration directory

The context is only half of "which cluster"; the other half is *which file says that context exists*.
Kubementat reads them in this order:

1. `--kubeconfig`, if you pass it.
2. `$CLUSTER_KUBECONFIG_PATH`, or the environment's own `CLUSTER_KUBECONFIG_PATH` in `static.json`.
3. **`<config-dir>/<environment>/kubeconfig.encrypted.yaml`, if that file is there.**
4. Kubernetes' own rule last: `$KUBECONFIG`, then `~/.kube/config`.

Step 3 is why the file is worth creating. Without it, a stray `export KUBECONFIG` in a shell — or a
different laptop — silently answers for an environment you named explicitly, and a deployment that
meant `dev` lands somewhere else while reporting success. Once the environment carries its own
kubeconfig, that variable cannot reach it.

```bash
# Copy the context you want out of wherever it lives.
kubectl config view --minify --context=dev-cluster --flatten > /tmp/dev.kubeconfig

# Put it where the environment keeps its own secrets.
cp /tmp/dev.kubeconfig ~/kmt-config/dev/kubeconfig.encrypted.yaml

# It is encrypted, so check that it reads as a document and not as ciphertext.
uv run kmt environments list
```

The name is not a style choice. A prepared tree declares `*.encrypted.*` for git-crypt, so a kubeconfig
called anything else lands in that directory as plaintext, and nothing will complain about it — git-crypt
simply stops touching the file. If that directory is locked in a checkout, run `git-crypt unlock` in that
directory before reading it; kubementat reads configuration and never unlocks anything.

That file is *yours*: kubementat reads it and never writes it, and `config init` knows to expect one
there. Deleting it puts you back at step 4, which is why removing it is a safe way to test that the
tree is genuinely what an install is reading.

### Configure your git repository

Push your repository to its upstream, and add the deployer key `init` generated to your git host as a
deploy key. The public half is `~/kmt-config/deployer_ssh_key.pub` and the private half sits beside
it, gitignored — it belongs to that directory and not to this program, so a second configuration
directory has its own. Nothing is registered by email address any more.

Per-team keys that pipelines use to clone go in the team's encrypted layer:

```
uv run kmt config set dev --team dev1 --encrypted --from-file ./ssh-deploy-keys.json
```

`--from-file` is how a private key gets answered without ever appearing in a command line.

### Install kubementat tooling to the cluster
```bash
# Install the kubementat tooling on your cluster
uv run kmt install dev dev1

# If you are using a private docker registry ensure to run
uv run kmt setup-docker-registry-access dev dev1

# If the team's encrypted layer declares SSH_DEPLOY_KEYS, write the Secrets they name
uv run kmt setup-secrets dev dev1


# Routing is not part of kmt install. Read the cluster first, then apply the routing group.
# Both ClusterIssuers register their ACME account with CLUSTER_MANAGER_EMAIL, so answer it
# first; a --update that would install a second Traefik controller asks first, and --yes answers.
uv run kmt config set dev CLUSTER_MANAGER_EMAIL=you@example.com
uv run kmt routing status dev
uv run kmt routing install dev


# Optional (but recommended)
# Configure cluster wide auto cleanup of finished tekton pipeline runs
# This is implemented via Kubernetes cronjob, in the namespace the environment's
# CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE names
uv run kmt tekton setup-cleanup-job dev --team dev1

# Test run a pipeline via tekton
uv run kmt tekton run-pipeline dev dev1 ../../examples/tekton_ci/pipeline-runs/hello-world-pipeline-run.yml

# view progress via tekton dashboard
uv run kmt tunnel tekton dev
```

### What `kmt install` answers with

The detail of a run goes to the log — dependencies, helmfile, the finish message with the next
steps — and the terminal gets the answer, built from the `InstallResult` the install now returns
instead of a stream of lines and a `None`:

```
Helmfile installation group: standard
Configure tekton pipelines: True
kubementat installed for dev (dev1): the three pinned Tekton releases and the ClusterRole that lets helm deploy, the platform's Helm releases through group=standard, the team's pipeline, task and pipeline-run manifests and the webhook triggers' RBAC, Secrets and generated resources.
the cluster answered, and this service account may create namespaces, deployments, clusterroles, roles, daemonsets and replicasets.
--yes answered the confirmation, so nothing was asked.
```

The first two lines are the ones this command printed before it had an answer to give, and they are
still printed before anything is called. The rest is the run's own record of what it wrote and of
the two reads that had to pass in front of the first apply — which is the half the cluster cannot
be asked about afterwards.

* **A skipped half is reported as skipped.** `kmt install dev dev1
  --no-configure-tekton-pipelines` adds a `skipped:` line naming what did not run and the plain
  `kmt install dev dev1` that runs it, so a run that wrote a quarter of the install does not print
  the same sentence as a run that wrote all of it.
* **The exit codes are the ones you already know.** A cluster that does not answer is still exit 1,
  a service account that cannot create what the install needs is still exit 1, and a run that
  reached this output wrote everything it was asked to. `--yes` changes only whether the
  confirmation was asked, never what the answer says.
* **The log did not shrink.** The finish message, with its next steps, is still written and still
  says the same words; the answer on stdout is the fact of what was written and not a replacement
  of what the log says to do next.

### What `kmt uninstall` answers with

`kmt uninstall` prints its plan before it asks, deletes in the order the cluster needs, and then
answers — the answer being `SweepResult`, the value that carries the same facts the log writes.
On a clean sweep the terminal gets:

```
kubementat uninstalled from dev (k3s-local): 15 namespaces deleted, 2 were already absent.
team directories swept: dev1.
nothing kubementat owned is left on the cluster.
```

The line about surviving objects is the verdict, and it is the whole meaning of the command: `DONE`
in the log is earned by an empty `residue` and printed for nothing otherwise.

* **A partial teardown is still exit 1, with the sentence you already know.** If the cluster still
  carries something kubementat owned, nothing is printed above and the run fails with
  `Error: uninstall left kubementat residue behind, so the cluster is not clean:` followed by one
  indented line per surviving object. That message did not change — it is assembled from the same
  list, in the same order, by the value now instead of by the code that joined it.
* **The residue is a field, not only a message.** `residue` names each surviving object in the
  step's own words, so a caller can ask *how much* went before deciding whether to raise. The CLI
  takes the raise (`SweepResult.raise_if_incomplete`), which is what keeps exit 1 and the operator's
  sentence exactly where they were.
* **Unchanged, and deliberate.** `kmt uninstall` still takes an environment and no team — the sweep
  covers every team the environment's layer configures, so naming one would be a claim the command
  cannot support — and `--yes` is still what answers the deletion. Without it and without a terminal
  to ask in, the command fails closed naming the flag, before anything is read.

## Kubernetes Dashboard

The Kubernetes dashboard component is **currently not part of a kubementat installation**.
The upstream `kubernetes/dashboard` chart was retired on 21 Jan 2026, so the release, its
configuration and the `kmt login-kubernetes-dashboard` utility were removed. A replacement
dashboard can be added later once a maintained chart is chosen.

## OpenBao
OpenBao replaced Vault when Vault moved to the Business Source Licence under IBM in 2.x, which does not
sit with kubementat's own claim of being free and open with no vendor lock-in. The component verb
installs it: `uv run kmt component install dev openbao`. The longhand is the same apply, and is what
`component install` builds:
```
uv run kmt helm apply dev --helmfile-label-filter 'component_name=openbao'
```
`kmt helm apply` builds the helmfile argv in Python; the script it used to run,
`scripts/automation/components/helmfile_apply.sh`, is deleted and a test holds it deleted.

Afterwards you can access the UI via:
```
uv run kmt tunnel openbao-ui dev
```

It is installed only when its group is asked for. `openbao` carries `group=extra`, and no lane installs
`extra`, which is why the end-to-end suite exercises the tunnel verb as a negative case.

## One component at a time

`kmt install` adds the platform, `kmt install --helmfile-installation-group` adds a group, and
`kmt component uninstall` takes one component off. This is the pair completed:

```
uv run kmt component install <environment> <component>   # apply one release
uv run kmt component uninstall <environment> <component>  # take it off again
```

Neither takes a `--team`: what the command applies is the environment's own helmfile label, and the namespace
it lands in is `static.json`'s `*_DEPLOYMENT_NAMESPACE` for that component. A team on these commands is a
`--team` the command could not act on, and v2 deleted the flag rather than ignore it.

Both go through helmfile with the label selector of the manifest kubementat ships
(`helmfile apply -l component_name=<component>`) — the same mechanism `kmt install` uses for a group
(`group=standard`) and `kmt routing install` uses for routing (`group=routing`). There is no
per-component `helm upgrade`, and no table of components in the code: what the command accepts is the
set of names kubementat addresses with a namespace, and what it can apply is read out of the
shipped helmfile. Both answers come from one module, `src/kubementat/core/components.py`, which
reads the manifest once per command and composes them — so the two cannot disagree, and a name cannot
be accepted by one verb and refused by another.

**What it installs.** `cert-manager`, `grafana`, `loki`, `openbao`, `polaris`, `prometheus`,
`traefik` — the seven components that have a release labelled after them. A group apply can be wider
than a component apply: the `standard` group installs seven releases, because
`alloy`, `prometheus-blackbox-exporter` and `prometheus-helm-exporter` carry `component_name:` labels
that belong to no component, so
`kmt component install dev prometheus` applies one release and `kmt install --helmfile-installation-group
standard` applies all seven of that group's. `alloy` is one of those three deliberately: it is the log
collector and it shares `loki`'s namespace, so it is installed by the group and swept by the namespace,
and an `alloy` entry in `constants` would have bought a key helmfile never reads and a namespace to
delete twice. The refusal says exactly that, names `kmt install <environment>
--helmfile-installation-group standard` as the verb that reaches them, and prints which labels are in
that position right now — read from the manifest, not counted from this paragraph.

**What it will not install, and what to run instead.** `tekton`, `tekton-dashboard`,
`tekton-pipelines-resolvers`, `cluster-cronjobs`, `goldilocks`, `vpa` are namespaces kubementat
creates, not releases it applies. Installing one is refused rather than attempted, because the filter
would select nothing and a green command that applied nothing is the failure mode this verb had to
avoid. Tekton (the three components) arrives with `kmt install <environment>`, which applies its
release manifests with `kubectl` and not helm; for the rest, kubementat installs nothing into that
namespace and a team's own helm deploy is what belongs there.

**`traefik`, and the question `install` asks about it.** Traefik is installed here, and it is
installed by `kmt routing install`'s decision — the command reads the cluster first and answers the
same question `kmt routing status` answers: *mine, foreign, or absent*. Nothing there → kubementat
applies its own release. Already kubementat's → nothing is applied, because upgrading a serving
controller is a values edit and a version bump, and `kmt routing install <environment> --update` is
the verb that does it. Already somebody else's → this command refuses and applies nothing, because a second
controller, class and status writer is not a thing a command named `install` gets to decide; `kmt
routing install <environment> --update` is the verb that can ask. The same finding through `kmt
routing install` without `--update` is not a refusal: kubementat's Traefik release is skipped and the
rest of `group=routing` — cert-manager and the ClusterIssuers — is applied anyway, because a skip that
suppressed the whole group would take the platform issuer down. `cert-manager` is not asked, because
it is an issuer and not a controller.

**The git providers are not components.** `gitea`, `gitbucket` and `gitlab` were retired as kubementat
components in `8678526`: no release, no namespace key, nothing for this command to install. A git
server is a normal helm deploy a team writes, under whatever name it likes — see the CHANGELOG's
first entry.

**The name that changed.** FIN-19's flat one-component uninstall command is now
`kmt component uninstall`. It is a rename and not a new command: same arguments, same
implementation, same answers. No alias, no deprecation branch — one deletion has one name, and the
old one is documented in the CHANGELOG rather than left as a second route to the same work.


## Additional Features

- Routing: `uv run kmt routing install <environment>` installs kubementat's Traefik controller and the
  cert-manager that fronts it, and `kmt routing status <environment>` says first which controller the cluster
  is already running, whose it is, and what `install` would do. Traefik ships as the cluster's default
  IngressClass, so an Ingress that names no class is routed. It takes `--yes` like every command that
  writes: applying `--update` beside a foreign controller means a second controller, class and status
  writer, and that is the one branch the command asks about — `--yes` answers it, and without it and
  without a terminal to ask in, nothing is applied and the run fails naming the flag. It also refuses
  until `CLUSTER_MANAGER_EMAIL` is answered, because both ClusterIssuers register their ACME account
  with it: `uv run kmt config set <environment> CLUSTER_MANAGER_EMAIL=<address>`. `kmt install` installs
  no controller; nginx
  ingress went out on 2026-10-28 and is swept from clusters that really had it. See the CHANGELOG and
  `docs/README_PLATFORM_CONFIG.md`.
- Helmfile based component installation: The releases kubementat applies are declared in
  `kubementat/core/assets/values/helmfile.yaml.gotmpl`, the manifest that ships inside the installed `kmt`
  (R-12.1/T-18). That file is the entry point: `helmfile apply` runs it directly, `helm_utils.py`'s
  `helmfile_paths()` names it, and nothing copies it into `platform_config/` any more. The
  configuration contributes values, not a manifest, so editing the shipped file is editing what every
  environment applies — and a `helmfile.yaml.gotmpl` someone left under
  `platform_config/<environment>/kubementat_components/` is data nothing loads.