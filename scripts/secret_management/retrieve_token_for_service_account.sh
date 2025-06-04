#!/usr/bin/env bash

#
# This script retrieves and outputs a token for a given service account
# e.g. for accessing the kubernetes dashboard
NAMESPACE="$1"
SERVICE_ACCOUNT_NAME="$2"

if [[ "$NAMESPACE" == "" || "$SERVICE_ACCOUNT_NAME" == "" ]]; then
  echo "Usage: retrieve_token_for_service_account.sh <NAMESPACE> <SERVICE_ACCOUNT_NAME>"
  echo "e.g.: retrieve_token_for_service_account.sh kubernetes-dashboard kubernetes-dashboard-read-only-cluster-user"
  exit 1
fi

set -eu

# retrieve token
secrets="$(kubectl -n "$NAMESPACE" get secret)"
# echo "$secrets"
token_name="$(echo "$secrets" |grep "${SERVICE_ACCOUNT_NAME}-token" | awk '{ print $1 }')"
echo "Found token: $token_name"
token="$(kubectl -n "$NAMESPACE" get secret "$token_name" --output="jsonpath={.data.token}" | base64 --decode)"
echo ""
echo "Token for service account $SERVICE_ACCOUNT_NAME:"
echo ""
echo "$token"