################################
# Minimal Docker image for CI automation tasks using Tekton CI
#
# Contains basic tooling: git, git-crypt, jq, gnupg2, kubectl, helm, tkn, linkerd cli
################################
# build via:
# export DOCKER_REGISTRY_BASE_URL="REPLACEME_WITH_DOCKER_REGISTRY_BASE_URL"
# e.g. export DOCKER_REGISTRY_BASE_URL="docker.io/julianweberdev"

# IMAGE_URL="${DOCKER_REGISTRY_BASE_URL}/ubuntu-ci-minimal:latest"
# docker build -t "$IMAGE_URL" .

# MULTI ARCH BUILD VIA BUILDX
# https://github.com/docker/buildx/#installing

#
# export PLATFORMS="linux/amd64"
# or:
# export PLATFORMS="linux/amd64,linux/arm64"
#
# build multi arch image on docker for desktop via:
# docker buildx build --push --platform "$PLATFORMS" --tag "${DOCKER_REGISTRY_BASE_URL}/ubuntu-ci-minimal:latest" .


# run bash in container via:
# docker run --name ci -it --rm "${DOCKER_REGISTRY_BASE_URL}/ubuntu-ci-minimal:latest"

# run on k8s cluster:
# kubectl run ubuntu-ci --rm -i --tty --image="${DOCKER_REGISTRY_BASE_URL}/ubuntu-ci-minimal:latest" --command /bin/bash

FROM ubuntu:20.04

ENV DEBIAN_FRONTEND noninteractive


ARG TKN_CLI_VERSION="0.17.0"
ARG YQ_VERSION="4.7.0"

RUN echo "ARCHITECTURE: $(uname -m)"

# packages
RUN apt-get update && apt-get install -y curl wget gnupg && \
  rm -rf /var/lib/apt/lists/*

# add repos
## kubectl
RUN curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add -
RUN echo "deb https://apt.kubernetes.io/ kubernetes-xenial main" | tee -a /etc/apt/sources.list.d/kubernetes.list

## helm
RUN curl https://baltocdn.com/helm/signing.asc | apt-key add -
RUN echo "deb https://baltocdn.com/helm/stable/debian/ all main" | tee /etc/apt/sources.list.d/helm-stable-debian.list

RUN apt-get update && apt-get install -y \
  git jq gnupg2 git-crypt kubectl helm && \
	apt-get clean && \
	rm -rf /var/lib/apt/lists/*

# install
# install yq for yaml parsing
RUN ARCH= && dpkgArch="$(uname -m)" \
  && case "${dpkgArch}" in \
  aarch64) ARCH='arm64';; \
  arm64) ARCH='arm64';; \
  armhf) ARCH='arm64';; \
  x86_64) ARCH='amd64';; \
  *) echo "unsupported architecture: $dpkgArch"; exit 1 ;; \
  esac \
  && wget -O /usr/bin/yq "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${ARCH}" \
  && chmod 755 /usr/bin/yq

# Install tkn cli for tekton interaction
RUN ARCH= && dpkgArch="$(uname -m)" \
    && case "${dpkgArch}" in \
    aarch64) ARCH='arm64';; \
    arm64) ARCH='arm64';; \
    armhf) ARCH='arm64';; \
    x86_64) ARCH='x86_64';; \
    *) echo "unsupported architecture: $dpkgArch"; exit 1 ;; \
    esac \
    && curl -LO "https://github.com/tektoncd/cli/releases/download/v${TKN_CLI_VERSION}/tkn_${TKN_CLI_VERSION}_Linux_${ARCH}.tar.gz" && \
    tar xvzf tkn_${TKN_CLI_VERSION}_Linux_${ARCH}.tar.gz -C /usr/bin/ tkn && rm tkn_${TKN_CLI_VERSION}_Linux_${ARCH}.tar.gz

# Install linkerd cli
RUN curl -sL run.linkerd.io/install | sh

CMD ["/bin/bash"]