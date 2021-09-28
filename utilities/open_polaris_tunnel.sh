#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to the polaris ui for the given environment
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: open_polaris_tunnel.sh <ENVIRONMENT_NAME>"
  echo "e.g.: open_polaris_tunnel.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
POLARIS_DEPLOYMENT_NAMESPACE="$(jq -r '.POLARIS_DEPLOYMENT_NAMESPACE' ../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "POLARIS_DEPLOYMENT_NAMESPACE: $POLARIS_DEPLOYMENT_NAMESPACE"
echo "#########################"

LOCAL_PORT="8200"
ADDRESS="0.0.0.0"
pod_name="$(kubectl -n "$POLARIS_DEPLOYMENT_NAMESPACE" get pod -l "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris" -o json | jq -r '.items[0].metadata.name')"
echo "Pod Name: $pod_name"
echo "Visit: http://localhost:$LOCAL_PORT"
kubectl -n "$POLARIS_DEPLOYMENT_NAMESPACE" port-forward --address $ADDRESS "$pod_name" $LOCAL_PORT:8200