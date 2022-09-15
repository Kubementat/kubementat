#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to a pod within the kubernetes cluster to allow
# access from the local machine
#
#################################

set -e

NAMESPACE="$1"
POD_NAME="$2"
LOCAL_PORT="$3"
REMOTE_PORT="$4"
ADDRESS="0.0.0.0"
if [[ "$5" != "" ]]; then
  ADDRESS="$5"
fi

if [[ "$NAMESPACE" == "" || "$POD_NAME" == "" || "$LOCAL_PORT" == "" || "$REMOTE_PORT" == "" ]]; then
  echo "Usage: open_pod_tunnel.sh <NAMESPACE> <POD_NAME> <LOCAL_PORT> <REMOTE_PORT> <optional: ADDRESS (default 0.0.0.0)>"
  echo "e.g.: open_pod_tunnel.sh dev1 mysql-0 3306 3306"
  exit 1
fi

set -u

echo "Opening tunnel connection in namespace: $NAMESPACE to pod: $POD_NAME with local port: $LOCAL_PORT to remote port: $REMOTE_PORT on address: $ADDRESS ..."
kubectl -n "$NAMESPACE" port-forward --address "$ADDRESS" "pod/${POD_NAME}" "$LOCAL_PORT:$REMOTE_PORT"