#!/usr/bin/env bash

# TODO: remove once migrating to kmt fully

######################################
#
# This script removes the pipelines and tasks setup completely
# It will keep the namespaces anyways
# configuration is read from the according config in platform_config for the given env
#
######################################

set -e

PLATFORM_CONFIG_DIRECTORY="../../../platform_config"

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: uninstall_pipelines.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: uninstall_pipelines.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
CONFIG_FILE="${PLATFORM_CONFIG_DIRECTORY}/${ENVIRONMENT}/${TEAM}/static.json"
PIPELINE_NAMESPACE=$(jq -r '.PIPELINE_NAMESPACE' "$CONFIG_FILE")
APP_DEPLOYMENT_NAMESPACE=$(jq -r '.APP_DEPLOYMENT_NAMESPACE' "$CONFIG_FILE")

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "#########################"

echo "Deleting the pipeline namespace: $PIPELINE_NAMESPACE ..."
kubectl delete namespace $PIPELINE_NAMESPACE || true

echo "#########################"
echo "Listing all objects in namespace $APP_DEPLOYMENT_NAMESPACE :"
kubectl get all -n "$APP_DEPLOYMENT_NAMESPACE"
