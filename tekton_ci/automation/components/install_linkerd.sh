#!/usr/bin/env bash

#################################
#
# This script installs linkerd on a k8s cluster
#
#################################

set -e

ENVIRONMENT="$1"

UPDATE_ENABLED="false"
if [[ "$2" == "true" ]]; then
  UPDATE_ENABLED="true"
fi

if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_linkerd.sh <ENVIRONMENT_NAME> <Optional: UPDATE_ENABLED>"
  echo "e.g.: install_linkerd.sh dev true"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
LINKERD_NAMESPACE="$(jq -r '.LINKERD_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "LINKERD_NAMESPACE: $LINKERD_NAMESPACE"
echo "#########################"

linkerd_namespace_present="$(kubectl get ns | grep linkerd)"
set -e
if [[ "$linkerd_namespace_present" == "" ]]; then
  echo "Linkerd namespace not present. Installing freshly"
  # install linkerd crs
  # TODO: ensure that this script also succeeds when a previous install is present for linkerd:
  # https://linkerd.io/2.10/tasks/upgrade/
  # maybe: install via helm instead https://linkerd.io/2.10/tasks/install-helm/
  linkerd check --pre
  linkerd install | kubectl -n "$LINKERD_NAMESPACE" apply -f -
  linkerd check

  # TODO: FIXME: use the existing prometheus for installing linkerd viz
  # alternatively configure platform prometheus to federate linkerd data from linkerd viz prometheus installation
  # linkerd viz install -f "../../../platform_config/${ENVIRONMENT}/linkerd/viz_config.encrypted.yaml" | kubectl apply -f -
  linkerd viz install | kubectl apply -f -
else
  if [[ "$UPDATE_ENABLED" == "true" ]]; then
    echo "Linkerd Namespace present: Performing update..."
    curl -sL https://run.linkerd.io/install | sh

    linkerd upgrade | kubectl apply --prune -l linkerd.io/control-plane-ns="$LINKERD_NAMESPACE" -f -
    linkerd check
    linkerd version

    kubectl -n "$LINKERD_NAMESPACE" rollout restart deploy
    linkerd check --proxy

    linkerd viz install | kubectl apply -f -
  else
    echo "Update disabled. Skipping script execution."
  fi
fi
