#!/usr/bin/env bash

######################################
#
# This script removes the logging components from the cluster in ENVIRONMENT
#
######################################

set -e

./uninstall_helm_deployment.sh "$1" "LOKI"