#!/usr/bin/env bash

#################################
#
# This script installs tekton on a k8s cluster
# It installs: tekton, tekton-dashboard and tekton-triggers to the cluster
# It also configures a storage class to use for tekton workspaces
#
#################################

set -e

ENVIRONMENT="$1"
if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: install_tekton.sh <ENVIRONMENT_NAME>"
  echo "e.g.: install_tekton.sh dev"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
TEKTON_NAMESPACE="tekton-pipelines"
TEKTON_VERSION_PIPELINE="$(jq -r '.TEKTON_VERSION_PIPELINE' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
TEKTON_VERSION_TRIGGERS="$(jq -r '.TEKTON_VERSION_TRIGGERS' ../../../platform_config/"${ENVIRONMENT}"/static.json)"
TEKTON_VERSION_DASHBOARD="$(jq -r '.TEKTON_VERSION_DASHBOARD' ../../../platform_config/"${ENVIRONMENT}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "TEKTON_VERSION_PIPELINE: $TEKTON_VERSION_PIPELINE"
echo "TEKTON_VERSION_TRIGGERS: $TEKTON_VERSION_TRIGGERS"
echo "TEKTON_VERSION_DASHBOARD: $TEKTON_VERSION_DASHBOARD"
echo "#########################"

# install tekton pipeline resources
kubectl apply -f "https://storage.googleapis.com/tekton-releases/pipeline/previous/v${TEKTON_VERSION_PIPELINE}/release.yaml"

# this will allow access to the needed resources for helm deployments
# and allow to request tekton resources
echo "Configuring helm-deployer-cluster-role ..."
kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: helm-deployer-cluster-role
  labels:
    managed-by: kubementat
rules:
# TODO: implement more fine grained access
- apiGroups: ["", "rbac.authorization.k8s.io", "networking.k8s.io", "batch", "extensions", "apps", "autoscaling", "tekton.dev", "resolution.tekton.dev"]
  resources: ["*"]
  verbs: ["*"]
EOF

echo "########################"
echo "Role Configuration:"
kubectl describe clusterrole helm-deployer-cluster-role

# view storageclasses
kubectl get sc

echo "########################"
echo "Configuring tekton-dashboard..."
# install tekton dashboard
kubectl apply -n "$TEKTON_NAMESPACE" -f "https://storage.googleapis.com/tekton-releases/dashboard/previous/v${TEKTON_VERSION_DASHBOARD}/tekton-dashboard-release.yaml"

# OPTIONAL: configure tekton dashboard
# TEKTON_DASHBOARD_URL="dashboard.domain.tld"
# kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
# apiVersion: extensions/v1beta1
# kind: Ingress
# metadata:
#   name: tekton-dashboard-ingress
#   namespace: $TEKTON_NAMESPACE
#   annotations:
#     nginx.ingress.kubernetes.io/rewrite-target: /
#     kubernetes.io/ingress.class: nginx
#     nginx.ingress.kubernetes.io/ssl-redirect: "true"
# spec:
#   rules:
#   # - http:
#   #     paths:
#   #     - path: /tekton
#   #       backend:
#   #         serviceName: tekton-dashboard
#   #         servicePort: 9097
#   - host: $TEKTON_DASHBOARD_URL
#     http:
#       paths:
#       - backend:
#           serviceName: tekton-dashboard
#           servicePort: 9097
# EOF

# install tekton triggers
kubectl apply -n "$TEKTON_NAMESPACE" -f "https://storage.googleapis.com/tekton-releases/triggers/previous/v${TEKTON_VERSION_TRIGGERS}/release.yaml"
kubectl apply -n "$TEKTON_NAMESPACE" -f "https://storage.googleapis.com/tekton-releases/triggers/previous/v${TEKTON_VERSION_TRIGGERS}/interceptors.yaml"