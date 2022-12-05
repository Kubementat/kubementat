#!/usr/bin/env bash

######################################
#
# This script sets up tekton triggers
#
######################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: setup_triggers.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: setup_triggers.sh dev dev1"
  exit 1
fi

set -u

echo "#########################"
echo "Loading configuration from platform_config ..."
TEKTON_NAMESPACE="$(jq -r '.TEKTON_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/static.json)"
PIPELINE_NAMESPACE="$(jq -r '.PIPELINE_NAMESPACE' ../../platform_config/"${ENVIRONMENT}"/"${TEAM}"/static.json)"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "TEKTON_NAMESPACE: $TEKTON_NAMESPACE"
echo "PIPELINE_NAMESPACE: $PIPELINE_NAMESPACE"
echo "#########################"
echo

echo "Setting up roles for tekton triggers ..."


echo "Setting tekton-triggers-sa ..."
kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tekton-triggers-sa
EOF

echo "Setting up tekton-triggers-role in namespace $TEKTON_NAMESPACE ..."
kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tekton-triggers-role
rules:
# EventListeners need to be able to fetch all namespaced resources
- apiGroups: ["triggers.tekton.dev"]
  resources: ["eventlisteners", "triggerbindings", "triggertemplates", "triggers"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
# configmaps is needed for updating logging config
  resources: ["configmaps"]
  verbs: ["get", "list", "watch"]
# Permissions to create resources in associated TriggerTemplates
- apiGroups: ["tekton.dev"]
  resources: ["pipelineruns", "pipelineresources", "taskruns"]
  verbs: ["create"]
- apiGroups: [""]
  resources: ["serviceaccounts"]
  verbs: ["impersonate"]
- apiGroups: ["policy"]
  resources: ["podsecuritypolicies"]
  resourceNames: ["tekton-triggers"]
  verbs: ["use"]
EOF


echo "Binding tekton-triggers-sa to tekton-triggers-role in namespace $TEKTON_NAMESPACE ..."
kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tekton-triggers-sa-to-role-binding
subjects:
- kind: ServiceAccount
  name: tekton-triggers-sa
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tekton-triggers-role
EOF

echo "Setting up tekton-triggers-pipeline-namespace-role in namespace $PIPELINE_NAMESPACE ..."
kubectl apply -n "$PIPELINE_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tekton-triggers-pipeline-namespace-role
rules:
# Permissions to create resources in associated TriggerTemplates
- apiGroups: ["tekton.dev"]
  resources: ["pipelineruns", "pipelineresources", "taskruns"]
  verbs: ["create"]
- apiGroups: [""]
  resources: ["serviceaccounts"]
  verbs: ["impersonate"]
- apiGroups: ["policy"]
  resources: ["podsecuritypolicies"]
  resourceNames: ["tekton-triggers"]
  verbs: ["use"]
EOF

echo "Binding tekton-triggers-sa to tekton-triggers-pipeline-namespace-role in namespace $PIPELINE_NAMESPACE ..."
kubectl apply -n "$PIPELINE_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tekton-triggers-sa-to-pipeline-namespace-role-binding
subjects:
- kind: ServiceAccount
  name: tekton-triggers-sa
  namespace: $TEKTON_NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tekton-triggers-pipeline-namespace-role
EOF

echo "Setting up tekton-triggers-bindings-and-interceptors-clusterrole ..."
kubectl apply -f - <<EOF
kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: tekton-triggers-bindings-and-interceptors-clusterrole
rules:
  # EventListeners need to be able to fetch any clustertriggerbindings
- apiGroups: ["triggers.tekton.dev"]
  resources: ["clustertriggerbindings", "clusterinterceptors", "interceptors"]
  verbs: ["get", "list", "watch"]
EOF

echo "Binding tekton-triggers-sa to tekton-triggers-bindings-and-interceptors-clusterrole in namespace $TEKTON_NAMESPACE ..."
kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: tekton-triggers-sa-to-tekton-triggers-bindings-and-interceptors-clusterrole-binding
subjects:
- kind: ServiceAccount
  name: tekton-triggers-sa
  namespace: $TEKTON_NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: tekton-triggers-bindings-and-interceptors-clusterrole
EOF

echo "Setting up tekton-triggers-createwebhook-role in $TEKTON_NAMESPACE ..."
# create create-webhook-rolebinding
kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tekton-triggers-createwebhook-role
rules:
- apiGroups:
  - ""
  resources:
  - secrets
  verbs:
  - get
  - list
  - create
  - update
  - delete
- apiGroups:
  - triggers.tekton.dev
  resources:
  - eventlisteners
  verbs:
  - get
  - list
  - create
  - update
  - delete
- apiGroups:
  - extensions
  resources:
  - ingresses
  verbs:
  - create
  - get
  - list
  - delete
  - update
EOF

echo "Setting up tekton-triggers-createwebhook-sa in namespace $TEKTON_NAMESPACE ..."
kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tekton-triggers-createwebhook-sa
EOF

echo "Binding tekton-triggers-createwebhook-sa to tekton-triggers-createwebhook-role in namespace $TEKTON_NAMESPACE ..."
kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tekton-triggers-createwebhook-rolebinding
subjects:
  - kind: ServiceAccount
    name: tekton-triggers-createwebhook-sa
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tekton-triggers-createwebhook-role
EOF

echo "Finished setting up roles for tekton triggers."


echo "#########################"
echo "Configuring webhook secrets in namespace $TEKTON_NAMESPACE ..."

echo "Configuring GITLAB_WEBHOOK_SECRET ..."
set +e
GITLAB_WEBHOOK_SECRET="$(jq -r '.GITLAB_WEBHOOK_SECRET' ../../platform_config/"${ENVIRONMENT}"/static.encrypted.json)"
set -e
if [[ "$GITLAB_WEBHOOK_SECRET" != "" ]]; then
  echo "Found GITLAB_WEBHOOK_SECRET configuration. Configuring gitlab-trigger-webhook-secret on cluster"
  GITLAB_WEBHOOK_SECRET_BASE64=$(echo -n "${GITLAB_WEBHOOK_SECRET}" | base64)
  kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: gitlab-trigger-webhook-secret
type: Opaque
data:
  secretToken: |
    $GITLAB_WEBHOOK_SECRET_BASE64
EOF
fi

echo "Configuring GITHUB_WEBHOOK_SECRET ..."
set +e
GITHUB_WEBHOOK_SECRET="$(jq -r '.GITHUB_WEBHOOK_SECRET' ../../platform_config/"${ENVIRONMENT}"/static.encrypted.json)"
set -e
if [[ "$GITHUB_WEBHOOK_SECRET" != "" ]]; then
  echo "Found GITHUB_WEBHOOK_SECRET configuration. Configuring github-trigger-webhook-secret on cluster"
  GITHUB_WEBHOOK_SECRET_BASE64=$(echo -n "${GITHUB_WEBHOOK_SECRET}" | base64)
  kubectl apply -n "$TEKTON_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: github-trigger-webhook-secret
type: Opaque
data:
  secretToken: |
    $GITHUB_WEBHOOK_SECRET_BASE64
EOF
fi

echo "Setting up configured triggers for team: ${TEAM} ..."
res="$(ls "../triggers/${TEAM}")"
if [[ "$res" == ""  ]]; then
  echo "No triggers configured for team: ${TEAM}"
else
  for dir in ../triggers/$TEAM/*/ ; do
    echo "Setting up triggers from: $dir ..."
    kubectl apply -n "${TEKTON_NAMESPACE}" -f $dir
  done
fi