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
  echo "Usage: install_easy_tekton.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_easy_tekton.sh dev dev1"
  exit 1
fi

set -u

function check_cluster_and_access(){
  echo "Checking cluster"
  echo "You are going to install easy_tekton automation to the following cluster:"
  kubectl cluster-info

  while true; do
    read -p "Do you really wish to install easy_tekton on this cluster?" yn
    case $yn in
        [Yy]* ) break;;
        [Nn]* ) echo "Cancelled install script."; exit;;
        * ) echo "Please answer yes or no.";;
    esac
  done

  kubectl auth can-i create namespace
  kubectl auth can-i create deployment
  kubectl auth can-i create clusterrole
  kubectl auth can-i create role
  kubectl auth can-i create daemonset
  kubectl auth can-i create replicaset

  echo "Finished checking cluster access"
  echo "################"
  echo ""
}

check_cluster_and_access

pushd tekton_ci/automation > /dev/null

./install_tekton.sh "${ENVIRONMENT}"
./install_logging.sh "${ENVIRONMENT}"
./install_monitoring.sh "${ENVIRONMENT}"
./setup_pipelines.sh "${ENVIRONMENT}" "${TEAM}"
./setup_triggers.sh "${ENVIRONMENT}" "${TEAM}"
./install_vault.sh "${ENVIRONMENT}"
./install_linkerd.sh "${ENVIRONMENT}"

popd > /dev/null

echo "Installed easy_tekton to cluster successfully"