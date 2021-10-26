#!/usr/bin/env bash

#
# Installs all needed components for a fully functional k8s cluster installation:
# - tekton
# - monitoring (prometheus, grafana)
# - logging (promtail, loki)
# - linkerd service mesh
# - tekton pipeline setup
# - tekton trigger setup

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: install_kubementat.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: install_kubementat.sh dev dev1"
  exit 1
fi

set -u

function check_dependencies(){
  echo "Checking local dependencies"
  command -v kubectl >/dev/null 2>&1 || { echo "kubectl is not installed. Aborting." >&2; exit 1; }
  command -v jq >/dev/null 2>&1 || { echo "jq is not installed. Aborting." >&2; exit 1; }
  command -v yq >/dev/null 2>&1 || { echo "yq is not installed. Aborting." >&2; exit 1; }
  command -v git >/dev/null 2>&1 || { echo "git is not installed. Aborting." >&2; exit 1; }
  command -v git-crypt >/dev/null 2>&1 || { echo "git-crypt is not installed. Aborting." >&2; exit 1; }
  command -v gpg >/dev/null 2>&1 || { echo "gpg is not installed. Aborting." >&2; exit 1; }
  command -v linkerd >/dev/null 2>&1 || { echo "linkerd is not installed. Aborting." >&2; exit 1; }
  echo "Finished checking local dependencies"
  echo "################"
  echo ""
}

function check_cluster_and_access(){
  echo "Checking cluster"
  echo "You are going to install kubementat automation to the following cluster:"
  kubectl cluster-info

  while true; do
    read -p "Do you really wish to install kubementat on this cluster?" yn
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

function print_configuring_section() {
  component="$1"
  echo ""
  echo "######################################################"
  echo "CONFIGURING: $component"
  echo "######################################################"
  echo ""
}

check_dependencies
check_cluster_and_access

pushd tekton_ci/automation > /dev/null

print_configuring_section "Tekton CI"
./install_tekton.sh "${ENVIRONMENT}"

print_configuring_section "Loki (Log Aggregator)"
./install_logging.sh "${ENVIRONMENT}"

print_configuring_section "Monitoring (Prometheus, Grafana)"
./install_monitoring.sh "${ENVIRONMENT}"

print_configuring_section "Tekton Pipelines for TEAM ${TEAM}"
./setup_pipelines.sh "${ENVIRONMENT}" "${TEAM}"

print_configuring_section "Tekton Pipeline Triggers for TEAM ${TEAM}"
./setup_triggers.sh "${ENVIRONMENT}" "${TEAM}"

# print_configuring_section "Vault"
# ./install_vault.sh "${ENVIRONMENT}"

print_configuring_section "Linkerd service mesh"
./install_linkerd.sh "${ENVIRONMENT}"

popd > /dev/null

echo "Installed kubementat to cluster successfully :D"
echo ""
echo "Now you are ready to start making it your own."
echo ""
echo "You can run your first hello world pipeline right now:"
echo "pushd tekton_ci/automation"
echo "./run_pipeline.sh dev dev1 ../pipeline-runs/hello-world-pipeline-run.yml"
echo ""
echo "And view the results via the tekton dashboard:"
echo "popd"
echo "pushd utilities"
echo "Once this command is executed you can visit http://127.0.0.1:9097 in your browser"
echo "./open_tekton_dashboard_tunnel.sh dev"