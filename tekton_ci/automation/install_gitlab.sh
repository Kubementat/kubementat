#!/usr/bin/env bash

#################################
#
# This script installs the gitlab helm chart into the provided environment.
#
#################################

# ATTENTION: THIS SCRIPT IS STILL A WORK IN PROGRESS WITH THE GOAL OF
# INSTALLING A MINIMAL GITLAB INSTALLATION

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_gitlab.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_gitlab.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
GITLAB_DEPLOYMENT_NAMESPACE="$(jq -r '.GITLAB_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GITLAB_DEPLOYMENT_NAME="$(jq -r '.GITLAB_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GITLAB_HELM_CHART_VERSION="$(jq -r '.GITLAB_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GITLAB_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.GITLAB_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "GITLAB:"
echo "GITLAB_DEPLOYMENT_NAMESPACE: $GITLAB_DEPLOYMENT_NAMESPACE"
echo "GITLAB_DEPLOYMENT_NAME: $GITLAB_DEPLOYMENT_NAME"
echo "GITLAB_HELM_CHART_VERSION: $GITLAB_HELM_CHART_VERSION"
echo "GITLAB_HELM_DEPLOYMENT_TIMEOUT: $GITLAB_HELM_DEPLOYMENT_TIMEOUT"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add gitlab https://charts.gitlab.io/
helm repo update

echo "#########################"
echo "Installing gitlab..."

helm upgrade -i --wait --timeout "$GITLAB_HELM_DEPLOYMENT_TIMEOUT" "$GITLAB_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${GITLAB_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/gitlab/values.encrypted.yaml" \
--version "$GITLAB_HELM_CHART_VERSION" \
gitlab/gitlab

kubectl get all -n "${GITLAB_DEPLOYMENT_NAMESPACE}"
