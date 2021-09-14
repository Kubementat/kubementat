#!/usr/bin/env bash

######################################
#
# This script removes the logging components from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_logging.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_logging.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
LOKI_DEPLOYMENT_NAMESPACE="$(jq -r '.LOKI_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
LOKI_DEPLOYMENT_NAME="$(jq -r '.LOKI_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "PROMETHEUS:"
echo "LOKI_DEPLOYMENT_NAMESPACE: $LOKI_DEPLOYMENT_NAMESPACE"
echo "LOKI_DEPLOYMENT_NAME: $LOKI_DEPLOYMENT_NAME"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling loki-stack..."
helm -n "${LOKI_DEPLOYMENT_NAMESPACE}" delete "${LOKI_DEPLOYMENT_NAME}" ||true

kubectl get all -n "${LOKI_DEPLOYMENT_NAMESPACE}"

echo "If you also want to remove collected data ensure to also delete the loki pv and pvc"
echo "You can do this via: kubectl -n ${LOKI_DEPLOYMENT_NAMESPACE} delete pvc <PVC_NAME>"
echo "PVC:"
kubectl get pvc -n "${LOKI_DEPLOYMENT_NAMESPACE}" | grep "${LOKI_DEPLOYMENT_NAME}"
echo "PV:"
kubectl get pv | grep "${LOKI_DEPLOYMENT_NAME}"