################################
# Minimal Docker image for CI automation tasks using Tekton CI
#
# Contains basic tooling: git, git-crypt, jq, yq, gnupg2, kubectl, helm (+ helm-diff), helmfile, tkn, linkerd cli,
# mysql-client, skopeo, python3, uv (python package/project management for the kmt cli), node (LTS) + npm
################################
# build via:
# export DOCKER_REGISTRY_BASE_URL="REPLACEME_WITH_DOCKER_REGISTRY_BASE_URL"
# export IMAGE_NAME="ubuntu-ci-minimal"
# export IMAGE_TAG="latest"
# e.g. export DOCKER_REGISTRY_BASE_URL="docker.io/julianweberdev"

# CLASSIC BUILD
# IMAGE_URL="${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}"
# docker build -t "$IMAGE_URL" .

# ----------------------

# MULTI ARCH BUILD VIA BUILDX
# see build.sh script for details (builds, tests via test.sh and pushes the image with --push)

# ----------------------

# run bash in container via:
# export IMAGE_NAME="ubuntu-ci-minimal"
# export IMAGE_TAG="latest-amd64"
# docker run --name ci -it --rm "${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}"

# run on k8s cluster:
# kubectl run ubuntu-ci --rm -i --tty --image="${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}" --command /bin/bash

# tool versions (test.sh reads the defaults below to verify the built image)
ARG BASE_IMAGE_URL=ubuntu:26.04
ARG KUBECTL_MINOR_VERSION="1.37"
ARG TKN_CLI_VERSION="0.46.2"
ARG YQ_VERSION="4.54.1"
ARG HELMFILE_VERSION="1.8.2"
ARG HELM_DIFF_VERSION="3.15.15"
ARG LINKERD_VERSION="edge-26.10.1"
ARG NODE_VERSION="24.22.1"
ARG UV_VERSION="0.13.6"

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ${BASE_IMAGE_URL}

ARG KUBECTL_MINOR_VERSION
ARG TKN_CLI_VERSION
ARG YQ_VERSION
ARG HELMFILE_VERSION
ARG HELM_DIFF_VERSION
ARG LINKERD_VERSION
ARG NODE_VERSION

ENV DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN echo "ARCHITECTURE: $(uname -m) / $(dpkg --print-architecture)"

# packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  openssh-client ca-certificates curl wget gnupg gnupg2 xz-utils python3 python3-pip python3-venv && \
  rm -rf /var/lib/apt/lists/*

# add repos
## kubectl
RUN mkdir -p -m 755 /etc/apt/keyrings \
  && curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${KUBECTL_MINOR_VERSION}/deb/Release.key" | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg \
  && chmod 644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg \
  && echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${KUBECTL_MINOR_VERSION}/deb/ /" > /etc/apt/sources.list.d/kubernetes.list \
  && chmod 644 /etc/apt/sources.list.d/kubernetes.list

## helm (the old baltocdn repo is gone, helm moved its apt repo to buildkite)
RUN curl -fsSL https://packages.buildkite.com/helm-linux/helm-debian/gpgkey | gpg --dearmor -o /etc/apt/keyrings/helm.gpg \
  && chmod 644 /etc/apt/keyrings/helm.gpg \
  && echo "deb [signed-by=/etc/apt/keyrings/helm.gpg] https://packages.buildkite.com/helm-linux/helm-debian/any/ any main" > /etc/apt/sources.list.d/helm-stable-debian.list

RUN apt-get update && apt-get install --no-install-recommends -y \
  git jq gnupg2 git-crypt kubectl helm mysql-client skopeo && \
  apt-get clean && \
  rm -rf /var/lib/apt/lists/*

### helm diff (prerequisite for helmfile)
# helm 4 verifies plugins by default, which git sources do not support; the source is pinned to a release tag instead
RUN helm plugin install https://github.com/databus23/helm-diff --version "v${HELM_DIFF_VERSION}" --verify=false

## helmfile
RUN ARCH="$(dpkg --print-architecture)" \
  && wget -qO helmfile.tar.gz "https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/helmfile_${HELMFILE_VERSION}_linux_${ARCH}.tar.gz" \
  && tar xfz helmfile.tar.gz helmfile \
  && rm helmfile.tar.gz \
  && mv helmfile /usr/bin/helmfile \
  && chmod 755 /usr/bin/helmfile

# install yq for yaml parsing
RUN ARCH="$(dpkg --print-architecture)" \
  && wget -qO /usr/bin/yq "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${ARCH}" \
  && chmod 755 /usr/bin/yq

# Install tkn cli for tekton interaction
RUN ARCH= && dpkgArch="$(dpkg --print-architecture)" \
  && case "${dpkgArch}" in \
  arm64) ARCH='aarch64';; \
  amd64) ARCH='x86_64';; \
  *) echo "unsupported architecture: $dpkgArch"; exit 1 ;; \
  esac \
  && curl -fsSLO "https://github.com/tektoncd/cli/releases/download/v${TKN_CLI_VERSION}/tkn_${TKN_CLI_VERSION}_Linux_${ARCH}.tar.gz" \
  && tar xzf "tkn_${TKN_CLI_VERSION}_Linux_${ARCH}.tar.gz" -C /usr/bin/ tkn \
  && rm "tkn_${TKN_CLI_VERSION}_Linux_${ARCH}.tar.gz"

# Install linkerd cli
RUN curl -fsSL https://run.linkerd.io/install-edge | LINKERD2_VERSION="${LINKERD_VERSION}" sh \
  && cp "/root/.linkerd2/bin/linkerd-${LINKERD_VERSION}" /usr/bin/linkerd \
  && rm -rf /root/.linkerd2

# Install node (LTS) + npm from the official release tarball, verified against the published checksums
RUN ARCH= && dpkgArch="$(dpkg --print-architecture)" \
  && case "${dpkgArch}" in \
  arm64) ARCH='arm64';; \
  amd64) ARCH='x64';; \
  *) echo "unsupported architecture: $dpkgArch"; exit 1 ;; \
  esac \
  && NODE_TARBALL="node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" \
  && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_TARBALL}" \
  && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" | grep " ${NODE_TARBALL}\$" | sha256sum -c - \
  && tar -xJf "${NODE_TARBALL}" -C /usr/local --strip-components=1 --no-same-owner \
  && rm "${NODE_TARBALL}"

# Install uv (python package/project management, used to run the python kmt cli)
COPY --from=uv /uv /uvx /usr/local/bin/

# List versions
RUN echo "INSTALLED VERSIONS:" \
  && echo "  - kubectl version: $(kubectl version --client)" \
  && echo "  - helm version: $(helm version)" \
  && echo "  - helmfile version: $(helmfile --version)" \
  && echo "  - tkn version: $(tkn version)" \
  && echo "  - skopeo version: $(skopeo --version)" \
  && echo "  - jq version: $(jq --version)" \
  && echo "  - yq version: $(yq --version)" \
  && echo "  - python3 version: $(python3 --version)" \
  && echo "  - uv version: $(uv --version)" \
  && echo "  - node version: $(node --version)" \
  && echo "  - npm version: $(npm --version)" \
  && echo "  - linkerd version: $(linkerd version --client)"

CMD ["/bin/bash"]
