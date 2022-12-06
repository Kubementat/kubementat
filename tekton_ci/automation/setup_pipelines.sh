#!/usr/bin/env bash

######################################
#
# This script sets up all prepared tekton tasks and pipelines for the given environment (e.g. dev, prod)
# It also sets up an APP_DEPLOYMENT_NAMESPACE for app deployments via helm via the according tekton pipelines
#
######################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: setup_pipelines.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: setup_pipelines.sh dev dev1"
  exit 1
fi

set +u

echo "#########################"
echo "Loading configuration from platform_config ..."
HELM_DEPLOYER_SERVICE_ACCOUNT_NAME="$(jq -r '.HELM_DEPLOYER_SERVICE_ACCOUNT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME: $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "#########################"

echo "Configuring namespace: $PIPELINE_NAMESPACE"
kubectl create namespace $PIPELINE_NAMESPACE || true

echo "#########################"
echo "Configuring namespace: $APP_DEPLOYMENT_NAMESPACE"
kubectl create namespace $APP_DEPLOYMENT_NAMESPACE || true

# This is needed for being able to checkout git repositories via the git-clone-with-ssh-auth task
# IMPORTANT: You need to configure the public key to the according private key
# in the according git repositories for enabling access.
echo "#########################"
echo "Configuring deployer ssh key secret..."
GIT_DEPLOYER_PRIVATE_KEY_BASE64="$(jq -r '.GIT_DEPLOYER_PRIVATE_KEY_BASE64' ../../platform_config/"${ENVIRONMENT}"/static.encrypted.json)"
kubectl apply -n "$PIPELINE_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: git-deployer-ssh-key
  labels:
    managed-by: kubementat
type: kubernetes.io/ssh-auth
data:
  ssh-privatekey: >-
    $GIT_DEPLOYER_PRIVATE_KEY_BASE64
EOF

# This is needed for unlocking the git-crypt encoded platform_config/**/*.encrypted.* files in the platform_config directory
# see tekton_ci/tasks/git-clone-with-ssh-auth.yml for usage commands
# HINT: we are encoding the git deployer private key with base64 again as it is a binary file and this is not working with k8s secrets correctly when decoding for a container
echo "#########################"
echo "Configuring deployer gpg key secret..."
GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64_DOUBLE_ENCODED="$(jq -r '.GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64 | @base64' ../../platform_config/"${ENVIRONMENT}"/static.encrypted.json)"
kubectl apply -n "$PIPELINE_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: git-deployer-gpg-key
  labels:
    managed-by: kubementat
type: Opaque
data:
  private-key: >-
    $GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64_DOUBLE_ENCODED
EOF

echo "#########################"
echo "Configuring $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME service account in namespace $PIPELINE_NAMESPACE ..."
kubectl apply -n "$PIPELINE_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME
  labels:
    managed-by: kubementat
EOF

echo "Configuring helm-deployer-role-binding-app-deployment for $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME and helm-deployer-cluster-role for target namespace $APP_DEPLOYMENT_NAMESPACE ..."
kubectl apply -n "$APP_DEPLOYMENT_NAMESPACE" -f - <<EOF
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: helm-deployer-role-binding-app-deployment
  labels:
    managed-by: kubementat
subjects:
- kind: ServiceAccount
  name: $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME
  namespace: $PIPELINE_NAMESPACE
roleRef:
  kind: ClusterRole
  name: helm-deployer-cluster-role
  apiGroup: rbac.authorization.k8s.io
EOF

echo "Configuring helm-deployer-role-binding-pipeline-namespace-access for $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME and helm-deployer-cluster-role for target namespace $PIPELINE_NAMESPACE ..."
kubectl apply -n "$PIPELINE_NAMESPACE" -f - <<EOF
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: helm-deployer-role-binding-pipeline-namespace-access
  labels:
    managed-by: kubementat
subjects:
- kind: ServiceAccount
  name: $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME
  namespace: $PIPELINE_NAMESPACE
roleRef:
  kind: ClusterRole
  name: helm-deployer-cluster-role
  apiGroup: rbac.authorization.k8s.io
EOF

echo "#########################"
echo "Configuring tasks..."
kubectl apply -n "$PIPELINE_NAMESPACE" -f ../tasks/

echo "#########################"
echo "Configuring pipelines..."
kubectl apply -n "$PIPELINE_NAMESPACE" -f ../pipelines/

echo "########################"
echo "Tasks:"
kubectl get tasks -n "$PIPELINE_NAMESPACE"

echo "########################"
echo "Pipelines:"
kubectl get pipelines -n "$PIPELINE_NAMESPACE"

# list namespaces
echo "########################"
echo "Namespaces:"
kubectl get ns

# list role config
echo "########################"
echo "Role Configuration:"
kubectl -n "$PIPELINE_NAMESPACE" get serviceaccounts
kubectl -n "$APP_DEPLOYMENT_NAMESPACE" describe rolebinding helm-deployer-role-binding-app-deployment

# list secrets
echo "########################"
echo "Secrets:"
kubectl get secrets -n "$PIPELINE_NAMESPACE"