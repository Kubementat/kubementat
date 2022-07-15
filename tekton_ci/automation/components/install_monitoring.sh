#!/usr/bin/env bash

#################################
#
# This script installs the prometheus, grafana and prometheus blackbox exporter helm charts into the provided environment.
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_monitoring.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_monitoring.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
PROMETHEUS_DEPLOYMENT_NAMESPACE="$(jq -r '.PROMETHEUS_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_DEPLOYMENT_NAME="$(jq -r '.PROMETHEUS_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_HELM_CHART_VERSION="$(jq -r '.PROMETHEUS_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.PROMETHEUS_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/monitoring/prometheus_values.encrypted.yaml"

PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE="$(jq -r '.PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME="$(jq -r '.PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_BLACKBOX_EXPORTER_HELM_CHART_VERSION="$(jq -r '.PROMETHEUS_BLACKBOX_EXPORTER_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_BLACKBOX_EXPORTER_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.PROMETHEUS_BLACKBOX_EXPORTER_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_BLACKBOX_EXPORTER_HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/monitoring/prometheus_blackbox_exporter_values.encrypted.yaml"

PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAMESPACE="$(jq -r '.PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAME="$(jq -r '.PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_HELM_EXPORTER_HELM_CHART_VERSION="$(jq -r '.PROMETHEUS_HELM_EXPORTER_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_HELM_EXPORTER_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.PROMETHEUS_HELM_EXPORTER_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
PROMETHEUS_HELM_EXPORTER_HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/monitoring/prometheus_blackbox_exporter_values.encrypted.yaml"

GRAFANA_DEPLOYMENT_NAMESPACE="$(jq -r '.GRAFANA_DEPLOYMENT_NAMESPACE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
GRAFANA_DEPLOYMENT_NAME="$(jq -r '.GRAFANA_DEPLOYMENT_NAME' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
GRAFANA_HELM_CHART_VERSION="$(jq -r '.GRAFANA_HELM_CHART_VERSION' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
GRAFANA_HELM_DEPLOYMENT_TIMEOUT="$(jq -r '.GRAFANA_HELM_DEPLOYMENT_TIMEOUT' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
GRAFANA_HELM_VALUES_FILE_LOCATION="../../../platform_config/${ENVIRONMENT}/monitoring/grafana_values.encrypted.yaml"
GRAFANA_ADMIN_USER="$(jq -r '.GRAFANA_ADMIN_USER' ../../../platform_config/"${ENVIRONMENT}"/static.encrypted.json)"
GRAFANA_ADMIN_PASSWORD="$(jq -r '.GRAFANA_ADMIN_PASSWORD' ../../../platform_config/"${ENVIRONMENT}"/static.encrypted.json)"


echo "ENVIRONMENT: $ENVIRONMENT"
echo ""
echo "PROMETHEUS:"
echo "PROMETHEUS_DEPLOYMENT_NAMESPACE: $PROMETHEUS_DEPLOYMENT_NAMESPACE"
echo "PROMETHEUS_DEPLOYMENT_NAME: $PROMETHEUS_DEPLOYMENT_NAME"
echo "PROMETHEUS_HELM_CHART_VERSION: $PROMETHEUS_HELM_CHART_VERSION"
echo "PROMETHEUS_HELM_DEPLOYMENT_TIMEOUT: $PROMETHEUS_HELM_DEPLOYMENT_TIMEOUT"
echo "PROMETHEUS_HELM_VALUES_FILE_LOCATION: $PROMETHEUS_HELM_VALUES_FILE_LOCATION"
echo ""
echo "PROMETHEUS BLACKBOX EXPORTER:"
echo "PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE: $PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE"
echo "PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME: $PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME"
echo "PROMETHEUS_BLACKBOX_EXPORTER_HELM_CHART_VERSION: $PROMETHEUS_BLACKBOX_EXPORTER_HELM_CHART_VERSION"
echo "PROMETHEUS_BLACKBOX_EXPORTER_HELM_DEPLOYMENT_TIMEOUT: $PROMETHEUS_BLACKBOX_EXPORTER_HELM_DEPLOYMENT_TIMEOUT"
echo "PROMETHEUS_BLACKBOX_EXPORTER_HELM_VALUES_FILE_LOCATION: $PROMETHEUS_BLACKBOX_EXPORTER_HELM_VALUES_FILE_LOCATION"
echo ""
echo "PROMETHEUS HELM EXPORTER:"
echo "PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAMESPACE: $PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAMESPACE"
echo "PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAME: $PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAME"
echo "PROMETHEUS_HELM_EXPORTER_HELM_CHART_VERSION: $PROMETHEUS_HELM_EXPORTER_HELM_CHART_VERSION"
echo "PROMETHEUS_HELM_EXPORTER_HELM_DEPLOYMENT_TIMEOUT: $PROMETHEUS_HELM_EXPORTER_HELM_DEPLOYMENT_TIMEOUT"
echo "PROMETHEUS_HELM_EXPORTER_HELM_VALUES_FILE_LOCATION: $PROMETHEUS_HELM_EXPORTER_HELM_VALUES_FILE_LOCATION"
echo ""
echo "GRAFANA:"
echo "GRAFANA_DEPLOYMENT_NAMESPACE: $GRAFANA_DEPLOYMENT_NAMESPACE"
echo "GRAFANA_DEPLOYMENT_NAME: $GRAFANA_DEPLOYMENT_NAME"
echo "GRAFANA_HELM_CHART_VERSION: $GRAFANA_HELM_CHART_VERSION"
echo "GRAFANA_HELM_DEPLOYMENT_TIMEOUT: $GRAFANA_HELM_DEPLOYMENT_TIMEOUT"
echo "GRAFANA_HELM_VALUES_FILE_LOCATION: $GRAFANA_HELM_VALUES_FILE_LOCATION"
echo "#########################"
echo "Helm version:"
helm version
echo "#########################"

echo "#########################"
echo "Setting up helm repos ..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add kube-state-metrics https://kubernetes.github.io/kube-state-metrics
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add sstarcher https://shanestarcher.com/helm-charts/
helm repo update

echo "#########################"
echo "Installing prometheus..."

helm upgrade -i --wait --timeout "$PROMETHEUS_HELM_DEPLOYMENT_TIMEOUT" "$PROMETHEUS_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${PROMETHEUS_DEPLOYMENT_NAMESPACE}" \
-f "$PROMETHEUS_HELM_VALUES_FILE_LOCATION" \
--version "$PROMETHEUS_HELM_CHART_VERSION" \
prometheus-community/prometheus

echo "#########################"
echo "Prometheus Deployment status:"
kubectl get pods -n "$PROMETHEUS_DEPLOYMENT_NAMESPACE" |grep "$PROMETHEUS_DEPLOYMENT_NAME"
helm -n "$PROMETHEUS_DEPLOYMENT_NAMESPACE" status "$PROMETHEUS_DEPLOYMENT_NAME"

echo "#########################"
echo "Installing prometheus blackbox exporter..."

helm upgrade -i --wait --timeout "$PROMETHEUS_BLACKBOX_EXPORTER_HELM_DEPLOYMENT_TIMEOUT" "$PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE}" \
-f "$PROMETHEUS_BLACKBOX_EXPORTER_HELM_VALUES_FILE_LOCATION" \
--version "$PROMETHEUS_BLACKBOX_EXPORTER_HELM_CHART_VERSION" \
prometheus-community/prometheus-blackbox-exporter

echo "#########################"
echo "Prometheus Blackbox Exporter Deployment status:"
kubectl get pods -n "$PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE" |grep "$PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME"
helm -n "$PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAMESPACE" status "$PROMETHEUS_BLACKBOX_EXPORTER_DEPLOYMENT_NAME"

echo "#########################"
echo "Installing prometheus helm exporter..."

helm upgrade -i --wait --timeout "$PROMETHEUS_HELM_EXPORTER_HELM_DEPLOYMENT_TIMEOUT" "$PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAMESPACE}" \
-f "$PROMETHEUS_HELM_EXPORTER_HELM_VALUES_FILE_LOCATION" \
--version "$PROMETHEUS_HELM_EXPORTER_HELM_CHART_VERSION" \
sstarcher/helm-exporter

echo "#########################"
echo "Prometheus Helm Exporter Deployment status:"
kubectl get pods -n "$PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAMESPACE" |grep "$PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAME"
helm -n "$PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAMESPACE" status "$PROMETHEUS_HELM_EXPORTER_DEPLOYMENT_NAME"

echo "#########################"
echo "Installing grafana..."

helm upgrade -i --wait --timeout "$GRAFANA_HELM_DEPLOYMENT_TIMEOUT" "$GRAFANA_DEPLOYMENT_NAME" \
--create-namespace \
--namespace "${GRAFANA_DEPLOYMENT_NAMESPACE}" \
--set adminUser="${GRAFANA_ADMIN_USER}" \
--set adminPassword="${GRAFANA_ADMIN_PASSWORD}" \
-f "$GRAFANA_HELM_VALUES_FILE_LOCATION" \
--version "$GRAFANA_HELM_CHART_VERSION" \
grafana/grafana

echo "#########################"
echo "Grafana Deployment status:"
kubectl get pods -n "$GRAFANA_DEPLOYMENT_NAMESPACE" |grep "$GRAFANA_DEPLOYMENT_NAME"
helm -n "$GRAFANA_DEPLOYMENT_NAMESPACE" status "$GRAFANA_DEPLOYMENT_NAME"