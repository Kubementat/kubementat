#!/usr/bin/env bash

######################################
#
# This script removes the linkerd components from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_linkerd.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_linkerd.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
LINKERD_NAMESPACE="linkerd"
LINKERD_VIZ_NAMESPACE="linkerd-viz"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "LINKERD_NAMESPACE: $LINKERD_NAMESPACE"
echo "LINKERD_VIZ_NAMESPACE: $LINKERD_VIZ_NAMESPACE"
echo ""
echo "#########################"

linkerd viz uninstall | kubectl delete -f -
linkerd uninstall | kubectl delete -f -

kubectl delete namespace "$LINKERD_VIZ_NAMESPACE" || true
kubectl delete namespace "$LINKERD_NAMESPACE" || true

echo "Uninstalled linkerd successfully"