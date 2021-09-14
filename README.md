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

### The hard manual way
```
# TODO:
# install jq

# install yq

# install gnugpg

# install kubectl

# install tkn cli

# install linkerd cli
curl -sL run.linkerd.io/install | sh
echo "export PATH=$PATH:/$HOME/.linkerd2/bin" >> $HOME/.zshrc
linkerd version
```

### Or just start a prebaked docker image
TODO:


## Installation / Getting Started
TODO: add full setup instructions

```
./initialize_easy_tekton.sh
cd tekton_ci/automation/
./00_full_setup.sh dev dev1
```