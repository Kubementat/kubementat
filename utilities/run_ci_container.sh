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

DOCKER_REGISTRY_BASE_URL="$(jq -r '.DOCKER_REGISTRY_BASE_URL' ../platform_config/"${ENVIRONMENT}"/static.json)"
CI_IMAGE="$DOCKER_REGISTRY_BASE_URL/ubuntu-ci-minimal:latest"
set -u

kubectl run ubuntu-ci -n "$NAMESPACE" --rm -i --tty --image="$CI_IMAGE" --command /bin/bash