#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to the grafana dashboard for the given environment
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: open_grafana_tunnel.sh <ENVIRONMENT_NAME>"
  echo "e.g.: open_grafana_tunnel.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
GRAFANA_DEPLOYMENT_NAMESPACE="$(jq -r '.GRAFANA_DEPLOYMENT_NAMESPACE' ../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "GRAFANA_DEPLOYMENT_NAMESPACE: $GRAFANA_DEPLOYMENT_NAMESPACE"
echo "#########################"

LOCAL_PORT="3000"
pod_name="$(kubectl -n "$GRAFANA_DEPLOYMENT_NAMESPACE" get pod -l "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana" -o json | jq -r '.items[0].metadata.name')"

echo "See GRAFANA_ADMIN_USER and GRAFANA_ADMIN_PASSWORD environment variables within ../platform_config/${ENVIRONMENT}/static.encrypted.json"
echo "Visit: http://localhost:3000"
echo "###########"

source open_pod_tunnel.sh "$GRAFANA_DEPLOYMENT_NAMESPACE" "$pod_name" "$LOCAL_PORT" "3000"