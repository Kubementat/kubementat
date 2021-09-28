#!/usr/bin/env bash

######################################
#
# This script removes the polaris components from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_polaris.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_polaris.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
POLARIS_DEPLOYMENT_NAMESPACE="$(jq -r '.POLARIS_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
POLARIS_DEPLOYMENT_NAME="$(jq -r '.POLARIS_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "POLARIS_DEPLOYMENT_NAMESPACE: $POLARIS_DEPLOYMENT_NAMESPACE"
echo "POLARIS_DEPLOYMENT_NAME: $POLARIS_DEPLOYMENT_NAME"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling polaris ..."
helm -n "${POLARIS_DEPLOYMENT_NAMESPACE}" delete "${POLARIS_DEPLOYMENT_NAME}" ||true

kubectl get all -n "${POLARIS_DEPLOYMENT_NAMESPACE}"

echo "If you also want to remove collected data ensure to also delete the pv and pvc"
echo "You can do this via: kubectl -n ${POLARIS_DEPLOYMENT_NAMESPACE} delete pvc <PVC_NAME>"
echo "PVC:"
kubectl get pvc -n "${POLARIS_DEPLOYMENT_NAMESPACE}" | grep "${POLARIS_DEPLOYMENT_NAME}"
echo "PV:"
kubectl get pv | grep "${POLARIS_DEPLOYMENT_NAME}"