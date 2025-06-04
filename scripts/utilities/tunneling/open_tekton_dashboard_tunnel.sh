#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to the tekton dashboard for the currently selected kubectl context
#
################################
set -eu

echo "#########################"
echo "Loading configuration from platform_config ..."
TEKTON_NAMESPACE="tekton-pipelines"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "#########################"

LOCAL_PORT="9097"
pod_name="$(kubectl -n "$TEKTON_NAMESPACE" get pod -l app=tekton-dashboard -o json | jq -r '.items[0].metadata.name')"
echo "Pod Name: $pod_name"
echo "Visit: http://127.0.0.1:${LOCAL_PORT}"

source open_pod_tunnel.sh "$TEKTON_NAMESPACE" "$pod_name" "$LOCAL_PORT" "9097" "0.0.0.0"