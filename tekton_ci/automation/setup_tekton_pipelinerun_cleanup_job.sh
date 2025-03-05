#!/usr/bin/env bash

######################################
#
# This script sets up the kubernetes cronjob for periodically cleaning up tekton pipeline_runs
# ATTENTION:
# This script assumes that there's a credential block in static.encrypted.json in the given environment's team directory
# This credential block should contain a definition for a secret named docker-registry-secret
#
######################################

set -e

ENVIRONMENT="$1"
TEAM="$2"
if [[ "$ENVIRONMENT" == "" || "$TEAM" == "" ]]; then
  echo "Usage: setup_tekton_pipelinerun_cleanup_job.sh <ENVIRONMENT_NAME> <TEAM>"
  echo "e.g.: setup_tekton_pipelinerun_cleanup_job.sh dev dev1"
  exit 1
fi

# constants
CRONJOB_NAMESPACE="cluster-cronjobs"
# e.g. this means keep 20 jobs
NUMBER_OF_JOBS_TO_KEEP_PLUS_1="21"
# This script assumes to use the default docker registry is used (default name: docker-registry-secret)
DOCKER_REGISTRY_SECRET_NAME="docker-registry-secret"


echo "#########################"
echo "Loading config from platform_config ..."
DOCKER_REGISTRY_BASE_URL="$(jq -r '.DOCKER_REGISTRY_BASE_URL' ../../platform_config/"${ENVIRONMENT}"/static.json)"
TEKTON_CI_IMAGE_NAME="$(jq -r '.TEKTON_CI_IMAGE_NAME' ../../platform_config/"${ENVIRONMENT}"/static.json)"
TEKTON_CI_IMAGE_TAG="$(jq -r '.TEKTON_CI_IMAGE_TAG' ../../platform_config/"${ENVIRONMENT}"/static.json)"
CI_IMAGE="$DOCKER_REGISTRY_BASE_URL/${TEKTON_CI_IMAGE_NAME}:${TEKTON_CI_IMAGE_TAG}"

DOCKER_REGISTRY_AUTH_URL="$(jq -r '.DOCKER_REGISTRY_CREDENTIALS[] | select(.NAME=="docker-registry-secret").DOCKER_REGISTRY_AUTH_URL' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_EMAIL="$(jq -r '.DOCKER_REGISTRY_CREDENTIALS[] | select(.NAME=="docker-registry-secret").DOCKER_REGISTRY_EMAIL' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_USERNAME="$(jq -r '.DOCKER_REGISTRY_CREDENTIALS[] | select(.NAME=="docker-registry-secret").DOCKER_REGISTRY_USERNAME' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"
DOCKER_REGISTRY_PASSWORD="$(jq -r '.DOCKER_REGISTRY_CREDENTIALS[] | select(.NAME=="docker-registry-secret").DOCKER_REGISTRY_PASSWORD' "../../platform_config/${ENVIRONMENT}/${TEAM}/static.encrypted.json")"

echo "ENVIRONMENT: $ENVIRONMENT"
echo "CRONJOB_NAMESPACE: $CRONJOB_NAMESPACE"
echo ""
echo "CI_IMAGE: $CI_IMAGE"
echo ""
echo "DOCKER_REGISTRY_AUTH_URL: $DOCKER_REGISTRY_AUTH_URL"
echo "DOCKER_REGISTRY_USERNAME: $DOCKER_REGISTRY_USERNAME"
echo "DOCKER_REGISTRY_EMAIL: $DOCKER_REGISTRY_EMAIL"
echo "DOCKER_REGISTRY_SECRET_NAME: $DOCKER_REGISTRY_SECRET_NAME"
echo "#########################"

echo "Creating $CRONJOB_NAMESPACE namespace for cronjobs..."
kubectl create namespace "$CRONJOB_NAMESPACE" || true

echo "Configuring secret $DOCKER_REGISTRY_SECRET_NAME for cronjob pods in namespace $CRONJOB_NAMESPACE ..."

kubectl -n "$CRONJOB_NAMESPACE" create secret docker-registry "$DOCKER_REGISTRY_SECRET_NAME" \
  --docker-server="$DOCKER_REGISTRY_AUTH_URL" \
  --docker-username="$DOCKER_REGISTRY_USERNAME" \
  --docker-password="$DOCKER_REGISTRY_PASSWORD" \
  --docker-email="$DOCKER_REGISTRY_EMAIL" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Configuring tekton-cleaner serviceaccount in namespace $CRONJOB_NAMESPACE ..."
kubectl apply -n "$CRONJOB_NAMESPACE" -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tekton-cleaner
  labels:
    managed-by: kubementat
EOF

full_patch_json="{\"imagePullSecrets\":[{\"name\":\"${DOCKER_REGISTRY_SECRET_NAME}\"}]}"
echo "Patching tekton-cleaner Service Account with:"
echo "$full_patch_json"
echo ""
kubectl -n "$CRONJOB_NAMESPACE" patch serviceaccount tekton-cleaner -p "$full_patch_json"

echo "Resulting service account configuration:"
kubectl -n "$CRONJOB_NAMESPACE" describe serviceaccount tekton-cleaner

echo "Configuring tekton-cleaner-clusterrole ..."
kubectl apply -f - <<EOF
kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: tekton-cleaner-clusterrole
  labels:
    managed-by: kubementat
rules:
  - apiGroups: ["tekton.dev"]
    resources: ["pipelineruns"]
    verbs: ["delete", "get", "watch", "list"]
EOF

echo "Binding tekton-cleaner-clusterrole to tekton-cleaner serviceaccount ..."
kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: tekton-cleaner-clusterrole-binding
  labels:
    managed-by: kubementat
subjects:
- kind: ServiceAccount
  name: tekton-cleaner
  namespace: $CRONJOB_NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: tekton-cleaner-clusterrole
EOF


echo "Configuring cleanup-tekton-pipelineruns cronjob ..."
kubectl apply  -n "$CRONJOB_NAMESPACE" -f - <<EOF
apiVersion: batch/v1
kind: CronJob
metadata:
  name: cleanup-tekton-pipelineruns
  labels:
    managed-by: kubementat
spec:
  # cleanup every day at 23:00
  schedule: "0 23 * * *"
  
  # cleanup all 15 minutes
  # schedule: "*/15 * * * *"

  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 2
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        metadata:
          labels:
            managed-by: kubementat
            tekton-cleanup: cleanup
        spec:
          restartPolicy: OnFailure
          serviceAccount: tekton-cleaner
          containers:
            - name: tekton-cleaner-ci-image
              image: $CI_IMAGE
              imagePullPolicy: IfNotPresent
              command:
                - /bin/bash
                - -c
                - |
                  ALL_COMPLETED_JOBS="\$(kubectl get pipelinerun --all-namespaces -o jsonpath='{range .items[?(@.status.completionTime)]}{.status.completionTime}{" "}{.metadata.name}{" -n"}{.metadata.namespace}{"\n"}{end}' | sort -s)"
                  echo "All completed jobs in all namespaces:"
                  echo "\$ALL_COMPLETED_JOBS"
                  echo "All completed job count: $(echo "\$ALL_COMPLETED_JOBS" | wc -l)"

                  echo ""
                  TO_DELETE="\$(echo "\$ALL_COMPLETED_JOBS" | tail -n +$NUMBER_OF_JOBS_TO_KEEP_PLUS_1 | awk '{ print \$2 " " \$3 }')"
                  echo "Full deletion list:"
                  echo "\$TO_DELETE"
                  echo "To delete job count: $(echo "\$TO_DELETE" | wc -l)"

                  echo "\$TO_DELETE" | while read -r delete_item
                  echo ""
                  do
                    echo "Deleting pipelinerun: \${delete_item}"
                    kubectl delete pipelinerun \${delete_item} || true
                  done
EOF

echo "###################"
echo "Details for created cronjob:"
echo ""
kubectl -n "$CRONJOB_NAMESPACE" get cronjob cleanup-tekton-pipelineruns -o=yaml