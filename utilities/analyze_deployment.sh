#!/usr/bin/env bash

# usage: analyze_deployment.sh NAMESPACE APP_LABEL

# use this on the inception vm for analyzing problems with deployments

namespace="$1"
app_label="$2"

# get pods
kubectl get pods -n "$namespace"

# get logs
kubectl logs -n "$namespace" -l 'app.kubernetes.io/name'="${app_label}"

# ssh into bash of a pod
# POD=$(kubectl get pod -l app="$app_label" -o jsonpath="{.items[0].metadata.name}")
# kubectl exec -n "$namespace" -it "$POD" -- /bin/sh


# get logs for the previous container process
# useful for CrashLoopBackoff containers
# kubectl logs -n "$namespace" -l app=${app_label} -p --tail=1000
# kubectl logs -n "$namespace" -l app=${app_label} -p --tail=1000