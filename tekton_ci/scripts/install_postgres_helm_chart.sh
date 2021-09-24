#!/usr/bin/env bash

#################################
#
# This script installs the postgresql helm chart into the provided environment and team.
# This can then be used as backing service for app deployments
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_postgres_helm_chart.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_postgres_helm_chart.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
POSTGRES_DEPLOYMENT_NAME="$(jq -r '.POSTGRES_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
POSTGRES_VOLUME_STORAGE_CLASS="$(jq -r '.POSTGRES_VOLUME_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
POSTGRES_VOLUME_SIZE="$(jq -r '.POSTGRES_VOLUME_SIZE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
POSTGRES_DATABASE_NAME="$(jq -r '.POSTGRES_DATABASE_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
POSTGRES_HOST="$(jq -r '.POSTGRES_HOST' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
POSTGRES_ROOT_PASSWORD="$(jq -r '.POSTGRES_ROOT_PASSWORD' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
POSTGRES_HELM_CHART_VERSION="$(jq -r '.POSTGRES_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
POSTGRES_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.POSTGRES_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "POSTGRES_DEPLOYMENT_NAME: $POSTGRES_DEPLOYMENT_NAME"
echo "POSTGRES_VOLUME_SIZE: $POSTGRES_VOLUME_SIZE"
echo "POSTGRES_VOLUME_STORAGE_CLASS: $POSTGRES_VOLUME_STORAGE_CLASS"
echo "POSTGRES_HOST: $POSTGRES_HOST"
echo "POSTGRES_DATABASE_NAME: $POSTGRES_DATABASE_NAME"
echo "POSTGRES_HELM_CHART_VERSION: $POSTGRES_HELM_CHART_VERSION"
echo "POSTGRES_HELM_DEPLOYMENT_TIMEOUT: $POSTGRES_HELM_DEPLOYMENT_TIMEOUT"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

EXPECTED_POSTGRES_HOST="${POSTGRES_DEPLOYMENT_NAME}.${APP_DEPLOYMENT_NAMESPACE}.svc.cluster.local"
if [[ "$EXPECTED_POSTGRES_HOST" != "$POSTGRES_HOST" ]]; then
  echo "Expected POSTGRES HOST to be configured as: ${EXPECTED_POSTGRES_HOST} ; but was:"
  echo "$POSTGRES_HOST"
  echo "Please check your platform_config/${ENVIRONMENT}/${TEAM}/*.json files."
  exit 2
fi

echo "#########################"
echo "Installing helm chart ..."
# add the helm repo
helm repo add groundhog2k https://groundhog2k.github.io/helm-charts/

# helm will wait this long for the deployment to finish (currently 10 min)
echo "POSTGRES_HELM_DEPLOYMENT_TIMEOUT: $POSTGRES_HELM_DEPLOYMENT_TIMEOUT"

# install helm chart
helm upgrade -i --wait --timeout "$POSTGRES_HELM_DEPLOYMENT_TIMEOUT" "$POSTGRES_DEPLOYMENT_NAME" \
--namespace "${APP_DEPLOYMENT_NAMESPACE}" \
--set userDatabase="$POSTGRES_DATABASE_NAME" \
--set settings.superuserPassword="$POSTGRES_ROOT_PASSWORD" \
--set storage.className="$POSTGRES_VOLUME_STORAGE_CLASS" \
--set storage.requestedSize="$POSTGRES_VOLUME_SIZE" \
--version "$POSTGRES_HELM_CHART_VERSION" \
groundhog2k/postgres

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$POSTGRES_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$POSTGRES_DEPLOYMENT_NAME"
