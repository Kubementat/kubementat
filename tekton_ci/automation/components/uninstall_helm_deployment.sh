#!/usr/bin/env bash

######################################
#
# This script removes the given component from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
COMPONENT_NAME="$2"

if [[ "$ENVIRONMENT" == "" || "$COMPONENT_NAME" == "" ]]; then
  echo "Usage: uninstall_helm_deployment.sh <ENVIRONMENT_NAME> <COMPONENT_NAME_UPPERCASE>"
  echo "e.g.: uninstall_helm_deployment.sh dev POLARIS"
  exit 1
fi

set -u

DEPLOYMENT_NAMESPACE_VARIABLE_NAME="$(printf '%s' "${COMPONENT_NAME}_DEPLOYMENT_NAMESPACE")"
echo "DEPLOYMENT_NAMESPACE_VARIABLE_NAME: $DEPLOYMENT_NAMESPACE_VARIABLE_NAME"
DEPLOYMENT_NAME_VARIABLE_NAME="$(printf '%s' "${COMPONENT_NAME}_DEPLOYMENT_NAME")"
echo "DEPLOYMENT_NAME_VARIABLE_NAME: $DEPLOYMENT_NAME_VARIABLE_NAME"

echo "#########################"
echo "Loading configuration from platform_config ..."
DEPLOYMENT_NAMESPACE="$(jq -r ".${DEPLOYMENT_NAMESPACE_VARIABLE_NAME}" ../../../platform_config/"${ENVIRONMENT}"/static.json)"
DEPLOYMENT_NAME="$(jq -r ".${DEPLOYMENT_NAME_VARIABLE_NAME}" ../../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "DEPLOYMENT_NAMESPACE: $DEPLOYMENT_NAMESPACE"
echo "DEPLOYMENT_NAME: $DEPLOYMENT_NAME"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling $COMPONENT_NAME ..."
helm -n "${DEPLOYMENT_NAMESPACE}" delete "${DEPLOYMENT_NAME}" ||true

set +e
kubectl get all -n "${DEPLOYMENT_NAMESPACE}"

echo "If you also want to remove collected data ensure to also delete the pv and pvc"
echo "You can do this via: kubectl -n ${DEPLOYMENT_NAMESPACE} delete pvc <PVC_NAME>"
echo "PVC:"
kubectl get pvc -n "${DEPLOYMENT_NAMESPACE}" | grep "${DEPLOYMENT_NAME}"
echo "PV:"
kubectl get pv | grep "${DEPLOYMENT_NAME}"

echo "Finished uninstalling helm deployment $DEPLOYMENT_NAME"
exit 0