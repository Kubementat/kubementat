#!/usr/bin/env python3
"""The kubementat E2E suite: `create VM -> provision k3s -> install -> exercise ->
re-run -> uninstall -> assert absence -> destroy VM`, one command, by hand.

Stdlib and `subprocess` only. No CI environment, no env-gated runner, no Makefile.
Run it from the repo root:

    uv run python tests/e2e/suite.py                       # smoke VM, install scope
    uv run python tests/e2e/suite.py --scope full          # every stage, reinstall included
    uv run python tests/e2e/suite.py --lane full --scope full

Exit code is 0 only when every assertion passed. A run's directory under
`~/.kmt-e2e/runs/` is the evidence; it names the git SHA it tested.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from harness import artifacts as artifacts_lib  # noqa: E402
from harness import asserts, config, stages, vmm  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PROVISION_SCRIPT = os.path.join(HERE, "stages", "01-provision.sh")


class StageTimeout(Exception):
    pass


class PreflightFailure(Exception):
    pass


class Opts:
    """Everything a lane needs, resolved from flags and defaults. Every input is a
    flag, a default, or read from the repo — never a CI environment variable."""

    def __init__(self, args):
        self.lane = args.lane
        self.scope = args.scope
        self.keep_vm = args.keep_vm
        self.dry_run = args.dry_run
        self.repo_root = REPO_ROOT
        self.provision_script = PROVISION_SCRIPT
        self.started = datetime.datetime.now(datetime.timezone.utc)
        self.run_id = "%s-%s-%s" % (self.started.strftime("%Y%m%dT%H%M%SZ"), args.lane, os.getpid())
        self.vm_name = "%s%s-%s-%s" % (config.VM_NAME_PREFIX, args.lane,
                                       int(time.mktime(self.started.timetuple())), os.getpid())
        self.known_hosts = None  # set once the run dir exists
        self.env = dict(config.DUMMY_ENV)
        # No credential is added here. `GRAFANA_ADMIN_PASSWORD` used to be randomized into the guest's
        # environment on every run, which is an answer arriving through the ambient environment --
        # exactly what the ambient-answer rule deleted -- and it was also inert, because kubementat reads the
        # key out of `static.encrypted.json` and `kmt config init` generates that value when the tree
        # is prepared. The lane reads the answer back, so the tree is the only source of truth.
        lane = config.LANES[args.lane]
        self.lane_object = lane
        self.ram = args.vm_ram or lane.ram_gib
        self.disk = args.vm_disk or lane.disk_gib
        self.vcpu = args.vm_vcpu or lane.vcpu

    def stage_timeout(self, stage, default):
        """A budget can be overridden from the environment: PROVISION_TIMEOUT=1s or
        E2E_TIMEOUT_01_PROVISION=30."""
        if stage == "01-provision" and "PROVISION_TIMEOUT" in os.environ:
            return _duration(os.environ["PROVISION_TIMEOUT"])
        key = "E2E_TIMEOUT_%s" % stage.upper().replace("-", "_")
        if key in os.environ:
            return _duration(os.environ[key])
        return default


def _duration(value):
    match = re.match(r"^(\d+(?:\.\d+)?)([smh]?)$", str(value).strip().lower())
    if not match:
        return float(value)
    number, unit = float(match.group(1)), (match.group(2) or "s")
    return number * {"s": 1, "m": 60, "h": 3600}[unit]


class Recorder:
    """Per-assertion bookkeeping. This is the thing that decides the exit code."""

    def __init__(self, state, log):
        self.state = state
        self.log = log
        self.assertions = []
        self.stages = []
        self.started = time.time()

    def stage(self, name, seconds, note=""):
        self.stages.append({"stage": name, "seconds": round(seconds, 1), "note": note})

    def record(self, stage, name, cls, passed, detail="", attempts=1, artifacts=None):
        entry = asserts.Assertion(stage, name, cls, bool(passed), detail, attempts, artifacts or []).to_dict()
        self.assertions.append(entry)
        marker = "PASS" if entry["passed"] else "FAIL"
        self.log("%-4s %-8s %s %s" % (stage, marker, name, detail if not entry["passed"] or len(detail) < 90 else detail[:87] + "..."))
        if entry["passed"] and entry["attempts"] > 1:
            self.state["flaky"] = True

    @property
    def failed(self):
        return [entry for entry in self.assertions if not entry["passed"]]

    @property
    def ok(self):
        return bool(self.assertions) and not self.failed


def sha(text):
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def git_sha():
    try:
        return subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def git_status():
    return subprocess.run(["git", "-C", REPO_ROOT, "status", "--porcelain"], capture_output=True,
                          text=True).stdout


def integrity_snapshot():
    """The checkout's dirty state is the point, so compare rather than enforce."""
    files = {}
    for name in config.INTEGRITY_FILES:
        path = os.path.join(REPO_ROOT, name)
        if os.path.exists(path):
            with open(path, "rb") as handle:
                files[name] = hashlib.sha256(handle.read()).hexdigest()
    return {"status": sorted(git_status().splitlines()), "files": files}


class Suite:
    def __init__(self, opts, args):
        self.opts = opts
        self.args = args
        self.log_lines = []
        self.started = time.time()
        self.state = {"lane": opts.lane, "scope": opts.scope, "git_sha": git_sha(), "flaky": False, "skipped": [],
                      "vm_name": opts.vm_name, "keep_vm": opts.keep_vm}
        self.artifacts = None
        self.recorder = None
        self.runner = None
        self.vmm = None
        self.lock_handle = None
        self.vm_created = False

    # ------------------------------------------------------------------ utils

    def log(self, message):
        stamp = time.strftime("%H:%M:%S")
        line = "[%s] %s" % (stamp, message)
        print(line, flush=True)
        self.log_lines.append(line)

    def record(self, stage, name, cls, passed, detail="", attempts=1, artifacts=None):
        self.recorder.record(stage, name, cls, passed, detail, attempts, artifacts)

    # ------------------------------------------------------------------ locking

    def acquire_lock(self):
        handle = open(config.LOCK_PATH, "w")
        deadline = time.time() + config.LOCK_QUEUE_TIMEOUT_S
        waited = 0
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if waited:
                    self.log("acquired the host lock after %ss" % waited)
                self.lock_handle = handle
                return
            except BlockingIOError:
                if waited == 0:
                    self.log("queued: waiting for the other kmt-e2e run on this host")
                time.sleep(5)
                waited += 5
                if waited % config.QUEUE_MSG_EVERY_S == 0:
                    self.log("queued: waiting for kmt-e2e run, %ss elapsed" % waited)
                if time.time() > deadline:
                    raise PreflightFailure("queue-timeout: another kmt-e2e run still holds %s after %ss"
                                           % (config.LOCK_PATH, waited))

    # --------------------------------------------------------------- preflight

    def preflight(self):
        """Everything that can be checked before a VM exists, plus the static audit."""
        self._audit_self()
        version = _text([vmm.VIRT_RUNNER, "--version"])
        if version is None:
            raise PreflightFailure("virt-runner is not installed. `uv tool install virt-runner`, then "
                                   "`virt-runner setup --check`, then `sudo virt-runner setup`, then log out and back in.")
        self.log("virt-runner %s" % version[0].strip())
        self.state["virt_runner_version"] = version[0].strip()
        try:
            listed, _stderr = vmm._run([vmm.VIRT_RUNNER, "list", "--json"], timeout=120)
        except vmm.VirtRunnerError as exc:
            raise PreflightFailure(
                "virt-runner cannot serve VMs (%s). Fix the host before running the suite: "
                "`virt-runner setup --check`, then `sudo virt-runner setup`, then re-login."
                % exc.describe())
        self.state["pre_existing_vms"] = sorted(vm.get("name") for vm in listed.get("vms", []) if vm.get("name"))

        facts = vmm.host_capabilities()
        self.state["host"] = facts
        needed_ram = self.opts.ram + config.HOST_RAM_HEADROOM_GIB
        self.record("preflight", "host-ram-sufficient", "infra", facts["ram_available_gib"] >= needed_ram,
                    "available=%s GiB, need >= %s GiB for a %s GiB VM"
                    % (facts["ram_available_gib"], needed_ram, self.opts.ram))
        self.record("preflight", "host-disk-sufficient", "infra", facts["disk_free_gib"] >= config.MIN_HOST_FREE_DISK_GIB,
                    "free=%s GiB, need >= %s GiB (measured: a 25 GiB smoke VM writes ~16 GiB of real data)"
                    % (facts["disk_free_gib"], config.MIN_HOST_FREE_DISK_GIB))
        if self.recorder.failed:
            raise PreflightFailure("host cannot host this lane: " + "; ".join(
                item["detail"] for item in self.recorder.failed))

        audit = _text([vmm.VIRT_RUNNER, "setup", "--check"], timeout=300)
        if audit is not None and audit[2] != 0:
            self.record("preflight", "host-setup-audit", "infra", False,
                        "virt-runner setup --check is not green (exit %s); `sudo virt-runner setup` then re-login. %s"
                        % (audit[2], (audit[0] + audit[1])[-240:]))

    def _audit_self(self):
        """Static self-audit: the requirements' greps, run by the suite itself."""
        for name, pattern in config.FORBIDDEN_PATTERNS:
            hits = []
            for root, _dirs, files in os.walk(HERE):
                if ".venv" in root or "__pycache__" in root:
                    continue
                for filename in files:
                    if not filename.endswith((".py", ".sh")):
                        continue
                    path = os.path.join(root, filename)
                    try:
                        with open(path) as handle:
                            text = handle.read()
                    except OSError:
                        continue
                    hits += ["%s:%s" % (os.path.relpath(path, HERE), line.strip())
                             for line in text.splitlines()
                             if re.search(pattern, line) and not line.strip().startswith("#")]
            self.record("preflight", "self-audit:%s" % name, "product", not hits,
                        "forbidden hits: %s" % (hits[:5] or "none"))
        # Exactly one stage may be delivered as a provisioning script.
        stage_dir = os.path.join(HERE, "stages")
        scripts = sorted(name for name in os.listdir(stage_dir) if name.endswith(".sh"))
        sources = [os.path.join(HERE, "suite.py"), os.path.join(HERE, "harness", "vmm.py")]
        uses = 0
        for source in sources:
            with open(source) as handle:
                uses += handle.read().count(config.SCRIPT_FLAG)
        self.record("preflight", "self-audit:script-used-once", "product", len(scripts) == 1,
                    "%s stage script(s), referenced by %s source file(s)" % (len(scripts), uses))
        legacy = [os.path.join(REPO_ROOT, "tests", name)
                  for name in ("test_installation.sh", "uninstall.sh", "reset_configuration.sh")]
        self.record("preflight", "self-audit:legacy-tests-removed", "product",
                    not [path for path in legacy if os.path.exists(path)],
                    "the three legacy tests/*.sh scripts must be gone")
        # Information, not an assertion: a row that could only pass inflated the count.
        self.state["needs_grouped_cli_form"] = list(config.NEEDS_FIN62)
        self.log("%d capability row(s) still need the grouped CLI form: %s"
                 % (len(config.NEEDS_FIN62), config.NEEDS_FIN62))

    # ------------------------------------------------------------- checkout guards

    def baseline_tree(self):
        self.state["tree_baseline"] = integrity_snapshot()
        self.state["tree_baseline_sha"] = sha(json.dumps(self.state["tree_baseline"], sort_keys=True))

    def assert_tree_untouched(self):
        """After a full run, the checkout is exactly as it was."""
        now = integrity_snapshot()
        changed_status = [line for line in (set(now["status"]) ^ set(self.state["tree_baseline"]["status"]))]
        changed_files = [name for name, digest in now["files"].items()
                         if self.state["tree_baseline"]["files"].get(name) != digest]
        added_files = [name for name in now["files"] if name not in self.state["tree_baseline"]["files"]]
        self.record("teardown", "checkout-untouched", "product", not changed_status and not changed_files and not added_files,
                    "dirty-state delta=%s, modified secret-ish files=%s, new=%s"
                    % (changed_status or "none", changed_files or "none", added_files or "none"))

    # ---------------------------------------------------------------- lane run

    def run(self):
        self.runner = stages.LaneRunner(self.opts.lane_object, self.opts, self.artifacts, self.vmm, self.state,
                                        self.record, self.log)
        for name, function, timeout in self.runner.stages():
            self.runner.stage = name
            if self.opts.dry_run:
                self.record(name, "dry-run:stage", "infra", True, "stage reached, nothing executed")
                continue
            started = time.time()
            outcome = "ok"
            try:
                with _alarm(timeout):
                    function()
            except stages.StageFailed as exc:
                outcome = "failed"
                self.record(name, "stage:%s" % name, "product", False, "stage aborted: %s" % exc)
            except StageTimeout:
                outcome = "timeout"
                self.record(name, "stage:%s" % name, "product", False,
                            "timeout:%s exceeded %ss budget" % (name, timeout))
            except asserts.CapacityAborted as exc:
                outcome = "capacity"
                self.record(name, "stage:%s" % name, "infra", False, "%s" % exc)
            except vmm.VirtRunnerError as exc:
                outcome = "virt-runner"
                self.record(name, "stage:%s" % name, "infra", False, exc.describe())
            except Exception as exc:  # a stack trace must become an assertion, not stdout
                outcome = "error"
                self.record(name, "stage:%s" % name, "product", False, "%s: %s" % (type(exc).__name__, exc))
            elapsed = time.time() - started
            note = ""
            if elapsed > timeout * config.SLOW_FACTOR:
                note = "slow (%.1fs over %.0fs target)" % (elapsed, timeout)
            self.recorder.stage(name, elapsed, note)
            if outcome != "ok":
                self.dump_diagnostics()
                self.state["failed_stage"] = name
                break

    def dump_diagnostics(self):
        if self.runner.guest:
            self.log("collecting failure diagnostics into the artifact dir")
            self.runner.dump_failure_context()

    # ------------------------------------------------------------------ teardown

    def destroy(self):
        if self.opts.keep_vm:
            self.log("--keep-vm: leaving the VM up")
            print("  debug with: virt-runner ssh %s" % self.opts.vm_name)
            return
        for name in self.vmm.owned_mine():
            self._let_create_settle(name)
            try:
                destroyed, tolerated = self.vmm.destroy(name)
                self.log("destroy %s: %s" % (name, "destroyed" if destroyed else "already gone"))
            except vmm.VirtRunnerError as exc:
                self.log("destroy %s FAILED: %s" % (name, exc.describe()))
                self.record("99-teardown", "vm-destroy:%s" % name, "infra", False, exc.describe())
        self.vmm.release()

    def _let_create_settle(self, name, cap=120):
        """A create that outlived its stage budget is still running; destroying it
        mid-flight can leave libvirt with an orphan. Give it a moment to land."""
        deadline = time.time() + cap
        while time.time() < deadline:
            try:
                if name in self.vmm.list():
                    return
            except vmm.VirtRunnerError:
                return
            time.sleep(5)

    def pre_clean(self):
        """Remove leftovers this harness owns, and nothing else."""
        leftovers = self.vmm.owned_leftovers(config.VM_NAME_PREFIX)
        for name in leftovers:
            try:
                self.vmm.destroy(name)
                self.log("pre-clean destroyed owned leftover %s" % name)
            except vmm.VirtRunnerError as exc:
                self.log("could not pre-clean %s: %s" % (name, exc.describe()))
        existing = set(self.vmm.list())
        self.state["foreign_vms_present"] = sorted(name for name in existing if not name.startswith(config.VM_NAME_PREFIX))
        if self.state["foreign_vms_present"]:
            self.log("foreign VMs present, left alone: %s" % ", ".join(self.state["foreign_vms_present"]))

    # ------------------------------------------------------------------ writing

    def finish(self):
        status = "passed" if self.recorder.ok else "failed"
        blockers = []
        haystack = " ".join(
            [item["detail"] for item in self.recorder.failed]
            + list(self.state.get("blocker_evidence") or []))
        for blocker in config.KNOWN_BLOCKERS:
            if re.search(blocker["pattern"], haystack, re.IGNORECASE):
                blockers.append(blocker)
        result = {
            "status": status,
            "exit_code": 0 if status == "passed" else 1,
            "lane": self.opts.lane,
            "scope": self.opts.scope,
            "run_id": self.opts.run_id,
            "git_sha": self.state.get("git_sha"),
            "started_utc": self.opts.started.isoformat(),
            "seconds": round(time.time() - self.started, 1),
            "vm": self.state.get("vm"),
            "vm_shape": {"ram_gib": self.opts.ram, "vcpu": self.opts.vcpu, "disk_gib": self.opts.disk},
            "image": self.state.get("image"),
            "storage_class": self.state.get("storage_class"),
            "config_adjustments": self.state.get("config_adjustments", []),
            "install_groups": self.state.get("install_groups"),
            "helm_releases": self.state.get("helm_releases"),
            "stages": self.recorder.stages,
            "assertions": self.recorder.assertions,
            "skipped": self.state.get("skipped", []),
            # What a stage said it could not answer. It is in result.json rather than only in the log
            # because the evidence attached to a lane run is this file, and a reader who only ever
            # reads `result.json` is exactly the reader the limitation has to reach.
            "not_covered": self.state.get("not_covered", []),
            "flaky": self.state.get("flaky", False),
            "host": self.state.get("host"),
            "tool_versions": self.state.get("tool_versions"),
            "known_blockers_matched": blockers,
            "needs_grouped_cli_form": self.state.get("needs_grouped_cli_form"),
            "keep_vm": self.opts.keep_vm,
            "failed_stage": self.state.get("failed_stage"),
        }
        path = self.artifacts.write_result(result)
        self.artifacts.log("suite", "suite.log", "\n".join(self.log_lines))
        pruned = artifacts_lib.Artifacts.prune(config.RUNS_ROOT)
        if pruned:
            self.log("pruned %s older successful run(s)" % len(pruned))

        print("\n=== assertions (%d) ===" % len(self.recorder.assertions))
        for entry in self.recorder.assertions:
            print("  %s %-44s %s" % ("PASS" if entry["passed"] else "FAIL", entry["name"],
                                     "" if entry["passed"] else entry["detail"][:160]))
        if blockers:
            print("\n=== known product blockers matched ===")
            for blocker in blockers:
                print("  %s: %s" % (blocker["ticket"], blocker["note"]))
        if self.state.get("not_covered"):
            print("\n=== what this rig did not prove ===")
            for line in self.state["not_covered"]:
                print("  %s" % line)
        summary = "\n=== %s: %d assertions, %d failed (lane %s, scope %s, sha %s) ===" % (
            status.upper(), len(self.recorder.assertions), len(self.recorder.failed), self.opts.lane, self.opts.scope,
            str(self.state.get("git_sha"))[:7])
        print(summary)
        print("artifact: %s" % path)
        if self.state.get("failed_stage") and self.opts.keep_vm:
            print("failed stage: %s  debug with: virt-runner ssh %s" % (self.state["failed_stage"], self.opts.vm_name))
        return result["exit_code"]


class _alarm:
    """Per-stage wall-clock budget; reports as `timeout:<stage>`."""

    def __init__(self, seconds):
        self.seconds = float(seconds)

    def __enter__(self):
        def handler(signum, frame):
            raise StageTimeout("stage budget of %ss exhausted" % self.seconds)

        self.previous = signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, *exc):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous)
        return False


def _text(argv, timeout=120):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.stdout, proc.stderr, proc.returncode
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def render_provision_script(opts, destination):
    """Materialise stages/01-provision.sh for this run.

    The rendered file is what gets handed to virt-runner for the single scripted
    stage: the template keeps the guest paths in sync with
    harness/config.py instead of hardcoding them twice.
    """
    source = os.path.join(HERE, "stages", "01-provision.sh")
    with open(source) as handle:
        text = handle.read()
    substitutions = {
        "@GUEST_TREE@": config.GUEST_TREE,
        "@GUEST_MOUNT@": config.GUEST_MOUNT,
        "@PROVISION_STATE_DIR@": config.PROVISION_STATE_DIR,
    }
    for marker, value in substitutions.items():
        text = text.replace(marker, value)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "w") as handle:
        handle.write(text)
    os.chmod(destination, 0o755)
    return destination


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="kubementat E2E suite (virt-runner)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--lane", choices=sorted(config.LANES), default="smoke")
    parser.add_argument("--scope", choices=sorted(config.SCOPES), default=config.DEFAULT_SCOPE,
                        help="install: config init, install, verify, uninstall, destroy the VM. "
                             "full: adds the pipeline run, rerun idempotency, features, the uninstall "
                             "confirmation probes and the reinstall-after-uninstall cycle")
    parser.add_argument("--keep-vm", action="store_true", help="leave the VM up for `virt-runner ssh`")
    parser.add_argument("--vm-ram", type=int, default=0, help="GiB (default: the lane shape)")
    parser.add_argument("--vm-disk", type=int, default=0, help="GiB (default: the lane shape)")
    parser.add_argument("--vm-vcpu", type=int, default=0, help="vCPUs (default: the lane shape)")
    parser.add_argument("--dry-run", action="store_true", help="preflight and stop; create nothing")
    parser.add_argument("--list-capabilities", action="store_true", help="print the capability table and exit")
    args = parser.parse_args(argv)
    # A lane and a scope are two different decisions and this pair is one of the few things in the suite
    # that can be wrong in a way nothing downstream reports: a scope names stages, a lane names their
    # budgets, and a lane that legitimately skips the run, re-run and feature stages has no numbers for
    # them to give. Failing here says "that lane is not measured for that scope" before a VM exists to
    # be created, destroyed, and reported on as if something had been tested -- and it is an argument
    # problem, so it belongs where the arguments are parsed rather than where the run is about to start.
    missing = config.missing_budgets(config.LANES[args.lane], args.scope)
    if missing:
        parser.error("lane %s has no budget for %s, which scope %s runs. Run it with --scope %s"
                     % (args.lane, ", ".join(missing), args.scope,
                        ", or ".join(sorted(scope for scope in config.SCOPES
                                           if config.is_runnable(config.LANES[args.lane], scope)))))
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.list_capabilities:
        for name, cap in config.CAPABILITIES.items():
            print("%-32s %s%s" % (name, " ".join(cap.argv), "  [" + cap.fin62 + "]" if cap.fin62 else ""))
        return 0
    opts = Opts(args)
    # The scope is in the name: an install-only run is not evidence for the reinstall cycle.
    run_dir = artifacts_lib.default_run_dir("%s-%s" % (args.lane, args.scope), git_sha(), opts.started.timetuple())
    suite = Suite(opts, args)
    suite.artifacts = artifacts_lib.Artifacts(run_dir)
    suite.recorder = Recorder(suite.state, suite.log)
    suite.vmm = vmm.VMM(config.OWNED_FILE, os.path.join(run_dir, "virt-runner"), suite.log)
    opts.known_hosts = os.path.join(run_dir, "known_hosts")
    opts.provision_script = render_provision_script(opts, os.path.join(run_dir, "stages", "01-provision.sh"))

    suite.log("kubementat E2E | lane=%s | scope=%s | sha=%s | vm=%s"
              % (args.lane, args.scope, git_sha()[:7], opts.vm_name))
    suite.log("artifact dir: %s" % run_dir)
    suite.baseline_tree()
    exit_code = 1
    try:
        suite.acquire_lock()
        suite.preflight()
        suite.pre_clean()
        suite.log("vm shape: %s vcpu / %s GiB ram / %s GiB disk, image %s %s"
                 % (opts.vcpu, opts.ram, opts.disk, config.DISTRO, config.RELEASE))
        suite.run()
    except PreflightFailure as exc:
        suite.log("PREFLIGHT FAILED: %s" % exc)
        suite.record("preflight", "preflight-ok", "infra", False, str(exc))
    except KeyboardInterrupt:
        suite.log("interrupted")
    except Exception as exc:
        import traceback

        suite.log("HARNESS ERROR: %s" % exc)
        traceback.print_exc(file=sys.stderr)
        suite.record("harness", "harness-error", "infra", False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        try:
            suite.assert_tree_untouched()
        except Exception as exc:
            suite.log("could not verify the checkout: %s" % exc)
        if suite.vmm.created:
            suite.destroy()
        exit_code = suite.finish()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
