#!/usr/bin/env bash

#################################
#
# This script installs the kafka akhq helm chart into the provided environment and team.
# This can then be used as backing service for app deployments
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_kafka_akhq_helm_chart.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_kafka_akhq_helm_chart.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_AKHQ_DEPLOYMENT_NAME="$(jq -r '.KAFKA_AKHQ_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_AKHQ_HELM_CHART_VERSION="$(jq -r '.KAFKA_AKHQ_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_AKHQ_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.KAFKA_AKHQ_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"


echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "KAFKA_AKHQ_DEPLOYMENT_NAME: $KAFKA_AKHQ_DEPLOYMENT_NAME"
echo "KAFKA_AKHQ_HELM_CHART_VERSION: $KAFKA_AKHQ_HELM_CHART_VERSION"
echo "KAFKA_AKHQ_HELM_DEPLOYMENT_TIMEOUT: $KAFKA_AKHQ_HELM_DEPLOYMENT_TIMEOUT"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Installing helm chart ..."
# add the helm repo for mysql
helm repo add akhq https://akhq.io/

# helm will wait this long for the deployment to finish (currently 10 min)
echo "KAFKA_AKHQ_HELM_DEPLOYMENT_TIMEOUT: $KAFKA_AKHQ_HELM_DEPLOYMENT_TIMEOUT"

# install helm chart
helm upgrade -i --wait --timeout "$KAFKA_AKHQ_HELM_DEPLOYMENT_TIMEOUT" "$KAFKA_AKHQ_DEPLOYMENT_NAME" \
--namespace "${APP_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/${TEAM}/akhq/values.encrypted.yaml" \
--version "$KAFKA_AKHQ_HELM_CHART_VERSION" \
akhq/akhq

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$KAFKA_AKHQ_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$KAFKA_AKHQ_DEPLOYMENT_NAME"
