#!/usr/bin/env bash

######################################
#
# This script removes the gitbucket components from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_gitbucket.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_gitbucket.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
GITBUCKET_NAMESPACE="$(jq -r '.GITBUCKET_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "GITBUCKET_NAMESPACE: $GITBUCKET_NAMESPACE"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling gitbucket ..."
helm -n "${GITBUCKET_DEPLOYMENT_NAMESPACE}" delete "${GITBUCKET_DEPLOYMENT_NAME}" ||true

kubectl get all -n "${GITBUCKET_DEPLOYMENT_NAMESPACE}"

echo "If you also want to remove collected data ensure to also delete the pv and pvc"
echo "You can do this via: kubectl -n ${GITBUCKET_DEPLOYMENT_NAMESPACE} delete pvc <PVC_NAME>"
echo "PVC:"
kubectl get pvc -n "${GITBUCKET_DEPLOYMENT_NAMESPACE}" | grep "${GITBUCKET_DEPLOYMENT_NAME}"
echo "PV:"
kubectl get pv | grep "${GITBUCKET_DEPLOYMENT_NAME}"