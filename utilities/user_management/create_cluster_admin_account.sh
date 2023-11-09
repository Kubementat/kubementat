#!/usr/bin/env bash

#################################
#
# This script creates a cluster wide admin service account (user account) within the given environment
#
#################################

set -e

source ./user_management_helpers.sh

ENVIRONMENT="$1"
SERVICE_ACCOUNT_NAME="$2"

if [[ "$ENVIRONMENT" == "" || "$SERVICE_ACCOUNT_NAME" == "" ]]; then
  echo "Usage: create_cluster_admin_account.sh <ENVIRONMENT> <SERVICE_ACCOUNT_NAME>"
  echo "e.g.: create_cluster_admin_account.sh dev superadmin"
  exit 1
fi

set -u

check_cluster_and_access

echo "#########################"
echo "Loading configuration from platform_config ..."

echo "ENVIRONMENT: $ENVIRONMENT"
echo "#########################"

echo "Configuring roles..."
# Creating namespace-admin role in app and pipeline namespaces
ROLE_NAME="cluster-admin"

# TODO: Implement the rest