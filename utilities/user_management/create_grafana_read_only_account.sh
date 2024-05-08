#!/usr/bin/env bash

#################################
#
# This script creates a grafana user with read only access rights for the given email address in the given environment
#
#################################

set -e

source ./grafana_helpers.sh

ENVIRONMENT="$1"
EMAIL="$2"
NAME="$3"
PASSWORD="$4"
GRAFANA_BASE_URL="$5"

DEFAULT_GRAFANA_BASE_URL="http://127.0.0.1:31827"
DEFAULT_TEAM_ID="1"

if [[ "$ENVIRONMENT" == "" || "$EMAIL" == "" || "$NAME" == "" ]]; then
  echo "Usage: create_grafana_read_only_account.sh <ENVIRONMENT> <EMAIL> <NAME> <PASSWORD (optional)> <GRAFANA_BASE_URL (optional)>"
  echo "e.g.: create_grafana_read_only_account.sh dev 'mranderson@something.com' 'ThomasAnderson' 'secrethere' 'http://127.0.0.1:31827'"
  exit 1
fi

if [[ "$GRAFANA_BASE_URL" == "" ]]; then
  echo "You did not provide a GRAFANA_BASE_URL. It will default to $DEFAULT_GRAFANA_BASE_URL"
  GRAFANA_BASE_URL="$DEFAULT_GRAFANA_BASE_URL"
fi

set -u


echo "#########################"
echo "Loading configuration from platform_config ..."
GRAFANA_SERVICE_ACCOUNT_TOKEN="$(jq -r '.GRAFANA_SERVICE_ACCOUNT_TOKEN' "../../platform_config/${ENVIRONMENT}/static.encrypted.json")"
GRAFANA_ADMIN_USER="$(jq -r '.GRAFANA_ADMIN_USER' "../../platform_config/${ENVIRONMENT}/static.encrypted.json")"
GRAFANA_ADMIN_PASSWORD="$(jq -r '.GRAFANA_ADMIN_PASSWORD' "../../platform_config/${ENVIRONMENT}/static.encrypted.json")"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "EMAIL: $EMAIL"
echo "GRAFANA_BASE_URL: $GRAFANA_BASE_URL"
echo "#########################"
echo ""


# imported from grafana_helpers.sh
create_new_user_for_team "$EMAIL" "$NAME" "$DEFAULT_TEAM_ID" "$PASSWORD"

