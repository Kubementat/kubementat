# Provisioning: single-node k3s plus kubementat's prerequisite set, in one shot.
#
# This is the only stage virt-runner delivers as a `--script` (FR-13, and FR-12/FR-24 below: the
# requirements live in `docs/plans/testsuite-requirements.md`). That delivery decides the shape of
# this file: a `--script` arrives with no arguments and no environment beyond the `@PLACEHOLDER@`
# paths `suite.py` substitutes, so nothing can be told to it per run and it must decide everything
# itself. Every later stage goes command by command through the ssh primitive instead, where each
# command gets its own exit code. Everything here is idempotent because a failed script leaves the
# VM running, and the operator's next move is to re-run it rather than destroy it.
#
# The guest is `--distro ubuntu --release resolute` (Ubuntu 26.04), which the harness names on the
# create instead of accepting virt-runner's profile default: a profile edit must not silently change
# which operating system the suite is proving.
#
# Budget: 600-630 s for this stage, the VM create inside it (`timeouts` in harness/config.py), of
# which a warm-cache smoke run measured 182 s (`docs/README_E2E_TESTS.md`). The create alone is
# 16-30 s of that, which is why each install below first asks whether the guest already has it.

set -euo pipefail

WORKDIR="@GUEST_TREE@"
MOUNT="@GUEST_MOUNT@"
STATE="@PROVISION_STATE_DIR@"
USER="ubuntu"

export DEBIAN_FRONTEND=noninteractive

log() { echo "[01-provision] $*" >&2; }

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
# The packages split two ways, and the split is not what the product checks. pip, gpg and git-crypt
# are what kubementat itself needs: `KUBEMENTAT_DEPENDENCIES` refuses a missing pip or gpg, and
# `config init` cannot make an encrypted tree without git-crypt.
#
# yq and jq are not -- nothing in `src/` execs either one since their rows were deleted -- and they
# go in anyway because the lane asks for them: `assert:guest-tools-present` counts yq among the
# tools that must be on the guest, and later stages read JSON back with jq. Installing them here is
# what makes a missing one a named infra failure at stage 01 instead of an unreadable one at 02.
# curl and ca-certificates are neither: they are only how section 2 fetches.
#
# python3-yaml goes in for a fifth reason: the version reads below run `versions.py` on the
# *system* python3, before any virtualenv exists, and it imports yaml to answer. Without it a
# missing yaml module would read as a missing default version, which is the wrong sentence to
# say about a guest that merely lacks a package.
log "apt update + git-crypt/yq/python3-pip/python3-yaml"
sudo apt-get -qq update
sudo apt-get -qq install -y git-crypt yq python3-pip python3-yaml curl ca-certificates jq

# ----------------------------------------------------------------- 2. tooling
# The pinned binaries here (helm, helmfile) take their versions from `constants.PINNED_BINARY_VERSIONS`,
# read out of the checkout, and never from a literal. The fixture tools this section
# also installs -- tkn below, k3s in section 3 -- take theirs from `versions.yaml`, the file that
# owns versions the product's binary table has no row for.
# The literal is what let the guest run helm 3.22.1 and helmfile 1.8.2 while
# the checkout declared 4.3.1 and 1.7.0: the lane was green, `tool_versions` knew, and the evidence
# was about whatever the installer handed us that day rather than about kubementat.
#
# Two things keep that from happening again, and neither one is a comment: this section installs
# what the table says, and stage 01 states what the guest ended up running
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

# `pin <binary>` -> the version that file declares for it. constants.py now reads that answer
# through versions.py out of `versions.yaml`, but the whole chain is yaml and pathlib, so plain
# python3 still reads it -- no virtualenv, and nothing installed later has to work first.
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

# `pin_versions <dotted.path>` -> the value `versions.yaml` declares there, read through the
# file's own CLI. It exists for the versions constants.py has no row for -- the k3s release this
# script installs and the tkn the CI image's build arg also carries -- so the answer and its
# refusal come from the same reader every Python consumer uses, and nothing here parses yaml.
pin_versions() {
  env PYTHONPATH="$PIN_SOURCE/src" python3 -m kubementat.core.versions --get "$1"
}

# The same answer from the binary on PATH, reduced to the triple the pin is written as. The product
# reads versions with `automations.install.dependencies.first_binary_version` and warns; this is its
# shell counterpart, used to decide whether an already-present binary is the pinned one and to name
# the version when something below fails. The verdict on a wrong version is the lane's, not this
# script's -- see `LaneRunner._tool_version_drift`.
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

# The tkn version is one line of `versions.yaml` -- `ci_image.TKN_CLI_VERSION` -- and both sides
# that used to own a copy resolve it now: this stage reads it through the file's CLI, and the CI
# image build passes that same line as its `TKN_CLI_VERSION` build arg. It was written twice and
# checked nowhere, which is exactly how the lane and the CI image drifted apart once; resolving
# one owner retires the agreement-in-luck, so there is no "change both together" note left to
# keep. The product itself still only asks whether tkn exists (`BINARY_VERSION_ARGV` has no row
# for it), so what this stage installs is the file's pin and what records it is `result.json`'s
# `tool_versions`, read off the guest's own binary.
TKN_VERSION="$(pin_versions ci_image.TKN_CLI_VERSION)"
log "tkn $TKN_VERSION"
if [ ! -x /usr/local/bin/tkn ]; then
  curl -fsSL -o /tmp/tkn.tgz \
    "https://github.com/tektoncd/cli/releases/download/v${TKN_VERSION}/tkn_${TKN_VERSION}_Linux_${ARCH_TKN}.tar.gz"
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
# Measured: a bare Ubuntu 26.04 guest needs no preparation of any kind -- no kernel modules, no apt
# upgrade -- and is Ready in ~10 s (FR-20's measured 9.6 s). What it does need is a version, which
# the line below resolves from `versions.yaml`'s `e2e.k3s` through the same CLI the pinned-binary
# reads above use.
#
# The harness records k3s in `result.json` (`_tool_versions`) and declares it nowhere else: k3s is
# the cluster and not a binary kubementat execs, so `PINNED_BINARY_VERSIONS` has no row for it.
# What a green full run measured is what the pin should name, so it is a certified version and not
# the stable channel's latest. Why a pin and not the channel: a promotion off stable used to arrive
# as a guest that crash-looped with no owner and no note, because recording the version had been
# the whole implementation and nothing read the record. The pin moves that drift out of accident
# and into one deliberate edit of one line of the file.

# /etc/rancher/k3s/k3s.yaml is 0600 root:root and k3s resets it to 600 on every restart, which is
# enough to break `initialize`: kubementat's cluster calls are kubectl calls, and kubectl falls back
# to ~/.kube/config when nothing points it elsewhere. Hence the mode k3s writes rather than a chmod
# a restart undoes -- the config file, not a permission bump. It is written before k3s ever starts,
# so the guest takes it on its first boot and nothing restarts that boot mid-flight: a restart
# during the first boot used to interrupt the embedded-CRD registration, a CRD left at
# reason=Installing is never completed afterwards, and k3s 1.37's server spends 60s waiting on one
# and exits -- a crash-looping service with a Ready node and zero pods. The mode and the restart
# must not be traded away either: leaving this write until after the install left a fresh guest
# running at 600 with nobody restarting it, and `kubectl` said only "Unable to read k3s.yaml".
# Only a guest whose k3s started before this file existed restarts for it, and by then every CRD is
# Established. The symlink that follows the install is for the Python client, which reads
# $KUBECONFIG or ~/.kube/config and nothing else.
log "kubeconfig mode"
sudo mkdir -p /etc/rancher/k3s
config_written=0
if ! grep -q 'write-kubeconfig-mode' /etc/rancher/k3s/config.yaml 2>/dev/null; then
  printf 'write-kubeconfig-mode: 644\n' | sudo tee /etc/rancher/k3s/config.yaml >/dev/null
  config_written=1
fi

K3S_VERSION="$(pin_versions e2e.k3s)"
log "k3s $K3S_VERSION"
k3s_installed_now=0
if [ ! -x /usr/local/bin/k3s ] && ! command -v k3s >/dev/null 2>&1; then
  curl -sfL https://get.k3s.io | sudo env INSTALL_K3S_VERSION="$K3S_VERSION" sh - >&2
  k3s_installed_now=1
# A guest that already has k3s is skipped only when it already has *this* k3s. The original guard
# asked "is k3s installed", which is how a promoted guest survived a re-run of this script: the check
# passed, the wrong binary stayed, and the lane died minutes later on a CRD that never finishes
# establishing. So a wrong version is reinstalled here rather than noticed downstream.
#
# The version is matched out of `k3s --version` ("k3s version v1.36.5+k3s1 (deadbeef)") rather than
# split by position: the last field is the commit hash, so a comparison that looked exact was
# comparing the wrong thing.
elif [ "$(k3s --version 2>/dev/null | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+' || true)" != "$K3S_VERSION" ]; then
  log "k3s is not $K3S_VERSION; reinstalling the pinned release"
  curl -sfL https://get.k3s.io | sudo env INSTALL_K3S_VERSION="$K3S_VERSION" sh - >&2
  k3s_installed_now=1
fi

# The config was answered above, before k3s started. A restart here is only for the guest whose
# k3s had already started before this file existed, and it is safe then: the CRDs long since
# established on that boot.
if [ "$config_written" = 1 ] && [ "$k3s_installed_now" = 0 ] && systemctl is-active --quiet k3s; then
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
# The checkout may be a linked git worktree, where `.git` is a `gitdir:` pointer
# file rather than a directory. The guard asks only whether the mounted checkout is
# there, so it tests existence: a pointer file is a supported checkout layout, not a
# missing tree. (The `tar` below copies the whole tree, `.git` pointer included.)
if [ -e "$MOUNT/.git" ]; then
  # The two exclusions are the copy's only edits: the guest builds its own virtualenv in
  # `uv sync` below, and copying the host's would hand it a tree of broken absolute paths.
  tar -C "$MOUNT" --exclude=./.venv --exclude=./.pytest_cache -cf - . | tar -C "$WORKDIR" -xf -
else
  echo "the mounted checkout is missing at $MOUNT" >&2
  exit 3
fi

# uv sync inside the copy so nothing writes into the host tree.
log "uv sync --all-extras"
( cd "$WORKDIR" && env PATH="/usr/local/bin:$PATH" uv sync --all-extras ) >&2

# k3s installs its own kubectl in /usr/local/bin, already on a plain user's PATH, so there is nothing
# here to fix: the dependency check's `kubectl` row resolves to that binary. This line records the
# version, because "the lane ran kubectl 1.35" is a fact worth having in the log without asking a
# cluster that may no longer be up.
if [ -x /usr/local/bin/kubectl ]; then
  log "kubectl -> $(kubectl version --client=true 2>/dev/null | head -1)"
fi

# The one artifact this stage writes that nothing reads. The harness takes its version evidence by
# asking the guest's own binaries (`LaneRunner._tool_versions`, and the pinned-version probe), never
# from a file this script wrote, and that is deliberate: a number recorded here could be wrong about
# the binary that is actually running. So $STATE/facts exists for whoever sshes into a
# half-provisioned guest and for no automation -- do not add a reader that trusts it.
{
  echo "arch=$ARCH"
  echo "helm=$(helm version --short 2>/dev/null | head -1)"
  echo "helmfile=$(helmfile --version 2>/dev/null | head -1)"
  echo "k3s=$(k3s --version 2>/dev/null | head -1)"
} | sudo tee "$STATE/facts" >/dev/null

log "provisioning complete"
exit 0
