#!/usr/bin/env bash

#################################
#
# This script installs vault helm chart into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_vault.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_vault.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
VAULT_DEPLOYMENT_NAMESPACE="$(jq -r '.VAULT_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
VAULT_DEPLOYMENT_NAME="$(jq -r '.VAULT_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
VAULT_HELM_CHART_VERSION="$(jq -r '.VAULT_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
VAULT_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.VAULT_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/vault/vault_values.encrypted.yaml"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "VAULT:"
echo "VAULT_DEPLOYMENT_NAMESPACE: $VAULT_DEPLOYMENT_NAMESPACE"
echo "VAULT_DEPLOYMENT_NAME: $VAULT_DEPLOYMENT_NAME"
echo "VAULT_HELM_CHART_VERSION: $VAULT_HELM_CHART_VERSION"
echo "VAULT_HELM_DEPLOYMENT_TIMEOUT: $VAULT_HELM_DEPLOYMENT_TIMEOUT"
echo "HELM_VALUES_FILE_LOCATION: $HELM_VALUES_FILE_LOCATION"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo update

echo "#########################"
echo "Installing vault ..."

helm upgrade -i --wait --timeout "$VAULT_HELM_DEPLOYMENT_TIMEOUT" "$VAULT_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${VAULT_DEPLOYMENT_NAMESPACE}" \
-f "$HELM_VALUES_FILE_LOCATION" \
--version "$VAULT_HELM_CHART_VERSION" \
hashicorp/vault

kubectl get all -n "${VAULT_DEPLOYMENT_NAMESPACE}"