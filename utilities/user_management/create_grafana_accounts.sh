#!/usr/bin/env bash

#################################
#
# This script creates grafana users from platform_config/ENV/static.encrypted.json -> GRAFANA_USERS
#
#################################

set -e

ENVIRONMENT="$1"
GRAFANA_BASE_URL="$2"

DEFAULT_GRAFANA_BASE_URL="http://127.0.0.1:31827"

if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: create_grafana_accounts.sh <ENVIRONMENT> <GRAFANA_BASE_URL (optional)>"
  echo "e.g.: create_grafana_accounts.sh dev 'http://127.0.0.1:31827'"
  exit 1
fi

if [[ "$GRAFANA_BASE_URL" == "" ]]; then
  echo "You did not provide a GRAFANA_BASE_URL. It will default to $DEFAULT_GRAFANA_BASE_URL"
  GRAFANA_BASE_URL="$DEFAULT_GRAFANA_BASE_URL"
fi

set -u


echo "#########################"
echo "Loading configuration from platform_config ..."
GRAFANA_USERS="$(jq -r '.GRAFANA_USERS' "../../platform_config/${ENVIRONMENT}/static.encrypted.json")"

for row in $(echo "${GRAFANA_USERS}" | jq -r '.[] | @base64'); do
    _jq() {
     echo ${row} | base64 --decode | jq -r ${1}
    }
    echo "Creating user for email: $(_jq '.email')"
    ./create_grafana_read_only_account.sh "$ENVIRONMENT" "$(_jq '.email')" "$(_jq '.name')" "$(_jq '.password')" "$GRAFANA_BASE_URL"
done