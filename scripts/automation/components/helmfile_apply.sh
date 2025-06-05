#!/usr/bin/env bash

# TODO: this can be removed once migrating to full kmt cli control
# This is used throughout the code on multiple places and needs to be replaced with a function call to HelmUtils.helmfile_apply
# keeping it for now, but this won't be maintained any longer

#################################
#
# This script executes helmfile apply with all required settings for a environment (cluster)
#################################

set -e

PLATFORM_CONFIG_DIRECTORY="../../../platform_config"

ENVIRONMENT="$1"
HELMFILE_LABEL_FILTER="$2"
INTERACTIVE_FLAG=""
if [[ "$3" == "true" ]]; then
  INTERACTIVE_FLAG="--interactive"
fi

if [[ "$ENVIRONMENT" == "" || "$HELMFILE_LABEL_FILTER" == "" ]]; then
  echo "Usage: helmfile_apply.sh <ENVIRONMENT_NAME> <HELMFILE_LABEL_FILTER> <OPTIONAL: INTERACTIVE (default: false)>"
  echo "e.g.: helmfile_apply.sh dev 'group=standard' true"
  exit 1
fi

set -u


echo "######################################################"
echo "Executing helmfile apply for environment ${ENVIRONMENT} with label filter: ${HELMFILE_LABEL_FILTER} ..."
echo "######################################################"
echo ""

HELMFILE_WORKING_DIRECTORY="${PLATFORM_CONFIG_DIRECTORY}/${ENVIRONMENT}/kubementat_components"
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
GRAFANA_ADMIN_USER=$(jq -r '.GRAFANA_ADMIN_USER' "${PLATFORM_CONFIG_DIRECTORY}/$ENVIRONMENT/static.encrypted.json")
export GRAFANA_ADMIN_USER
GRAFANA_ADMIN_PASSWORD=$(jq -r '.GRAFANA_ADMIN_PASSWORD' "${PLATFORM_CONFIG_DIRECTORY}/$ENVIRONMENT/static.encrypted.json")
export GRAFANA_ADMIN_PASSWORD

helmfile apply --color $INTERACTIVE_FLAG -f "$HELMFILE_FILENAME" -l "$HELMFILE_LABEL_FILTER"

popd > /dev/null

echo "Finished helmfile apply."
echo ""