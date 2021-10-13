################################
# Docker image for CI automation tasks using Tekton CI
#
# Contains basic tooling: java mvn kubectl helm tkn mysql-client jq git-crypt
################################
# build via:
# DOCKER_REGISTRY_BASE_URL="REPLACEME_WITH_DOCKER_REGISTRY_BASE_URL"

# IMAGE_URL="REPLACEME"
# docker build -t "$IMAGE_URL" .
# run bash in container via:
# docker run --name ci -it --rm "${DOCKER_REGISTRY_BASE_URL}/ubuntu-ci:latest"

# run on k8s cluster:
# kubectl run ubuntu-ci --rm -i --tty --image="${DOCKER_REGISTRY_BASE_URL}/ubuntu-ci:latest" --command /bin/bash

FROM ubuntu:20.04

ENV DEBIAN_FRONTEND noninteractive

ARG MAVEN_VERSION=3.6.3
ARG TKN_CLI_VERSION="0.17.0"
ARG YQ_VERSION=4.7.0
ARG CQLSH_VERSION="6.0.0b4"
ARG USER_HOME_DIR="/root"
ARG BASE_URL=https://apache.osuosl.org/maven/maven-3/${MAVEN_VERSION}/binaries

# on which architecture am i running?
RUN uname -a

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

RUN apt-get update && apt-get install -y openjdk-8-jdk openjdk-11-jdk-headless \
  git jq ant ca-certificates-java findutils apt-transport-https gnupg2 git-crypt \
  build-essential python3 python3-pip libssl-dev libffi-dev python3-dev \
  dnsutils kubectl helm mysql-client nano iputils-ping netcat-openbsd dnsutils xz-utils && \
	apt-get clean && \
	rm -rf /var/lib/apt/lists/* && \
	rm -rf /var/cache/oracle-jdk8-installer

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

# Setup JAVA_HOME, this is useful for docker commandline
# TODO: Differentiate between Java 8 and Java 11
# ENV JAVA_HOME /usr/lib/jvm/java-8-openjdk-amd64/
ENV JAVA_HOME /usr/lib/jvm/java-11-openjdk-amd64/
RUN export JAVA_HOME

# Install maven
RUN mkdir -p /usr/share/maven /usr/share/maven/ref \
  && curl -fsSL -o /tmp/apache-maven.tar.gz ${BASE_URL}/apache-maven-${MAVEN_VERSION}-bin.tar.gz \
  && tar -xzf /tmp/apache-maven.tar.gz -C /usr/share/maven --strip-components=1 \
  && rm -f /tmp/apache-maven.tar.gz \
  && ln -s /usr/share/maven/bin/mvn /usr/bin/mvn

ENV MAVEN_HOME /usr/share/maven
ENV MAVEN_CONFIG "$USER_HOME_DIR/.m2"

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

# Install cqlsh for cassandra shell access
RUN pip3 install cqlsh==${CQLSH_VERSION}
RUN cqlsh --version

CMD ["/bin/bash"]