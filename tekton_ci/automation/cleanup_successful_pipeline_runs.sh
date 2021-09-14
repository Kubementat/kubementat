#!/usr/bin/env bash

#################################
#
# This script removes all pipeline runs in status: Succeeded
# in the configured namespace for the env
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: cleanup_successful_pipeline_runs.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: cleanup_successful_pipeline_runs.sh dev dev1"
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
echo "Cleaning up succeeded pipeline runs..."
pipeline_runs="$(tkn -n "$PIPELINE_NAMESPACE" pipelinerun list -o json)"
succeeded_runs="$(echo "$pipeline_runs" |jq -r '.items[]? | select((.status.conditions[0].type=="Succeeded") and (.status.conditions[0].status=="True")).metadata.name')"
echo "Succeeded runs:"
echo "$succeeded_runs"
for prun in $succeeded_runs; do
  echo "Cleanup pipeline run: $prun"
  tkn -n "$PIPELINE_NAMESPACE" pipelinerun delete -f "$prun"
done

echo "#########################"
echo "Listing all pipeline runs in $PIPELINE_NAMESPACE namespace after cleanup:"
tkn -n "$PIPELINE_NAMESPACE" pipelinerun list
