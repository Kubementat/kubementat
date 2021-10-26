#!/usr/bin/env bash

if [[ "$1" == "" || "$2" == "" ]]; then
  echo "Usage: ./view_app_deployment_statuses NAMESPACE DEPLOYMENT_NAME_1 DEPLOYMENT_NAME_2 ...."
  exit 1
fi

NAMESPACE="$1"
shift

echo "NAMESPACE: $NAMESPACE"
echo "Displaying status for deployments: $*"


for dep in "$@"
do
  echo "------- deployment: $dep ------------"
  kubectl describe deployment "$dep" -n "$NAMESPACE"
  echo "-------------------------------------"
done