#!/usr/bin/env bash

######################################
#
# This script removes the kubernetes dashboard component from the cluster in ENVIRONMENT
#
######################################

# currently static configuration
SERVICE_ACCOUNT_NAME="kubernetes-dashboard-read-only-cluster-user"
KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE="kubernetes-dashboard"

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_kubernetes_dashboard.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_kubernetes_dashboard.sh dev"
  exit 1
fi

set -e

echo "Removing helm deployment for kubernetes dashboard ..."
./uninstall_helm_deployment.sh "$1" "KUBERNETES_DASHBOARD"

echo ""
echo "Removing service account and binding for kubernetes dashboard read only access ..."

set +e
kubectl delete clusterrolebinding "${SERVICE_ACCOUNT_NAME}-cluster-read-only-binding"
kubectl -n "$KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE" delete serviceaccount "$SERVICE_ACCOUNT_NAME"

echo "Uninstalled kubernetes dashboard successfully."