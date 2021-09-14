#!/usr/bin/env bash

#################################
#
# This script creates a tekton trigger configuration set of yml files in ../triggers/ENVIRONMENT/TEAM
#
#################################

set -e

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" || "$APP_NAME" == "" || "$PIPELINE_NAME" == "" ]]; then
  echo "Set all according environment variables first."
  echo "E.g.:"
  echo "export ENVIRONMENT='dev'"
  echo "export TEAM='dev1'"
  echo "export APP_NAME='nginx-example'"
  echo "export PIPELINE_NAME='build-nginx-example'"
  exit 1
fi

set -u

## HELPER FUNCTIONS
check_dir_exists() {
  dir_path="$1"
  if [ -d "$dir_path" ]; then
    echo "Directory $dir_path already exists: aborting script"
    return 1
  fi
  return 0
}


# read config
echo "Reading config for env: $ENVIRONMENT ..."
DOCKER_REGISTRY_BASE_URL="$(jq -r '.DOCKER_REGISTRY_BASE_URL' ../../platform_config/"${ENVIRONMENT}"/static.json)"
BASE_DOMAIN="$(jq -r '.BASE_DOMAIN' ../../platform_config/"${ENVIRONMENT}"/static.json)"
TEKTON_KUBERNETES_STORAGE_CLASS="$(jq -r '.TEKTON_KUBERNETES_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/static.json)"
TARGET_DIRECTORY="../triggers/${TEAM}/${APP_NAME}"

echo "Settings: "
echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_NAME: $APP_NAME"
echo "PIPELINE_NAME: $PIPELINE_NAME"
echo "BASE_DOMAIN: $BASE_DOMAIN"
echo "DOCKER_REGISTRY_BASE_URL: $DOCKER_REGISTRY_BASE_URL"
echo "TEKTON_KUBERNETES_STORAGE_CLASS: $TEKTON_KUBERNETES_STORAGE_CLASS"
echo "TARGET_DIRECTORY: $TARGET_DIRECTORY"

INGRESS_FILE_TEMPLATE="../triggers/templates/template-event-listener-ingress.yml"
EVENT_LISTENER_TEMPLATE="../triggers/templates/template-event-listener.yml"
TRIGGER_BINDING_TEMPLATE="../triggers/templates/template-trigger-binding.yml"
TRIGGER_TEMPLATE_TEMPLATE="../triggers/templates/template-trigger-template.yml"

check_dir_exists "$TARGET_DIRECTORY"
echo "Generating directory: $TARGET_DIRECTORY"
mkdir -p "$TARGET_DIRECTORY"

echo "Generating ingress resource ..."
contents="$(cat "$INGRESS_FILE_TEMPLATE" | sed "s|TEAM_PLACEHOLDER|${TEAM}|g;s|APP_NAME_PLACEHOLDER|${APP_NAME}|g;s|TEAM_PLACEHOLDER|${TEAM}|g;s|BASE_DOMAIN_PLACEHOLDER|${BASE_DOMAIN}|g;" )"
target_file_name="${TARGET_DIRECTORY}/${APP_NAME}-event-listener-ingress.yml"
echo "$contents" > "$target_file_name"
echo "Finished generating ingress resource."

echo "Generating event listener resource ..."
contents="$(cat "$EVENT_LISTENER_TEMPLATE" | sed "s|APP_NAME_PLACEHOLDER|${APP_NAME}|g;s|TEAM_PLACEHOLDER|${TEAM}|g")"
target_file_name="${TARGET_DIRECTORY}/${APP_NAME}-event-listener.yml"
echo "$contents" > "$target_file_name"
echo "Finished generating event listener resource."

echo "Generating trigger binding resource ..."
contents="$(cat "$TRIGGER_BINDING_TEMPLATE" | sed "s|APP_NAME_PLACEHOLDER|${APP_NAME}|g;s|TEAM_PLACEHOLDER|${TEAM}|g")"
target_file_name="${TARGET_DIRECTORY}/${APP_NAME}-trigger-binding.yml"
echo "$contents" > "$target_file_name"
echo "Finished generating trigger binding resource."

echo "Generating trigger template resource ..."
seds="s|TEAM_PLACEHOLDER|${TEAM}|g;"
seds+="s|APP_NAME_PLACEHOLDER|${APP_NAME}|g;"
seds+="s|PIPELINE_NAME_PLACEHOLDER|${PIPELINE_NAME}|g;"
seds+="s|ENVIRONMENT_PLACEHOLDER|${ENVIRONMENT}|g;"
seds+="s|DOCKER_REGISTRY_BASE_URL_PLACEHOLDER|${DOCKER_REGISTRY_BASE_URL}|g;"
seds+="s|STORAGE_CLASS_PLACEHOLDER|${TEKTON_KUBERNETES_STORAGE_CLASS}|g;"

contents="$(cat "$TRIGGER_TEMPLATE_TEMPLATE" | sed "${seds}" )"
target_file_name="${TARGET_DIRECTORY}/${APP_NAME}-trigger-template.yml"
echo "$contents" > "$target_file_name"
echo "Finished generating trigger template resource."

echo "###################################"
echo
echo "$TARGET_DIRECTORY contents:"
ls "$TARGET_DIRECTORY"

echo "###################################"
echo
echo "ATTENTION: please adjust and review the generated files carefully, especially the trigger-template."
echo "As this script is not able to have knowledge about the required pipeline-run parameters and their intended configuration those changes need to be done manually."
echo
echo "###################################"
echo
echo "Please configure the git webhook url within the according repo:"
echo "Hook URL: https://${APP_NAME}.trigger-hooks.${TEAM}.${BASE_DOMAIN}"
echo "You can find the secret to configure in: platform_config/$ENVIRONMENT/static.encrypted.json -> GITLAB_WEBHOOK_SECRET"