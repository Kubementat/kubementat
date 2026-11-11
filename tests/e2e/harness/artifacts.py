"""Artifact writing: run directory, redacted logs, snapshots, `result.json`, retention.

A failed run's directory must be self-sufficient to diagnose the bug without
re-running it. Nothing is uploaded and nothing is scheduled: retention is
"keep failures until deleted, prune successes to the last 10" (NFR 6.4).
"""

import json
import os
import re
import time

from harness import config


def redact(text):
    """Mask secret-shaped values for the fixed key list. Applied to every log file."""
    if not text:
        return text
    for key in config.REDACT_KEYS:
        text = re.sub(r'("%s"\s*:\s*)"[^"]*"' % re.escape(key), r"\1\"REDACTED\"", text, flags=re.IGNORECASE)
        text = re.sub(r"(%s\s*[=:]\s*)[^\s;|'\"]+" % re.escape(key), r"\1REDACTED", text, flags=re.IGNORECASE)
    return text


class Artifacts:
    def __init__(self, run_dir, keep_failures=True):
        self.run_dir = run_dir
        self.stages_dir = os.path.join(run_dir, "stages")
        self.snapshots_dir = os.path.join(run_dir, "snapshots")
        self.virt_runner_dir = os.path.join(run_dir, "virt-runner")
        for directory in (self.run_dir, self.stages_dir, self.snapshots_dir, self.virt_runner_dir):
            os.makedirs(directory, exist_ok=True)

    # ------------------------------------------------------------------ files

    def log(self, stage, name, text):
        path = os.path.join(self.stages_dir, "%s--%s.log" % (stage, name))
        with open(path, "w") as handle:
            handle.write(redact(text or ""))
        return path

    def snapshot(self, name, obj):
        path = os.path.join(self.snapshots_dir, "%s.json" % name)
        with open(path, "w") as handle:
            json.dump(obj, handle, indent=2, sort_keys=True)
        return path

    def envelope(self, name, envelope):
        path = os.path.join(self.virt_runner_dir, "%s.json" % name)
        with open(path, "w") as handle:
            json.dump(envelope, handle, indent=2)
        return path

    # ----------------------------------------------------------------- result

    def write_result(self, result):
        path = os.path.join(self.run_dir, "result.json")
        with open(path, "w") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, default=str)
        return path

    # -------------------------------------------------------------- retention

    @staticmethod
    def prune(runs_root, keep_successes=10):
        root = os.path.expanduser(runs_root)
        if not os.path.isdir(root):
            return []
        successes, failures = [], []
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            result_path = os.path.join(path, "result.json")
            if not os.path.isfile(result_path):
                continue
            try:
                with open(result_path) as handle:
                    status = json.load(handle).get("status")
            except (OSError, ValueError):
                continue
            (successes if status == "passed" else failures).append(path)
        pruned = []
        for path in sorted(successes)[:-keep_successes]:
            import shutil

            shutil.rmtree(path, ignore_errors=True)
            pruned.append(path)
        return pruned


def default_run_dir(lane, sha, started_utc):
    stamp = time.strftime("%Y%m%dT%H%M%SZ", started_utc)
    return os.path.expanduser(os.path.join(config.RUNS_ROOT, "%s-%s-%s" % (stamp, lane, sha[:7])))
