#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to the vault ui for the given environment
#
#################################

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: open_vault_tunnel.sh <ENVIRONMENT_NAME>"
  echo "e.g.: open_vault_tunnel.sh dev"
  exit 1
fi

set -eu

echo "#########################"
echo "Loading configuration from platform_config ..."
VAULT_DEPLOYMENT_NAMESPACE="vault"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "VAULT_DEPLOYMENT_NAMESPACE: $VAULT_DEPLOYMENT_NAMESPACE"
echo "#########################"

LOCAL_PORT="8200"
pod_name="$(kubectl -n "$VAULT_DEPLOYMENT_NAMESPACE" get pod -l "app.kubernetes.io/name=vault,app.kubernetes.io/instance=vault" -o json | jq -r '.items[0].metadata.name')"
echo "Pod Name: $pod_name"
echo "Visit: http://localhost:$LOCAL_PORT"

source open_pod_tunnel.sh "$VAULT_DEPLOYMENT_NAMESPACE" "$pod_name" "$LOCAL_PORT" "8200" "0.0.0.0"