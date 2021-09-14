#!/usr/bin/env bash

######################################
#
# This script removes the monitoring components from the cluster in ENVIRONMENT
#
######################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: uninstall_monitoring.sh <ENVIRONMENT_NAME>"
  echo "e.g.: uninstall_monitoring.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
PROMETHEUS_DEPLOYMENT_NAMESPACE="$(jq -r '.PROMETHEUS_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_DEPLOYMENT_NAME="$(jq -r '.PROMETHEUS_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

GRAFANA_DEPLOYMENT_NAMESPACE="$(jq -r '.GRAFANA_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
GRAFANA_DEPLOYMENT_NAME="$(jq -r '.GRAFANA_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE="$(jq -r '.PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME="$(jq -r '.PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "PROMETHEUS:"
echo "PROMETHEUS_DEPLOYMENT_NAMESPACE: $PROMETHEUS_DEPLOYMENT_NAMESPACE"
echo "PROMETHEUS_DEPLOYMENT_NAME: $PROMETHEUS_DEPLOYMENT_NAME"
echo ""
echo "PROMETHEUS BLACKBOX EXPORTER:"
echo "PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE: $PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE"
echo "PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME: $PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME"
echo ""
echo "GRAFANA:"
echo "GRAFANA_DEPLOYMENT_NAMESPACE: $GRAFANA_DEPLOYMENT_NAMESPACE"
echo "GRAFANA_DEPLOYMENT_NAME: $GRAFANA_DEPLOYMENT_NAME"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "Uninstalling grafana..."
helm -n "${GRAFANA_DEPLOYMENT_NAMESPACE}" delete "${GRAFANA_DEPLOYMENT_NAME}" ||true

echo "Uninstalling prometheus blackbox exporter..."
helm -n "${PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE}" delete "${PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME}" ||true

echo "Uninstalling prometheus..."
helm -n "${PROMETHEUS_DEPLOYMENT_NAMESPACE}" delete "${PROMETHEUS_DEPLOYMENT_NAME}" ||true


kubectl get all -n "${PROMETHEUS_DEPLOYMENT_NAMESPACE}"
kubectl get all -n "${PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE}"
kubectl get all -n "${GRAFANA_DEPLOYMENT_NAMESPACE}"