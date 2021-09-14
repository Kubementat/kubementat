#!/usr/bin/env bash

#################################
#
# This script creates a new deployment pipeline for an app in ../pipelines
#
#################################

set -e

ENV_VARS=("APP_NAME" "APP_GIT_URL" "APP_GIT_PROJECT_NAME" "APP_GIT_SERVER_HOST" "APP_GIT_SERVER_PORT" "APP_GIT_SERVER_SSH_USER")

set -u

## HELPER FUNCTIONS
check_file_exists() {
  file_path="$1"
  if [ -f "$file_path" ]; then
    echo "File $file_path already exists: aborting script"
    exit 1
  fi
  return 0
}

usage() {
  echo "Set all according environment variables first."
  echo "E.g.:"
  echo "export APP_NAME='nginx-example'"
  echo "export APP_GIT_URL='ssh://github.com/julweber/nginx-example.git'"
  echo "export APP_GIT_PROJECT_NAME='nginx-example'"
  echo "export APP_GIT_SERVER_HOST='github.com'"
  echo "export APP_GIT_SERVER_PORT='22'"
  echo "export APP_GIT_SERVER_SSH_USER='git'"
}

check_env() {
  for each in "${ENV_VARS[@]}"; do
    if [ -z "${!each+x}" ]; then
        echo "$each is not defined"
        usage
        exit 1
    fi
  done
}

check_env

# read config
PIPELINE_TEMPLATE="../pipelines/templates/template-deploy-pipeline.yml"
TARGET_FILE="../pipelines/deploy-pipeline-${APP_NAME}.yml"

check_file_exists "$TARGET_FILE"


echo "Generating deploy pipeline resource ..."
seds+="s|APP_NAME_PLACEHOLDER|${APP_NAME}|g;"
seds+="s|APP_GIT_URL_PLACEHOLDER|${APP_GIT_URL}|g;"
seds+="s|APP_GIT_PROJECT_NAME_PLACEHOLDER|${APP_GIT_PROJECT_NAME}|g;"
seds+="s|APP_GIT_SERVER_HOST_PLACEHOLDER|${APP_GIT_SERVER_HOST}|g;"
seds+="s|APP_GIT_SERVER_PORT_PLACEHOLDER|${APP_GIT_SERVER_PORT}|g;"
seds+="s|APP_GIT_SERVER_SSH_USER_PLACEHOLDER|${APP_GIT_SERVER_SSH_USER}|g;"
contents="$(cat "$PIPELINE_TEMPLATE" | sed "${seds}" )"
echo "$contents" > "$TARGET_FILE"
echo "Finished generating deploy pipeline."

echo "###################################"
echo
echo "ATTENTION: please adjust and review the generated file carefully."
echo "As this script is not able to have knowledge about the required database setup and seeding processes and their intended configuration those changes need to be done manually."
echo "-> ${TARGET_FILE}"
echo
echo "###################################"