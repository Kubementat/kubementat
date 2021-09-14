#!/usr/bin/env bash

#################################
#
# Tail the logs for a helm deployment
# in the configured namespace for the env
#
#################################

set -e

NAMESPACE="$1"
HELM_DEPLOYMENT_NAME="$2"
CONTAINER_NAME="$3"

if [[ "$NAMESPACE" == "" || "$HELM_DEPLOYMENT_NAME" == "" ]]; then
  echo "Usage: tail_logs_for_helm_deployment.sh <NAMESPACE> <HELM_DEPLOYMENT_NAME> <Optional: CONTAINER_NAME>"
  echo "e.g.: tail_logs_for_helm_deployment.sh dev1 sso-login-service"
  exit 1
fi

set -u
extra_args=""
if [ "$CONTAINER_NAME" != "" ]; then
  extra_args="-c $CONTAINER_NAME"
fi

kubectl logs  -n "$NAMESPACE" -l 'app.kubernetes.io/instance'="$HELM_DEPLOYMENT_NAME" --timestamps=true $extra_args -f