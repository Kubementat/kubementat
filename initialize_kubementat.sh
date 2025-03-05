#!/usr/bin/env bash

####
# This script initializes a basic configuration for getting up and running with kubementat automations and platform setup
####
set -e

TARGET_ENVIRONMENT="dev"
TARGET_TEAM="dev1"

################## FUNCTION DEFINITIONS ####################
function check_is_already_initialized(){
  if [[ -f platform_config/$TARGET_ENVIRONMENT/static.encrypted.json ]]; then
    echo "Found platform_config/$TARGET_ENVIRONMENT/static.encrypted.json . It seems that kubementat has been initialized already! Exiting"
    exit 1
  elif [[ -f platform_config/$TARGET_ENVIRONMENT/static.json ]]; then
    echo "Found platform_config/$TARGET_ENVIRONMENT/static.json . It seems that kubementat has been initialized already! Exiting"
    exit 1
  elif [[ -f platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.json ]]; then
    echo "Found platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.json . It seems that kubementat has been initialized already! Exiting"
    exit 1
  elif [[ -f platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.encrypted.json ]]; then
    echo "Found platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.encrypted.json . It seems that kubementat has been initialized already! Exiting"
    exit 1
  else
    echo "kubementat is not initialized yet! Starting basic configuration."
  fi
}

function check_required_environment_variables(){
  if [[ -z "$BASE_DOMAIN" || -z "$AUTOMATION_GIT_URL" || -z "$AUTOMATION_GIT_SERVER_HOST" || -z "$AUTOMATION_GIT_SERVER_PORT" || -z "$AUTOMATION_GIT_SERVER_SSH_USER" || -z "$KUBERNETES_DEFAULT_STORAGE_CLASS" || -z "$DOCKER_REGISTRY_BASE_URL" || -z "$CLUSTER_MANAGER_EMAIL" ]]; then
    echo "Unsufficient environment configuration provided! Exiting."
    echo "Please define the required according ENVIRONMENT variables via exports:"
    echo "e.g.:"
    echo "export BASE_DOMAIN='example.com'"
    echo "export AUTOMATION_GIT_URL='git@github.com:Kubementat/kubementat.git'"
    echo "export AUTOMATION_GIT_SERVER_HOST='github.com'"
    echo "export AUTOMATION_GIT_SERVER_PORT='22'"
    echo "export AUTOMATION_GIT_SERVER_SSH_USER='git'"
    echo "export KUBERNETES_DEFAULT_STORAGE_CLASS='local-path'"
    echo "export DOCKER_REGISTRY_BASE_URL='docker.io/julianweberdev'"
    echo "export CLUSTER_MANAGER_EMAIL='yourmail@example.com'"
    echo ""
    echo "Then run this script via:"
    echo "./initialize_kubementat.sh"
    exit 1
  fi
}

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
  echo "Finished checking local dependencies"
  echo "################"
  echo ""
}

function print_cli_versions(){
  echo ""
  echo "CLI VERSIONS:"
  echo ""
  echo "kubectl: $(kubectl version)"
  echo "helm: $(helm version)"
  echo "helmfile: $(helmfile version)"
  echo "jq: $(jq --version)"
  echo "yq: $(yq --version)"
  echo "git: $(git --version)"
  echo "git-crypt: $(git-crypt --version)"
  echo "gpg: $(gpg --version)"
  echo "################"
  echo ""
}

function generate_password(){
  gpg --gen-random --armor 1 14
}

function does_file_with_pattern_exist {
   local arg="$*"
   local files=($arg)
   [ ${#files[@]} -gt 1 ] || [ ${#files[@]} -eq 1 ] && [ -e "${files[0]}" ]
}

# writes directories and config files from .template files for given directory
function write_config_from_templates_for_directory(){
  local source_directory="$1"
  local target_directory="$2"

  echo ""
  echo "template source directory: $source_directory"
  echo "target directory: $target_directory"
  echo ""
  for directory in "$source_directory"/* ; do
    # for each directory
    if [[ -d "$directory" ]]; then
      dir_name="$(basename "$directory")"
      echo "Creating directory: $target_directory/$dir_name"
      mkdir -p "$target_directory/$dir_name"
      echo ""

      # for each *.template file within the directory
      for template_file in "$source_directory/$dir_name"/*.template ; do
        template_file_name="$(basename "$template_file")"
        new_filename="${template_file_name//.template/}"
        new_path="$target_directory/$dir_name/$new_filename"
        echo "  Copy template: $template_file --> $new_path"
        cp "$template_file" "$new_path"
        echo ""
      done
    fi
  done
  echo ""
}

################## FUNCTION DEFINITION END ####################

# initial checks
check_required_environment_variables
check_is_already_initialized
check_dependencies
print_cli_versions

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
  GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64="$(cat gpg_private.key | base64 -w 0)"
  GIT_DEPLOYER_PRIVATE_KEY_BASE64="$(cat deployer_ssh_key | base64 -w 0)"
fi

GIT_DEPLOYER_GPG_PUBLIC_KEY="$(cat gpg_public_key.gpg)"

# Configure platform_config/$TARGET_ENVIRONMENT/static.json
echo "Creating config sub-directory: platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM"
mkdir -p "platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM"

echo "Writing platform_config/$TARGET_ENVIRONMENT/static.json"
jq \
  --arg automation_git_url "$AUTOMATION_GIT_URL" \
  --arg automation_git_server_host "$AUTOMATION_GIT_SERVER_HOST" \
  --arg automation_git_server_port "$AUTOMATION_GIT_SERVER_PORT" \
  --arg automation_git_server_ssh_user "$AUTOMATION_GIT_SERVER_SSH_USER" \
  --arg docker_registry_base_url "$DOCKER_REGISTRY_BASE_URL" \
  --arg cluster_manager_email "$CLUSTER_MANAGER_EMAIL" \
  --arg base_domain "$BASE_DOMAIN" \
  --arg git_deployer_gpg_public_key "$GIT_DEPLOYER_GPG_PUBLIC_KEY" \
  --arg git_deployer_email "$GIT_DEPLOYER_EMAIL" \
  --arg ssh_public_key "$(cat deployer_ssh_key.pub)" \
  --arg tekton_kubernetes_storage_class "$KUBERNETES_DEFAULT_STORAGE_CLASS" \
  '.AUTOMATION_GIT_URL |= $automation_git_url | .AUTOMATION_GIT_SERVER_HOST |= $automation_git_server_host | .AUTOMATION_GIT_SERVER_PORT |= $automation_git_server_port | .AUTOMATION_GIT_SERVER_SSH_USER |= $automation_git_server_ssh_user | .DOCKER_REGISTRY_BASE_URL |= $docker_registry_base_url | .BASE_DOMAIN |= $base_domain | .GIT_DEPLOYER_GPG_PUBLIC_KEY |= $git_deployer_gpg_public_key | .GIT_DEPLOYER_EMAIL |= $git_deployer_email | .GIT_DEPLOYER_PUBLIC_KEY |= $ssh_public_key | .TEKTON_KUBERNETES_STORAGE_CLASS |= $tekton_kubernetes_storage_class | .CLUSTER_MANAGER_EMAIL |= $cluster_manager_email' \
  templates/environment/static.json.template > "platform_config/$TARGET_ENVIRONMENT/static.json"

# Configure platform_config/$TARGET_ENVIRONMENT/static.encrypted.json
echo "Writing platform_config/$TARGET_ENVIRONMENT/static.encrypted.json"
jq \
  --arg gpg_private_key "$GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64" \
  --arg ssh_private_key "$GIT_DEPLOYER_PRIVATE_KEY_BASE64" \
  --arg grafana_password "$(generate_password)" \
  '.GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64 |= $gpg_private_key | .GIT_DEPLOYER_PRIVATE_KEY_BASE64 |= $ssh_private_key | .GRAFANA_ADMIN_PASSWORD |= $grafana_password' \
  templates/environment/static.encrypted.json.template >platform_config/$TARGET_ENVIRONMENT/static.encrypted.json

# configure template for docker image mirroring
echo "Writing platform_config/$TARGET_ENVIRONMENT/mirrored_docker_images.json"
cp templates/environment/mirrored_docker_images.json.template platform_config/$TARGET_ENVIRONMENT/mirrored_docker_images.json

# Configure platform_config/$TARGET_ENVIRONMENT/dev1/static.json
echo "Writing platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.json"
jq \
  --arg storage_class "$KUBERNETES_DEFAULT_STORAGE_CLASS" \
  '.POSTGRES_VOLUME_STORAGE_CLASS |= $storage_class | .MONGODB_VOLUME_STORAGE_CLASS |= $storage_class | .MYSQL_VOLUME_STORAGE_CLASS |= $storage_class | .REDIS_VOLUME_STORAGE_CLASS |= $storage_class' \
  templates/environment/team/static.json.template >platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.json

# Configure platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.encrypted.json
echo "Writing platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.encrypted.json"
jq \
  --arg mongodb_pw "$(generate_password)" \
  --arg mysql_database_pw "$(generate_password)" \
  --arg mysql_root_pw "$(generate_password)" \
  --arg redis_pw "$(generate_password)" \
  --arg postgres_pw "$(generate_password)" \
  --arg gitlab_webhook_secret "$(generate_password)" \
  --arg github_webhook_secret "$(generate_password)" \
  '.POSTGRES_ADMIN_PASSWORD |= $postgres_pw | .MONGODB_ROOT_PASSWORD |= $mongodb_pw | .MYSQL_DATABASE_CONFIGURATION[0].PASSWORD |= $mysql_database_pw | .MYSQL_ROOT_PASSWORD |= $mysql_root_pw | .REDIS_PASSWORD |= $redis_pw | .GITLAB_WEBHOOK_SECRET |= $gitlab_webhook_secret | .GITHUB_WEBHOOK_SECRET |= $github_webhook_secret' \
  templates/environment/team/static.encrypted.json.template >platform_config/$TARGET_ENVIRONMENT/$TARGET_TEAM/static.encrypted.json
echo ""
echo "#####################"

# copy over default helm chart and platform component configuration files from templates
echo "Configuring default platform_config values files for dev platform components ..."

echo "Creating kubementat_components sub-directory: platform_config/$TARGET_ENVIRONMENT/kubementat_components"
mkdir -p "platform_config/$TARGET_ENVIRONMENT/kubementat_components"
# copy over kubementat_components helmfile.yaml
cp templates/environment/kubementat_components/helmfile.yaml.template platform_config/$TARGET_ENVIRONMENT/kubementat_components/helmfile.yaml

write_config_from_templates_for_directory "templates/environment/kubementat_components" "platform_config/${TARGET_ENVIRONMENT}/kubementat_components"
echo "#####################"
echo ""

echo "Configuring default platform_config values files for team dev1 ..."
write_config_from_templates_for_directory "templates/environment/team" "platform_config/${TARGET_ENVIRONMENT}/${TARGET_TEAM}"
echo "#####################"

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
echo "If you have any questions, feel free to create an issue via https://github.com/Kubementat/kubementat/issues"
echo ""
echo "##############################"
echo ""
echo "You can now use this configuration to roll out the platform components on the cluster via:"
echo ""
echo "./install_kubementat.sh $TARGET_ENVIRONMENT $TARGET_TEAM"
echo ""
echo "In case you encounter error messages you can just rerun the full setup script to continue where the error occured."
echo "The setup script is non-destructive"
echo "##############################"
echo ""
echo "HAVE FUN WITH YOUR AUTOMATED KUBERNETES CLUSTER :D"
