#!/usr/bin/env bash

# load helpers
source "${0%/*}/base64_library.sh"

NAMESPACE="$1"
KEYNAME="$2"
if [[ "$NAMESPACE" == "" || "$KEYNAME" == "" ]]; then
  echo "Usage: create_k8s_secret_for_ssh_key.sh <NAMESPACE> <KEYNAME>"
  echo "e.g.: create_k8s_secret_for_ssh_key.sh dev1-pipelines deployerkey1"
  exit 1
fi

set -eu

if [[ ! -f $KEYNAME ]]; then
  echo "Could not find a key file with name $KEYNAME . ABORTING."
  exit 1
fi

SECRET_NAME="${KEYNAME}-ssh-secret"
# This is needed for being able to checkout git repositories via the git-clone-with-ssh-auth task
# IMPORTANT: You need to configure the public key to the according private key
# in the according git repositories for enabling access.
echo "#########################"
echo "Configuring ssh key secret with name: $SECRET_NAME in namespace: $NAMESPACE ..."
private_key_contents="$(cat "${KEYNAME}")"
PRIVATE_KEY_BASE64="$(base64_encode "$private_key_contents")"

kubectl apply -n "$NAMESPACE" -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: $SECRET_NAME
type: kubernetes.io/ssh-auth
data:
  ssh-privatekey: >-
    $PRIVATE_KEY_BASE64
EOF

echo "Finished configuring ssh key secret: $SECRET_NAME in namespace: $NAMESPACE"

echo ""
echo "Available secrets in namespace $NAMESPACE :"
kubectl -n "$NAMESPACE" get secrets