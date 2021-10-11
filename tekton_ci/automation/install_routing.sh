#!/usr/bin/env bash

#################################
#
# This script installs the routing helm charts into the provided environment.
# - nginx-ingress-controller
# - cert-manager
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_routing.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_routing.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAMESPACE="$(jq -r '.NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAME="$(jq -r '.NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
NGINX_INGRESS_CONTROLLER_HELM_CHART_VERSION="$(jq -r '.NGINX_INGRESS_CONTROLLER_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/static.json)"
NGINX_INGRESS_CONTROLLER_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.NGINX_INGRESS_CONTROLLER_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "NGINX_INGRESS_CONTROLLER:"
echo "NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAMESPACE: $NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAMESPACE"
echo "NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAME: $NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAME"
echo "NGINX_INGRESS_CONTROLLER_HELM_CHART_VERSION: $NGINX_INGRESS_CONTROLLER_HELM_CHART_VERSION"
echo "NGINX_INGRESS_CONTROLLER_HELM_DEPLOYMENT_TIMEOUT: $NGINX_INGRESS_CONTROLLER_HELM_DEPLOYMENT_TIMEOUT"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

echo "#########################"
echo "Installing nginx ingress controller..."

helm upgrade -i --wait --timeout "$NGINX_INGRESS_CONTROLLER_HELM_DEPLOYMENT_TIMEOUT" "$NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/routing/nginx_ingress_controller.encrypted.yaml" \
--version "$NGINX_INGRESS_CONTROLLER_HELM_CHART_VERSION" \
ingress-nginx/ingress-nginx

kubectl get all -n "${NGINX_INGRESS_CONTROLLER_DEPLOYMENT_NAMESPACE}"

###################

echo "##################################################"
echo "Installing cert-manager..."
echo "#########################"
echo "Loading configuration from platform_config ..."
CERT_MANAGER_DEPLOYMENT_NAMESPACE="$(jq -r '.CERT_MANAGER_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
CERT_MANAGER_DEPLOYMENT_NAME="$(jq -r '.CERT_MANAGER_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
CERT_MANAGER_HELM_CHART_VERSION="$(jq -r '.CERT_MANAGER_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/static.json)"
CERT_MANAGER_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.CERT_MANAGER_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "CERT_MANAGER:"
echo "CERT_MANAGER_DEPLOYMENT_NAMESPACE: $CERT_MANAGER_DEPLOYMENT_NAMESPACE"
echo "CERT_MANAGER_DEPLOYMENT_NAME: $CERT_MANAGER_DEPLOYMENT_NAME"
echo "CERT_MANAGER_HELM_CHART_VERSION: $CERT_MANAGER_HELM_CHART_VERSION"
echo "CERT_MANAGER_HELM_DEPLOYMENT_TIMEOUT: $CERT_MANAGER_HELM_DEPLOYMENT_TIMEOUT"
echo ""
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repo ..."
helm repo add jetstack https://charts.jetstack.io
helm repo update

echo "#########################"
echo "Installing cert-manager..."

helm upgrade -i --wait --timeout "$CERT_MANAGER_HELM_DEPLOYMENT_TIMEOUT" "$CERT_MANAGER_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${CERT_MANAGER_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/routing/cert_manager.encrypted.yaml" \
--version "$CERT_MANAGER_HELM_CHART_VERSION" \
jetstack/cert-manager

kubectl get all -n "${CERT_MANAGER_DEPLOYMENT_NAMESPACE}"