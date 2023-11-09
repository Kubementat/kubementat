#!/usr/bin/env bash

#################################
#
# This script deletes a service account (user account) for the teams namespace and the team's app deployment namespace within the given environment
#
#################################

set -e

source ./user_management_helpers.sh

TEKTON_NAMESPACE="tekton-pipelines"
GRAFANA_NAMESPACE="grafana"
POLARIS_NAMESPACE="polaris"

ENVIRONMENT="$1"
TEAM="$2"
SERVICE_ACCOUNT_NAME="$3"

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" || "$SERVICE_ACCOUNT_NAME" == "" ]]; then
  echo "Usage: delete_team_account.sh <ENVIRONMENT> <TEAM> <SERVICE_ACCOUNT_NAME>"
  echo "e.g.: delete_team_account.sh dev dev1 mranderson"
  exit 1
fi

set -u

echo ""
echo "Current Service Accounts in namespace $TEAM"
kubectl get serviceaccount -n "$TEAM"
echo ""
echo ""

check_cluster_and_access

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "#########################"

#### SA ####
for namespace in $APP_DEPLOYMENT_NAMESPACE $PIPELINE_NAMESPACE; do
  delete_role_binding "namespace-admin-$SERVICE_ACCOUNT_NAME-binding" $namespace || true
  delete_role_binding "namespace-readonly-$SERVICE_ACCOUNT_NAME-binding" $namespace || true
done
delete_role_binding "grafana-tunneling-$SERVICE_ACCOUNT_NAME-binding" $GRAFANA_NAMESPACE || true
delete_role_binding "tekton-tunneling-$SERVICE_ACCOUNT_NAME-binding" $TEKTON_NAMESPACE || true
delete_role_binding "polaris-tunneling-$SERVICE_ACCOUNT_NAME-binding" $POLARIS_NAMESPACE || true

delete_service_account $SERVICE_ACCOUNT_NAME $APP_DEPLOYMENT_NAMESPACE || true

echo "Deleted, showing current state:"
echo ""
print_account_info_for_namespace $APP_DEPLOYMENT_NAMESPACE
print_account_info_for_namespace $PIPELINE_NAMESPACE
print_account_info_for_namespace $TEKTON_NAMESPACE
print_account_info_for_namespace $GRAFANA_NAMESPACE
print_account_info_for_namespace $POLARIS_NAMESPACE