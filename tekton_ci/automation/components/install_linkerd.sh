#!/usr/bin/env bash

#################################
#
# This script installs linkerd on a k8s cluster
#
#################################

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

set -eu

echo "#########################"
echo "Loading configuration from platform_config ..."
LINKERD_NAMESPACE="linkerd"
LINKERD_VIZ_NAMESPACE="linkerd-viz"
LINKERD_HA_ENABLED="$(jq -r '.LINKERD_HA_ENABLED' ../../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "LINKERD_NAMESPACE: $LINKERD_NAMESPACE"
echo "LINKERD_VIZ_NAMESPACE: $LINKERD_VIZ_NAMESPACE"
echo "LINKERD_HA_ENABLED: $LINKERD_HA_ENABLED"
echo "#########################"

linkerd_ha_option=""
if [[ "$LINKERD_HA_ENABLED" == "true" ]]; then
  linkerd_ha_option="--ha"
fi

set +e
echo "Checking linkerd namespace presence ..."
linkerd_namespace_present="$(kubectl get ns | grep linkerd)"
set -e

echo "###################"
echo "linkerd version:"
linkerd version
echo "###################"

if [[ "$linkerd_namespace_present" == "" ]]; then
  echo "Linkerd namespace not present. Installing freshly ..."
  linkerd check --pre
  linkerd install --crds | kubectl -n "$LINKERD_NAMESPACE" apply -f -
  linkerd install $linkerd_ha_option | kubectl -n "$LINKERD_NAMESPACE" apply -f -
  linkerd check

  # TODO: FIXME: use the existing prometheus for installing linkerd viz
  # alternatively configure platform prometheus to federate linkerd data from linkerd viz prometheus installation
  # linkerd viz install -f "../../../platform_config/${ENVIRONMENT}/linkerd/viz_config.encrypted.yaml" | kubectl apply -f -

  # TODO: FEATURE: Install grafana???
  # Docs: https://linkerd.io/2.12/tasks/grafana/
  # helm values for grafanna: https://raw.githubusercontent.com/linkerd/linkerd2/main/grafana/values.yaml
  linkerd viz install | kubectl apply -f -
else
  if [[ "$UPDATE_ENABLED" == "true" ]]; then
    # Upgrade Docs: https://linkerd.io/2.12/tasks/upgrade/
    echo "Linkerd Namespace present: Performing update ..."
    curl -sL https://run.linkerd.io/install | sh

    # linkerd install --crds | kubectl -n "$LINKERD_NAMESPACE" apply -f -
    linkerd upgrade $linkerd_ha_option | kubectl apply --prune -l linkerd.io/control-plane-ns="$LINKERD_NAMESPACE" -f -
    linkerd check
    linkerd version

    kubectl -n "$LINKERD_NAMESPACE" rollout restart deploy
    kubectl -n "$LINKERD_VIZ_NAMESPACE" rollout restart deploy
    linkerd check --proxy

    linkerd viz install | kubectl apply -f -
  else
    echo "###################"
    echo "Update disabled. Skipping script execution."
    echo "###################"
  fi
fi
