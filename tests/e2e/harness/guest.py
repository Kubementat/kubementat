"""The harness's single execution primitive: one hardened ssh line.

`virt-runner ssh` is interactive-only by design, `create --script` carries no
arguments and no environment, and the raw `vm.ssh_command` that `create --json`
reports aborts on a reused DHCP lease. Everything the guest runs therefore goes
through `Guest.run`, which composes exactly one command line, preserves the exit
code and both streams separately, and is the only place that ever spells `ssh`.

Verified contract that this relies on: `exit 42` in the guest exits 42 on the
host, and stdout/stderr stay separate streams.
"""

import os
import re
import shlex
import subprocess

from harness import config

GUEST_WORKDIR_SENTINEL = object()

SSH_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
]


def shquote(value):
    return shlex.quote(str(value))


class GuestTimeout(Exception):
    pass


class Result:
    def __init__(self, exit_code, stdout, stderr, command=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.command = command

    @property
    def ok(self):
        return self.exit_code == 0

    @property
    def output(self):
        return self.stdout + self.stderr

    def excerpt(self, limit=600):
        text = self.output.strip().replace("\n", " | ")
        return text[:limit] + ("..." if len(text) > limit else "")

    def __repr__(self):
        return "Result(exit=%s, out=%r)" % (self.exit_code, self.excerpt(120))


class Guest:
    """All guest interaction, one primitive. No stage builds its own ssh line."""

    def __init__(self, ip, user, identity_file, known_hosts_path, workdir, env=None, log=None):
        self.ip = ip
        self.user = user
        self.identity_file = identity_file
        self.known_hosts_path = known_hosts_path
        self.workdir = workdir
        # Held by reference, not copied: the harness discovers the storage class
        # mid-stage and must be able to add it to the guest environment afterwards.
        self.env = env if env is not None else {}
        self.log = log or (lambda message: None)
        self.retried = []

    # ------------------------------------------------------------- the line

    def _ssh_argv(self, remote_command, use_stdin=False):
        target = "%s@%s" % (self.user, self.ip)
        argv = ["ssh", "-i", self.identity_file] + list(SSH_OPTIONS) + [
            "-o", "UserKnownHostsFile=%s" % self.known_hosts_path,
            target,
            remote_command,
        ]
        return argv

    def _remote(self, command, env, cwd, stdin):
        prefix = "cd %s && " % shquote(cwd) if cwd is not None else ""
        exports = dict(self.env)
        exports.update(env or {})
        envstr = ""
        if exports:
            envstr = "env %s " % " ".join("%s=%s" % (key, shquote(value)) for key, value in sorted(exports.items()))
        body = "bash -s" if command is None else "bash -c %s" % shquote(command)
        return prefix + envstr + body

    def run(self, command=None, timeout=120, env=None, cwd=GUEST_WORKDIR_SENTINEL, stdin=None, retry=True):
        """Run one guest command, return a Result. Never raises for a non-zero exit."""
        if cwd is GUEST_WORKDIR_SENTINEL:
            cwd = self.workdir
        remote = self._remote(command, env, cwd, stdin)
        argv = self._ssh_argv(remote)
        first = self._invoke(argv, stdin, timeout)
        if first.exit_code != 0 and retry and self._is_infra(first):
            self.log("infra-shaped failure, retrying once: %s" % first.excerpt(160))
            self.retried.append(self._pattern(first))
            second = self._invoke(argv, stdin, timeout)
            return second
        return first

    def run_argv(self, argv, **kwargs):
        """Run an argv list (what the capability table resolves to)."""
        return self.run(command=" ".join(shquote(token) for token in argv), **kwargs)

    def run_script(self, script_text, timeout=120, env=None):
        """A multi-line stage, delivered over stdin (`bash -s`)."""
        return self.run(stdin=script_text, timeout=timeout, env=env)

    def write_file(self, path, content, timeout=30):
        """Small file into the guest via stdin; the directory must already exist."""
        return self.run(command="cat > %s" % shquote(path), stdin=content, timeout=timeout)

    # -------------------------------------------------------------- internals

    def _invoke(self, argv, stdin, timeout):
        try:
            proc = subprocess.run(
                argv,
                input=stdin if stdin is not None else "",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return Result(124, "", "ssh timed out after %ss\n%s" % (timeout, exc.stdout or ""), "ssh")
        except OSError as exc:
            return Result(127, "", "could not run ssh: %s" % exc, "ssh")
        return Result(proc.returncode, proc.stdout or "", proc.stderr or "", " ".join(argv[-1:]))

    def _is_infra(self, result):
        return self._pattern(result) is not None

    def _pattern(self, result):
        if result.exit_code == 0:
            return None
        haystack = (result.output or "").lower()
        for pattern in config.RETRYABLE_INFRA_PATTERNS:
            if pattern.lower() in haystack:
                return pattern
        return None
