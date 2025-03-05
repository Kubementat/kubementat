#!/usr/bin/env bash
export DOCKER_REGISTRY_BASE_URL="docker.io/julianweberdev"
export IMAGE_NAME="ubuntu-ci-minimal"
export IMAGE_TAG="${IMAGE_TAG:-test}"
docker run -it "$DOCKER_REGISTRY_BASE_URL/$IMAGE_NAME:$IMAGE_TAG" /bin/bash