#!/usr/bin/env bash

######################################
#
# This script removes the pipelines and tasks setup completely
# It will keep the namespaces anyways
# configuration is read from the according config in platform_config for the given env
#
######################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: uninstall_pipelines.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: uninstall_pipelines.sh dev dev1"
  exit 1
fi

set -u

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

echo "Deleting deployer ssh key secret..."
kubectl -n $PIPELINE_NAMESPACE delete secret git-deployer-ssh-key || true

echo "#########################"
echo "Deleting deployer gpg key secret..."
kubectl -n $PIPELINE_NAMESPACE delete secret git-deployer-gpg-key || true

echo "#########################"
echo "Deleting helm-deployer-role-binding-app-deployment for $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME and helm-deployer-cluster-role for target namespace $APP_DEPLOYMENT_NAMESPACE ..."
kubectl -n $APP_DEPLOYMENT_NAMESPACE delete rolebinding helm-deployer-role-binding-app-deployment || true

echo "Deleting $HELM_DEPLOYER_SERVICE_ACCOUNT_NAME service account in namespace $PIPELINE_NAMESPACE ..."
kubectl -n $PIPELINE_NAMESPACE delete serviceaccount "$HELM_DEPLOYER_SERVICE_ACCOUNT_NAME" || true

echo "#########################"
echo "Deleting all tekton pipeline-runs and task-runs:"
tkn -n $PIPELINE_NAMESPACE pipelinerun delete --all -f || true
tkn -n $PIPELINE_NAMESPACE taskrun delete --all -f || true

echo "#########################"
echo "Deleting all tekton pipelines:"
tkn -n $PIPELINE_NAMESPACE pipeline delete --all -f || true

echo "#########################"
echo "Deleting all tekton tasks:"
tkn -n $PIPELINE_NAMESPACE task delete --all -f || true

echo "#########################"
echo "Listing all objects in namespace $PIPELINE_NAMESPACE :"
kubectl get all -n "$PIPELINE_NAMESPACE"

echo "#########################"
echo "Listing all objects in namespace $APP_DEPLOYMENT_NAMESPACE :"
kubectl get all -n "$APP_DEPLOYMENT_NAMESPACE"
