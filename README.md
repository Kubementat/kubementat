# Easy Tekton

This repository contains code for automating installation and setting up pipelines via tekton CI on an existing kubernetes cluster.
It also delivers examples on how to create pipelines for building and deploying k8s applications.

## Included platform component automations
- Tekton + Tekton Trigger + Tekton Dashboard
- Helm
- Loki
- Grafana
- Prometheus
- Linkerd

## Included backing service automations
- MySQL
- MongoDB
- Redis
- Cassandra
- Kafka

## Sub-directories

Each of the given sub-directories contains additional README*.md files that document the actual component. For further details dive into the sub-directories.

- __docker_files__: All custom Dockerfiles used for creating docker images and running CI tasks
- __docker_registry__: Automation for setting up a private docker registry within a K8S cluster
  - ATTENTION: CURRENTLY THIS IS NOT WORKING AS EXPECTED ON THE GIVEN CLUSTER.
- __helm_charts__: All helm charts used for deploying apps for the POC
  - nginx-example: the helm chart for deploying the nginx-example helm chart to a k8s cluster
- __platform_config__: Stores configuration as json files encrypted via git-crypt
  - This configuration is used by the automation scripts
- __tekton_ci__: The tekton CI installation and pipeline scripts for automating tasks of the POC
  - this contains all needed scripts and pipeline descriptions for spinning up the build and deployment pipelines on a k8s cluster
- __utilities__: Useful scripts for working with K8S
  - e.g. for starting containers, debugging, viewing logs, viewing cluster status and usage ...

## Git-Crypt
You need to unlock the repository to be able to use \*.encrypted.\* files in the repository (for more details see: README_GIT_CRYPT.md).
```
git-crypt unlock
```

## Local Environment Prerequisites

### Or just start a prebaked docker image
```
# Docker
docker run --name ubuntu-ci -it "docker.io/julianweberdev/ubuntu-ci-minimal:latest"

# Kubernetes
kubectl run ubuntu-ci -i --tty --image="docker.io/julianweberdev/ubuntu-ci-minimal:latest" --command /bin/bash
```

### Or: The hard manual way
```
# install jq
# install yq
# install gnugpg
# install kubectl
# install tkn cli
# install linkerd cli
```

## Installation / Getting Started
For installing easy_tekton on your kubernetes cluster (either running in the Cloud or on a raspberry pi)
we need to take some initial configuration steps.
- Initialize and configure the git repository you will use for running and developing with easy_tekton
- Configure the docker registry you will use
- Configure kubernetes specific settings

### Clone the repository
```
git clone https://github.com/julweber/easy_tekton
cd easy_tekton

# if you have not set your git configuration yet:
git config --global user.email "smith@matrix.com"
git config --global user.name "Agent Smith"
```

### Git Repository
- This project is intended as a template to build your own customizations on top.
- You need to either fork this repository to your own public github account or clone and push to your own private git repository.
  - The according location should be configured via the environment variables:
    - AUTOMATION_GIT_URL - e.g. 'git@github.com:julweber/easy_tekton.git'
      - for your own registry: 'git@github.com:YOUR_USERNAME/easy_tekton_YOUR_ENVIRONMENT.git'
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
export AUTOMATION_GIT_URL='git@github.com:YOUR_USERNAME/easy_tekton_YOUR_ENVIRONMENT.git'
export AUTOMATION_GIT_SERVER_HOST='github.com'
export AUTOMATION_GIT_SERVER_PORT='22'
export AUTOMATION_GIT_SERVER_SSH_USER='git'
export KUBERNETES_DEFAULT_STORAGE_CLASS='YOUR_KUBERNETES_DEFAULT_STORAGE_CLASS'
export DOCKER_REGISTRY_BASE_URL='YOUR_DOCKER_REGISTRY_BASE_URL'

./initialize_easy_tekton.sh

```

### Configure your git repository
Now you need to push your repository to your upstream git repo and configure the generated deployer key in your github repository (GIT_DEPLOYER_PUBLIC_KEY in platform_config/dev/static.json)

### Install easy tekton tooling to the cluster
```
# Install the tekton tooling on your cluster
./install_easy_tekton.sh dev dev1

# Test run a pipeline via tekton
pushd tekton_ci/automation/
./run_pipeline.sh dev dev1 ../pipeline-runs/hello-world-pipeline-run.yml
popd

# view progress via tekton dashboard
pushd utilities
./open_dashboard_tunnel.sh dev
```