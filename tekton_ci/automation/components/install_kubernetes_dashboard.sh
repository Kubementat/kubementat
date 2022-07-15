#!/usr/bin/env bash

#################################
#
# This script installs the kubernetes dashboard helm chart into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_kubernetes_dashboard.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_kubernetes_dashboard.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE="$(jq -r '.KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
KUBERNETES_DASHBOARD_DEPLOYMENT_NAME="$(jq -r '.KUBERNETES_DASHBOARD_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
KUBERNETES_DASHBOARD_HELM_CHART_VERSION="$(jq -r '.KUBERNETES_DASHBOARD_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
KUBERNETES_DASHBOARD_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.KUBERNETES_DASHBOARD_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/kubernetes_dashboard/values.encrypted.yaml"

# currently static configuration
SERVICE_ACCOUNT_NAME="kubernetes-dashboard-read-only-cluster-user"
CLUSTER_ROLE_NAME="view"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "KUBERNETES_DASHBOARD:"
echo "KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE: $KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE"
echo "KUBERNETES_DASHBOARD_DEPLOYMENT_NAME: $KUBERNETES_DASHBOARD_DEPLOYMENT_NAME"
echo "KUBERNETES_DASHBOARD_HELM_CHART_VERSION: $KUBERNETES_DASHBOARD_HELM_CHART_VERSION"
echo "KUBERNETES_DASHBOARD_HELM_DEPLOYMENT_TIMEOUT: $KUBERNETES_DASHBOARD_HELM_DEPLOYMENT_TIMEOUT"
echo "HELM_VALUES_FILE_LOCATION: $HELM_VALUES_FILE_LOCATION"
echo "SERVICE_ACCOUNT_NAME: $SERVICE_ACCOUNT_NAME"
echo "CLUSTER_ROLE_NAME: $CLUSTER_ROLE_NAME"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add kubernetes-dashboard https://kubernetes.github.io/dashboard/
helm repo update

echo "#########################"
echo "Installing kubernetes dashboard..."

helm upgrade -i --wait --timeout "$KUBERNETES_DASHBOARD_HELM_DEPLOYMENT_TIMEOUT" "$KUBERNETES_DASHBOARD_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE}" \
-f "$HELM_VALUES_FILE_LOCATION" \
--version "$KUBERNETES_DASHBOARD_HELM_CHART_VERSION" \
kubernetes-dashboard/kubernetes-dashboard

kubectl get all -n "${KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE}"

echo "Finished kubernetes dashboard helm deployment."

echo "#########################"
echo ""

echo "Configuring service account for dashboard access ..."

###
echo "Checking if $CLUSTER_ROLE_NAME clusterrole is available ..."
kubectl describe clusterrole $CLUSTER_ROLE_NAME
echo ""

###
echo ""
echo "Creating service account $SERVICE_ACCOUNT_NAME in namespace $KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE"
echo ""
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $SERVICE_ACCOUNT_NAME
  namespace: $KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE
EOF

kubectl -n "$KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE" get serviceaccount "$SERVICE_ACCOUNT_NAME"

###
echo ""
echo "Binding $CLUSTER_ROLE_NAME cluster role to $SERVICE_ACCOUNT_NAME ..."
echo ""

cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: $SERVICE_ACCOUNT_NAME-cluster-read-only-binding
subjects:
  - kind: ServiceAccount
    name: $SERVICE_ACCOUNT_NAME
    namespace: $KUBERNETES_DASHBOARD_DEPLOYMENT_NAMESPACE
    apiGroup: ""
roleRef:
  kind: ClusterRole
  name: $CLUSTER_ROLE_NAME
  apiGroup: rbac.authorization.k8s.io
EOF

echo ""
echo "#########################"
echo ""

echo "Kubernetes dashboard installation finished. You can access the dashboard via the url provided in the output of the helm deployment."
echo "For retrieving the login token you can use the utility script in utilities/secret_management/retrieve_token_for_service_account.sh , e.g. like this:"
echo "./retrieve_token_for_service_account.sh kubernetes-dashboard kubernetes-dashboard-read-only-cluster-user"
