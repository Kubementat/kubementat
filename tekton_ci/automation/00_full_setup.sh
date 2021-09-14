#!/usr/bin/env bash

#
# Installs all needed components for a fully functional k8s cluster installation:
# - tekton
# - monitoring (prometheus, grafana)
# - logging (promtail, loki)
# - tekton pipeline setup
# - tekton trigger setup

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: 00_full_setup.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: 00_full_setup.sh dev dev1"
  exit 1
fi

set -u

./install_tekton.sh "${ENVIRONMENT}"
./install_logging.sh "${ENVIRONMENT}"
./install_monitoring.sh "${ENVIRONMENT}"
./install_linkerd.sh "${ENVIRONMENT}"
./install_vault.sh "${ENVIRONMENT}"
./setup_pipelines.sh "${ENVIRONMENT}" "${TEAM}"
./setup_triggers.sh "${ENVIRONMENT}" "${TEAM}"