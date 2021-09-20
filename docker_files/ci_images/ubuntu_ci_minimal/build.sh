#!/usr/bin/env bash
export DOCKER_REGISTRY_BASE_URL="docker.io/julianweberdev"
export IMAGE_NAME="ubuntu-ci-minimal"
export IMAGE_TAG="latest"

export PLATFORMS="linux/amd64,linux/arm64"
docker buildx create --name multibuilder || true
docker buildx use multibuilder
docker buildx build --push --platform "$PLATFORMS" --tag "${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}" .
