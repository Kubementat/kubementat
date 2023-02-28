#!/usr/bin/env bash

#################################
#
# This script installs the routing helm charts into the provided environment.
# - nginx-ingress-controller
# - cert-manager
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_routing.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_routing.sh dev"
  exit 1
fi

set -u

echo "#########################"
date
echo "Loading configuration from platform_config ..."
CLUSTER_MANAGER_EMAIL="$(jq -r '.CLUSTER_MANAGER_EMAIL' ../../../platform_config/"${ENVIRONMENT}"/static.json)"

#### helmfile apply
date
# helmfile apply
./helmfile_apply.sh "${ENVIRONMENT}" 'group=routing' "true"

########## LETSENCRYPT CLUSTER ISSUER ################
echo "Configuring Cluster Issuers for letsencrypt..."

echo "Configuring letsencrypt staging cluster issuer..."
kubectl apply  -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory # letsencrypt staging endpoint
    email: $CLUSTER_MANAGER_EMAIL
    # Secret resource that will be used to store the account's private key.
    privateKeySecretRef:
      name: letsencrypt
    # Add a single challenge solver, HTTP01 using nginx
    solvers:
    - http01:
        ingress:
          class: nginx
EOF

echo "Configuring letsencrypt production cluster issuer..."
kubectl apply  -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory # production
    email: $CLUSTER_MANAGER_EMAIL
    # Secret resource that will be used to store the account's private key.
    privateKeySecretRef:
      name: letsencrypt
    # Add a single challenge solver, HTTP01 using nginx
    solvers:
    - http01:
        ingress:
          class: nginx
EOF

kubectl get clusterissuer