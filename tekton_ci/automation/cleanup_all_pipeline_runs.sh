#!/usr/bin/env bash

#################################
#
# This script removes all pipeline runs
# in the configured namespace for the env
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: cleanup_all_pipelineruns.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: cleanup_all_pipelineruns.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "#########################"

echo "Listing all pipeline runs in $PIPELINE_NAMESPACE namespace :"
tkn -n "$PIPELINE_NAMESPACE" pipelinerun list

echo "#########################"
echo "Cleaning up all pipeline runs..."
tkn -n "$PIPELINE_NAMESPACE" pipelinerun delete -f --all

echo "#########################"
echo "Listing all pipeline runs in $PIPELINE_NAMESPACE namespace after cleanup:"
tkn -n "$PIPELINE_NAMESPACE" pipelinerun list
