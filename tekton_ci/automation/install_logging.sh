#!/usr/bin/env bash

#################################
#
# This script installs logi and promtail helm charts into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_logging.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_logging.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
LOKI_DEPLOYMENT_NAMESPACE="$(jq -r '.LOKI_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
LOKI_DEPLOYMENT_NAME="$(jq -r '.LOKI_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
LOKI_HELM_CHART_VERSION="$(jq -r '.LOKI_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/static.json)"
LOKI_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.LOKI_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "LOKI:"
echo "LOKI_DEPLOYMENT_NAMESPACE: $LOKI_DEPLOYMENT_NAMESPACE"
echo "LOKI_DEPLOYMENT_NAME: $LOKI_DEPLOYMENT_NAME"
echo "LOKI_HELM_CHART_VERSION: $LOKI_HELM_CHART_VERSION"
echo "LOKI_HELM_DEPLOYMENT_TIMEOUT: $LOKI_HELM_DEPLOYMENT_TIMEOUT"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

echo "#########################"
echo "Installing loki-stack..."

helm upgrade -i --wait --timeout "$LOKI_HELM_DEPLOYMENT_TIMEOUT" "$LOKI_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${LOKI_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/logging/loki_stack_values.encrypted.yaml" \
--version "$LOKI_HELM_CHART_VERSION" \
grafana/loki-stack

kubectl get all -n "${LOKI_DEPLOYMENT_NAMESPACE}"