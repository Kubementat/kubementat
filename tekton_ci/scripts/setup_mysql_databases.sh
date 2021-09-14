#!/usr/bin/env bash

#################################
#
# This script sets a previously installed mysql service
# It creates according databases and users as defined in the env/team platform_config
#
#################################

set -e

ENVIRONMENT="$1"
TEAM="$2"

if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: setup_mysql_databases.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: setup_mysql_databases.sh dev dev2 true"
  exit 1
fi

set -u


echo "#########################"
echo "Loading configuration from platform_config ..."
APP_DEPLOYMENT_NAMESPACE="$(jq -r '.APP_DEPLOYMENT_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"
MYSQL_DATABASE_NAME="$(jq -r '.MYSQL_DATABASE_NAME' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
MYSQL_HOST="$(jq -r '.MYSQL_HOST' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
MYSQL_ROOT_PASSWORD="$(jq -r '.MYSQL_ROOT_PASSWORD' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEAM: $TEAM"
echo "APP_DEPLOYMENT_NAMESPACE: $APP_DEPLOYMENT_NAMESPACE"
echo "MYSQL_HOST: $MYSQL_HOST"
echo "MYSQL_DATABASE_NAME: $MYSQL_DATABASE_NAME"
echo "#########################"

echo "Iterating through db configuration..."

SQL_COMMANDS="SHOW DATABASES;"
MYSQL_DATABASE_CONFIGURATION="$(jq -r '.MYSQL_DATABASE_CONFIGURATION' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.encrypted.json)"
for row in $(echo "${MYSQL_DATABASE_CONFIGURATION}" | jq -r '.[] | @base64'); do
    _jq() {
     echo ${row} | base64 --decode | jq -r ${1}
    }

   name="$(_jq '.DATABASE_NAME')"
   user="$(_jq '.USERNAME')"
   password="$(_jq '.PASSWORD')"

   echo "Generating commands for database: ${name} and user: ${user} ..."

   COMMAND="CREATE DATABASE IF NOT EXISTS \`$name\`; CREATE USER IF NOT EXISTS '${user}'@'%' IDENTIFIED BY '${password}';  GRANT ALL PRIVILEGES ON \`${name}\`.* TO '${user}'@'%' WITH GRANT OPTION;"
   SQL_COMMANDS="${SQL_COMMANDS} ${COMMAND}"
done
SQL_COMMANDS="${SQL_COMMANDS} SHOW DATABASES;"

echo "Waiting for cluster to be available..."
CONNECT_TIMEOUT_LOOPS_MAX=40
LOOP_NUMBER=1
SLEEP_TIME=5
while ! mysql -h "$MYSQL_HOST" -uroot --password="$MYSQL_ROOT_PASSWORD" -e "SHOW DATABASES;"; do
  echo "Connection try number: $LOOP_NUMBER"
  if [ "$LOOP_NUMBER" -gt "$CONNECT_TIMEOUT_LOOPS_MAX" ]; then
    echo "Reached max loop number $CONNECT_TIMEOUT_LOOPS_MAX. Failed to connect to mysql instance"
    exit 1
  fi
  echo "Could not establish connection to ${MYSQL_HOST} ..."
  echo "Sleeping ${SLEEP_TIME} seconds..."
  sleep $SLEEP_TIME
  LOOP_NUMBER="$(($LOOP_NUMBER + 1))"
done

echo "Connection available."

echo "Executing SQL setup commands..."
mysql -h "$MYSQL_HOST" -uroot --password="$MYSQL_ROOT_PASSWORD" -e "$SQL_COMMANDS"