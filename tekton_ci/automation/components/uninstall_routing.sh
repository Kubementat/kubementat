#!/usr/bin/env bash

######################################
#
# This script removes the routing components from the cluster in ENVIRONMENT
# - cert manager
# - nginx-ingress-controller
#
######################################

set -e

./uninstall_helm_deployment.sh "$1" "CERT_MANAGER"
./uninstall_helm_deployment.sh "$1" "NGINX_INGRESS_CONTROLLER"