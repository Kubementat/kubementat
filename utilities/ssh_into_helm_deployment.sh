#!/usr/bin/env bash

#################################
#
# ssh into container for a helm deployment
# in the configured namespace for the env
#
#################################

set -e

NAMESPACE="$1"
HELM_DEPLOYMENT_NAME="$2"
CONTAINER_NAME="$3"

if [[ "$NAMESPACE" == "" || "$HELM_DEPLOYMENT_NAME" == "" ]]; then
  echo "Usage: ssh_into_helm_deployment.sh <NAMESPACE> <HELM_DEPLOYMENT_NAME> <Optional: CONTAINER_NAME>"
  echo "e.g.: ssh_into_helm_deployment.sh dev1 sso-login-service"
  exit 1
fi

set -u

POD_NAME="$(kubectl -n dev1 get pods -l 'app.kubernetes.io/instance'="$HELM_DEPLOYMENT_NAME" -o=jsonpath='{.items[0].metadata.name}')"
extra_args=""
if [ "$CONTAINER_NAME" != "" ]; then
  extra_args="-c $CONTAINER_NAME"
fi
echo "POD_NAME: $POD_NAME"
echo "CONTAINER_NAME: $CONTAINER_NAME"

kubectl exec -n "$NAMESPACE" --stdin --tty "$POD_NAME" $extra_args -- /bin/bash