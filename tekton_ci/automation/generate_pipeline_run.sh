#!/usr/bin/env bash

#################################
#
# This script generates a pipeline run
#
#################################

## HELPER FUNCTIONS
check_target_file_exists() {
  file_path="$1"
  if [ -f "$file_path" ]; then
    echo "File $file_path already exists: aborting script"
    return 1
  fi
  return 0
}

check_file_exists() {
  file_path="$1"
  if [ ! -f "$file_path" ]; then
    echo "Pipeline file $file_path does not exist: aborting script"
    return 1
  fi
  return 0
}

set -e

ENVIRONMENT="$1"
TEAM="$2"
PIPELINE_FILE="$3"

if [[ "$ENVIRONMENT" == "" || "$PIPELINE_FILE" == "" || "$TEAM" == "" ]]; then
  echo "Usage: generate_pipeline_run.sh <ENVIRONMENT_NAME> <TEAM> <PIPELINE_FILE>"
  echo "e.g.: generate_pipeline_run.sh dev dev1 ../pipelines/build-pipeline-ci-images.yml"
  echo "##############"
  echo "Available pipelines:"
  ls ../pipelines/*.yml
  echo "##############"
  exit 1
fi

check_file_exists "$PIPELINE_FILE"

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "#########################"

# pipeline_contents="$(cat "$PIPELINE_FILE" | yq e -j |jq '.'--arg run_name "$run_name" '.metadata.name = $run_name' | yq e -P)"
pipeline_contents="$(cat "$PIPELINE_FILE" | yq e -j |jq '.')"
pipeline_name="$(echo "$pipeline_contents" | jq -r '.metadata.name')"
pipeline_params="$(echo "$pipeline_contents" | jq -r '.spec.params[].name')"

echo "Pipeline Name:"
echo "$pipeline_name"
echo "#########################"
echo "Pipeline Parameters:"
echo "$pipeline_params"
echo "#########################"
pipeline_run_name="${pipeline_name}-run"
TARGET_FILE="../pipeline-runs/${TEAM}/${pipeline_run_name}.yml"
check_target_file_exists "$TARGET_FILE"
echo "#########################"

content="$(cat <<EOF
apiVersion: tekton.dev/v1beta1
kind: PipelineRun
metadata:
  name: $pipeline_run_name
spec:
  # here we need to use our previously created service account (see setup_pipelines.sh)
  # as we are deploying to another namespace than the tekton pipeline
  # and want to grant according permissions for the helm deploy task
  serviceAccountName: "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME_PLACEHOLDER"
  workspaces:
    - name: pipeline-workspace
      volumeClaimTemplate:
        spec:
          storageClassName: STORAGE_CLASS_PLACEHOLDER
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              # HINT: TODO: ADJUST ME IF MORE SPACE IS NEEDED FOR THE PIPELINE TO RUN
              storage: 100Mi
  pipelineRef:
    name: $pipeline_name
  timeout: 10m
  params:
    - name: environment
      value: $ENVIRONMENT
    - name: team
      value: "$TEAM"
    - name: docker-registry-base-url
      value: "DOCKER_REGISTRY_BASE_URL_PLACEHOLDER"
    - name: tekton-ci-image-name
      value: "TEKTON_CI_IMAGE_NAME_PLACEHOLDER"
    - name: tekton-ci-image-tag
      value: "TEKTON_CI_IMAGE_TAG_PLACEHOLDER"

    - name: automation-git-project-name
      value: "AUTOMATION_GIT_PROJECT_NAME_PLACEHOLDER"
    - name: automation-git-revision
      value: "AUTOMATION_GIT_REVISION_PLACEHOLDER"
    - name: automation-git-server-host
      value: "AUTOMATION_GIT_SERVER_HOST_PLACEHOLDER"
    - name: automation-git-server-port
      value: "AUTOMATION_GIT_SERVER_PORT_PLACEHOLDER"
    - name: automation-git-server-ssh-user
      value: "AUTOMATION_GIT_SERVER_SSH_USER_PLACEHOLDER"
    - name: automation-git-url
      value: "AUTOMATION_GIT_URL_PLACEHOLDER"

    # TODO: implement your desired values for the following parameters:
EOF
)"

# add variable placeholders
for param in $pipeline_params; do
  if [[  "$param" != "automation-git-url" && "$param" != "automation-git-server-ssh-user" && "$param" != "automation-git-server-port" && "$param" != "automation-git-server-host" && "$param" != "automation-git-revision" && "$param" != "automation-git-project-name" && "$param" != "docker-registry-base-url" && "$param" != "environment" && "$param" != "team" ]]; then
    content="$(printf "%s\n    - name: %s\n" "$content" "$param")"
    content="$(printf "%s\n      value: TODO\n" "$content")"
  fi
done

echo "$content" > "$TARGET_FILE"

echo "Generated pipelinerun for $pipeline_name for ENVIRONMENT=$ENVIRONMENT and team TEAM=$TEAM ..."
echo "PLEASE ADJUST THE FILE ACCORDING TO YOUR TEAM AND APP SPECIFIC PARAMETERS:"
echo "$TARGET_FILE"