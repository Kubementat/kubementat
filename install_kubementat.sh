#!/usr/bin/env bash

#
# Installs all needed components for a fully functional k8s cluster installation:
#
# uses the helmfile specification from the selected environment:
# platform_config/${ENVIRONMENT}/kubementat_components/helmfile.yaml
# e.g. platform_config/dev/kubementat_components/helmfile.yaml

# Also sets up tekton pipelines and triggers (see: tekton_ci/automation/setup_pipelines.sh and tekton_ci/automation/setup_triggers.sh)

set -e

# ---------------- DEFAULT ENVIRONMENT VARIABLES -----------------
if [ -z ${HELMFILE_INSTALLATION_GROUP+x} ]; then
  HELMFILE_INSTALLATION_GROUP="standard"
fi

if [ -z ${CONFIGURE_TEKTON_PIPELINES+x} ]; then
  CONFIGURE_TEKTON_PIPELINES="true"
fi

if [ -z ${COMPONENT_ENABLED_LINKERD+x} ]; then
  COMPONENT_ENABLED_LINKERD="false"
fi

# ---------------- DEFAULT ENVIRONMENT VARIABLES END -----------------

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "#################"
  echo "Available environment variables:"
  echo "HELMFILE_INSTALLATION_GROUP - the group within the kubementat_components helmfile to apply to the cluster - default: standard"
  echo "CONFIGURE_TEKTON_PIPELINES - boolean, run pipeline setup - default: true"
  echo "COMPONENT_ENABLED_LINKERD - boolean, install linkerd? - default: false"
  echo ""
  echo "e.g."
  echo "export HELMFILE_INSTALLATION_GROUP=standard"
  echo "export CONFIGURE_TEKTON_PIPELINES=false"
  echo "export COMPONENT_ENABLED_LINKERD=false"
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
  command -v helm >/dev/null 2>&1 || { echo "helm is not installed. Aborting." >&2; exit 1; }
  command -v helmfile >/dev/null 2>&1 || { echo "helmfile is not installed. Aborting." >&2; exit 1; }
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

echo ""
echo "Installing Kubementat components..."
pushd tekton_ci/automation/components > /dev/null

# tekton
print_configuring_section "Tekton CI"
./install_tekton.sh "${ENVIRONMENT}"

# linkerd
if [[ "$COMPONENT_ENABLED_LINKERD" == "true" ]]; then
  print_configuring_section "Linkerd service mesh"
  ./install_linkerd.sh "${ENVIRONMENT}"
else
  print_skip_section "COMPONENT_ENABLED_LINKERD"
fi

date
# helmfile apply
./helmfile_apply.sh "${ENVIRONMENT}" "${HELMFILE_INSTALLATION_GROUP}" "true"

popd > /dev/null


if [[ "$CONFIGURE_TEKTON_PIPELINES" == "true" ]]; then
  pushd tekton_ci/automation > /dev/null
  echo "######################################################"
  date
  echo "Setting up pipelines and triggers in tekton for team ${TEAM} ..."
  echo "######################################################"
  echo ""

  print_configuring_section "Tekton Pipelines for TEAM ${TEAM}"
  ./setup_pipelines.sh "${ENVIRONMENT}" "${TEAM}"

  print_configuring_section "Tekton Pipeline Triggers for TEAM ${TEAM}"
  ./setup_triggers.sh "${ENVIRONMENT}" "${TEAM}"

  popd > /dev/null
else
  print_skip_section "CONFIGURE_TEKTON_PIPELINES"
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