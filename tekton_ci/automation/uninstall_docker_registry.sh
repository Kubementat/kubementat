#!/usr/bin/env bash

######################################
#
# This script removes the docker registry components from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_docker_registry.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_docker_registry.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE="$(jq -r '.DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
DOCKER_REGISTRY_DEPLOYMENT_NAME="$(jq -r '.DOCKER_REGISTRY_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE: $DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE"
echo "DOCKER_REGISTRY_DEPLOYMENT_NAME: $DOCKER_REGISTRY_DEPLOYMENT_NAME"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling registry ..."
helm -n "${DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE}" delete "${DOCKER_REGISTRY_DEPLOYMENT_NAME}" ||true

kubectl get all -n "${DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE}"

echo "If you also want to remove collected data ensure to also delete the pv and pvc"
echo "You can do this via: kubectl -n ${DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE} delete pvc <PVC_NAME>"
echo "PVC:"
kubectl get pvc -n "${DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE}" | grep "${DOCKER_REGISTRY_DEPLOYMENT_NAME}"
echo "PV:"
kubectl get pv | grep "${DOCKER_REGISTRY_DEPLOYMENT_NAME}"