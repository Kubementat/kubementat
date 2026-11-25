#!/usr/bin/env bash
# Smoke test for the ubuntu-ci-minimal image: starts a container and checks that every installed tool is present,
# runs, and (for the pinned tools) reports the expected version.
# usage: ./test.sh <image-url>
# test another platform under emulation: DOCKER_RUN_ARGS="--platform linux/arm64" ./test.sh <image-url>
set -euo pipefail

IMAGE_URL="${1:?usage: $0 <image-url>}"

# The expected versions come from the environment: build.sh resolves them from
# src/kubementat/core/assets/versions.yaml and exports them before invoking this script.
# The Dockerfile carries no defaults anymore, so a missing variable here means nobody told
# this script what is expected — refuse instead of verifying against nothing.
missing=()
for var in BASE_IMAGE_URL KUBECTL_MINOR_VERSION TKN_CLI_VERSION YQ_VERSION HELMFILE_VERSION \
           HELM_DIFF_VERSION LINKERD_VERSION NODE_VERSION UV_VERSION; do
  [[ -v "$var" ]] || missing+=("$var")
done
if ((${#missing[@]})); then
  echo "missing expected-version variables: ${missing[*]} (run via build.sh, which exports them from versions.yaml)" >&2
  exit 1
fi

echo "Testing image: $IMAGE_URL"
docker run --rm -i ${DOCKER_RUN_ARGS:-} \
  -e EXPECT_UBUNTU="${BASE_IMAGE_URL#ubuntu:}" \
  -e EXPECT_KUBECTL="$KUBECTL_MINOR_VERSION" \
  -e EXPECT_TKN="$TKN_CLI_VERSION" \
  -e EXPECT_YQ="$YQ_VERSION" \
  -e EXPECT_HELMFILE="$HELMFILE_VERSION" \
  -e EXPECT_HELM_DIFF="$HELM_DIFF_VERSION" \
  -e EXPECT_LINKERD="$LINKERD_VERSION" \
  -e EXPECT_NODE="$NODE_VERSION" \
  -e EXPECT_UV="$UV_VERSION" \
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
