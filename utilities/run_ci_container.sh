#!/usr/bin/env bash

#################################
#
# Run a container with the CI image within the given ENV and NAMESPACE
#
#################################

set -e

ENVIRONMENT="$1"
NAMESPACE="$2"

if [[ "$ENVIRONMENT" == "" || "$NAMESPACE" == "" ]]; then
  echo "Usage: run_ci_container.sh <ENVIRONMENT> <NAMESPACE>"
  echo "e.g.: run_ci_container.sh dev dev1"
  exit 1
fi

echo "#########################"
echo "Current kubectl context:"
kubectl config current-context
echo "#########################"
echo ""

DOCKER_REGISTRY_BASE_URL="$(jq -r '.DOCKER_REGISTRY_BASE_URL' ../platform_config/"${ENVIRONMENT}"/static.json)"
TEKTON_CI_IMAGE_NAME="$(jq -r '.TEKTON_CI_IMAGE_NAME' ../platform_config/"${ENVIRONMENT}"/static.json)"
TEKTON_CI_IMAGE_TAG="$(jq -r '.TEKTON_CI_IMAGE_TAG' ../platform_config/"${ENVIRONMENT}"/static.json)"

CI_IMAGE="$DOCKER_REGISTRY_BASE_URL/${TEKTON_CI_IMAGE_NAME}:${TEKTON_CI_IMAGE_TAG}"
set -u

kubectl run ubuntu-ci -n "$NAMESPACE" --rm -i --tty --image="$CI_IMAGE" --command /bin/bash