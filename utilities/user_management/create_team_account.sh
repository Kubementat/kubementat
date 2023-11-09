#!/usr/bin/env bash

#################################
#
# This script creates a service account (user account) for the teams namespace and the team's app deployment namespace within the given environment
#
#################################

set -e

source ./user_management_helpers.sh

ENVIRONMENT="$1"
TEAM="$2"
SERVICE_ACCOUNT_NAME="$3"

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" || "$SERVICE_ACCOUNT_NAME" == "" ]]; then
  echo "Usage: create_team_account.sh <ENVIRONMENT> <TEAM> <SERVICE_ACCOUNT_NAME>"
  echo "e.g.: create_team_account.sh dev dev1 mranderson"
  exit 1
fi

set -u

check_cluster_and_access

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"
TEKTON_NAMESPACE="tekton-pipelines"
GRAFANA_NAMESPACE="grafana"
POLARIS_NAMESPACE="polaris"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "GRAFANA_NAMESPACE: $GRAFANA_NAMESPACE"
echo "POLARIS_NAMESPACE: $POLARIS_NAMESPACE"
echo "#########################"

echo "Creating team namespaces if not present already..."
echo "Configuring namespace: $PIPELINE_NAMESPACE"
kubectl create namespace "$PIPELINE_NAMESPACE" || true
echo "Configuring namespace: $APP_DEPLOYMENT_NAMESPACE"
kubectl create namespace "$APP_DEPLOYMENT_NAMESPACE" || true
echo "Finished creating namespaces."
echo "#########################"
echo ""

#### SA ####
create_service_account_in_namespace $SERVICE_ACCOUNT_NAME $APP_DEPLOYMENT_NAMESPACE
SERVICE_ACCOUNT_SECRET_NAME="$SERVICE_ACCOUNT_NAME-sa-secret"
create_service_account_secret_in_namespace $SERVICE_ACCOUNT_SECRET_NAME $SERVICE_ACCOUNT_NAME $APP_DEPLOYMENT_NAMESPACE

#### Roles ####

echo "Configuring roles..."
for namespace in $APP_DEPLOYMENT_NAMESPACE $PIPELINE_NAMESPACE; do
  create_namespace_admin_role $namespace
done

create_tekton_tunneling_role
create_grafana_tunneling_role
create_polaris_tunneling_role

echo "Finished configuring roles."
echo "#########################"
echo ""

### BIND Roles ###
echo "Binding roles to service account $SERVICE_ACCOUNT_NAME ..."
for namespace in $APP_DEPLOYMENT_NAMESPACE $PIPELINE_NAMESPACE; do
  bind_namespace_role_to_service_account namespace-admin "$namespace" "$SERVICE_ACCOUNT_NAME" "$APP_DEPLOYMENT_NAMESPACE"
done

bind_namespace_role_to_service_account tekton-tunneling "$TEKTON_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$APP_DEPLOYMENT_NAMESPACE"
bind_namespace_role_to_service_account grafana-tunneling "$GRAFANA_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$APP_DEPLOYMENT_NAMESPACE"
bind_namespace_role_to_service_account polaris-tunneling "$POLARIS_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$APP_DEPLOYMENT_NAMESPACE"

echo "Finished binding roles to service account $SERVICE_ACCOUNT_NAME ."
echo "#########################"
echo ""

echo "Created, showing current state:"
echo ""
print_account_info_for_namespace $APP_DEPLOYMENT_NAMESPACE
print_account_info_for_namespace $PIPELINE_NAMESPACE
print_account_info_for_namespace $TEKTON_NAMESPACE
print_account_info_for_namespace $GRAFANA_NAMESPACE
print_account_info_for_namespace $POLARIS_NAMESPACE

print_kubeconfig_for_service_account $ENVIRONMENT $TEAM $SERVICE_ACCOUNT_NAME $APP_DEPLOYMENT_NAMESPACE $SERVICE_ACCOUNT_SECRET_NAME