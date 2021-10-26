#!/usr/bin/env bash

##
## This script generates a new ssh keypair and display the according information on screen
## It will generate the keypair in the directory from where the script is called with the given KEYNAME as filename
##

# load helpers
source "${0%/*}/base64_library.sh"

KEYNAME="$1"

if [[ "$KEYNAME" == "" ]]; then
  echo "Usage: generate_ssh_key.sh <KEYNAME>"
  echo "e.g.: generate_ssh_key.sh deployerkey1"
  exit 1
fi

if [[ ! -f $KEYNAME ]]; then
  echo "Generating ssh key with name: $KEYNAME ..."
  ssh-keygen -b 2048 -t rsa -f "$KEYNAME" -q -N ""
  echo "Finished generating git deployer ssh key."
else
  echo "$KEYNAME ssh key file is already present within the directory. Skipping generation."
fi

echo "Key information:"
echo "----------------"
echo "SSH Public key:"
cat "${KEYNAME}.pub"
echo ""
echo "----------------"
echo "Base64 encoded SSH private key:"
private_key_contents="$(cat "${KEYNAME}")"
base64_encode "$private_key_contents"
echo ""