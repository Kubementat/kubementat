#!/usr/bin/env bash

#################################
#
# Opens a tunnel connection to a pod running mysql within the kubernetes cluster to allow
# access from the local machine
#
#################################

NAMESPACE="$1"
POD_NAME="$2"
PORT="3306"
if [[ "$3" != "" ]]; then
  PORT="$3"
fi

if [[ "$NAMESPACE" == "" || "$POD_NAME" == "" ]]; then
  echo "Usage: open_mysql_tunnel.sh <NAMESPACE> <POD_NAME> <optional: PORT>"
  echo "e.g.: open_mysql_tunnel.sh dev1 mysql-0 3306"
  exit 1
fi

set -eu

echo "#########################"
echo "NAMESPACE: $NAMESPACE"
echo "POD_NAME: $POD_NAME"
echo "PORT: $PORT"
echo "#########################"

echo "Open another shell and connect via:"
echo  "mysql -h 127.0.0.1 -P $PORT -u <DATABASE_USERNAME> -p <DATABASE_NAME>"
echo "######"

source open_pod_tunnel.sh "$NAMESPACE" "$POD_NAME" "$PORT" "$PORT" "0.0.0.0"