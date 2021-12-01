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

###################
###### HELPERS
###################
function create_docker_secret(){
  namespace="$1"
  name="$2"
  auth_url="$3"
  username="$4"
  password="$5"
  email="6"

  echo "Creating docker secret: $name in namespace: $namespace for docker auth url: $auth_url ..."

  kubectl -n "$namespace" create secret docker-registry "$name" \
    --docker-server="$auth_url" \
    --docker-username="$username" \
    --docker-password="$password" \
    --docker-email="$email" \
    --dry-run=client -o yaml | kubectl apply -f -
}


echo "#########################"
echo "Loading configuration from platform_config ..."
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
HELM_DEPLOYER_SERVICE_ACCOUNT_NAME="$(jq -r '.HELM_DEPLOYER_SERVICE_ACCOUNT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

# e.g. https://index.docker.io/v1/ for dockerhub
DOCKER_REGISTRY_AUTH_URL="$(jq -r '.DOCKER_REGISTRY_AUTH_URL' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_EMAIL="$(jq -r '.DOCKER_REGISTRY_EMAIL' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_USERNAME="$(jq -r '.DOCKER_REGISTRY_USERNAME' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_PASSWORD="$(jq -r '.DOCKER_REGISTRY_PASSWORD' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_SECRET_NAME="docker-registry-secret"

ADDITIONAL_DOCKER_REGISTRY_CREDENTIALS="$(jq -r '.ADDITIONAL_DOCKER_REGISTRY_CREDENTIALS' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"

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

# start constructing json array for service account patch calls
image_pull_secrets_array="[{\"name\":\"$DOCKER_REGISTRY_SECRET_NAME\"}"

# create default docker registry secrets in team namespace (e.g. dev1) and pipeline namespace (e.g. dev1-pipelines)
for namespace in $APP_DEPLOYMENT_NAMESPACE $PIPELINE_NAMESPACE; do
  echo "#####################"
  echo "Configuring namespace: $namespace ..."

  create_docker_secret "$namespace" "$DOCKER_REGISTRY_SECRET_NAME" "$DOCKER_REGISTRY_AUTH_URL" "$DOCKER_REGISTRY_USERNAME" "$DOCKER_REGISTRY_PASSWORD" "$DOCKER_REGISTRY_EMAIL"

  echo "Configuring ADDITIONAL_DOCKER_REGISTRY_CREDENTIALS ..."
  for row in $(echo "${ADDITIONAL_DOCKER_REGISTRY_CREDENTIALS}" | jq -r '.[] | @base64'); do
    _jq() {
      echo ${row} | base64 --decode | jq -r ${1}
    }

    name="$(_jq '.NAME')"
    auth_url="$(_jq '.DOCKER_REGISTRY_AUTH_URL')"
    email="$(_jq '.DOCKER_REGISTRY_EMAIL')"
    password="$(_jq '.DOCKER_REGISTRY_PASSWORD')"
    username="$(_jq '.DOCKER_REGISTRY_USERNAME')"
    echo "Configuring docker registry secret: $name ..."

    create_docker_secret "$namespace" "$name" "$auth_url" "$username" "$password" "$email"

    if [[ "$namespace" == "$APP_DEPLOYMENT_NAMESPACE" ]]; then
      image_pull_secrets_array="${image_pull_secrets_array},{\"name\":\"$name\"}"
    fi

    echo ""
  done

  echo "#####################"
  echo ""
done
echo ""

full_patch_json="{\"imagePullSecrets\":${image_pull_secrets_array}]}"
echo "Patching Service Accounts with:"
echo "$full_patch_json"
echo ""
echo "Patching service account $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME in namespace $PIPELINE_NAMESPACE for secrets usage"
kubectl -n "$PIPELINE_NAMESPACE" patch serviceaccount "$HELM_DEPLOYER_SERVICE_ACCOUNT_NAME" -p "$full_patch_json"
echo "Patching default service account in namespace $APP_DEPLOYMENT_NAMESPACE for secrets usage"
kubectl -n "$APP_DEPLOYMENT_NAMESPACE" patch serviceaccount default -p "$full_patch_json"

echo "##############"
echo "Resulting service account configurations:"
kubectl -n "$PIPELINE_NAMESPACE" describe serviceaccount "$HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"
echo "##############"
kubectl -n "$APP_DEPLOYMENT_NAMESPACE" describe serviceaccount default
echo "##############"
echo ""
echo "Finished configuring docker registry access."