#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to the kafka akhq dashboard for the given environment
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: open_kafka_akhq_tunnel.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: open_kafka_akhq_tunnel.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' "../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "#########################"

POD_PORT="8080"
LOCAL_PORT="8080"
pod_name=$(kubectl get pods --namespace "$APP_DEPLOYMENT_NAMESPACE" -l "app.kubernetes.io/name=akhq,app.kubernetes.io/instance=akhq" -o jsonpath="{.items[0].metadata.name}")
echo "Pod Name: $pod_name"
echo "Connect via http://localhost:${LOCAL_PORT}/ui"
echo "######"

source open_pod_tunnel.sh "$APP_DEPLOYMENT_NAMESPACE" "$pod_name" "$LOCAL_PORT" "$POD_PORT"