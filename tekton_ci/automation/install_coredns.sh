#!/usr/bin/env bash

#################################
#
# This script installs the gitea helm chart into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_gitea.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_gitea.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
GITEA_DEPLOYMENT_NAMESPACE="$(jq -r '.GITEA_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GITEA_DEPLOYMENT_NAME="$(jq -r '.GITEA_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GITEA_HELM_CHART_VERSION="$(jq -r '.GITEA_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GITEA_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.GITEA_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "GITEA:"
echo "GITEA_DEPLOYMENT_NAMESPACE: $GITEA_DEPLOYMENT_NAMESPACE"
echo "GITEA_DEPLOYMENT_NAME: $GITEA_DEPLOYMENT_NAME"
echo "GITEA_HELM_CHART_VERSION: $GITEA_HELM_CHART_VERSION"
echo "GITEA_HELM_DEPLOYMENT_TIMEOUT: $GITEA_HELM_DEPLOYMENT_TIMEOUT"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add gitea-charts https://dl.gitea.io/charts/
helm repo update

echo "#########################"
echo "Installing gitea..."

helm upgrade -i --wait --timeout "$GITEA_HELM_DEPLOYMENT_TIMEOUT" "$GITEA_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${GITEA_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/gitea/values.encrypted.yaml" \
--version "$GITEA_HELM_CHART_VERSION" \
gitea-charts/gitea

kubectl get all -n "${GITEA_DEPLOYMENT_NAMESPACE}"
