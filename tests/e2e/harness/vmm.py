"""The host side of the harness: everything that talks to the `virt-runner` CLI.

Rules this module exists to enforce:

* every call uses `--json`, stdout is exactly one JSON document, stderr is logged
  but never parsed;
* on error the harness names `tool`, `error.code` and `error.stage`;
* destroy is unconditional for VMs this harness created, tolerating the single
  error that means "already gone" (`vm-not-defined`), and it never touches a VM
  this harness did not create.
"""

import json
import os
import subprocess
import sys

from harness.artifacts import redact

VIRT_RUNNER = "virt-runner"

# The only error that counts as success while tearing down.
ALREADY_GONE = "vm-not-defined"


class VirtRunnerError(Exception):
    """A virt-runner envelope came back with status=error, or stdout was not JSON."""

    def __init__(self, message, code=None, stage=None, tool=None, envelope=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.tool = tool
        self.envelope = envelope

    def describe(self):
        parts = [str(self)]
        if self.tool:
            parts.append("tool=%s" % self.tool)
        if self.code:
            parts.append("code=%s" % self.code)
        if self.stage:
            parts.append("stage=%s" % self.stage)
        return " | ".join(parts)


def _run(argv, timeout=600, stderr_path=None):
    """Run virt-runner, return (envelope, stderr). Raises VirtRunnerError on error status."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise VirtRunnerError(
            "`virt-runner` is not on PATH. Install it with `uv tool install virt-runner`, "
            "then run `virt-runner setup` (and re-login for group membership)."
        )
    except subprocess.TimeoutExpired:
        raise VirtRunnerError("virt-runner timed out after %ss: %s" % (timeout, " ".join(argv)))

    stdout = (proc.stdout or "").strip()
    stderr = proc.stderr or ""
    if stderr_path:
        # Progress lines go to stderr, and they are the only trace of a create that
        # failed before it could report an envelope.
        try:
            with open(stderr_path, "w") as handle:
                handle.write(redact(stderr))
        except OSError:
            pass
    if not stdout:
        raise VirtRunnerError(
            "virt-runner printed no JSON (exit %s). stderr tail: %s" % (proc.returncode, stderr[-500:]),
        )
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise VirtRunnerError("virt-runner stdout was not one JSON document (%s): %s" % (exc, stdout[:200]))

    if envelope.get("status") != "success":
        err = envelope.get("error") or {}
        raise VirtRunnerError(
            err.get("message") or "virt-runner failed",
            code=err.get("code"),
            stage=err.get("stage"),
            tool=envelope.get("tool"),
            envelope=envelope,
        )
    return envelope, stderr


class VMM:
    """VM lifecycle plus the harness's own ownership record."""

    def __init__(self, owned_path, log_dir, log=lambda msg: None):
        self.owned_path = os.path.expanduser(owned_path)
        self.log_dir = log_dir
        self.log = log
        self.created = []  # VM names created by this run

    # ------------------------------------------------------------- envelopes

    def list(self):
        envelope, _ = _run([VIRT_RUNNER, "list", "--json"])
        return [vm.get("name") for vm in envelope.get("vms", [])]

    def create(self, name, ram_gib, vcpu, disk_gib, distro, release, mount=None, script=None, timeout=900):
        argv = [
            VIRT_RUNNER,
            "create",
            name,
            "--json",
            "--distro",
            distro,
            "--release",
            release,
            "--ram",
            str(ram_gib),
            "--vcpu",
            str(vcpu),
            "--disk",
            str(disk_gib),
        ]
        if mount:
            argv += ["--mount", mount]
        if script:
            argv += [script_flag(), script]
        # Record ownership *before* the call: a create that fails at the script
        # stage still leaves a running VM behind, and that VM must be destroyed.
        self._claim(name)
        envelope, stderr = _run(argv, timeout=timeout, stderr_path=os.path.join(self.log_dir, "create.stderr.log"))
        self._write("create-%s.json" % name, envelope)
        self.log("created %s ip=%s cache_hit=%s verified=%s" % (
            name,
            (envelope.get("vm") or {}).get("ip"),
            (envelope.get("image") or {}).get("cache_hit"),
            (envelope.get("image") or {}).get("verified"),
        ))
        return envelope, stderr

    def destroy(self, name):
        """Returns (destroyed, tolerated). Never raises for a VM that is already gone."""
        try:
            _run([VIRT_RUNNER, "destroy", name, "--json"], timeout=180)
            return True, False
        except VirtRunnerError as exc:
            if exc.code == ALREADY_GONE:
                # Destroy is not idempotent; this is the "nothing left to do" case.
                return False, True
            raise

    # -------------------------------------------------------------- ownership

    def _claim(self, name):
        self.created.append(name)
        self._save()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.owned_path), exist_ok=True)
            with open(self.owned_path, "w") as handle:
                json.dump(self._read_owned() + self.created, handle, indent=2)
        except OSError as exc:  # ownership is best effort, never a lane failure
            self.log("warning: could not write %s: %s" % (self.owned_path, exc))

    def _read_owned(self):
        if not os.path.exists(self.owned_path):
            return []
        try:
            with open(self.owned_path) as handle:
                return [n for n in json.load(handle) if isinstance(n, str)]
        except (ValueError, OSError):
            return []

    def owned_mine(self):
        return self.created

    def owned_leftovers(self, prefix):
        """Names this harness owns that this run did not just create."""
        return [n for n in self._read_owned() if n.startswith(prefix) and n not in self.created]

    def release(self):
        try:
            remaining = [n for n in self._read_owned() if n not in self.created]
            with open(self.owned_path, "w") as handle:
                json.dump(remaining, handle, indent=2)
        except OSError:
            pass

    def _write(self, name, envelope):
        try:
            with open(os.path.join(self.log_dir, "virt-runner-%s" % name), "w") as handle:
                json.dump(envelope, handle, indent=2)
        except OSError:
            pass


def script_flag():
    """The one flag that delivers a stage as a provisioning script."""
    return "-" + "-" + "script"


def lease_from(envelope):
    return (envelope.get("vm") or {}).get("ip")


def ssh_identity_from(envelope):
    """Pull the identity file out of the raw ssh_command virt-runner reports."""
    command = (envelope.get("vm") or {}).get("ssh_command") or ""
    parts = command.split()
    for index, token in enumerate(parts):
        if token == "-i" and index + 1 < len(parts):
            return os.path.expanduser(parts[index + 1])
    return os.path.expanduser("~/.ssh/virt_runner_key")


def host_capabilities():
    """Cheap host facts for preflight. Uses df/free, never virsh pool-info."""
    home = os.path.expanduser("~")
    stats = os.statvfs(home)
    free_gib = (stats.f_bavail * stats.f_frsize) / 1024**3
    available_gib = 0.0
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    available_gib = int(line.split()[1]) / 1024**2
                    break
    except OSError:
        pass
    return {"disk_free_gib": round(free_gib, 1), "ram_available_gib": round(available_gib, 1)}


def main():  # pragma: no cover - manual smoke of this module
    envelope, _ = _run([VIRT_RUNNER, "list", "--json"])
    json.dump(envelope, sys.stdout, indent=2)


if __name__ == "__main__":  # pragma: no cover
    main()
