#!/usr/bin/env bash

#################################
#
# This script runs the pipeline with the given name
# It does this by applying the according yml file in the pipeline-runs directory
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
PIPELINE_RUN_FILE="$3"
ALLOW_PARALLEL_RUN="false"
if [[ "$4" == "true" ]]; then
  ALLOW_PARALLEL_RUN="true"
fi

if [[ "$ENVIRONMENT" == "" || "$PIPELINE_RUN_FILE" == "" || "$TEAM" == "" ]]; then
  echo "Usage: run_pipeline.sh <ENVIRONMENT_NAME> <TEAM> <PIPELINE_RUN_FILE> <OPTIONAL: ALLOW_PARALLEL_RUN>"
  echo "e.g.: run_pipeline.sh dev dev1 ../pipeline-runs/deploy-pipeline-nginx-example-run.yml false"
  echo "##############"
  echo "Available pipeline runs for team ${TEAM}:"
  ls ../pipeline-runs/${TEAM}/*.yml
  echo "##############"
  echo "General pipeline runs:"
  ls ../pipeline-runs/*.yml
  echo "##############"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
export PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
export TEKTON_KUBERNETES_STORAGE_CLASS="$(jq -r '.TEKTON_KUBERNETES_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/static.json)"

export DOCKER_REGISTRY_BASE_URL="$(jq -r '.DOCKER_REGISTRY_BASE_URL' ../../platform_config/"${ENVIRONMENT}"/static.json)"
export TEKTON_CI_IMAGE_NAME="$(jq -r '.TEKTON_CI_IMAGE_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
export TEKTON_CI_IMAGE_TAG="$(jq -r '.TEKTON_CI_IMAGE_TAG' ../../platform_config/"${ENVIRONMENT}"/static.json)"

export AUTOMATION_GIT_URL="$(jq -r '.AUTOMATION_GIT_URL' ../../platform_config/"${ENVIRONMENT}"/static.json)"
export AUTOMATION_GIT_PROJECT_NAME="$(jq -r '.AUTOMATION_GIT_PROJECT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
export AUTOMATION_GIT_REVISION="$(jq -r '.AUTOMATION_GIT_REVISION' ../../platform_config/"${ENVIRONMENT}"/static.json)"
export AUTOMATION_GIT_SERVER_HOST="$(jq -r '.AUTOMATION_GIT_SERVER_HOST' ../../platform_config/"${ENVIRONMENT}"/static.json)"
export AUTOMATION_GIT_SERVER_PORT="$(jq -r '.AUTOMATION_GIT_SERVER_PORT' ../../platform_config/"${ENVIRONMENT}"/static.json)"
export AUTOMATION_GIT_SERVER_SSH_USER="$(jq -r '.AUTOMATION_GIT_SERVER_SSH_USER' ../../platform_config/"${ENVIRONMENT}"/static.json)"

export HELM_DEPLOYER_SERVICE_ACCOUNT_NAME="$(jq -r '.HELM_DEPLOYER_SERVICE_ACCOUNT_NAME' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "TEKTON_KUBERNETES_STORAGE_CLASS: $TEKTON_KUBERNETES_STORAGE_CLASS"
echo ""
echo "DOCKER_REGISTRY_BASE_URL: $DOCKER_REGISTRY_BASE_URL"
echo "TEKTON_CI_IMAGE_NAME: $TEKTON_CI_IMAGE_NAME"
echo "TEKTON_CI_IMAGE_TAG: $TEKTON_CI_IMAGE_TAG"
echo ""
echo "AUTOMATION_GIT_URL: $AUTOMATION_GIT_URL"
echo "AUTOMATION_GIT_PROJECT_NAME: $AUTOMATION_GIT_PROJECT_NAME"
echo "AUTOMATION_GIT_REVISION: $AUTOMATION_GIT_REVISION"
echo "AUTOMATION_GIT_SERVER_HOST: $AUTOMATION_GIT_SERVER_HOST"
echo "AUTOMATION_GIT_SERVER_PORT: $AUTOMATION_GIT_SERVER_PORT"
echo "AUTOMATION_GIT_SERVER_SSH_USER: $AUTOMATION_GIT_SERVER_SSH_USER"
echo ""
echo "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME: $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"
echo ""
echo "KUBECTL version:"
kubectl version
echo "TKN version:"
tkn version
echo "#########################"

echo "Running pipeline run file $PIPELINE_RUN_FILE in $PIPELINE_NAMESPACE namespace ..."

# generate a name for the run
name_in_file="$(cat "$PIPELINE_RUN_FILE" | yq e -j |jq -r '.metadata.name')"
pipeline_name="$(cat "$PIPELINE_RUN_FILE" | yq e -j |jq -r '.spec.pipelineRef.name')"
echo "pipeline-run general name: $name_in_file"
echo "pipeline-run target pipeline: $pipeline_name"

# check whether the selected pipeline is already running within the namespace
if [[ "$ALLOW_PARALLEL_RUN" != "true" ]]; then
  runs="$(tkn pipelinerun list -n "$PIPELINE_NAMESPACE" -o json | jq -r --arg pipeline_name "$pipeline_name" '.items')"
  if [[ "$runs" == "null" ]]; then
    echo "No runs for pipeline $pipeline_name found in namespace $PIPELINE_NAMESPACE"
  else
    already_running_pipelines="$(echo "$runs" | jq -r --arg pipeline_name "$pipeline_name" '.[] | select((.spec.pipelineRef.name==$pipeline_name) and (.status.conditions[0].reason=="Running")).metadata.name')"
    if [[ "$already_running_pipelines" != "" ]]; then
      echo "##############################"
      echo "There are running tasks for pipeline $pipeline_name within the namespace $PIPELINE_NAMESPACE"
      echo "Running pipelines: $already_running_pipelines"
      echo "##############################"
      echo "Listing runs for reference"
      tkn -n "$PIPELINE_NAMESPACE" pipelinerun list
      echo "##############################"
      echo "CANCELLED TO AVOID PARALLEL RUN!"
      exit 1
    fi
  fi
fi

# write temp run file with adjusted name
random_postfix="$(openssl rand -hex 5)"
export PIPELINE_RUN_NAME="${name_in_file}-${random_postfix}"
echo "Generated pipeline-run name: $PIPELINE_RUN_NAME"
apply_file_contents="$(cat "$PIPELINE_RUN_FILE" | yq e -j |jq --arg PIPELINE_RUN_NAME "$PIPELINE_RUN_NAME" '.metadata.name = $PIPELINE_RUN_NAME' | yq e)"

# replace placeholders
seds+="s|DOCKER_REGISTRY_BASE_URL_PLACEHOLDER|${DOCKER_REGISTRY_BASE_URL}|g;"
seds+="s|TEKTON_CI_IMAGE_NAME_PLACEHOLDER|${TEKTON_CI_IMAGE_NAME}|g;"
seds+="s|TEKTON_CI_IMAGE_TAG_PLACEHOLDER|${TEKTON_CI_IMAGE_TAG}|g;"
seds+="s|STORAGE_CLASS_PLACEHOLDER|${TEKTON_KUBERNETES_STORAGE_CLASS}|g;"
seds+="s|AUTOMATION_GIT_PROJECT_NAME_PLACEHOLDER|${AUTOMATION_GIT_PROJECT_NAME}|g;"
seds+="s|AUTOMATION_GIT_REVISION_PLACEHOLDER|${AUTOMATION_GIT_REVISION}|g;"
seds+="s|AUTOMATION_GIT_SERVER_HOST_PLACEHOLDER|${AUTOMATION_GIT_SERVER_HOST}|g;"
seds+="s|AUTOMATION_GIT_SERVER_PORT_PLACEHOLDER|${AUTOMATION_GIT_SERVER_PORT}|g;"
seds+="s|AUTOMATION_GIT_SERVER_SSH_USER_PLACEHOLDER|${AUTOMATION_GIT_SERVER_SSH_USER}|g;"
seds+="s|AUTOMATION_GIT_URL_PLACEHOLDER|${AUTOMATION_GIT_URL}|g;"
seds+="s|HELM_DEPLOYER_SERVICE_ACCOUNT_NAME_PLACEHOLDER|${HELM_DEPLOYER_SERVICE_ACCOUNT_NAME}|g;"

apply_file_contents="$(echo "$apply_file_contents" | sed "${seds}")"
echo "#########################"
echo "Applying pipeline run:"
echo ""
printf "$apply_file_contents"
echo ""
echo "#########################"
echo ""
echo "$apply_file_contents" | kubectl -n "$PIPELINE_NAMESPACE" apply -f -
tkn -n "$PIPELINE_NAMESPACE" pipelinerun list
