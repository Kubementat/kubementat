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


echo "###################################"
echo "You are going to uninstall kubementat automation from the following cluster:"
kubectl cluster-info
echo ""
echo "###################################"
echo "###################################"
echo ""

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
TEKTON_RESOLVER_NAMESPACE="tekton-pipelines-resolvers"
LOKI_DEPLOYMENT_NAMESPACE="loki"
PROMETHEUS_DEPLOYMENT_NAMESPACE="prometheus"
GRAFANA_DEPLOYMENT_NAMESPACE="grafana"
GOLDILOCKS_NAMESPACE="goldilocks"
VPA_NAMESPACE="vpa"
PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE="prometheus"
VAULT_DEPLOYMENT_NAMESPACE="vault"
LINKERD_VIZ_NAMESPACE="linkerd-viz"
LINKERD_NAMESPACE="linkerd"
KUBERNETES_DASHBOARD_NAMESPACE="kubernetes-dashboard"

echo "ATTENTION: If you were using more teams than dev1, please remove the according remaining namespaces manually."
DEV1_APP_DEPLOYMENT_NAMESPACE=dev1
DEV1_PIPELINE_NAMESPACE=dev1-pipelines
SMOKE_APP_DEPLOYMENT_NAMESPACE=smoke
SMOKE_PIPELINE_NAMESPACE=smoke-pipelines

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "PROMETHEUS_DEPLOYMENT_NAMESPACE: $PROMETHEUS_DEPLOYMENT_NAMESPACE"
echo "PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE: $PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE"
echo "GRAFANA_DEPLOYMENT_NAMESPACE: $GRAFANA_DEPLOYMENT_NAMESPACE"
echo "LOKI_DEPLOYMENT_NAMESPACE: $LOKI_DEPLOYMENT_NAMESPACE"
echo "LINKERD_NAMESPACE: $LINKERD_NAMESPACE"
echo "LINKERD_VIZ_NAMESPACE: $LINKERD_VIZ_NAMESPACE"
echo "VAULT_DEPLOYMENT_NAMESPACE: $VAULT_DEPLOYMENT_NAMESPACE"
echo "GOLDILOCKS_NAMESPACE: $GOLDILOCKS_NAMESPACE"
echo "VPA_NAMESPACE: $VPA_NAMESPACE"
echo "KUBERNETES_DASHBOARD_NAMESPACE: $KUBERNETES_DASHBOARD_NAMESPACE"

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

echo ""
echo "Deleting team dev1 and smoke test setups ..."
kubectl delete namespace "$DEV1_PIPELINE_NAMESPACE" || true
kubectl delete namespace "$DEV1_APP_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$SMOKE_PIPELINE_NAMESPACE" || true
kubectl delete namespace "$SMOKE_APP_DEPLOYMENT_NAMESPACE" || true
echo "Finished deleting setups"
echo "#########################"

echo "Deleting platform components..."
kubectl delete namespace "$TEKTON_NAMESPACE" || true
kubectl delete namespace "$TEKTON_RESOLVER_NAMESPACE" || true
kubectl delete namespace "$PROMETHEUS_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$GRAFANA_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$LOKI_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$VAULT_DEPLOYMENT_NAMESPACE" || true
kubectl delete namespace "$GOLDILOCKS_NAMESPACE" || true
kubectl delete namespace "$VPA_NAMESPACE" || true
kubectl delete namespace "$KUBERNETES_DASHBOARD_NAMESPACE" || true

linkerd viz uninstall | kubectl delete -f -
linkerd uninstall | kubectl delete -f -
kubectl delete namespace "$LINKERD_VIZ_NAMESPACE" || true
kubectl delete namespace "$LINKERD_NAMESPACE" || true
echo "Finished deleting platform components"
echo "#########################"

echo "Deleting CRDs..."
kubectl delete crd clusterinterceptors.triggers.tekton.dev || true
kubectl delete crd clustertasks.tekton.dev || true
kubectl delete crd clustertriggerbindings.triggers.tekton.dev || true
kubectl delete crd eventlisteners.triggers.tekton.dev || true
kubectl delete crd extensions.dashboard.tekton.dev || true
kubectl delete crd interceptors.triggers.tekton.dev || true
kubectl delete crd pipelineresources.tekton.dev || true
kubectl delete crd pipelineruns.tekton.dev || true
kubectl delete crd pipelines.tekton.dev || true
kubectl delete crd resolutionrequests.resolution.tekton.dev || true
kubectl delete crd runs.tekton.dev || true
kubectl delete crd taskruns.tekton.dev || true
kubectl delete crd tasks.tekton.dev || true
kubectl delete crd triggerbindings.triggers.tekton.dev || true
kubectl delete crd triggers.triggers.tekton.dev || true
kubectl delete crd triggertemplates.triggers.tekton.dev || true
kubectl delete crd verificationpolicies.tekton.dev || true
kubectl delete crd customruns.tekton.dev || true

echo "Finished deleting CRDs"
echo "#########################"

echo ""
echo "Namespaces after uninstallation:"
kubectl get ns
echo "#########################"
echo "DONE"
