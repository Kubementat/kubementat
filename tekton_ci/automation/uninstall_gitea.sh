#!/usr/bin/env bash

######################################
#
# This script removes the gitea components from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_gitea.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_gitea.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
GITEA_DEPLOYMENT_NAMESPACE="$(jq -r '.GITEA_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GITEA_DEPLOYMENT_NAME="$(jq -r '.GITEA_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "GITEA_DEPLOYMENT_NAMESPACE: $GITEA_DEPLOYMENT_NAMESPACE"
echo "GITEA_DEPLOYMENT_NAME: $GITEA_DEPLOYMENT_NAME"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling gitea ..."
helm -n "${GITEA_DEPLOYMENT_NAMESPACE}" delete "${GITEA_DEPLOYMENT_NAME}" ||true

kubectl get all -n "${GITEA_DEPLOYMENT_NAMESPACE}"

echo "If you also want to remove collected data ensure to also delete the pv and pvc"
echo "You can do this via: kubectl -n ${GITEA_DEPLOYMENT_NAMESPACE} delete pvc <PVC_NAME>"
echo "PVC:"
kubectl get pvc -n "${GITEA_DEPLOYMENT_NAMESPACE}" | grep "${GITEA_DEPLOYMENT_NAME}"
echo "PV:"
kubectl get pv | grep "${GITEA_DEPLOYMENT_NAME}"