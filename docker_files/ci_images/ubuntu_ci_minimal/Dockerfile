################################
# Minimal Docker image for CI automation tasks using Tekton CI
#
# Contains basic tooling: git, git-crypt, jq, gnupg2, kubectl, helm, helmfile, tkn, linkerd cli, mysql-client
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
# see build.sh script for details

# ----------------------

# run bash in container via:
# export IMAGE_NAME="ubuntu-ci-minimal"
# export IMAGE_TAG="latest-amd64"
# docker run --name ci -it --rm "${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}"

# run on k8s cluster:
# kubectl run ubuntu-ci --rm -i --tty --image="${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}" --command /bin/bash
ARG BASE_IMAGE_URL=ubuntu:24.04
FROM ${BASE_IMAGE_URL}

ENV DEBIAN_FRONTEND=noninteractive

ARG TKN_CLI_VERSION="0.21.0"
ARG YQ_VERSION="4.7.0"
ARG HELMFILE_VERSION="0.169.1"

RUN echo "ARCHITECTURE: $(uname -m)"

# packages
RUN apt-get update && apt-get install --no-install-recommends -y ca-certificates curl wget gnupg gnupg2 python3 python3-pip && \
  rm -rf /var/lib/apt/lists/*

# add repos
## kubectl
RUN mkdir -p -m 755 /etc/apt/keyrings
RUN curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.31/deb/Release.key | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg && chmod 644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg
RUN echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.31/deb/ /' | tee /etc/apt/sources.list.d/kubernetes.list && chmod 644 /etc/apt/sources.list.d/kubernetes.list

## helm
RUN curl https://baltocdn.com/helm/signing.asc | apt-key add -
RUN echo "deb https://baltocdn.com/helm/stable/debian/ all main" | tee /etc/apt/sources.list.d/helm-stable-debian.list

RUN apt-get update && apt-get install --no-install-recommends -y \
  git jq gnupg2 git-crypt kubectl helm mysql-client skopeo && \
	apt-get clean && \
	rm -rf /var/lib/apt/lists/*

### helm diff (prerequisite for helmfile)
RUN helm plugin install https://github.com/databus23/helm-diff

## helmfile
RUN ARCH= && dpkgArch="$(uname -m)" \
  && case "${dpkgArch}" in \
  aarch64) ARCH='arm64';; \
  arm64) ARCH='arm64';; \
  armhf) ARCH='arm64';; \
  x86_64) ARCH='amd64';; \
  *) echo "unsupported architecture: $dpkgArch"; exit 1 ;; \
  esac \
  && wget -O helmfile.tar.gz "https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/helmfile_${HELMFILE_VERSION}_linux_${ARCH}.tar.gz" \
  && tar xfz helmfile.tar.gz \
  && rm helmfile.tar.gz \
  && mv helmfile /usr/bin/helmfile \
  && chmod 755 /usr/bin/helmfile

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
RUN ln -s /root/.linkerd2/bin/linkerd /usr/bin/linkerd


# List versions
RUN echo "INSTALLED VERSIONS:"
RUN echo "  - kubectl version: $(kubectl version)"
RUN echo "  - helm version: $(helm version)"
RUN echo "  - tkn version: $(tkn version)"
RUN echo "  - skopeo version: $(skopeo --version)"
RUN echo "  - jq version: $(jq --version)"
RUN echo "  - yq version: $(yq --version)"
RUN echo "  - python3 version: $(python3 --version)"
RUN echo "  - linkerd version: $(linkerd version --client)"

CMD ["/bin/bash"]