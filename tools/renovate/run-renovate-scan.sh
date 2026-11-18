#!/usr/bin/env bash
# run-renovate-scan.sh — the one command that re-runs kubementat's version scan.
#
# Renovate's own CLI runs once and exits, so this script is what the schedule calls;
# the config it uses is the renovate.json committed at the repo root.
#
# Modes:
#   --dry-run  (default)  read-only scan. Nothing is written to the repo (it is mounted
#                         read-only) and no host or token is required. Findings land in a
#                         timestamped log under the output directory. The releases whose
#                         versions live on GitHub — the pinned binaries, the Tekton
#                         releases, k3s, the Actions — need a GITHUB_TOKEN to answer at
#                         all: without one they are reported as skipped, not as current.
#   --pr                  real mode against Forgejo: writes branches and pull requests.
#                         Requires RENOVATE_ENDPOINT, the HTTPS web URL of the Forgejo
#                         instance and not the ssh remote, and RENOVATE_TOKEN, a personal
#                         access token with repo read+write and user read. Off by default.
#   --help                usage.
#
# Scheduling: cron is the scheduler, and the weekly slot is not arbitrary. The uv.lock
# refresh carries a schedule gate of "before 4am on monday", so a PR-mode run outside
# that window never refreshes the lock file and the 34 locked packages rot in silence.
#
#   17 3 * * *   tools/renovate/run-renovate-scan.sh --dry-run
#   5  3 * * 1   tools/renovate/run-renovate-scan.sh --pr
#
# Environment knobs:
#   RENOVATE_TARGET_REPO   (default: the checkout this script lives in)
#   RENOVATE_IMAGE         (default renovate/renovate:44.251.0-full — pinned, and the full
#                           flavour, because the lock-file refresh delegates to uv)
#   RENOVATE_OUTPUT_DIR    (default /tmp/renovate-scans)
#   RENOVATE_REPOS         (default Kubementat/kubementat, used only by --pr)
#   RENOVATE_LOG_LEVEL     (default debug — in the read-only mode Renovate names the candidate
#                           updates only at debug, so a quieter run reports that it looked without
#                           saying what it found)
#   GITHUB_TOKEN           (optional, forwarded to the container for GitHub lookups)
#   RENOVATE_ENDPOINT      (required by --pr)
#   RENOVATE_TOKEN         (required by --pr)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${RENOVATE_TARGET_REPO:-$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)}"
IMAGE="${RENOVATE_IMAGE:-renovate/renovate:44.251.0-full}"
OUT_DIR="${RENOVATE_OUTPUT_DIR:-${TMPDIR:-/tmp}/renovate-scans}"
REPOS="${RENOVATE_REPOS:-Kubementat/kubementat}"
LOG_LEVEL="${RENOVATE_LOG_LEVEL:-debug}"

case "${1:-dry-run}" in
  --dry-run|dry-run) MODE="dry-run" ;;
  --pr|pr|run)       MODE="pr" ;;
  -h|--help)
    awk 'NR>1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "$0"
    exit 0 ;;
  *) echo "unknown mode: $1 (expected --dry-run, --pr, --help)" >&2; exit 2 ;;
esac

[ -d "$REPO" ] || { echo "target repo not found: $REPO" >&2; exit 1; }
[ -f "$REPO/renovate.json" ] || { echo "no renovate.json at the repo root: $REPO" >&2; exit 1; }
mkdir -p "$OUT_DIR"
LOG_FILE="$OUT_DIR/renovate-$(date -u +%Y%m%dT%H%M%SZ)-$MODE.log"

ARGS=(-e "LOG_LEVEL=$LOG_LEVEL")
[ -n "${GITHUB_TOKEN:-}" ] && ARGS+=(-e GITHUB_TOKEN)

if [ "$MODE" = dry-run ]; then
  # A read-only mount is the guarantee: a local-platform run neither clones nor writes.
  ARGS+=(-v "$REPO:/usr/src/app:ro" -v renovate-cache:/tmp/renovate/cache)
  docker run --rm "${ARGS[@]}" "$IMAGE" --platform=local --dry-run=full 2>&1 | tee "$LOG_FILE"
else
  if [ -z "${RENOVATE_ENDPOINT:-}" ] || [ -z "${RENOVATE_TOKEN:-}" ]; then
    cat >&2 <<'EOF'
PR mode needs both, and is off by default:
  RENOVATE_ENDPOINT=https://<forgejo-web-url>/   the HTTPS web URL, not the ssh remote
  RENOVATE_TOKEN=<PAT with repo=rw and user=read>
Run the scan with --dry-run until both are set.
EOF
    exit 1
  fi
  # Real mode clones through the platform API, so no repo mount and no local working tree.
  ARGS+=(-v renovate-cache:/tmp/renovate/cache
         -e RENOVATE_PLATFORM=forgejo
         -e "RENOVATE_ENDPOINT=$RENOVATE_ENDPOINT"
         -e "RENOVATE_TOKEN=$RENOVATE_TOKEN"
         -e RENOVATE_ONBOARDING=false
         -e RENOVATE_AUTODISCOVER=false
         -e "RENOVATE_REPOSITORIES=$REPOS")
  docker run --rm "${ARGS[@]}" "$IMAGE" 2>&1 | tee "$LOG_FILE"
fi

echo "mode: $MODE" >&2
echo "repo: $REPO" >&2
echo "log:  $LOG_FILE" >&2
