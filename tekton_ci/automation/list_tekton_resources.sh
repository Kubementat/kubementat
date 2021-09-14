#!/usr/bin/env bash

######################################
#
# This script lists all relevant tekton resources in the given environment
#
######################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: list_tekton_resources.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: list_tekton_resources.sh dev dev1"
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

echo "Listing pipelines:"
tkn -n "$PIPELINE_NAMESPACE" pipeline list

echo "#########################"
echo "Listing task:"
tkn -n "$PIPELINE_NAMESPACE" task list

echo "#########################"
echo "Listing taskruns:"
tkn -n "$PIPELINE_NAMESPACE" taskrun list

echo "#########################"
echo "Listing pipelineruns:"
tkn -n "$PIPELINE_NAMESPACE" pipelineruns list