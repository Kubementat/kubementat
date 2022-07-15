#!/usr/bin/env bash

######################################
#
# This script removes the monitoring components from the cluster in ENVIRONMENT
#
######################################

set -e

./uninstall_helm_deployment.sh "$1" "PROMETHEUS"
./uninstall_helm_deployment.sh "$1" "GRAFANA"
./uninstall_helm_deployment.sh "$1" "PROMETHEUS_BLACKBOX_EXPORTER"
./uninstall_helm_deployment.sh "$1" "PROMETHEUS_HELM_EXPORTER"