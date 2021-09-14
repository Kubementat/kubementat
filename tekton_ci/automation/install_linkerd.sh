#!/usr/bin/env bash

#################################
#
# This script installs linkerd on a k8s cluster
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_linkerd.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_linkerd.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
LINKERD_NAMESPACE="$(jq -r '.LINKERD_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "LINKERD_NAMESPACE: $LINKERD_NAMESPACE"
echo "#########################"

# install linkerd crs
linkerd check --pre
linkerd install | kubectl -n "$LINKERD_NAMESPACE" apply -f -
# linkerd check

# TODO: FIXME: use the existing prometheus for installing linkerd viz
# linkerd viz install -f "../../platform_config/${ENVIRONMENT}/linkerd/viz_config.encrypted.yaml" | kubectl apply -f -
linkerd viz install | kubectl apply -f -

# linkerd example apps
# curl -sL run.linkerd.io/emojivoto.yml | kubectl apply -f -
# kubectl -n emojivoto port-forward svc/web-svc 8080:80
# visit: http://localhost:8080
# inject linkerd proxy
# kubectl get -n emojivoto deploy -o yaml | linkerd inject - | kubectl apply -f -
# linkerd -n emojivoto check --proxy

# open dashboard
# linkerd viz dashboard