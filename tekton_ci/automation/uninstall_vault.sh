#!/usr/bin/env bash

######################################
#
# This script removes vault from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_vault.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_vault.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
VAULT_DEPLOYMENT_NAMESPACE="$(jq -r '.VAULT_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
VAULT_DEPLOYMENT_NAME="$(jq -r '.VAULT_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "VAULT:"
echo "VAULT_DEPLOYMENT_NAMESPACE: $VAULT_DEPLOYMENT_NAMESPACE"
echo "VAULT_DEPLOYMENT_NAME: $VAULT_DEPLOYMENT_NAME"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling vault..."
helm -n "${VAULT_DEPLOYMENT_NAMESPACE}" delete "${VAULT_DEPLOYMENT_NAME}" ||true

kubectl get all -n "${VAULT_DEPLOYMENT_NAMESPACE}"

# # TODO:
# echo "If you also want to remove collected data ensure to also delete the VAULT pv and pvc"
# echo "You can do this via: kubectl -n ${VAULT_DEPLOYMENT_NAMESPACE} delete pvc <PVC_NAME>"
# echo "PVC:"
# kubectl get pvc -n "${VAULT_DEPLOYMENT_NAMESPACE}" | grep "${VAULT_DEPLOYMENT_NAME}"
# echo "PV:"
# kubectl get pv | grep "${VAULT_DEPLOYMENT_NAME}"