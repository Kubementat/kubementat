#!/usr/bin/env bash

#################################
#
# This script installs nginx as reverse proxy
#
#################################
set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_nginx_reverse_proxy.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_nginx_reverse_proxy.sh dev dev1"
  exit 1
fi

echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
NGINX_REVERSE_PROXY_DEPLOYMENT_NAME="$(jq -r '.NGINX_REVERSE_PROXY_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
NGINX_REVERSE_PROXY_HELM_CHART_VERSION="$(jq -r '.NGINX_REVERSE_PROXY_HELM_CHART_VERSION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
NGINX_REVERSE_PROXY_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.NGINX_REVERSE_PROXY_HELM_DEPLOYMENT_TIMEOUT' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "NGINX_REVERSE_PROXY_DEPLOYMENT_NAME: $NGINX_REVERSE_PROXY_DEPLOYMENT_NAME"
echo "NGINX_REVERSE_PROXY_HELM_CHART_VERSION: $NGINX_REVERSE_PROXY_HELM_CHART_VERSION"
echo "NGINX_REVERSE_PROXY_HELM_DEPLOYMENT_TIMEOUT: $NGINX_REVERSE_PROXY_HELM_DEPLOYMENT_TIMEOUT"
echo "#########################"

echo "Installing helm chart ..."
# add the helm repo for mysql
helm repo add bitnami https://charts.bitnami.com/bitnami

helm upgrade -i --wait --timeout "$NGINX_REVERSE_PROXY_HELM_DEPLOYMENT_TIMEOUT" "$NGINX_REVERSE_PROXY_DEPLOYMENT_NAME" \
--namespace "${APP_DEPLOYMENT_NAMESPACE}" \
-f "../../platform_config/${ENVIRONMENT}/${TEAM}/nginx-reverse-proxy/values.encrypted.yaml" \
--version "$NGINX_REVERSE_PROXY_HELM_CHART_VERSION" \
bitnami/nginx

echo "#########################"
echo "Deployment status:"
kubectl get pods -n "$APP_DEPLOYMENT_NAMESPACE" |grep "$NGINX_REVERSE_PROXY_DEPLOYMENT_NAME"
helm -n "$APP_DEPLOYMENT_NAMESPACE" status "$NGINX_REVERSE_PROXY_DEPLOYMENT_NAME"