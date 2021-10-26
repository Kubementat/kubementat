#!/usr/bin/env bash

#################################
#
# This script installs the gitbucket helm charts into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_gitbucket.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_gitbucket.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
GITBUCKET_DEPLOYMENT_NAMESPACE="$(jq -r '.GITBUCKET_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
GITBUCKET_DEPLOYMENT_NAME="$(jq -r '.GITBUCKET_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
GITBUCKET_HELM_CHART_VERSION="$(jq -r '.GITBUCKET_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
GITBUCKET_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.GITBUCKET_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/gitbucket/values.encrypted.yaml"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "GITBUCKET:"
echo "GITBUCKET_DEPLOYMENT_NAMESPACE: $GITBUCKET_DEPLOYMENT_NAMESPACE"
echo "GITBUCKET_DEPLOYMENT_NAME: $GITBUCKET_DEPLOYMENT_NAME"
echo "GITBUCKET_HELM_CHART_VERSION: $GITBUCKET_HELM_CHART_VERSION"
echo "GITBUCKET_HELM_DEPLOYMENT_TIMEOUT: $GITBUCKET_HELM_DEPLOYMENT_TIMEOUT"
echo "HELM_VALUES_FILE_LOCATION: $HELM_VALUES_FILE_LOCATION"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add int128.github.io https://int128.github.io/helm-charts
helm repo update

echo "#########################"
echo "Installing gitbucket..."

helm upgrade -i --wait --timeout "$GITBUCKET_HELM_DEPLOYMENT_TIMEOUT" "$GITBUCKET_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${GITBUCKET_DEPLOYMENT_NAMESPACE}" \
-f "$HELM_VALUES_FILE_LOCATION" \
--version "$GITBUCKET_HELM_CHART_VERSION" \
int128.github.io/gitbucket

kubectl get all -n "${GITBUCKET_DEPLOYMENT_NAMESPACE}"
