#!/usr/bin/env bash

#################################
#
# This script installs the mongodb helm chart into the provided environment and team.
# This can then be used as backing service for app deployments
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_mongodb_helm_chart.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_mongodb_helm_chart.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MONGODB_DEPLOYMENT_NAME="$(jq -r '.MONGODB_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MONGODB_VOLUME_STORAGE_CLASS="$(jq -r '.MONGODB_VOLUME_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MONGODB_VOLUME_SIZE="$(jq -r '.MONGODB_VOLUME_SIZE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MONGODB_HOST="$(jq -r '.MONGODB_HOST' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
MONGODB_ARCHITECTURE="$(jq -r '.MONGODB_ARCHITECTURE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MONGODB_ROOT_PASSWORD="$(jq -r '.MONGODB_ROOT_PASSWORD' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
MONGODB_AUTH_ENABLED="$(jq -r '.MONGODB_AUTH_ENABLED' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MONGODB_HELM_CHART_VERSION="$(jq -r '.MONGODB_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MONGODB_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.MONGODB_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

# Constants
MONGODB_REPLICACOUNT="2"
MONGODB_ARBITER_ENABLED="false"
MONGODB_REPLICASETNAME="rs0"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "MONGODB_DEPLOYMENT_NAME: $MONGODB_DEPLOYMENT_NAME"
echo "MONGODB_VOLUME_SIZE: $MONGODB_VOLUME_SIZE"
echo "MONGODB_VOLUME_STORAGE_CLASS: $MONGODB_VOLUME_STORAGE_CLASS"
echo "MONGODB_HOST: $MONGODB_HOST"
echo "MONGODB_ARCHITECTURE: $MONGODB_ARCHITECTURE"
echo "MONGODB_ARBITER_ENABLED: $MONGODB_ARBITER_ENABLED"
echo "MONGODB_REPLICACOUNT: $MONGODB_REPLICACOUNT"
echo "MONGODB_REPLICASETNAME: $MONGODB_REPLICASETNAME"
echo "MONGODB_AUTH_ENABLED: $MONGODB_AUTH_ENABLED"
echo "MONGODB_HELM_CHART_VERSION: $MONGODB_HELM_CHART_VERSION"
echo "MONGODB_HELM_DEPLOYMENT_TIMEOUT: $MONGODB_HELM_DEPLOYMENT_TIMEOUT"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

# EXPECTED_MONGODB_HOST="${MONGODB_DEPLOYMENT_NAME}.${APP_DEPLOYMENT_NAMESPACE}.svc.cluster.local"
# if [[ "$EXPECTED_MONGODB_HOST" != "$MONGODB_HOST" ]]; then
#   echo "Expected MONGODB HOST to be configured as: ${EXPECTED_MONGODB_HOST} ; but was:"
#   echo "$MONGODB_HOST"
#   echo "Please check your platform_config/${ENVIRONMENT}/${TEAM}/*.json files."
#   exit 2
# fi

echo "#########################"
echo "Installing helm chart ..."
# add the helm repo for mongodb
helm repo add bitnami https://charts.bitnami.com/bitnami

# helm will wait this long for the deployment to finish (currently 10 min)
echo "MONGODB_HELM_DEPLOYMENT_TIMEOUT: $MONGODB_HELM_DEPLOYMENT_TIMEOUT"

if [[ "$MONGODB_ARCHITECTURE" == "replicaset" ]]; then
  # install helm chart
  helm upgrade -i --wait --timeout "$MONGODB_HELM_DEPLOYMENT_TIMEOUT" "$MONGODB_DEPLOYMENT_NAME" \
  --namespace "${APP_DEPLOYMENT_NAMESPACE}" \
  --set architecture="$MONGODB_ARCHITECTURE" \
  --set replicaCount="$MONGODB_REPLICACOUNT" \
  --set arbiter.enabled="$MONGODB_ARBITER_ENABLED" \
  --set replicaSetName="$MONGODB_REPLICASETNAME" \
  --set auth.enabled="$MONGODB_AUTH_ENABLED" \
  --set auth.rootPassword="$MONGODB_ROOT_PASSWORD" \
  --set metrics.enabled="true" \
  --set global.storageClass="$MONGODB_VOLUME_STORAGE_CLASS" \
  --set persistence.enabled="true" \
  --set persistence.size="$MONGODB_VOLUME_SIZE" \
  --version "$MONGODB_HELM_CHART_VERSION" \
  bitnami/mongodb
else
  # install helm chart
  helm upgrade -i --wait --timeout "$MONGODB_HELM_DEPLOYMENT_TIMEOUT" "$MONGODB_DEPLOYMENT_NAME" \
  --namespace "${APP_DEPLOYMENT_NAMESPACE}" \
  --set architecture="$MONGODB_ARCHITECTURE" \
  --set auth.enabled="$MONGODB_AUTH_ENABLED" \
  --set auth.rootPassword="$MONGODB_ROOT_PASSWORD" \
  --set metrics.enabled="true" \
  --set global.storageClass="$MONGODB_VOLUME_STORAGE_CLASS" \
  --set persistence.enabled="true" \
  --set persistence.size="$MONGODB_VOLUME_SIZE" \
  --version "$MONGODB_HELM_CHART_VERSION" \
  bitnami/mongodb
fi

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$MONGODB_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$MONGODB_DEPLOYMENT_NAME"
