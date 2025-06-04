#!/usr/bin/env bash

#
# This script can be used to create k8s secerts within a defined namespace
# with flat json files as input to define data keys
# e.g.: it can be used to allow creating terraform variable secrets from *.tfvars.json files
#

# load helpers
source "${0%/*}/base64_library.sh"

NAMESPACE="$1"
SECRET_NAME="$2"
JSON_STRING="$3"
if [[ "$NAMESPACE" == "" || "$SECRET_NAME" == "" || "$JSON_STRING" == "" ]]; then
  echo "Usage: create_secret_from_json.sh <NAMESPACE> <SECRET_NAME> <JSON_STRING>"
  echo "e.g.: create_secret_from_json.sh dev1-pipelines terraform-main-automation-secret '{\"TF_VAR_my_variable\":\"my_value\",\"TF_VAR_my_var2\":123}'"
  exit 1
fi

set -eu

# This is needed for being able to checkout git repositories via the git-clone-with-ssh-auth task
# IMPORTANT: You need to configure the public key to the according private key
# in the according git repositories for enabling access.
echo "#########################"
echo "Configuring secret with name: $SECRET_NAME in namespace: $NAMESPACE ..."
apply_yaml="$(kubectl -n "$NAMESPACE" create secret generic --dry-run='client' "$SECRET_NAME" -o yaml --from-env-file <(echo "$JSON_STRING" | jq -r "to_entries|map(\"\(.key)=\(.value|tostring)\")|.[]"))"
echo "$apply_yaml" | kubectl apply -n "$NAMESPACE" -f -

echo ""
echo "Available secrets in namespace $NAMESPACE :"
kubectl -n "$NAMESPACE" get secrets