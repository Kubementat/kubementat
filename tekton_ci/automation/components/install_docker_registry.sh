#!/usr/bin/env bash

#################################
#
# This script installs the docker registry helm charts into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_docker_registry.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_docker_registry.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE="$(jq -r '.DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
DOCKER_REGISTRY_DEPLOYMENT_NAME="$(jq -r '.DOCKER_REGISTRY_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
DOCKER_REGISTRY_HELM_CHART_VERSION="$(jq -r '.DOCKER_REGISTRY_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
DOCKER_REGISTRY_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.DOCKER_REGISTRY_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/docker_registry/values.encrypted.yaml"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "DOCKER_REGISTRY:"
echo "DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE: $DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE"
echo "DOCKER_REGISTRY_DEPLOYMENT_NAME: $DOCKER_REGISTRY_DEPLOYMENT_NAME"
echo "DOCKER_REGISTRY_HELM_CHART_VERSION: $DOCKER_REGISTRY_HELM_CHART_VERSION"
echo "DOCKER_REGISTRY_HELM_DEPLOYMENT_TIMEOUT: $DOCKER_REGISTRY_HELM_DEPLOYMENT_TIMEOUT"
echo "HELM_VALUES_FILE_LOCATION: $HELM_VALUES_FILE_LOCATION"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add twuni https://helm.twun.io
helm repo update

echo "#########################"
echo "Installing docker registry..."

helm upgrade -i --wait --timeout "$DOCKER_REGISTRY_HELM_DEPLOYMENT_TIMEOUT" "$DOCKER_REGISTRY_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE}" \
-f "$HELM_VALUES_FILE_LOCATION" \
--version "$DOCKER_REGISTRY_HELM_CHART_VERSION" \
twuni/docker-registry

kubectl get all -n "${DOCKER_REGISTRY_DEPLOYMENT_NAMESPACE}"