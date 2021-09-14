#!/usr/bin/env bash
IMAGE_URL="busybox:latest"
DOCKER_REGISTRY_SECRET_NAME="docker-registry-secret"

kubectl run my-shell --rm -i --tty --image ubuntu -- bash

# run k8s registry docker image
kubectl run my-shell --rm -i --tty --overrides="{ \"spec\": { \"imagePullSecrets\": [{\"name\": \"$DOCKER_REGISTRY_SECRET_NAME\"}] } }" --image "$IMAGE_URL" -- /bin/sh
