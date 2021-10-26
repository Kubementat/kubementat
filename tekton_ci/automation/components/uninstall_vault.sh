#!/usr/bin/env bash

######################################
#
# This script removes vault from the cluster in ENVIRONMENT
#
######################################

set -e

./uninstall_helm_deployment.sh "$1" "VAULT"