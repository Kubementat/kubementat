#!/usr/bin/env bash

###################
# This script removes all kubementat components from the kubernetes cluster
###################

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_kubementat.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_kubementat.sh dev"
  exit 1
fi

set -eu

while true; do
    read -p "Do you really wish to uninstall all kubementat resources?" yn
    case $yn in
        [Yy]* ) break;;
        [Nn]* ) echo "Cancelled uninstall script."; exit;;
        * ) echo "Please answer yes or no.";;
    esac
done

echo "#########################"
echo "Uninstalling all kubementat managed platform components and team spaces"
echo ""

echo "#########################"
echo "Loading configuration from platform_config ..."
TEKTON_NAMESPACE="tekton-pipelines"
LOKI_DEPLOYMENT_NAMESPACE="loki"
PROMETHEUS_DEPLOYMENT_NAMESPACE="prometheus"
GRAFANA_DEPLOYMENT_NAMESPACE="grafana"
PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE="prometheus"
VAULT_DEPLOYMENT_NAMESPACE="vault"
LINKERD_VIZ_NAMESPACE="linkerd-viz"
LINKERD_NAMESPACE="linkerd"


echo "ATTENTION: If you were using more teams than dev1, please remove the according remaining namespaces manually."
DEV1_PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' platform_config/"${ENVIRONMENT}"/dev1/static.json)"
DEV1_APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' platform_config/"${ENVIRONMENT}"/dev1/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "PROMETHEUS_DEPLOYMENT_NAMESPACE: $PROMETHEUS_DEPLOYMENT_NAMESPACE"
echo "PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE: $PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE"
echo "GRAFANA_DEPLOYMENT_NAMESPACE: $GRAFANA_DEPLOYMENT_NAMESPACE"
echo "LOKI_DEPLOYMENT_NAMESPACE: $LOKI_DEPLOYMENT_NAMESPACE"
echo "LINKERD_NAMESPACE: $LINKERD_NAMESPACE"
echo "LINKERD_VIZ_NAMESPACE: $LINKERD_VIZ_NAMESPACE"
echo "VAULT_DEPLOYMENT_NAMESPACE: $VAULT_DEPLOYMENT_NAMESPACE"
echo ""
echo "DEV1_PIPELINE_NAMESPACE: $DEV1_PIPELINE_NAMESPACE"
echo "DEV1_APP_DEPLOYMENT_NAMESPACE: $DEV1_APP_DEPLOYMENT_NAMESPACE"
echo "#########################"

echo ""
echo "Namespaces before uninstallation:"
kubectl get ns
echo "#########################"
echo ""

echo "Removing clusterrole helm-deployer-cluster-role"
kubectl delete clusterrole helm-deployer-cluster-role || true

echo "Deleting platform components..."
kubectl delete namespace "$TEKTON_NAMESPACE" || true
kubectl delete namespace "$PROMETHEUS_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$GRAFANA_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$LOKI_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$VAULT_DEPLOYMENT_NAMESPACE" || true
linkerd viz uninstall | kubectl delete -f -
linkerd uninstall | kubectl delete -f -
kubectl delete namespace "$LINKERD_VIZ_NAMESPACE" || true
kubectl delete namespace "$LINKERD_NAMESPACE" || true
echo "Finished deleting platform components"
echo "#########################"

echo ""
echo "Deleting team dev1 setup..."
kubectl delete namespace "$DEV1_PIPELINE_NAMESPACE" || true
kubectl delete namespace "$DEV1_APP_DEPLOYMENT_NAMESPACE" || true
echo "Finished deleting team dev1 setup"
echo "#########################"

echo ""
echo "Namespaces after uninstallation:"
kubectl get ns
echo "#########################"
echo "DONE"
