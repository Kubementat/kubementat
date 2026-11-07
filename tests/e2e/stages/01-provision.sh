# Provisioning: single-node k3s plus kubementat's prerequisite set, in one shot.
#
# This is the only stage delivered as a virt-runner provisioning script (FR-13). It
# is one-shot and parameter-free: a provisioning script gets no arguments and no
# environment, so every later stage runs command-by-command through the ssh
# primitive (FR-12) instead. Everything here is idempotent, because a failed script
# keeps the VM running and the operator may re-run it.
#
# The guest is `--distro ubuntu --release resolute` (Ubuntu 26.04), pinned by the
# harness rather than taken from virt-runner's profile default (FR-24).
#
# Timings this stage aims to stay inside (see docs/README_E2E_TESTS.md): apt update
# ~15s, git-crypt ~5s, tooling ~45s, k3s 9.6s, kubeconfig fix ~8s, tree copy 1.4s,
# `uv sync` 2.9s.

set -euo pipefail

WORKDIR="@GUEST_TREE@"
MOUNT="@GUEST_MOUNT@"
STATE="@PROVISION_STATE_DIR@"
USER="ubuntu"

export DEBIAN_FRONTEND=noninteractive

log() { echo "[01-provision] $*" >&2; }

# The one k3s release this lane is certified against, and the only place the number is written.
# `v1.36.5+k3s1` is the version the last green full run recorded, so the pin names a cluster the harness
# has actually measured rather than one chosen for today's mood. This replaces "recorded, not pinned"
# (D8): a promotion off the stable channel used to arrive as a guest that crash-looped with no owner and
# no note, because nothing in the lane noticed the version had changed. Pinning is what makes parity with
# the Pi reference cluster checkable instead of aspirational, and it puts the drift the lane was
# discovering by accident into one deliberate commit. See docs/plans/k3s-pi-reference-cluster.md D8.
K3S_VERSION="v1.36.5+k3s1"

sudo() { command sudo -n env DEBIAN_FRONTEND=noninteractive "$@"; }
mkdir_p() { sudo mkdir -p "$@"; }

# ---------------------------------------------------------------- host shapes

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) GOARCH=amd64; ARCH_TKN=x86_64 ;;
  aarch64) GOARCH=arm64; ARCH_TKN=aarch64 ;;
  *) echo "unsupported guest arch $ARCH" >&2; exit 2 ;;
esac

log "guest arch=$ARCH, distro=$(. /etc/os-release && echo "$PRETTY_NAME")"

# -------------------------------------------------------------------- 1. apt
# kubementat's own dependency check requires yq and pip; jq ships in the cloud
# image and is deliberately not installed here.
log "apt update + git-crypt/yq/python3-pip"
sudo apt-get -qq update
sudo apt-get -qq install -y git-crypt yq python3-pip curl ca-certificates jq

# ----------------------------------------------------------------- 2. tooling
# The versions here come from `constants.PINNED_BINARY_VERSIONS`, read out of the checkout, and
# never from a literal. The literal is what let the guest run helm 3.22.1 and helmfile 1.8.2 while
# the checkout declared 4.3.1 and 1.7.0: the lane was green, `tool_versions` knew, and the evidence
# was about whatever the installer handed us that day rather than about kubementat.
#
# Two things keep that from happening again, and neither one is a comment: this section installs
# what the table says (`tests/test_the_lane_installs_the_binaries_the_product_declares.py` is what
# keeps it that way), and stage 01 states what the guest ended up running
# (`assert:guest-tool-versions-match-the-pin`).
#
# A checkout that cannot be read is an error and not a licence to guess: without the table this
# stage would be installing a version nobody declared, which is the failure it is written to avoid.
for PIN_CANDIDATE in "$MOUNT" "$WORKDIR"; do
  if [ -r "$PIN_CANDIDATE/src/kubementat/core/constants.py" ]; then
    PIN_SOURCE="$PIN_CANDIDATE"
    break
  fi
done
if [ -z "${PIN_SOURCE:-}" ]; then
  echo "no readable src/kubementat/core/constants.py under $MOUNT or $WORKDIR: the pinned helm and"
  echo "helmfile versions are the product's declaration, and this stage will not guess them." >&2
  exit 4
fi

# `pin <binary>` -> the version that file declares for it. constants.py imports nothing, so plain
# python3 reads it -- no virtualenv, and nothing installed later has to work first.
pin() {
  env PYTHONPATH="$PIN_SOURCE/src" python3 -c '
import sys
from kubementat.core import constants
try:
    print(constants.PINNED_BINARY_VERSIONS[sys.argv[1]])
except KeyError:
    sys.exit("constants.PINNED_BINARY_VERSIONS declares no version for " + sys.argv[1])
' "$1"
}

# The same answer from the binary on PATH, reduced to the triple the pin is written as. The product
# parses these answers with `automation.first_binary_version` and warns; this is its shell
# counterpart, used only to decide whether an already-present binary is the pinned one. The verdict
# on a wrong version is the lane's, not this script's -- see `_assert_pinned_tool_versions`.
installed_version() {
  case "$1" in
    helm) helm version --short 2>/dev/null | head -1 ;;
    *) helmfile --version 2>/dev/null | head -1 ;;
  esac | sed -n 's/.*\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -1
}

HELM_VERSION="$(pin helm)"
HELMFILE_VERSION="$(pin helmfile)"
log "pinned by constants.py: helm $HELM_VERSION, helmfile $HELMFILE_VERSION"

# The tarball rather than the installer script: `get-helm-3` is helm 3's helper and cannot fetch the
# helm 4 the pin declares, and an installer that picks the version for us is precisely how the guest
# drifted. Both majors publish the same URL shape, so the version is the only thing in the URL and
# there is nothing here to update when the table changes.
log "helm $HELM_VERSION"
if [ "$(installed_version helm)" != "$HELM_VERSION" ]; then
  curl -fsSL -o /tmp/helm.tgz "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${GOARCH}.tar.gz"
  tar -xzf /tmp/helm.tgz -C /tmp "linux-${GOARCH}/helm"
  sudo install -m 0755 "/tmp/linux-${GOARCH}/helm" /usr/local/bin/helm
fi

log "helmfile $HELMFILE_VERSION"
if [ "$(installed_version helmfile)" != "$HELMFILE_VERSION" ]; then
  curl -fsSL -o /tmp/helmfile.tgz \
    "https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/helmfile_${HELMFILE_VERSION}_linux_${GOARCH}.tar.gz"
  sudo tar -xzf /tmp/helmfile.tgz -C /usr/local/bin helmfile
  sudo chmod 0755 /usr/local/bin/helmfile
fi

log "helm-diff plugin"
if ! helm plugin list 2>/dev/null | grep -q '^diff'; then
  # Measured at 4.3.1: helm 4 verifies plugin sources by default and helm-diff ships no provenance,
  # so a plain `helm plugin install` exits 1 with "plugin source does not support verification. Use
  # --verify=false to skip verification" (databus23/helm-diff#887). helm 3 does not know the flag,
  # so the flag is the fallback and not the default: plain attempt first, retry only once that has
  # said no. The attempt's own stderr goes into the log either way -- sending it to /dev/null is
  # what left the last lane guessing at the cause 15 minutes later.
  if ! diff_stderr="$(helm plugin install https://github.com/databus23/helm-diff 2>&1)"; then
    log "plain helm plugin install failed, retrying with --verify=false: $diff_stderr"
    if ! diff_stderr="$(helm plugin install https://github.com/databus23/helm-diff --verify=false 2>&1)"; then
      log "helm-diff did not install onto helm $(installed_version helm): $diff_stderr"
    fi
  fi
fi
# Ask the plugin itself last: stage 02's `initialize` execs `helm diff version` and exits 1 without
# it, so "installed" and "runnable" are not the same question. assert:guest-tools-present measures
# the same thing for the lane's evidence; this line only keeps the next such lane from having to
# open JSON to find out why stage 02 aborted.
if ! diff_runnable="$(helm diff version 2>&1)"; then
  log "FAILED: 'helm diff version' is not runnable on the guest, and stage 02's initialize will exit 1 on it: $diff_runnable"
fi
log "tkn"
if [ ! -x /usr/local/bin/tkn ]; then
  curl -fsSL -o /tmp/tkn.tgz \
    "https://github.com/tektoncd/cli/releases/download/v0.46.1/tkn_0.46.1_Linux_${ARCH_TKN}.tar.gz"
  tar -xzf /tmp/tkn.tgz -C /tmp tkn
  sudo install -m 0755 /tmp/tkn /usr/local/bin/tkn
fi

log "uv"
if ! command -v uv >/dev/null; then
  curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
fi
# uv lands in ~/.local/bin, which a non-interactive ssh session does not put on
# PATH; a symlink is the smallest thing that makes `uv run` reachable.
if [ -x "$HOME/.local/bin/uv" ]; then
  sudo ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv
fi

# ------------------------------------------------------------------- 3. k3s
# Measured: a bare Ubuntu 26.04 guest needs no preparation of any kind — no
# kernel modules, no apt upgrade. It is Ready in ~10s. What it does need is a
# version, which the paragraph above explains.
log "k3s"
if [ ! -x /usr/local/bin/k3s ] && ! command -v k3s >/dev/null 2>&1; then
  curl -sfL https://get.k3s.io | sudo env INSTALL_K3S_VERSION="$K3S_VERSION" sh - >&2
# A guest that already has k3s is skipped only when it already has *this* k3s. The
# original guard asked "is k3s installed", which is how a promoted guest survived a
# re-run of this script: the check passed, the wrong binary stayed, and the lane died
# minutes later on a CRD that never finishes establishing. So a wrong version is
# reinstalled here rather than noticed downstream.
# A guest that already has k3s is skipped only when it already has *this* k3s. The
# original guard asked "is k3s installed", which is how a promoted guest survived a
# re-run of this script: the check passed, the wrong binary stayed, and the lane died
# minutes later on a CRD that never finishes establishing. So a wrong version is
# reinstalled here rather than noticed downstream.
#
# The version is matched out of `k3s --version` ("k3s version v1.36.5+k3s1 (deadbeef)")
# rather than split by position: the last field is the commit hash, so a comparison that
# looked exact was comparing the wrong thing.
elif [ "$(k3s --version 2>/dev/null | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+' || true)" != "$K3S_VERSION" ]; then
  log "k3s is not $K3S_VERSION; reinstalling the pinned release"
  curl -sfL https://get.k3s.io | sudo env INSTALL_K3S_VERSION="$K3S_VERSION" sh - >&2
fi

# /etc/rancher/k3s/k3s.yaml is 0600 root:root and k3s resets it to 600 on every
# restart, which makes even `initialize` fail (KubernetesUtils calls
# load_kube_config). The durable fix is the config file plus a restart, and a
# symlink for the Python client, which reads $KUBECONFIG or ~/.kube/config and
# nothing else.
log "kubeconfig mode"
if ! grep -q 'write-kubeconfig-mode' /etc/rancher/k3s/config.yaml 2>/dev/null; then
  printf 'write-kubeconfig-mode: 644\n' | sudo tee /etc/rancher/k3s/config.yaml >/dev/null
  sudo systemctl restart k3s
fi
mkdir -p "$HOME/.kube"
ln -sf /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"

# gpg is required by the dependency check and present in the image; only pay for
# the install when it is not.
if ! command -v gpg >/dev/null 2>&1; then
  sudo apt-get -qq install -y gnupg
fi

# ------------------------------------------------------------ 4. the guest copy
# INV-1: the tree is copied before anything runs `initialize` in it, dirty state
# included. The virtiofs mount is read-write with uid passthrough; the copy is
# what makes the host checkout safe.
mkdir_p "$WORKDIR" "$STATE"
sudo chown "$(id -u):$(id -g)" "$WORKDIR" "$STATE"
log "copying $MOUNT -> $WORKDIR"
if [ -d "$MOUNT/.git" ]; then
  # INV-1: the tree, dirty state included, copied before anything initializes in
  # it. Built artifacts are not the tree: the guest's own virtualenv is created by
  # `uv sync` below, so .venv/.pytest_cache are excluded.
  tar -C "$MOUNT" --exclude=./.venv --exclude=./.pytest_cache -cf - . | tar -C "$WORKDIR" -xf -
else
  echo "the mounted checkout is missing at $MOUNT" >&2
  exit 3
fi

# uv sync inside the copy so nothing writes into the host tree.
log "uv sync --all-extras"
( cd "$WORKDIR" && env PATH="/usr/local/bin:$PATH" uv sync --all-extras ) >&2

# k3s's own kubectl is what `kmt` finds; make sure it is reachable for a plain user.
if [ -x /usr/local/bin/kubectl ]; then
  log "kubectl -> $(kubectl version --client=true 2>/dev/null | head -1)"
fi

# Record what the harness needs to read back: the resolved state of this stage.
{
  echo "arch=$ARCH"
  echo "helm=$(helm version --short 2>/dev/null | head -1)"
  echo "helmfile=$(helmfile --version 2>/dev/null | head -1)"
  echo "k3s=$(k3s --version 2>/dev/null | head -1)"
} | sudo tee "$STATE/facts" >/dev/null

log "provisioning complete"
exit 0
