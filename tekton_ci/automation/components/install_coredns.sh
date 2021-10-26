#!/usr/bin/env bash

#################################
#
# This script installs the coredns helm chart into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_coredns.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_coredns.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
COREDNS_DEPLOYMENT_NAMESPACE="$(jq -r '.COREDNS_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
COREDNS_DEPLOYMENT_NAME="$(jq -r '.COREDNS_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
COREDNS_HELM_CHART_VERSION="$(jq -r '.COREDNS_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
COREDNS_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.COREDNS_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/coredns/values.encrypted.yaml"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "COREDNS:"
echo "COREDNS_DEPLOYMENT_NAMESPACE: $COREDNS_DEPLOYMENT_NAMESPACE"
echo "COREDNS_DEPLOYMENT_NAME: $COREDNS_DEPLOYMENT_NAME"
echo "COREDNS_HELM_CHART_VERSION: $COREDNS_HELM_CHART_VERSION"
echo "COREDNS_HELM_DEPLOYMENT_TIMEOUT: $COREDNS_HELM_DEPLOYMENT_TIMEOUT"
echo "HELM_VALUES_FILE_LOCATION: $HELM_VALUES_FILE_LOCATION"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add coredns https://coredns.github.io/helm
helm repo update

echo "#########################"
echo "Installing coredns ..."

helm upgrade -i --wait --timeout "$COREDNS_HELM_DEPLOYMENT_TIMEOUT" "$COREDNS_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${COREDNS_DEPLOYMENT_NAMESPACE}" \
-f "$HELM_VALUES_FILE_LOCATION" \
--version "$COREDNS_HELM_CHART_VERSION" \
coredns/coredns

kubectl get all -n "${COREDNS_DEPLOYMENT_NAMESPACE}"
