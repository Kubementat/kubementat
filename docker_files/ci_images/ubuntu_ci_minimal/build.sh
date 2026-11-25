#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# versions.py resolves the build args from the repo it lives in, so run it from the repo root,
# not from this script's directory.
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

export DOCKER_REGISTRY_BASE_URL="docker.io/julianweberdev"
export IMAGE_NAME="ubuntu-ci-minimal"
export IMAGE_TAG="${IMAGE_TAG:-test}"
export PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PUSH_TARGET="${DOCKER_REGISTRY_BASE_URL}/${IMAGE_NAME}:${IMAGE_TAG}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--push] [--help]

Builds the ${IMAGE_NAME} image for all PLATFORMS, loads the native platform image into the
local docker daemon and smoke tests it via test.sh.

Options:
  --push    after a successful test, push the multi platform image to ${PUSH_TARGET}
  --help    show this help

Environment:
  IMAGE_TAG  image tag (default: test, current: ${IMAGE_TAG})
  PLATFORMS  platforms to build (default: linux/amd64,linux/arm64, current: ${PLATFORMS})
EOF
}

PUSH=false
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

echo "Building for PLATFORMS: $PLATFORMS"
echo "Image: $PUSH_TARGET (push: $PUSH)"

# Resolve every version build arg from versions.yaml through the python module (it owns
# the yaml parsing, so this needs no yq). They are also exported as variables for test.sh,
# which can no longer read them from the Dockerfile.
mapfile -t VERSION_ARGS < <(uv run --project "$REPO_ROOT" python -m kubementat.core.versions --print-build-args)
BUILD_ARGS=()
for line in "${VERSION_ARGS[@]}"; do
  # one argv each, not "ARG=K=V" as a single word: docker's flag parser splits --build-arg
  # from its value and would report the whole pair as an unknown flag
  BUILD_ARGS+=("--build-arg" "${line#--build-arg }")
  export "${line#--build-arg }"
done
echo "Resolved ${#VERSION_ARGS[@]} build args from versions.yaml"

docker buildx inspect multibuilder >/dev/null 2>&1 || docker buildx create --name multibuilder
docker buildx use multibuilder

# build all platforms first, so a broken platform fails before anything is pushed
docker buildx build --progress=plain --platform "$PLATFORMS" "${BUILD_ARGS[@]}" .

# load the native platform image into the local docker daemon (cached from above) and test it
docker buildx build --progress=plain --load --tag "$PUSH_TARGET" "${BUILD_ARGS[@]}" .
./test.sh "$PUSH_TARGET"

if [[ "$PUSH" == "true" ]]; then
  docker buildx build --progress=plain --push --platform "$PLATFORMS" --tag "$PUSH_TARGET" "${BUILD_ARGS[@]}" .
else
  echo "Not pushing (run with --push to push ${PUSH_TARGET})"
fi
