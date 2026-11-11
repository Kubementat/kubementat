#!/usr/bin/env bash
# Smoke test for the ubuntu-ci-minimal image: starts a container and checks that every installed tool is present,
# runs, and (for the pinned tools) reports the version pinned in the Dockerfile.
# usage: ./test.sh <image-url>
# test another platform under emulation: DOCKER_RUN_ARGS="--platform linux/arm64" ./test.sh <image-url>
set -euo pipefail

IMAGE_URL="${1:?usage: $0 <image-url>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# read the pinned version defaults from the Dockerfile, e.g. ARG YQ_VERSION="4.54.1"
dockerfile_arg() {
  sed -nE "s/^ARG $1=\"?([^\"]*)\"?$/\1/p" "$SCRIPT_DIR/Dockerfile" | head -1
}

echo "Testing image: $IMAGE_URL"
docker run --rm -i ${DOCKER_RUN_ARGS:-} \
  -e EXPECT_UBUNTU="$(dockerfile_arg BASE_IMAGE_URL | sed 's/^ubuntu://')" \
  -e EXPECT_KUBECTL="$(dockerfile_arg KUBECTL_MINOR_VERSION)" \
  -e EXPECT_TKN="$(dockerfile_arg TKN_CLI_VERSION)" \
  -e EXPECT_YQ="$(dockerfile_arg YQ_VERSION)" \
  -e EXPECT_HELMFILE="$(dockerfile_arg HELMFILE_VERSION)" \
  -e EXPECT_HELM_DIFF="$(dockerfile_arg HELM_DIFF_VERSION)" \
  -e EXPECT_LINKERD="$(dockerfile_arg LINKERD_VERSION)" \
  -e EXPECT_NODE="$(dockerfile_arg NODE_VERSION)" \
  -e EXPECT_UV="$(dockerfile_arg UV_VERSION)" \
  "$IMAGE_URL" /bin/bash -s <<'EOF'
FAILED=0

# check NAME EXPECTED CMD...: runs CMD, prints its first output line and fails if it does not contain EXPECTED
check() {
  local name="$1" expected="$2"; shift 2
  local out
  if ! out="$("$@" 2>&1)"; then
    printf '  FAIL %-10s command failed: %s\n' "$name" "$(head -1 <<<"$out")"; FAILED=1; return
  fi
  out="$(grep -m1 . <<<"$out")"
  if [[ -n "$expected" && "$out" != *"$expected"* ]]; then
    printf '  FAIL %-10s expected "%s", got: %s\n' "$name" "$expected" "$out"; FAILED=1; return
  fi
  printf '  ok   %-10s %s\n' "$name" "$out"
}

echo "Arch: $(uname -m)"
check ubuntu    "$EXPECT_UBUNTU"     grep PRETTY_NAME /etc/os-release
check kubectl   "v$EXPECT_KUBECTL."  kubectl version --client
check helm      "v4."                helm version --short
check helm-diff "$EXPECT_HELM_DIFF"  helm diff version
check helmfile  "$EXPECT_HELMFILE"   helmfile --version
check tkn       "$EXPECT_TKN"        tkn version
check yq        "v$EXPECT_YQ"        yq --version
check jq        "jq-1."              jq --version
check linkerd   "$EXPECT_LINKERD"    linkerd version --client --short
check node      "v$EXPECT_NODE"      node --version
check npm       ""                   npm --version
check npx       ""                   npx --version
check uv        "uv $EXPECT_UV"      uv --version
check uvx       "$EXPECT_UV"         uvx --version
check python3   "Python 3."          python3 --version
check pip       "pip"                python3 -m pip --version
check git       "git version"        git --version
check git-crypt "git-crypt"          git-crypt --version
check gpg       "GnuPG"              gpg --version
check ssh       "OpenSSH"            ssh -V
check mysql     "mysql"              mysql --version
check skopeo    "skopeo version"     skopeo --version
check curl      "curl"               curl --version
check wget      "Wget"               wget --version

# functional checks: uv can create a venv and run python in it, node runs code, yq/jq parse
check uv-venv   "ok"  bash -c 'cd "$(mktemp -d)" && uv venv -q .venv && .venv/bin/python -c "print(\"ok\")"'
check node-run  "42"  node -e 'console.log(6 * 7)'
check yq-parse  "1"   bash -c 'echo "a: 1" | yq ".a"'
check jq-parse  "1"   bash -c 'echo "{\"a\": 1}" | jq ".a"'

if [[ "$FAILED" -ne 0 ]]; then echo "IMAGE TEST FAILED"; exit 1; fi
echo "IMAGE TEST PASSED"
EOF
