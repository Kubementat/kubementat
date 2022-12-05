#!/usr/bin/env bash

#################################
#
# This script executes helmfile apply with all required settings for a environment (cluster)
#################################

set -e

ENVIRONMENT="$1"
HELMFILE_INSTALLATION_GROUP="$2"
INTERACTIVE_FLAG=""
if [[ "$3" == "true" ]]; then
  INTERACTIVE_FLAG="--interactive"
fi

if [[ "$ENVIRONMENT" == "" || "$HELMFILE_INSTALLATION_GROUP" == "" ]]; then
  echo "Usage: helmfile_apply.sh <ENVIRONMENT_NAME> <HELMFILE_INSTALLATION_GROUP> <OPTIONAL: INTERACTIVE (default: false)>"
  echo "e.g.: helmfile_apply.sh dev standard true"
  exit 1
fi

set -u


echo "######################################################"
echo "Executing helmfile apply for environment ${ENVIRONMENT} with group: ${HELMFILE_INSTALLATION_GROUP} ..."
echo "######################################################"
echo ""

HELMFILE_WORKING_DIRECTORY="../../../platform_config/${ENVIRONMENT}/kubementat_components"
HELMFILE_FILENAME="helmfile.yaml"

echo ""
echo "helmfile version: "
helmfile version
echo ""
echo "HELMFILE_WORKING_DIRECTORY: $HELMFILE_WORKING_DIRECTORY"
echo ""
if [[ "$INTERACTIVE_FLAG" != "" ]]; then
  echo "INTERACTIVE: true"
else
  echo "INTERACTIVE: false"
fi
echo ""
pushd "$HELMFILE_WORKING_DIRECTORY" > /dev/null
echo "Applying helmfile: $HELMFILE_FILENAME"
echo ""

# read GRAFANA_ADMIN_USER and GRAFANA_ADMIN_PASSWORD from platform_config
GRAFANA_ADMIN_USER="$(jq -r '.GRAFANA_ADMIN_USER' "../../../platform_config/$ENVIRONMENT/static.encrypted.json")"
export GRAFANA_ADMIN_USER
GRAFANA_ADMIN_PASSWORD="$(jq -r '.GRAFANA_ADMIN_PASSWORD' "../../../platform_config/$ENVIRONMENT/static.encrypted.json")"
export GRAFANA_ADMIN_PASSWORD

helmfile apply --color $INTERACTIVE_FLAG -f "$HELMFILE_FILENAME" -l "group=$HELMFILE_INSTALLATION_GROUP"

popd > /dev/null

echo "Finished helmfile apply."
echo ""