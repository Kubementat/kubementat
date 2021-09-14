#!/usr/bin/env bash

#################################
#
# This script installs the mysql helm chart into the provided environment and team.
# This can then be used as backing service for app deployments
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_mysql_helm_chart.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_mysql_helm_chart.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MYSQL_DEPLOYMENT_NAME="$(jq -r '.MYSQL_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MYSQL_VOLUME_STORAGE_CLASS="$(jq -r '.MYSQL_VOLUME_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MYSQL_VOLUME_SIZE="$(jq -r '.MYSQL_VOLUME_SIZE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MYSQL_DATABASE_NAME="$(jq -r '.MYSQL_DATABASE_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
MYSQL_HOST="$(jq -r '.MYSQL_HOST' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
MYSQL_ROOT_PASSWORD="$(jq -r '.MYSQL_ROOT_PASSWORD' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
MYSQL_HELM_CHART_VERSION="$(jq -r '.MYSQL_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MYSQL_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.MYSQL_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "MYSQL_DEPLOYMENT_NAME: $MYSQL_DEPLOYMENT_NAME"
echo "MYSQL_VOLUME_SIZE: $MYSQL_VOLUME_SIZE"
echo "MYSQL_VOLUME_STORAGE_CLASS: $MYSQL_VOLUME_STORAGE_CLASS"
echo "MYSQL_HOST: $MYSQL_HOST"
echo "MYSQL_DATABASE_NAME: $MYSQL_DATABASE_NAME"
echo "MYSQL_HELM_CHART_VERSION: $MYSQL_HELM_CHART_VERSION"
echo "MYSQL_HELM_DEPLOYMENT_TIMEOUT: $MYSQL_HELM_DEPLOYMENT_TIMEOUT"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

EXPECTED_MYSQL_HOST="${MYSQL_DEPLOYMENT_NAME}.${APP_DEPLOYMENT_NAMESPACE}.svc.cluster.local"
if [[ "$EXPECTED_MYSQL_HOST" != "$MYSQL_HOST" ]]; then
  echo "Expected MYSQL HOST to be configured as: ${EXPECTED_MYSQL_HOST} ; but was:"
  echo "$MYSQL_HOST"
  echo "Please check your platform_config/${ENVIRONMENT}/${TEAM}/*.json files."
  exit 2
fi

echo "#########################"
echo "Installing helm chart ..."
# add the helm repo for mysql
helm repo add bitnami https://charts.bitnami.com/bitnami

# helm will wait this long for the deployment to finish (currently 10 min)
echo "MYSQL_HELM_DEPLOYMENT_TIMEOUT: $MYSQL_HELM_DEPLOYMENT_TIMEOUT"

# install helm chart
helm upgrade -i --wait --timeout "$MYSQL_HELM_DEPLOYMENT_TIMEOUT" "$MYSQL_DEPLOYMENT_NAME" \
--namespace "${APP_DEPLOYMENT_NAMESPACE}" \
--set auth.database="$MYSQL_DATABASE_NAME" \
--set auth.rootPassword="$MYSQL_ROOT_PASSWORD" \
--set metrics.enabled="true" \
--set global.storageClass="$MYSQL_VOLUME_STORAGE_CLASS" \
--set persistence.enabled="true" \
--set persistence.size="$MYSQL_VOLUME_SIZE" \
--version "$MYSQL_HELM_CHART_VERSION" \
bitnami/mysql

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$MYSQL_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$MYSQL_DEPLOYMENT_NAME"
