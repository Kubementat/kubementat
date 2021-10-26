#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to the tekton dashboard for the given environment
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: open_tekton_dashboard_tunnel.sh <ENVIRONMENT_NAME>"
  echo "e.g.: open_tekton_dashboard_tunnel.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
TEKTON_NAMESPACE="$(jq -r '.TEKTON_NAMESPACE' ../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "#########################"

LOCAL_PORT="9097"
ADDRESS="0.0.0.0"
pod_name="$(kubectl -n "$TEKTON_NAMESPACE" get pod -l app=tekton-dashboard -o json | jq -r '.items[0].metadata.name')"
echo "Pod Name: $pod_name"
echo "Visit: http://127.0.0.1:${LOCAL_PORT}"
kubectl -n "$TEKTON_NAMESPACE" port-forward --address $ADDRESS "$pod_name" $LOCAL_PORT:9097