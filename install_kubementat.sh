#!/usr/bin/env bash

#
# Installs all needed components for a fully functional k8s cluster installation:
# - tekton
# - monitoring (prometheus, grafana)
# - logging (promtail, loki)
# - linkerd service mesh
# - vault keystore
# - tekton pipeline setup
# - tekton trigger setup

set -e

# ---------------- DEFAULT ENVIRONMENT VARIABLES -----------------
# This is highly recommended, as a lot of the benefits resulting from kubementat relies on tekton automation currently
if [ -z ${COMPONENT_ENABLED_TEKTON+x} ]; then
  COMPONENT_ENABLED_TEKTON="true"
fi

if [ -z ${COMPONENT_ENABLED_LOGGING+x} ]; then
  COMPONENT_ENABLED_LOGGING="true"
fi

if [ -z ${COMPONENT_ENABLED_MONITORING+x} ]; then
  COMPONENT_ENABLED_MONITORING="true"
fi

if [ -z ${COMPONENT_ENABLED_LINKERD+x} ]; then
  COMPONENT_ENABLED_LINKERD="true"
fi

if [ -z ${COMPONENT_ENABLED_VAULT+x} ]; then
  COMPONENT_ENABLED_VAULT="false"
fi

# ---------------- DEFAULT ENVIRONMENT VARIABLES END -----------------

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "#################"
  echo "Available environment variables:"
  echo "COMPONENT_ENABLED_TEKTON - enable tekton installation - default: true"
  echo "COMPONENT_ENABLED_LOGGING - enable loki installation - default: true"
  echo "COMPONENT_ENABLED_MONITORING - enable prometheus and grafana installation - default: true"
  echo "COMPONENT_ENABLED_LINKERD - enable linkerd installation - default: true"
  echo "COMPONENT_ENABLED_VAULT - enable vault installation - default: false"
  echo "#################"
  echo ""
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
  echo "###################################"
  echo "Checking cluster..."
  echo "You are going to install kubementat automation to the following cluster:"
  kubectl cluster-info
  echo ""
  echo "###################################"
  echo "###################################"
  echo ""
  while true; do
    read -p "Do you really wish to install kubementat on this cluster?" yn
    case $yn in
        [Yy]* ) break;;
        [Nn]* ) echo "Cancelled install script."; exit;;
        * ) echo "Please answer yes or no.";;
    esac
  done

  echo "Checking permissions on cluster for installation ..."
  kubectl auth can-i create namespace
  kubectl auth can-i create deployment
  kubectl auth can-i create clusterrole
  kubectl auth can-i create role
  kubectl auth can-i create daemonset
  kubectl auth can-i create replicaset

  echo "Finished checking cluster permissions."
  echo "################"
  echo ""
}

function print_configuring_section() {
  component="$1"
  echo ""
  echo "######################################################"
  date
  echo "CONFIGURING: $component"
  echo "######################################################"
  echo ""
}

function print_skip_section() {
  env_variable_name="$1"
  echo ""
  echo "------------------------------------------------------"
  date
  echo "${env_variable_name}: false - Skipping installation"
  echo "------------------------------------------------------"
  echo ""
}

check_dependencies
check_cluster_and_access

echo "Installing Kubementat components..."
pushd tekton_ci/automation/components > /dev/null

if [[ "$COMPONENT_ENABLED_TEKTON" == "true" ]]; then
  print_configuring_section "Tekton CI"
  ./install_tekton.sh "${ENVIRONMENT}"
else
  print_skip_section "COMPONENT_ENABLED_TEKTON"
fi

if [[ "$COMPONENT_ENABLED_LOGGING" == "true" ]]; then
  print_configuring_section "Logging (loki, promtail)"
  ./install_logging.sh "${ENVIRONMENT}"
else
  print_skip_section "COMPONENT_ENABLED_LOGGING"
fi

if [[ "$COMPONENT_ENABLED_MONITORING" == "true" ]]; then
  print_configuring_section "Monitoring (Prometheus, Grafana)"
  ./install_monitoring.sh "${ENVIRONMENT}"
else
  print_skip_section "COMPONENT_ENABLED_MONITORING"
fi

if [[ "$COMPONENT_ENABLED_LINKERD" == "true" ]]; then
  print_configuring_section "Linkerd service mesh"
  ./install_linkerd.sh "${ENVIRONMENT}"
else
  print_skip_section "COMPONENT_ENABLED_LINKERD"
fi

if [[ "$COMPONENT_ENABLED_VAULT" == "true" ]]; then
  print_configuring_section "Vault"
  ./install_vault.sh "${ENVIRONMENT}"
else
  print_skip_section "COMPONENT_ENABLED_VAULT"
fi

if [[ "$COMPONENT_ENABLED_TEKTON" == "true" ]]; then
  echo "######################################################"
  date
  echo "Setting up pipelines and triggers in tekton for team ${TEAM} ..."
  echo "######################################################"
  echo ""
  popd > /dev/null
  pushd tekton_ci/automation > /dev/null

  print_configuring_section "Tekton Pipelines for TEAM ${TEAM}"
  ./setup_pipelines.sh "${ENVIRONMENT}" "${TEAM}"

  print_configuring_section "Tekton Pipeline Triggers for TEAM ${TEAM}"
  ./setup_triggers.sh "${ENVIRONMENT}" "${TEAM}"

  popd > /dev/null
else
  print_skip_section "COMPONENT_ENABLED_TEKTON"
  echo "Skipped tekton pipeline and trigger setup."
fi

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