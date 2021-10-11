#!/usr/bin/env bash

#################################
#
# This script adds an according secret for a docker registry within the provided environment and namespace
# It creates the secrets in the pipeline and app namespaces for the given team
# In addition it assigns the created secret to the service account with name from variable HELM_DEPLOYER_SERVICE_ACCOUNT_NAME
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: configure_docker_registry_access.sh <ENVIRONMENT> <TEAM>"
  echo "e.g.: configure_docker_registry_access.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
HELM_DEPLOYER_SERVICE_ACCOUNT_NAME="$(jq -r '.HELM_DEPLOYER_SERVICE_ACCOUNT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

# e.g. https://index.docker.io/v1/ for dockerhub
DOCKER_REGISTRY_AUTH_URL="$(jq -r '.DOCKER_REGISTRY_AUTH_URL' "../../platform_config/${ENVIRONMENT}/static.json")"
DOCKER_REGISTRY_EMAIL="$(jq -r '.DOCKER_REGISTRY_EMAIL' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_USERNAME="$(jq -r '.DOCKER_REGISTRY_USERNAME' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_PASSWORD="$(jq -r '.DOCKER_REGISTRY_PASSWORD' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_SECRET_NAME="docker-registry-secret"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME: $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"
echo ""
echo "DOCKER_REGISTRY_AUTH_URL: $DOCKER_REGISTRY_AUTH_URL"
echo "DOCKER_REGISTRY_USERNAME: $DOCKER_REGISTRY_USERNAME"
echo "DOCKER_REGISTRY_EMAIL: $DOCKER_REGISTRY_EMAIL"
echo "DOCKER_REGISTRY_SECRET_NAME: $DOCKER_REGISTRY_SECRET_NAME"
echo "#########################"

for namespace in $APP_DEPLOYMENT_NAMESPACE $PIPELINE_NAMESPACE; do
  kubectl -n "$namespace" create secret docker-registry "$DOCKER_REGISTRY_SECRET_NAME" \
    --docker-server="$DOCKER_REGISTRY_AUTH_URL" \
    --docker-username="$DOCKER_REGISTRY_USERNAME" \
    --docker-password="$DOCKER_REGISTRY_PASSWORD" \
    --docker-email="$DOCKER_REGISTRY_EMAIL" \
    --dry-run=client -o yaml | kubectl apply -f -
done

echo "Patching service account $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME in namespace $PIPELINE_NAMESPACE for $DOCKER_REGISTRY_SECRET_NAME usage"
kubectl -n "$PIPELINE_NAMESPACE" patch serviceaccount "$HELM_DEPLOYER_SERVICE_ACCOUNT_NAME" -p "{\"imagePullSecrets\": [{\"name\": \"$DOCKER_REGISTRY_SECRET_NAME\"}]}"
kubectl -n "$PIPELINE_NAMESPACE" describe serviceaccount "$HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"

echo "Patching default service account in namespace $APP_DEPLOYMENT_NAMESPACE for $DOCKER_REGISTRY_SECRET_NAME usage"
kubectl -n "$APP_DEPLOYMENT_NAMESPACE" patch serviceaccount default -p "{\"imagePullSecrets\": [{\"name\": \"$DOCKER_REGISTRY_SECRET_NAME\"}]}"
kubectl -n "$APP_DEPLOYMENT_NAMESPACE" describe serviceaccount default

echo "Finished configuring docker registry access."