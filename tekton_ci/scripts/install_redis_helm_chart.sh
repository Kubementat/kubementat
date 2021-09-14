#!/usr/bin/env bash

#################################
#
# This script installs the redis helm chart into the provided environment and team.
# This can then be used as backing service for app deployments
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_redis_helm_chart.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_redis_helm_chart.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_DEPLOYMENT_NAME="$(jq -r '.REDIS_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_VOLUME_STORAGE_CLASS="$(jq -r '.REDIS_VOLUME_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_VOLUME_SIZE="$(jq -r '.REDIS_VOLUME_SIZE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_REPLICA_COUNT="$(jq -r '.REDIS_REPLICA_COUNT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_MASTER_HOST="$(jq -r '.REDIS_MASTER_HOST' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
REDIS_PASSWORD="$(jq -r '.REDIS_PASSWORD' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
REDIS_HELM_CHART_VERSION="$(jq -r '.REDIS_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.REDIS_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_AUTH_SENTINEL_ENABLED="$(jq -r '.REDIS_AUTH_SENTINEL_ENABLED' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
REDIS_AUTH_ENABLED="$(jq -r '.REDIS_AUTH_ENABLED' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "REDIS_DEPLOYMENT_NAME: $REDIS_DEPLOYMENT_NAME"
echo "REDIS_VOLUME_SIZE: $REDIS_VOLUME_SIZE"
echo "REDIS_VOLUME_STORAGE_CLASS: $REDIS_VOLUME_STORAGE_CLASS"
echo "REDIS_REPLICA_COUNT: $REDIS_REPLICA_COUNT"
echo "REDIS_MASTER_HOST: $REDIS_MASTER_HOST"
echo "REDIS_HELM_CHART_VERSION: $REDIS_HELM_CHART_VERSION"
echo "REDIS_HELM_DEPLOYMENT_TIMEOUT: $REDIS_HELM_DEPLOYMENT_TIMEOUT"
echo "REDIS_AUTH_SENTINEL_ENABLED: $REDIS_AUTH_SENTINEL_ENABLED"
echo "REDIS_AUTH_ENABLED: $REDIS_AUTH_ENABLED"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

EXPECTED_REDIS_MASTER_HOST="${REDIS_DEPLOYMENT_NAME}-master.${APP_DEPLOYMENT_NAMESPACE}.svc.cluster.local"
if [[ "$EXPECTED_REDIS_MASTER_HOST" != "$REDIS_MASTER_HOST" ]]; then
  echo "Expected REDIS HOST to be configured as: ${EXPECTED_REDIS_MASTER_HOST} ; but was:"
  echo "$REDIS_MASTER_HOST"
  echo "Please check your platform_config/${ENVIRONMENT}/${TEAM}/*.json files."
  exit 2
fi

echo "#########################"
echo "Installing helm chart ..."
# add the helm repo for redis
helm repo add bitnami https://charts.bitnami.com/bitnami

# helm will wait this long for the deployment to finish (currently 10 min)
echo "REDIS_HELM_DEPLOYMENT_TIMEOUT: $REDIS_HELM_DEPLOYMENT_TIMEOUT"

# install helm chart
helm upgrade -i --wait --timeout "$REDIS_HELM_DEPLOYMENT_TIMEOUT" "$REDIS_DEPLOYMENT_NAME" \
--namespace "${APP_DEPLOYMENT_NAMESPACE}" \
--set auth.enabled="$REDIS_AUTH_ENABLED" \
--set auth.sentinel="$REDIS_AUTH_SENTINEL_ENABLED" \
--set auth.password="$REDIS_PASSWORD" \
--set metrics.enabled="true" \
--set global.storageClass="$REDIS_VOLUME_STORAGE_CLASS" \
--set persistence.enabled="true" \
--set persistence.size="$REDIS_VOLUME_SIZE" \
--set replica.replicaCount="$REDIS_REPLICA_COUNT" \
--version "$REDIS_HELM_CHART_VERSION" \
bitnami/redis

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$REDIS_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$REDIS_DEPLOYMENT_NAME"
