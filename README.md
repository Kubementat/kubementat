# Kubementat

Kubementat allows developers to quickly get started with their kubernetes environment by providing an opinionated framework for kubernetes automation.It contains code for automating installation and setup of kubernetes environments and running pipelines via `tekton CI`.
In addition it contains a complete Open Source standard toolset for monitoring (prometheus, grafana), log aggregation & analysis (loki, grafana) and an optional lighweight service mesh (linkerd).
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
- [Tekton + Tekton Triggers + Tekton Dashboard](https://tekton.dev/)
- [Helm](https://helm.sh/) &amp; [Helmfile](https://github.com/helmfile/helmfile)
- [Grafana](https://grafana.com/grafana/)
- [Prometheus](https://prometheus.io/)
- [Loki](https://grafana.com/oss/loki/)

## Sub-directories

Each of the given sub-directories contains additional README*.md files that document the actual component. For further details dive into the sub-directories.

- __docs__: Documentation for kubementat
- __src/cli__: the `kmt` command-line tool — the entry point every verb in this product is defined in.
  There is no top-level `cli/`; the package is `src/cli`.
- __docker_files__: All custom Dockerfiles used for creating docker images and running CI tasks
- __examples__: Various examples of scripts, helm charts and tekton resources
  - `examples/helm_charts/nginx-example`: the example chart the nginx-example deployment installs.
    There is no `helm_charts/nginx-example`.
- __helm_charts__: a directory this checkout keeps empty. Nothing in `src/` installs a chart out of
  it, and the example charts live under `examples/helm_charts/`.
- __src/kubementat__: the Python library — automation, configuration, cluster resolution, everything  
  the CLI delegates to.
- __src/kubementat/core/assets__: the **shipped data** — `assets/templates/` (7 files), `assets/values/`
  (11 files: the helmfile manifest and the ten default values files it names) and
  `assets/tekton_ci/` without `pipeline-runs/` (37 manifests). It is read from inside the package, so an
  installed `kmt` needs no checkout to find it (R-12.1). Editing one of these files changes what the
  product does; no command ever writes here.
- __tekton_ci__: `pipeline-runs/` only — the run manifests this checkout holds as **output**.
  The Tasks and Pipelines the cluster is given, and the generator templates they are built from, are
  product data and ship inside the installed `kmt` (R-12.1, `kubementat/core/assets/tekton_ci/`).
      - tunneling (also implemented into the kmt cli)
      - kubernetes helpers
      - helm helpers
  - __user_management__: Managing users and groups on k8s clusters
  - __secret_management__: Managing secrets in Kubernetes and Kubementat

## Git-Crypt
You need to unlock the repository to be able to use \*.encrypted.\* files in the repository (for more details see: docs/README_GIT_CRYPT.md).
```
git-crypt unlock
```

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
```
# install git-crypt
# install python & pip
# install jq          <- still needed: not by the Python code, but by the one shipped script that
#                        parses with it, scripts/utilities/kubernetes/retrieve_node_addresses.sh
# install gnugpg
# install kubectl
# install helm
# install helmfile
# install helm diff plugin -> helm plugin install https://github.com/databus23/helm-diff --verify=false
#                        helm 4 verifies plugin sources and helm-diff ships no provenance, so the plain command fails
# install tkn cli
# install linkerd cli
```

## Installation / Getting Started
For installing kubementat on your kubernetes cluster (either running in the Cloud, a raspberry pi, on-prem...)
we need to take some initial configuration steps.
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
The kmt cli serves as a central tool for managing your kubementat processes.

```
uv sync --all-extras

# view kmt cli help
uv run kmt --help
```

## Platform Configuration
The whole kubementat stack is configured via the platform_config directory.
The directories structure is as follows:
- ENVIRONMENT - The kubernetes cluster level (e.g. dev, prod)
  - __static.json__ - contains static configuration for the environment/cluster
  - __static.encrypted.json__ - contains credentials for the environment/cluster
  - 'tekton' directory - contains team specific pipelines and tasks in the according directories (optional)
  
  - TEAM - Per team configurations (e.g. dev1, smoke)
    - __static.json__ - contains static configuration for the team
    - __static.encrypted.json__ - contains credentials for the team
    - 'tekton' directory - contains team specific pipelines and tasks in the according directories (optional)
    - contains helm deployment configurations for the according team namespace

For tekton pipelines and tasks the load order when executing `kmt tekton setup-pipelines` is as follows:
- Kubementat default resources
- Environment resources (in platform_config/{ENVIRONMENT_NAME}/tekton)
- Team resources (in platform_config/{ENVIRONMENT_NAME}/{TEAM_NAME}/tekton)
When resources with the same name occur, they will be overwritten in that order also. So the team resource overrides the environment resources overwrites the default resource.

### Git Repository
- This project is intended as a template to build your own customizations on top.
- You need to either fork this repository to your own public github account or clone and push to your own private git repository.
  - The location is answered by the configuration directory, not by the environment:
    - `AUTOMATION_GIT_URL` - e.g. 'git@github.com:Kubementat/kubementat.git'
      - for your own registry: 'git@github.com:YOUR_USERNAME/kubementat_YOUR_ENVIRONMENT.git'
    - `AUTOMATION_GIT_SERVER_HOST` - e.g. 'github.com'
    - `AUTOMATION_GIT_SERVER_PORT` - e.g. '22'
    - `AUTOMATION_GIT_SERVER_SSH_USER` - e.g. 'git'
  Answer them with `kmt config set <environment> KEY=VALUE --config-dir <configuration-root>`, and
  check what the tree answers with
  `kmt config show <environment> --config-dir <configuration-root>`. Your configuration and script
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

`kmt config init` prepares a configuration directory. It is the only verb that lays down an
environment, and the directory it lays it down in is the one `--config-dir` names — or the one
`$KUBEMENTAT_PLATFORM_CONFIG_DIR` names, which is the spelling a container or a scheduled run already
has:

```
uv run kmt config init --environment dev --team dev1 --gpg-email you@example.com --config-dir ~/kmt-config
```

That writes `kubementat.yaml`, the environment's two static layers and the team's two, the deployer
keypair, and git-crypt made live in that directory — its attributes declared before the first
encrypted file is written, the collaborator registered, and the symmetric key exported at mode 0600.
It commits its own work once. It copies nothing out of the checkout, so the directory does not depend
on this repository staying where it is.

What the tree answers comes from that directory and from kubementat's own shipped defaults. It does
**not** come from your shell environment, and an `export` of a key name will not answer it; the
variables kubementat reads are only `KUBEMENTAT_HOME`, `KUBEMENTAT_PLATFORM_CONFIG_DIR`, `KUBECONFIG`
and `ENVIRONMENT`, and each of those points somewhere rather than configuring something. So answer
the keys your setup genuinely needs with the verb that writes them:

```
uv run kmt config init --environment dev \
    --set BASE_DOMAIN=example.com \
    --set DOCKER_REGISTRY_BASE_URL=registry.example.com \
    --encrypted-set CLUSTER_MANAGER_EMAIL=you@example.com \
    --config-dir ~/kmt-config
```

`--set` writes the plain layer, `--encrypted-set` the git-crypt-protected one; the value is JSON if
it parses, so `false` is a boolean and `example.test` is a string. A key you do not answer is not
missing — it is answered by a shipped default, which `kmt config show` tags for you.

The symmetric key that directory now holds cannot be regenerated and nothing can rotate it; losing
it means losing every credential in that environment. `init` says so when it writes the file.

### Tell kubementat which cluster `dev` is

A new environment answers `CLUSTER_KUBE_CONTEXT` as null, and `init` prints a sentence saying so
rather than filling it in. This is deliberate: copying the context `kubectl` happens to be pointed at
into your configuration would be a claim the tool did not earn, so **install refuses until you name
one**. Nothing is resolved from `kubectl`'s current-context.

```
# What contexts does this machine's kubeconfig offer?
kubectl config get-contexts

# Name the one this environment means:
uv run kmt config set dev CLUSTER_KUBE_CONTEXT=dev-cluster --config-dir ~/kmt-config

# Confirm what kubementat resolves, before installing anything:
uv run kmt environments list --config-dir ~/kmt-config
```

Note the symmetry, because it used to be asymmetrical: every `kmt config` verb takes the directory the
same way every command that acts on a cluster does, as `--config-dir` or as
`$KUBEMENTAT_PLATFORM_CONFIG_DIR`, and none of them refuses to say which one it read. The verbs here
once took the directory as a positional, which meant a machine that had configured its root once could
install against it and could not configure it. Nothing is located by proximity either: with neither the
flag nor the variable, a command says so and stops.

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
uv run kmt environments list --config-dir ~/kmt-config
```

The name is not a style choice. A prepared tree declares `*.encrypted.*` for git-crypt, so a kubeconfig
called anything else lands in that directory as plaintext, and nothing will complain about it — git-crypt
simply stops touching the file. If you are working in a locked checkout, run `git-crypt unlock` there
before reading it; kubementat reads configuration and never unlocks anything.

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
uv run kmt config set dev --team dev1 --encrypted --from-file ./ssh-deploy-keys.json --config-dir ~/kmt-config
```

`--from-file` is how a private key gets answered without ever appearing in a command line.

### Install kubementat tooling to the cluster
```
# Install the kubementat tooling on your cluster
uv run kmt install dev dev1 --config-dir ~/kmt-config

# If you are using a private docker registry ensure to run
uv run kmt setup-docker-registry-access dev dev1 --config-dir ~/kmt-config

# If the team's encrypted layer declares SSH_DEPLOY_KEYS, write the Secrets they name
uv run kmt setup-secrets dev dev1 --config-dir ~/kmt-config


# Optional (but recommmended)
# Configure cluster wide auto cleanup of finished tekton pipeline runs
# This is implemented via Kubernetes cronjob, in the namespace the environment's
# CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE names
uv run kmt tekton setup-cleanup-job dev --team dev1

# Test run a pipeline via tekton
uv run kmt tekton run-pipeline dev dev1 ../../examples/tekton_ci/pipeline-runs/hello-world-pipeline-run.yml

# view progress via tekton dashboard
uv run kmt tunnel tekton dev
```

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

Both go through helmfile with the environment's own label selector
(`helmfile apply -l component_name=<component>`) — the same mechanism `kmt install` uses for a group
(`group=standard`) and `kmt routing install` uses for routing (`group=routing`). There is no
per-component `helm upgrade`, and no table of components in the code: what the command accepts is
`constants.DEFAULT_NAMESPACES`, and what it can apply is read out of the environment's helmfile.

**What it installs.** `cert-manager`, `grafana`, `loki`, `openbao`, `polaris`, `prometheus`,
`traefik` — the seven components that have a release labelled after them. A group apply can be wider
than a component apply: the `standard` group installs seven releases, because
`alloy`, `prometheus-blackbox-exporter` and `prometheus-helm-exporter` carry `component_name:` labels
that belong to no component, so
`kmt component install dev prometheus` applies one release and `kmt install --helmfile-installation-group
standard` applies all seven of that group's. `alloy` is one of those three deliberately: it is the log
collector and it shares `loki`'s namespace, so it is installed by the group and swept by the namespace,
and an `alloy` entry in `constants` would have bought a key helmfile never reads and a namespace to
delete twice.

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
the verb that does it. Already somebody else's → refused, applying nothing, because a second
controller, class and status writer is not a thing a command named `install` gets to decide; `kmt
routing install <environment> --update` is the verb that can ask. `cert-manager` is not asked, because
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
  IngressClass, so an Ingress that names no class is routed. `kmt install` installs no controller; nginx
  ingress went out on 2026-10-28 and is swept from clusters that really had it. See the CHANGELOG and
  `docs/README_PLATFORM_CONFIG.md`.
- Helmfile based component installation: The releases kubementat applies are declared in
  `kubementat/core/assets/values/helmfile.yaml.gotmpl`, the manifest that ships inside the installed `kmt`
  (R-12.1/T-18). That file is the entry point: `helmfile apply` runs it directly, `helm_utils.py`'s
  `helmfile_paths()` names it, and nothing copies it into `platform_config/` any more. The
  configuration contributes values, not a manifest, so editing the shipped file is editing what every
  environment applies — and a `helmfile.yaml.gotmpl` someone left under
  `platform_config/<environment>/kubementat_components/` is data nothing loads.