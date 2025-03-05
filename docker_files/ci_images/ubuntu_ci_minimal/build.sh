#!/usr/bin/env bash
export DOCKER_REGISTRY_BASE_URL="docker.io/julianweberdev"
export IMAGE_NAME="ubuntu-ci-minimal"
export IMAGE_TAG="${IMAGE_TAG:-test}"

export PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PUSH_TARGET="${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}"
echo "Building for PLATFORMS: $PLATFORMS"
echo "Pushing to: $PUSH_TARGET"

docker buildx create --name multibuilder || true
docker buildx use multibuilder
docker buildx build --progress=plain --push --platform "$PLATFORMS" --tag "$PUSH_TARGET" .
