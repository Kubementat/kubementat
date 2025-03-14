# Kubementat

This repository contains code for automating installation and setting up  a kubernetes development environment. It provides pipelines via tekton CI on an existing kubernetes cluster.
In addition it contains a complete Open Source standard toolset for monitoring (prometheus, grafana), log aggregation & analysis (loki, grafana) and a lighweight service mesh (linkerd).
It also delivers examples on how to create tasks & pipelines for building and deploying k8s applications and standard backing services (SQL, MongoDB, Redis & more).

__The general goal of this project is to allow developers/operators to get up and running; aka productive; with their kubernetes cluster as easy and comfortable as possible.__

If you are interested in the naming of this project, you can have a look [here](https://dune.fandom.com/wiki/Mentat) :)

## Included platform component automations
- [Tekton + Tekton Trigger + Tekton Dashboard](https://tekton.dev/)
- [Helm](https://helm.sh/)
- [Grafana](https://grafana.com/grafana/)
- [Prometheus](https://prometheus.io/)
- [Loki](https://grafana.com/oss/loki/)
- [Linkerd](https://linkerd.io/)
- [Polaris](https://github.com/FairwindsOps/polaris)

## Included backing service automations
- PostgreSQL
- MySQL
- MongoDB
- Redis

## Sub-directories

Each of the given sub-directories contains additional README*.md files that document the actual component. For further details dive into the sub-directories.

- __docker_files__: All custom Dockerfiles used for creating docker images and running CI tasks
- __helm_charts__: All helm charts used for deploying apps for the POC
  - nginx-example: the helm chart for deploying the nginx-example helm chart to a k8s cluster
- __platform_config__: Stores configuration as json files encrypted via git-crypt
  - This configuration is used by the automation scripts
- __tekton_ci__: The tekton CI installation and pipeline scripts for automating tasks of the POC
  - this contains all needed scripts and pipeline descriptions for spinning up the build and deployment pipelines on a k8s cluster
- __utilities__: Useful scripts for working with K8S
  - e.g. for starting containers, debugging, viewing logs, viewing cluster status and usage ...
  - this also contains scripts for the following use cases:
    - user management
    - secret management
    - tunneling
    - kubernetes helpers
    - helm helpers

## Git-Crypt
You need to unlock the repository to be able to use \*.encrypted.\* files in the repository (for more details see: README_GIT_CRYPT.md).
```
git-crypt unlock
```

## Local Environment Prerequisites

### Or just start a prebaked docker image with everything installed
```
# PREFERED WAY:
# Run image via Docker and mount this directory
docker run --name ubuntu-ci -it --mount type=bind,source="$(pwd)",target=/src "docker.io/julianweberdev/ubuntu-ci-minimal:latest"
# Then on the container: cd /src

# Alternative: Kubernetes - but then you need to directly commit all changes to your fork of the kubementat repo
# In addition you also need to transfer all generated key files manually using this approach
kubectl run ubuntu-ci -i --tty --image="docker.io/julianweberdev/ubuntu-ci-minimal:latest" --command /bin/bash
```

### Or: The hard manual way
```
# install git-crypt
# install python & pip
# install jq
# install yq
# install gnugpg
# install kubectl
# install helm
# install helmfile
# install helm diff plugin -> helm plugin install https://github.com/databus23/helm-diff
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

### Install the kmt cli requirements via pip
The kmt cli serves as a central tool for managing your kubementat processes.

```
pip install -r cli/requirements.txt

# view kmt cli help
cd cli
./kmt --help
```

### Git Repository
- This project is intended as a template to build your own customizations on top.
- You need to either fork this repository to your own public github account or clone and push to your own private git repository.
  - The according location should be configured via the environment variables:
    - AUTOMATION_GIT_URL - e.g. 'git@github.com:Kubementat/kubementat.git'
      - for your own registry: 'git@github.com:YOUR_USERNAME/kubementat_YOUR_ENVIRONMENT.git'
    - AUTOMATION_GIT_SERVER_HOST - e.g. 'github.com'
    - AUTOMATION_GIT_SERVER_PORT - e.g. '22'
    - AUTOMATION_GIT_SERVER_SSH_USER - e.g. 'git'
  Your configuration and script adjustments will then be pulled from this location for executing your CI tasks (search for "automation-git-url" within the pipeline yaml files to learn more about the specifics)

### Docker Registry
- We are providing a prebuilt standard docker image here:
- Anyways if you really want to use the system and adjust to your needs you should configure your own docker registry via the environment variable:
  - DOCKER_REGISTRY_BASE_URL - e.g. 'docker.io/julianweberdev'

### Kubernetes Settings
- Available environment variables:
  - KUBERNETES_DEFAULT_STORAGE_CLASS - e.g. 'local-path'

### Generate initial configuration
```
echo "ATTENTION: Please replace the placeholder starting with YOUR_ below

export BASE_DOMAIN='YOUR_DOMAIN.com'
export AUTOMATION_GIT_URL='git@github.com:YOUR_USERNAME/kubementat_YOUR_ENVIRONMENT.git'
export AUTOMATION_GIT_SERVER_HOST='github.com'
export AUTOMATION_GIT_SERVER_PORT='22'
export AUTOMATION_GIT_SERVER_SSH_USER='git'
export KUBERNETES_DEFAULT_STORAGE_CLASS='YOUR_KUBERNETES_DEFAULT_STORAGE_CLASS'
export DOCKER_REGISTRY_BASE_URL='YOUR_DOCKER_REGISTRY_BASE_URL'
export CLUSTER_MANAGER_EMAIL='YOUR_EMAIL_ADDRESS'

./initialize_kubementat.sh

```

### Configure your git repository
Now you need to push your repository to your upstream git repo and configure the generated deployer key in your github repository (GIT_DEPLOYER_PUBLIC_KEY in platform_config/dev/static.json)

### Install kubementat tooling to the cluster
```
# Install the tekton tooling on your cluster
pushd cli
./kmt install dev dev1
popd

# If you are using a private docker registry ensure to run
pushd cli
./kmt tekton-configure-docker-registry-access dev dev1
popd

# Optional (but recommmended)
# Configure cluster wide auto cleanup of finished tekton pipeline runs
# This is implemented via Kubernetes cronjob
pushd tekton_ci/automation/
./setup_tekton_pipelinerun_cleanup_job.sh dev dev1
popd

# Test run a pipeline via tekton
pushd cli
./kmt tekton-run-pipeline dev dev1 ../../examples/tekton_ci/pipeline-runs/hello-world-pipeline-run.yml

# view progress via tekton dashboard
./kmt tunnel-tekton
```

## Additional Features
- Routing: Kubementat provides templated configuration for configuring nginx ingress controller and cert-manager for ingress routing (see install_routing.sh)
- Helmfile based component installation: See templates/environment/kubementat_components/helmfile.yaml.template for already preconfigured/templated components
