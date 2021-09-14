#!/usr/bin/env bash

####
# This script initializes a basic configuration for getting up and running with easy tekton automations and platform setup
####
set -e

################## FUNCTION DEFINITIONS ####################
function check_is_already_initialized(){
  if [[ -f platform_config/dev/static.encrypted.json ]]; then
    echo "Found platform_config/dev/static.encrypted.json . It seems that easy_tekton has been initialized already! Exiting"
    exit 1
  elif [[ -f platform_config/dev/static.json ]]; then
    echo "Found platform_config/dev/static.json . It seems that easy_tekton has been initialized already! Exiting"
    exit 1
  elif [[ -f platform_config/dev/dev1/static.json ]]; then
    echo "Found platform_config/dev/dev1/static.json . It seems that easy_tekton has been initialized already! Exiting"
    exit 1
  elif [[ -f platform_config/dev/dev1/static.encrypted.json ]]; then
    echo "Found platform_config/dev/dev1/static.encrypted.json . It seems that easy_tekton has been initialized already! Exiting"
    exit 1
  else
    echo "easy_tekton is not initialized yet! Starting basic configuration."
  fi
}

function check_required_environment_variables(){
  if [[ -z "$BASE_DOMAIN" || -z "$AUTOMATION_GIT_URL" || -z "$AUTOMATION_GIT_SERVER_HOST" || -z "$AUTOMATION_GIT_SERVER_PORT" || -z "$AUTOMATION_GIT_SERVER_SSH_USER" || -z "$KUBERNETES_DEFAULT_STORAGE_CLASS" || -z "$DOCKER_REGISTRY_BASE_URL" ]]; then
    echo "Unsufficient environment configuration provided! Exiting."
    echo "Please define the required according ENVIRONMENT variables via exports:"
    echo "e.g.:"
    echo "export BASE_DOMAIN='example.com'"
    echo "export AUTOMATION_GIT_URL='git@github.com:julweber/easy_tekton.git'"
    echo "export AUTOMATION_GIT_SERVER_HOST='github.com'"
    echo "export AUTOMATION_GIT_SERVER_PORT='22'"
    echo "export AUTOMATION_GIT_SERVER_SSH_USER='git'"
    echo "export KUBERNETES_DEFAULT_STORAGE_CLASS='local-path'"
    echo "export DOCKER_REGISTRY_BASE_URL='docker.io/julianweberdev'"
    echo ""
    echo "Then run this script via:"
    echo "./initialize_easy_tekton.sh"
    exit 1
  fi
}

function check_dependencies(){
  command -v kubectl >/dev/null 2>&1 || { echo "kubectl is not installed. Aborting." >&2; exit 1; }
  command -v jq >/dev/null 2>&1 || { echo "jq is not installed. Aborting." >&2; exit 1; }
  command -v yq >/dev/null 2>&1 || { echo "yq is not installed. Aborting." >&2; exit 1; }
  command -v git >/dev/null 2>&1 || { echo "git is not installed. Aborting." >&2; exit 1; }
  command -v git-crypt >/dev/null 2>&1 || { echo "git-crypt is not installed. Aborting." >&2; exit 1; }
  command -v gpg >/dev/null 2>&1 || { echo "gpg is not installed. Aborting." >&2; exit 1; }
}

function check_cluster_and_access(){
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
}

function generate_password(){
  gpg --gen-random --armor 1 14
}

################## FUNCTION DEFINITION END ####################

# initial checks
check_dependencies
check_cluster_and_access
check_is_already_initialized
check_required_environment_variables


# static.json configs (dev dev1)

# DEPLOYER SSH KEY SETUP
if [[ ! -f deployer_ssh_key ]]; then
  echo "Generating git deployer ssh key ..."
  ssh-keygen -b 2048 -t rsa -f deployer_ssh_key -q -N ""
  echo "Finished generating git deployer ssh key."
else
  echo "git deployer ssh key is already present on machine. Skipping generation."
fi

# GPG setup
if [[ ! -f gpg_private.key ]]; then
  echo "Generating deployer gpg key ..."
  # list all local gpg keys
  # gpg -k

  # generate a gpg key for the deployer user
  random_number="$((1 + $RANDOM % 1000))"
  GIT_DEPLOYER_EMAIL="deployer${random_number}@${BASE_DOMAIN}"
  echo "GPG GIT_DEPLOYER_EMAIL: $GIT_DEPLOYER_EMAIL"
  gpg --batch --passphrase '' --quick-gen-key "$GIT_DEPLOYER_EMAIL" default default

  # gpg --list-secret-keys "$GIT_DEPLOYER_EMAIL"

  # export private key
  gpg --export-secret-keys "$GIT_DEPLOYER_EMAIL" > gpg_private.key

  # export public key
  gpg --export -a "$GIT_DEPLOYER_EMAIL" > gpg_public_key.gpg

  # for importing:

  # private key
  # gpg --import gpg_private.key

  # public key
  # gpg --import gpg_public_key.gpg
  echo "Finished generating deployer gpg key."
else
  echo "git deployer gpg key is already present on machine. Skipping generation."
fi

# for transforming to base64
## ATTENTION: base64 behaves differently on some Operating system
GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64=""
GIT_DEPLOYER_PRIVATE_KEY_BASE64=""
if [[ "$(uname -a |grep -o Darwin | head -n1)" == "Darwin"  ]]; then
  ### OS X Variant
  GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64="$(cat gpg_private.key | openssl base64 -A)"
  GIT_DEPLOYER_PRIVATE_KEY_BASE64="$(cat deployer_ssh_key | openssl base64 -A)"
else
  ### linux variant
  GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64"$(cat gpg_private.key | base64 -w 0)"
  GIT_DEPLOYER_PRIVATE_KEY_BASE64="$(cat deployer_ssh_key | base64 -w 0)"
fi

GIT_DEPLOYER_GPG_PUBLIC_KEY="$(cat gpg_public_key.gpg)"

# Configure platform_config/dev/static.json
echo "Writing platform_config/dev/static.json"
jq \
  --arg automation_git_url "$AUTOMATION_GIT_URL" \
  --arg automation_git_server_host "$AUTOMATION_GIT_SERVER_HOST" \
  --arg automation_git_server_port "$AUTOMATION_GIT_SERVER_PORT" \
  --arg automation_git_server_ssh_user "$AUTOMATION_GIT_SERVER_SSH_USER" \
  --arg docker_registry_base_url "$DOCKER_REGISTRY_BASE_URL" \
  --arg base_domain "$BASE_DOMAIN" \
  --arg git_deployer_gpg_public_key "$GIT_DEPLOYER_GPG_PUBLIC_KEY" \
  --arg git_deployer_email "$GIT_DEPLOYER_EMAIL" \
  --arg ssh_public_key "$(cat deployer_ssh_key.pub)" \
  --arg tekton_kubernetes_storage_class "$KUBERNETES_DEFAULT_STORAGE_CLASS" \
  '.AUTOMATION_GIT_URL |= $automation_git_url | .AUTOMATION_GIT_SERVER_HOST |= $automation_git_server_host | .AUTOMATION_GIT_SERVER_PORT |= $automation_git_server_port | .AUTOMATION_GIT_SERVER_SSH_USER |= $automation_git_server_ssh_user | .DOCKER_REGISTRY_BASE_URL |= $docker_registry_base_url | .BASE_DOMAIN |= $base_domain | .GIT_DEPLOYER_GPG_PUBLIC_KEY |= $git_deployer_gpg_public_key | .GIT_DEPLOYER_EMAIL |= $git_deployer_email | .GIT_DEPLOYER_PUBLIC_KEY |= $ssh_public_key | .TEKTON_KUBERNETES_STORAGE_CLASS |= $tekton_kubernetes_storage_class' \
  platform_config/dev/static.json.template >platform_config/dev/static.json

# Configure platform_config/dev/static.encrypted.json
echo "Writing platform_config/dev/static.encrypted.json"
jq \
  --arg gpg_private_key "$GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64" \
  --arg ssh_private_key "$GIT_DEPLOYER_PRIVATE_KEY_BASE64" \
  --arg grafana_password "$(generate_password)" \
  --arg gitlab_webhook_secret "$(generate_password)" \
  '.GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64 |= $gpg_private_key | .GIT_DEPLOYER_PRIVATE_KEY_BASE64 |= $ssh_private_key | .GRAFANA_ADMIN_PASSWORD |= $grafana_password | .GITLAB_WEBHOOK_SECRET |= $gitlab_webhook_secret' \
  platform_config/dev/static.encrypted.json.template >platform_config/dev/static.encrypted.json

# Configure platform_config/dev/dev1/static.json
echo "Writing platform_config/dev/dev1/static.json"
jq \
  --arg storage_class "$KUBERNETES_DEFAULT_STORAGE_CLASS" \
  '.CASSANDRA_VOLUME_STORAGE_CLASS |= $storage_class | .KAFKA_VOLUME_STORAGE_CLASS |= $storage_class | .MONGODB_VOLUME_STORAGE_CLASS |= $storage_class | .MYSQL_VOLUME_STORAGE_CLASS |= $storage_class | .REDIS_VOLUME_STORAGE_CLASS |= $storage_class' \
  platform_config/dev/dev1/static.json.template >platform_config/dev/dev1/static.json

# Configure platform_config/dev/dev1/static.encrypted.json
echo "Writing platform_config/dev/dev1/static.encrypted.json"
jq \
  --arg cassandra_pw "$(generate_password)" \
  --arg mongodb_pw "$(generate_password)" \
  --arg mysql_database_pw "$(generate_password)" \
  --arg mysql_root_pw "$(generate_password)" \
  --arg redis_pw "$(generate_password)" \
  '.CASSANDRA_ADMIN_PASSWORD |= $cassandra_pw | .MONGODB_ROOT_PASSWORD |= $mongodb_pw | .MYSQL_DATABASE_CONFIGURATION[0].PASSWORD |= $mysql_database_pw | .MYSQL_ROOT_PASSWORD |= $mysql_root_pw | .REDIS_PASSWORD |= $redis_pw' \
  platform_config/dev/dev1/static.encrypted.json.template >platform_config/dev/dev1/static.encrypted.json


if [[ ! -f git_crypt_symmetric.key ]]; then
  # GIT CRYPT SETUP
  echo "Initializing git-crypt ..."
  # initialize (only executed when initializing a new repo with git crypt)
  git-crypt init

  # add previously generated deployer user to git-crypt keystore
  git-crypt add-gpg-user "$GIT_DEPLOYER_EMAIL"

  # optional: add another key
  # git-crypt add-gpg-user "your@email.com"

  # show git-crypt encryption status
  # git-crypt status

  # optional: export a symmetric key for git crypt
  git-crypt export-key git_crypt_symmetric.key

  # if you have a private key registered
  # git-crypt unlock

  # for the symmetric key
  git-crypt unlock git_crypt_symmetric.key

  echo "Finished initializing git-crypt."
else
  echo "Found git_crypt_symmetric.key . Git crypt seems to be configured already. Skipping git-crypt configuration."
fi

# start cluster installation
echo "Executing setup on cluster"
pushd tekton_ci/automation
./00_full_setup.sh dev dev1
popd
echo "Finished executing setup on cluster"


# Final output
echo "##############################"
echo "Please ensure to push this repo to a git server:"
echo "git remote add upstream $AUTOMATION_GIT_URL"
echo "git add ."
echo "git commit -m '00 - initial configuration'"
echo "git push upstream master"
echo ""
echo "And configure the git server to allow at minimum read access to the generated deployer_ssh_key.pub :"
cat deployer_ssh_key.pub
echo ""
echo "##############################"
echo ""
echo "Finished initialization successfully!"
echo "If you have any questions, feel free to create an issue via https://github.com/julweber/easy_tekton/issues"
echo ""
echo "##############################"
