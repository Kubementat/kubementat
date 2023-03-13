#!/usr/bin/env bash

#################################
#
# Helper script for logging in to kubernetes dashboard as cluster read only user
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: login_kubernetes_dashboard.sh <ENVIRONMENT_NAME>"
  echo "e.g.: login_kubernetes_dashboard.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE="$(jq -r '.KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE' ../platform_config/"${ENVIRONMENT}"/static.json)"
KUBERNETES_DASHBOARD_DEPLOYMENT_NAME="$(jq -r '.KUBERNETES_DASHBOARD_DEPLOYMENT_NAME' ../platform_config/"${ENVIRONMENT}"/static.json)"
echo "ENVIRONMENT: $ENVIRONMENT"
echo "KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE: $KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE"
echo "KUBERNETES_DASHBOARD_DEPLOYMENT_NAME: $KUBERNETES_DASHBOARD_DEPLOYMENT_NAME"
echo "#########################"
echo ""
echo "Current kubectl context:"
kubectl config current-context
echo "#########################"
echo ""

echo "Helm information:"
helm -n "$KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE" status "$KUBERNETES_DASHBOARD_DEPLOYMENT_NAME"


echo "Use the url provided below for login."
echo "The required token for login will be displayed below."
pushd secret_management

./retrieve_token_for_service_account.sh "$KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE" kubernetes-dashboard-read-only-cluster-user

popd > /dev/null