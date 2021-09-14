#!/usr/bin/env bash

namespace="$1"

echo "#################################"
echo "Pods for namespace: ${namespace}"
kubectl get pods -n "${namespace}" -o json |jq '.items[].metadata.name'

echo "Used Images for namespace: ${namespace}"
kubectl get pods -n "${namespace}" -o json |jq '.items[].spec.containers[].image'

echo "#################################"
