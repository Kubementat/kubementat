#!/usr/bin/env bash

DOCKER_REPO=julianweberdev
IMAGE=ubuntu-ci-minimal
TAG=latest
docker run -i -t "$DOCKER_REPO/$IMAGE:$TAG" /bin/bash