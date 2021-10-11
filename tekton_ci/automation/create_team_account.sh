#!/usr/bin/env bash

#################################
#
# This script creates a service account (user account) for the teams namespace and the team's app deployment namespace within the given environment
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
SERVICE_ACCOUNT_NAME="$3"

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" || "$SERVICE_ACCOUNT_NAME" == "" ]]; then
  echo "Usage: create_team_account.sh <ENVIRONMENT> <TEAM> <SERVICE_ACCOUNT_NAME>"
  echo "e.g.: create_team_account.sh dev dev1 mr-anderson"
  exit 1
fi

set -u

function check_cluster_and_access(){
  echo "Checking cluster access"
  echo "You are going to create a service account on the following cluster:"
  kubectl cluster-info

  while true; do
    read -p "Do you really wish to create the service account on this cluster?" yn
    case $yn in
        [Yy]* ) break;;
        [Nn]* ) echo "Cancelled script."; exit;;
        * ) echo "Please answer yes or no.";;
    esac
  done

  kubectl auth can-i create namespace

  kubectl auth can-i create serviceaccount
  kubectl auth can-i update serviceaccount
  kubectl auth can-i patch serviceaccount

  kubectl auth can-i create role
  kubectl auth can-i update role
  kubectl auth can-i patch role

  kubectl auth can-i create rolebinding
  kubectl auth can-i update rolebinding
  kubectl auth can-i patch rolebinding

  echo "Finished checking cluster access"
  echo "################"
  echo ""
}

check_cluster_and_access

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.json")"
TEKTON_NAMESPACE="$(jq -r '.TEKTON_NAMESPACE' "../../platform_config/${ENVIRONMENT}/static.json")"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "#########################"

echo "Creating team namespaces if not present already..."
echo "Configuring namespace: $PIPELINE_NAMESPACE"
kubectl create namespace "$PIPELINE_NAMESPACE" || true
echo "Configuring namespace: $APP_DEPLOYMENT_NAMESPACE"
kubectl create namespace "$APP_DEPLOYMENT_NAMESPACE" || true
echo "Finished creating namespaces."
echo "#########################"
echo ""

echo "Configuring roles..."
# Creating namespace-admin role in app and pipeline namespaces
ROLE_NAME="namespace-admin"
for namespace in $APP_DEPLOYMENT_NAMESPACE $PIPELINE_NAMESPACE; do
  kubectl -n "$namespace" apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: $ROLE_NAME
rules:
- apiGroups: ["", "rbac.authorization.k8s.io", "networking.k8s.io", "batch", "extensions", "apps", "autoscaling", "tekton.dev"]
  resources: ["*"]
  verbs: ["*"]
EOF
done

ROLE_NAME_TEKTON_TUNNELING="tekton-tunneling"
kubectl -n "$TEKTON_NAMESPACE" apply  -f - <<EOF
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: $ROLE_NAME_TEKTON_TUNNELING
rules:
- apiGroups: [""]
  resources: ["pods", "pods/portforward"]
  verbs: ["get", "list", "create"]
EOF

echo "Finished configuring roles."
echo "#########################"
echo ""

echo "Configuring service account..."
kubectl -n "$APP_DEPLOYMENT_NAMESPACE" apply  -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $SERVICE_ACCOUNT_NAME
EOF
echo "Finished configuring service account."
echo "#########################"
echo ""

echo "Binding roles to service account $SERVICE_ACCOUNT_NAME ..."
for namespace in $APP_DEPLOYMENT_NAMESPACE $PIPELINE_NAMESPACE; do
kubectl apply -n "$namespace" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${ROLE_NAME}-${SERVICE_ACCOUNT_NAME}-binding
subjects:
- kind: ServiceAccount
  name: $SERVICE_ACCOUNT_NAME
  namespace: $APP_DEPLOYMENT_NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: $ROLE_NAME
EOF
done

kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${ROLE_NAME_TEKTON_TUNNELING}-${SERVICE_ACCOUNT_NAME}-binding
subjects:
- kind: ServiceAccount
  name: $SERVICE_ACCOUNT_NAME
  namespace: $APP_DEPLOYMENT_NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: $ROLE_NAME_TEKTON_TUNNELING
EOF

echo "Finished binding roles to service account $SERVICE_ACCOUNT_NAME ."
echo "#########################"
echo ""

secret_name="$(kubectl -n "$APP_DEPLOYMENT_NAMESPACE" get serviceaccount "$SERVICE_ACCOUNT_NAME"  -o=jsonpath='{.secrets[0].name}')"
ca_crt_data="$(kubectl -n "$APP_DEPLOYMENT_NAMESPACE" get secret "$secret_name" -o=jsonpath='{.data.ca\.crt}')"
token="$(kubectl -n "$APP_DEPLOYMENT_NAMESPACE" get secret "$secret_name" -o=jsonpath='{.data.token}' | base64 -d)"

echo "TOKEN:"
echo  "$token"
echo "CA.crt:"
echo  "$ca_crt_data"


# retrieve local kubeconfig settings
certificate_authority_data="$(kubectl config view --flatten --minify | yq eval -j | jq -r '.clusters[0].cluster."certificate-authority-data"')"
server="$(kubectl config view --flatten --minify | yq eval -j | jq -r '.clusters[0].cluster.server')"
echo "certificate_authority_data:"
echo  "$certificate_authority_data"
echo "server:"
echo  "$server"


echo ""
echo "###########################"
echo "Please put the settings below in your ~/.kube/config file:"
echo "###########################"
echo ""

CLUSTER_NAME="${ENVIRONMENT}-cluster"

cat <<EOF
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: $certificate_authority_data
    server: $server
  name: ${ENVIRONMENT}-cluster
contexts:
- context:
    cluster: $CLUSTER_NAME
    namespace: $APP_DEPLOYMENT_NAMESPACE
    user: $SERVICE_ACCOUNT_NAME
  name: ${ENVIRONMENT}-cluster-${TEAM}
- context:
    cluster: $CLUSTER_NAME
    namespace: $APP_DEPLOYMENT_NAMESPACE
    user: ${SERVICE_ACCOUNT_NAME}
  name: ${TEAM}-admin
current-context: ${ENVIRONMENT}-cluster-${TEAM}
kind: Config
preferences: {}
users:
- name: $SERVICE_ACCOUNT_NAME
  user:
    token: $token
EOF