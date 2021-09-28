#!/usr/bin/env bash

#################################
#
# This script installs the polaris helm chart into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_polaris.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_polaris.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
POLARIS_DEPLOYMENT_NAMESPACE="$(jq -r '.POLARIS_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
POLARIS_DEPLOYMENT_NAME="$(jq -r '.POLARIS_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
POLARIS_HELM_CHART_VERSION="$(jq -r '.POLARIS_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/static.json)"
POLARIS_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.POLARIS_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "POLARIS:"
echo "POLARIS_DEPLOYMENT_NAMESPACE: $POLARIS_DEPLOYMENT_NAMESPACE"
echo "POLARIS_DEPLOYMENT_NAME: $POLARIS_DEPLOYMENT_NAME"
echo "POLARIS_HELM_CHART_VERSION: $POLARIS_HELM_CHART_VERSION"
echo "POLARIS_HELM_DEPLOYMENT_TIMEOUT: $POLARIS_HELM_DEPLOYMENT_TIMEOUT"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add fairwinds-stable https://charts.fairwinds.com/stable
helm repo update

echo "#########################"
echo "Installing polaris dashboard..."

helm upgrade -i --wait --timeout "$POLARIS_HELM_DEPLOYMENT_TIMEOUT" "$POLARIS_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${POLARIS_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/polaris/values.encrypted.yaml" \
--version "$POLARIS_HELM_CHART_VERSION" \
fairwinds-stable/polaris

kubectl get all -n "${POLARIS_DEPLOYMENT_NAMESPACE}"
