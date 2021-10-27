#!/usr/bin/env bash

#################################
#
# This script will take the configured ssh keys in platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json
# and configure according secrets within the k8s cluster
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: configure_secrets.sh <ENVIRONMENT> <TEAM>"
  echo "e.g.: configure_secrets.sh dev dev1"
  exit 1
fi

set -u

CONFIG_FILE="../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json"
SECRET_TYPE="kubernetes.io/ssh-auth"
DATA_KEY_PRIVATE_KEY="ssh-privatekey"

echo "Loading ssh deploy keys from $CONFIG_FILE ..."
SSH_DEPLOY_KEYS="$(jq -r '.SSH_DEPLOY_KEYS' "$CONFIG_FILE")"

echo "Iterating through configured ssh keys..."
for row in $(echo "${SSH_DEPLOY_KEYS}" | jq -r '.[] | @base64'); do
  _jq() {
    echo ${row} | base64 --decode | jq -r ${1}
  }

  name="$(_jq '.NAME')"
  namespace="$(_jq '.TARGET_NAMESPACE')"
  secret_name="$(_jq '.TARGET_SECRET_NAME')"
  private_key_base64="$(_jq '.PRIVATE_KEY_BASE64')"
  echo "Configuring ssh key: $name as secret: $secret_name in namespace: $namespace ..."

  kubectl apply -n "$namespace" -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: $secret_name
  labels:
    team: $TEAM
    environment: $ENVIRONMENT
    key_name: $name
  annotations:
    team: $TEAM
    environment: $ENVIRONMENT
    key_name: $name
type: $SECRET_TYPE
data:
  ${DATA_KEY_PRIVATE_KEY}: >-
    $private_key_base64
EOF

  kubectl -n "$namespace" describe secret "$secret_name"
  echo "#####################"
  echo ""
done