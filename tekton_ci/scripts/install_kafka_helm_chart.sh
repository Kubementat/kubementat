#!/usr/bin/env bash

#################################
#
# This script installs the kafka helm chart into the provided environment and team.
# This can then be used as backing service for app deployments
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_kafka_helm_chart.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_kafka_helm_chart.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_DEPLOYMENT_NAME="$(jq -r '.KAFKA_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_VOLUME_STORAGE_CLASS="$(jq -r '.KAFKA_VOLUME_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_VOLUME_SIZE="$(jq -r '.KAFKA_VOLUME_SIZE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_HOST="$(jq -r '.KAFKA_HOST' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
KAFKA_HELM_CHART_VERSION="$(jq -r '.KAFKA_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
KAFKA_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.KAFKA_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "KAFKA_DEPLOYMENT_NAME: $KAFKA_DEPLOYMENT_NAME"
echo "KAFKA_VOLUME_STORAGE_CLASS: $KAFKA_VOLUME_STORAGE_CLASS"
echo "KAFKA_VOLUME_SIZE: $KAFKA_VOLUME_SIZE"
echo "KAFKA_HOST: $KAFKA_HOST"
echo "KAFKA_HELM_CHART_VERSION: $KAFKA_HELM_CHART_VERSION"
echo "KAFKA_HELM_DEPLOYMENT_TIMEOUT: $KAFKA_HELM_DEPLOYMENT_TIMEOUT"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

EXPECTED_HOST="${KAFKA_DEPLOYMENT_NAME}.${APP_DEPLOYMENT_NAMESPACE}.svc.cluster.local"
if [[ "$EXPECTED_HOST" != "$KAFKA_HOST" ]]; then
  echo "Expected Kafka HOST to be configured as: ${EXPECTED_HOST} ; but was:"
  echo "$KAFKA_HOST"
  echo "Please check your platform_config/${ENVIRONMENT}/${TEAM}/*.json files."
  exit 2
fi

echo "#########################"
echo "Installing helm chart ..."
# add the helm repo for mysql
helm repo add bitnami https://charts.bitnami.com/bitnami

# helm will wait this long for the deployment to finish (currently 10 min)
echo "KAFKA_HELM_DEPLOYMENT_TIMEOUT: $KAFKA_HELM_DEPLOYMENT_TIMEOUT"

# install helm chart
helm upgrade -i --wait --timeout "$KAFKA_HELM_DEPLOYMENT_TIMEOUT" "$KAFKA_DEPLOYMENT_NAME" \
--namespace "${APP_DEPLOYMENT_NAMESPACE}" \
--set persistence.storageClass="$KAFKA_VOLUME_STORAGE_CLASS" \
--set persistence.enabled="true" \
--set persistence.size="$KAFKA_VOLUME_SIZE" \
--version "$KAFKA_HELM_CHART_VERSION" \
bitnami/kafka

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$KAFKA_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$KAFKA_DEPLOYMENT_NAME"
