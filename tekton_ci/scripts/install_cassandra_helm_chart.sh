#!/usr/bin/env bash

#################################
#
# This script installs the cassandra helm chart into the provided environment and team.
# This can then be used as backing service for app deployments
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_cassandra_helm_chart.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_cassandra_helm_chart.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
CASSANDRA_DEPLOYMENT_NAME="$(jq -r '.CASSANDRA_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
CASSANDRA_VOLUME_STORAGE_CLASS="$(jq -r '.CASSANDRA_VOLUME_STORAGE_CLASS' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
CASSANDRA_VOLUME_SIZE="$(jq -r '.CASSANDRA_VOLUME_SIZE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
CASSANDRA_AUTH_ENABLED="$(jq -r '.CASSANDRA_AUTH_ENABLED' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
CASSANDRA_ADMIN_USER="$(jq -r '.CASSANDRA_ADMIN_USER' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
CASSANDRA_ADMIN_PASSWORD="$(jq -r '.CASSANDRA_ADMIN_PASSWORD' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
CASSANDRA_HOST="$(jq -r '.CASSANDRA_HOST' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
CASSANDRA_REPLICACOUNT="$(jq -r '.CASSANDRA_REPLICACOUNT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
CASSANDRA_HELM_CHART_VERSION="$(jq -r '.CASSANDRA_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
CASSANDRA_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.CASSANDRA_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "CASSANDRA_DEPLOYMENT_NAME: $CASSANDRA_DEPLOYMENT_NAME"
echo "CASSANDRA_VOLUME_STORAGE_CLASS: $CASSANDRA_VOLUME_STORAGE_CLASS"
echo "CASSANDRA_VOLUME_SIZE: $CASSANDRA_VOLUME_SIZE"
echo "CASSANDRA_AUTH_ENABLED: $CASSANDRA_AUTH_ENABLED"
echo "CASSANDRA_ADMIN_USER: $CASSANDRA_ADMIN_USER"
echo "CASSANDRA_HOST: $CASSANDRA_HOST"
echo "CASSANDRA_REPLICACOUNT: $CASSANDRA_REPLICACOUNT"
echo "CASSANDRA_HELM_CHART_VERSION: $CASSANDRA_HELM_CHART_VERSION"
echo "CASSANDRA_HELM_DEPLOYMENT_TIMEOUT: $CASSANDRA_HELM_DEPLOYMENT_TIMEOUT"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

EXPECTED_HOST="${CASSANDRA_DEPLOYMENT_NAME}.${APP_DEPLOYMENT_NAMESPACE}.svc.cluster.local"
if [[ "$EXPECTED_HOST" != "$CASSANDRA_HOST" ]]; then
  echo "Expected Cassandra HOST to be configured as: ${EXPECTED_HOST} ; but was:"
  echo "$CASSANDRA_HOST"
  echo "Please check your platform_config/${ENVIRONMENT}/${TEAM}/*.json files."
  exit 2
fi

echo "#########################"
echo "Installing helm chart ..."
# add the helm repo for mysql
helm repo add bitnami https://charts.bitnami.com/bitnami

# helm will wait this long for the deployment to finish (currently 10 min)
echo "CASSANDRA_HELM_DEPLOYMENT_TIMEOUT: $CASSANDRA_HELM_DEPLOYMENT_TIMEOUT"

# install helm chart
if [[ "$CASSANDRA_AUTH_ENABLED" == "false" ]]; then
  helm upgrade -i --wait --timeout "$CASSANDRA_HELM_DEPLOYMENT_TIMEOUT" "$CASSANDRA_DEPLOYMENT_NAME" \
  --namespace "${APP_DEPLOYMENT_NAMESPACE}" \
  --set dbUser.user="$CASSANDRA_ADMIN_USER" \
  --set dbUser.password="$CASSANDRA_ADMIN_PASSWORD" \
  --set "extraEnvVars[0].name=CASSANDRA_AUTHENTICATOR" \
  --set "extraEnvVars[0].value=AllowAllAuthenticator" \
  --set "extraEnvVars[1].name=CASSANDRA_AUTHORIZER" \
  --set "extraEnvVars[1].value=AllowAllAuthorizer" \
  --set persistence.storageClass="$CASSANDRA_VOLUME_STORAGE_CLASS" \
  --set persistence.enabled="true" \
  --set persistence.size="$CASSANDRA_VOLUME_SIZE" \
  --set replicaCount="$CASSANDRA_REPLICACOUNT" \
  --version "$CASSANDRA_HELM_CHART_VERSION" \
  bitnami/cassandra
else
  helm upgrade -i --wait --timeout "$CASSANDRA_HELM_DEPLOYMENT_TIMEOUT" "$CASSANDRA_DEPLOYMENT_NAME" \
  --namespace "${APP_DEPLOYMENT_NAMESPACE}" \
  --set dbUser.user="$CASSANDRA_ADMIN_USER" \
  --set dbUser.password="$CASSANDRA_ADMIN_PASSWORD" \
  --set persistence.storageClass="$CASSANDRA_VOLUME_STORAGE_CLASS" \
  --set persistence.enabled="true" \
  --set persistence.size="$CASSANDRA_VOLUME_SIZE" \
  --set replicaCount="$CASSANDRA_REPLICACOUNT" \
  --version "$CASSANDRA_HELM_CHART_VERSION" \
  bitnami/cassandra
fi

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$CASSANDRA_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$CASSANDRA_DEPLOYMENT_NAME"
