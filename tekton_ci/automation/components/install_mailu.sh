#!/usr/bin/env bash

#################################
#
# This script installs the mailu helm chart into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_mailu.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_mailu.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
MAILU_DEPLOYMENT_NAMESPACE="$(jq -r '.MAILU_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
MAILU_DEPLOYMENT_NAME="$(jq -r '.MAILU_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
MAILU_HELM_CHART_VERSION="$(jq -r '.MAILU_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
MAILU_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.MAILU_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/mailu/values.encrypted.yaml"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "MAILU:"
echo "MAILU_DEPLOYMENT_NAMESPACE: $MAILU_DEPLOYMENT_NAMESPACE"
echo "MAILU_DEPLOYMENT_NAME: $MAILU_DEPLOYMENT_NAME"
echo "MAILU_HELM_CHART_VERSION: $MAILU_HELM_CHART_VERSION"
echo "MAILU_HELM_DEPLOYMENT_TIMEOUT: $MAILU_HELM_DEPLOYMENT_TIMEOUT"
echo "HELM_VALUES_FILE_LOCATION: $HELM_VALUES_FILE_LOCATION"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add mailu https://mailu.github.io/helm-charts/
helm repo update

echo "#########################"
echo "Installing mailu..."

helm upgrade -i --wait --timeout "$MAILU_HELM_DEPLOYMENT_TIMEOUT" "$MAILU_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${MAILU_DEPLOYMENT_NAMESPACE}" \
-f "$HELM_VALUES_FILE_LOCATION" \
--version "$MAILU_HELM_CHART_VERSION" \
mailu/mailu

kubectl get all -n "${MAILU_DEPLOYMENT_NAMESPACE}"
