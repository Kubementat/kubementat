"""The wait behind `kmt tekton run-pipeline --wait`, and the exit codes that make it useful.

`tekton_ci/tasks/run-tekton-pipeline.yml` used to run one bash step that started a pipeline run and
then polled it forever: `while true`, `tkn pipelinerun describe`, no bound. The owner's decision is
that this becomes a flag on the command and nothing else — the Task runs one step and the waiting
lives in `runs.wait_for_pipeline_run`, which reads the Kubernetes API instead of a binary.

Two things this suite exists because they were not true before:

* **The command cannot stay silent about refusing.** `runs.run_pipeline` has always had a
  code for a parallel-run refusal or an unreadable file, and the command function dropped it, so a
  run that was never started exited 0. Every exit code here is asserted, not assumed.
* **The wait has a bound.** `PIPELINE_RUN_TIMEOUT_SECONDS` is thirty minutes, not infinity, and the
  path that runs out of it is non-zero and says what it was waiting for.

Everything below drives the waiter with a fake client and an injected sleep. Nothing here starts a
pod or waits for a real cluster: what a unit suite can say about "one step that blocks for thirty
minutes inside a Task" is what its loop does with the answers it is handed.
"""

import logging
from types import SimpleNamespace

import pytest
from asset_paths import REPO_ROOT, tekton_ci_path
from conftest import PIPELINE_NAMESPACE
from kubernetes import client

from cli.commands import tekton
from kubementat.infrastructure.tekton import runs
from kubementat.infrastructure.tekton.runs import (
    PIPELINE_RUN_POLL_INTERVAL_SECONDS,
    PIPELINE_RUN_TIMEOUT_SECONDS,
)

# The Task ships inside the package: `REPO_ROOT` is where this file's own source reads
# come from, and the manifest is asked of the resolver the install path uses.
TASK = tekton_ci_path("tasks", "run-tekton-pipeline.yml")
# Every module of the package, for the half of the rule that holds everywhere: an import-time sleep
# is a hang for whoever imports the module, whichever module of the package it sits in.
TEKTON_PACKAGE = sorted((REPO_ROOT / "src" / "kubementat" / "infrastructure" / "tekton").glob("*.py"))
def pipelinerun(status, reason, message, name="hello-run-abcdef"):
    """One run, as the API answers it: `status.conditions[0]` is the whole contract.

    `status` is the field that says whether the run is over (`True`/`False`), `reason` and `message`
    are the fields that say why, and a real controller is free to write more than one condition —
    only the first is read, which is the same contract bash's `jq` had.
    """
    return {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
            "metadata": {"name": name, "namespace": PIPELINE_NAMESPACE},
            "status": {"conditions": [{"type": "Succeeded", "status": status,
                                       "reason": reason, "message": message}]}}


class FakePipelineRunReads:
    """The single-object read, answered from a list of replies.

    A reply that is an exception is raised; anything else is returned. When the replies run out the
    last one repeats, because a test that asserts "it stopped asking" is asserting that the loop did
    not ask a fifth time, and a test that did not care should not fail for exhausting its script.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.reads = []

    def get_custom_resource(self, **kwargs):
        self.reads.append(kwargs)
        reply = self.replies[min(len(self.reads) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def read_count(self):
        return len(self.reads)


class Clock:
    """A monotonic clock a test can move, so the bound is measured and not slept through."""

    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return seconds


def waiter(replies):
    """The two things the waiter reads, in the order its signature takes them now.

    This used to build a `TektonUtils` with `__new__` -- the shape
    `test_a_namespace_that_cannot_be_deleted_is_not_mistaken_for_success` uses for the same reason:
    the constructor wants a kubeconfig and a `platform_config`, and the loop under test needs
    neither. The loop lost its `self`, so what it used to read off the instance is returned as the
    arguments it now takes: the client that answers the reads, and the two config answers it lists
    objects by. The namespace is passed per call, exactly as it was before.
    """
    return (FakePipelineRunReads(replies),
            SimpleNamespace(TEKTON_API_GROUP="tekton.dev", TEKTON_API_VERSION="v1"))


def test_the_defaults_are_the_bound_the_ci_task_can_rely_on():
    """The two numbers the Task is written against, in the code and not only in a param.

    Three seconds is what the Task's `polling-sleep-time` default says, and thirty minutes is the
    bound the bash loop never had. A default of `None`, or a loop that treated `timeout_seconds` as
    advisory, would be the same `while true` wearing different clothes.
    """
    assert PIPELINE_RUN_POLL_INTERVAL_SECONDS == 3
    assert PIPELINE_RUN_TIMEOUT_SECONDS == 1800 == 30 * 60


def test_a_run_that_reports_false_fails_and_hands_over_the_reason_and_the_message(caplog):
    """`False` is the failure half, and the text is the whole point of it.

    An operator reading a failed pipeline step gets this sentence and nothing else, so a
    `PipelineRunValidationFailed` has to be told apart from a `Failed` Task by something other than
    the word "failed". Both halves of the condition are in the message the operator sees.
    """
    cluster, config = waiter([pipelinerun("False", "Failed",
                                "Tasks Failed: \"build\"; step \"compile\" exited with code 1")])

    with caplog.at_level(logging.ERROR):
        code = runs.wait_for_pipeline_run(cluster, config, "hello-run-abcdef", PIPELINE_NAMESPACE)

    assert code == 1
    assert "PipelineRunValidationFailed" not in caplog.text  # not invented; the real reason is
    assert "Failed" in caplog.text
    assert "step \"compile\" exited with code 1" in caplog.text, caplog.text
    assert "hello-run-abcdef" in caplog.text


def test_the_bound_expires_on_a_run_that_never_finishes_and_says_what_it_was_waiting_for(
        caplog, monkeypatch):
    """The case bash could not have. A wedged run holds a pod until this number says stop.

    Ten seconds of bound, three-second polls, a run that never reports. The clock is the test's own,
    so the claim that the wait ends *at* the bound and not after it is measured and not asserted:
    five reads, four sleeps, the last one shortened so it lands on the deadline instead of past it.
    """
    clock = Clock()
    # The loop reads `monotonic` through `runs`, so the string names `runs`: patching a module
    # that no longer calls it reaches nothing, and the bound would be measured on a wall clock.
    monkeypatch.setattr("kubementat.infrastructure.tekton.runs.time.monotonic", clock.monotonic)
    cluster, config = waiter([pipelinerun("Unknown", "Running", "Tasks Running")])
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    with caplog.at_level(logging.ERROR):
        code = runs.wait_for_pipeline_run(cluster, config, "hello-run-abcdef", PIPELINE_NAMESPACE,
                                           poll_interval_seconds=3, timeout_seconds=10,
                                           sleep=sleep)

    assert code == 1, "running out of time is not a success"
    assert slept == [3, 3, 3, 1], f"the sleep schedule missed the bound: {slept}"
    assert cluster.read_count == 5
    assert "had not finished after 10s" in caplog.text, caplog.text
    assert "kmt tekton list" in caplog.text, "the timeout says nothing about where to look next"


def test_a_run_that_vanishes_is_reported_as_gone_and_not_as_a_traceback_or_a_hang(caplog):
    """The state waiting can never resolve: somebody deleted the run.

    A 404 is not a condition the controller writes, so this is the one answer that ends the loop
    immediately rather than after the bound — and it is the one place where "not found" has to be
    said in words a human can act on, because the run was there a moment ago.
    """
    cluster, config = waiter([client.exceptions.ApiException(status=404, reason="Not Found")])
    slept = []

    with caplog.at_level(logging.ERROR):
        code = runs.wait_for_pipeline_run(cluster, config, "hello-run-abcdef", PIPELINE_NAMESPACE, sleep=slept.append)

    assert code == 1
    assert slept == [], "a run that is not there is waited for"
    assert "not in namespace" in caplog.text, caplog.text
    assert "kmt tekton list" in caplog.text, caplog.text


def test_a_read_that_is_refused_is_not_relabelled_as_a_missing_run():
    """403 is not 404. A permissions failure has to reach the caller like every other status.

    Tolerating everything would be the same mistake `|| true` made in the deleted scripts: a cluster
    that says no would come out as a run that is not there, which is a lie about the cluster and not
    a graceful degradation.
    """
    cluster, config = waiter([client.exceptions.ApiException(status=403, reason="Forbidden")])

    with pytest.raises(client.exceptions.ApiException) as error:
        runs.wait_for_pipeline_run(cluster, config, "hello-run-abcdef", PIPELINE_NAMESPACE)
    assert error.value.status == 403


def test_the_wait_reads_the_api_and_never_a_command_line_tool(fake_binaries):
    """The other half of "the Task runs one step": the waiter adds no binaries to the pod.

    The bash loop called `tkn` and `jq`, and a step image that has neither cannot run the Task. This
    is the same empty-log assertion the keypair guard makes, applied to the polling path: an empty
    log after a real wait means nothing was execed.
    """
    cluster, config = waiter([pipelinerun("Unknown", "Running", "not yet"),
                    pipelinerun("Unknown", "Running", "not yet"),
                    pipelinerun("True", "Succeeded", "done")])

    assert runs.wait_for_pipeline_run(cluster, config, "hello-run-abcdef", PIPELINE_NAMESPACE,
                                       poll_interval_seconds=0, sleep=lambda _seconds: None) == 0

    assert fake_binaries.take_calls() == []


# --- the command's own behaviour -----------------------------------------------------------------


def _answers(recorded_api, running=None, final=None):
    """What the recorded client says: the guard's list, and the run the waiter reads."""
    recorded_api.answer("list_namespaced_custom_object",
                        lambda **kwargs: {"items": list(running or [])})
    if final is not None:
        recorded_api.answer("get_namespaced_custom_object", lambda **kwargs: final)


def test_the_command_waits_for_the_run_it_started_and_exits_zero(fixture_repo, dry_run_env,
                                                                 recorded_api):
    """`--wait` is the CI path: one command, started, and a verdict instead of a hope.

    The name the waiter watches is the one the apply generated, so this is also the test that says
    the run is not re-derived: the read is addressed at the object the write created.
    """
    from click.testing import CliRunner

    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    _answers(recorded_api, final=pipelinerun("True", "Succeeded", "All Tasks have completed"))

    result = CliRunner().invoke(tekton.run_pipeline,
                                ["dev", "fixture", run_file.name, "--wait"])

    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    writes = [kwargs for method, kwargs in recorded_api.calls
              if method.endswith("custom_object") and "body" in kwargs]
    reads = [kwargs for method, kwargs in recorded_api.calls if method == "get_namespaced_custom_object"]
    assert reads, "the command exited without ever looking at the run it was told to wait for"
    assert reads[0]["name"] == writes[0]["body"]["metadata"]["name"], \
        "the waiter watched a different object than the one it applied"
    assert reads[0]["namespace"] == PIPELINE_NAMESPACE


def test_a_run_file_that_cannot_be_read_is_reported_as_a_failure(fixture_repo, dry_run_env,
                                                                 recorded_api):
    """The other half of the defect, with a different cause: nothing was applied, so it is not a 0.

    Before this, `return 1` out of `runs.run_pipeline` reached a command module that ignored
    it. A typo in a run file's name produced a pipeline that said it had started a run.
    """
    from click.testing import CliRunner

    result = CliRunner().invoke(tekton.run_pipeline, ["dev", "fixture", "no-such-run.yml"])

    assert result.exit_code == 1, result.output
    assert not any("create" in method for method, _arguments in recorded_api.calls), \
        "the command said it failed and applied a run anyway"


def test_an_absolute_path_is_used_as_it_is_given(fixture_repo, dry_run_env, recorded_api):
    """What the Task hands over: an absolute path, resolved against nothing.

    `tekton_ci/tasks/run-tekton-pipeline.yml` passes `$(workspaces.workspace.path)/...` and the old
    script `cat`ed it. The command has to do the same, because a caller that was rewritten to a
    convention it cannot read is a caller that will be wrong.
    """
    from click.testing import CliRunner

    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    _answers(recorded_api)

    result = CliRunner().invoke(tekton.run_pipeline, ["dev", "fixture", str(run_file)])

    assert result.exit_code == 0, result.output
    assert any("create" in method for method, _ in recorded_api.calls), \
        "an absolute path that exists was not applied"


def test_a_name_with_a_directory_in_it_still_resolves_as_an_identifier(fixture_repo, dry_run_env,
                                                                       recorded_api):
    """The documented identifier shape that happens to contain a slash.

    `smoke/bla-pipeline-run.yml` is how the docstring in `run_pipeline` described a team run that
    lives in a subdirectory of the team's pipeline-run directory. It is not a path relative to the
    directory `kmt` was run from, and it still is not.
    """
    from click.testing import CliRunner

    fixture_repo.write_pipeline_run(relative_path="smoke/bla-pipeline-run.yml",
                                               name="smoke-run", pipeline="smoke")
    _answers(recorded_api)

    result = CliRunner().invoke(tekton.run_pipeline, ["dev", "fixture", "smoke/bla-pipeline-run.yml"])

    assert result.exit_code == 0, result.output
    assert any("create" in method for method, _ in recorded_api.calls), \
        "the identifier that looked like a path was not found in the team's directory"


def test_a_global_run_is_still_started_by_a_global_prefix(fixture_repo, dry_run_env, recorded_api):
    """`global/` is an identifier and never a path, whichever directory `kmt` happens to be run in.

    The prefix is older than this ticket, and the Task's own example pipeline uses run files that
    carry it, so the change is that the *other* conventions got clearer, not that this one went.
    """
    from click.testing import CliRunner

    global_dir = fixture_repo.root / "tekton_ci" / "pipeline-runs"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "hello-run.yml").write_text(
        "apiVersion: tekton.dev/v1\nkind: PipelineRun\n"
        "metadata:\n  name: hello-run\n"
        "spec:\n  pipelineRef:\n    name: hello\n")
    _answers(recorded_api)

    result = CliRunner().invoke(tekton.run_pipeline, ["dev", "fixture", "global/hello-run.yml"])

    assert result.exit_code == 0, result.output
    applied = [arguments["body"] for method, arguments in recorded_api.calls
               if method.endswith("custom_object") and "body" in arguments][-1]
    assert applied["spec"]["pipelineRef"]["name"] == "hello"


def test_the_task_runs_the_command_and_declares_everything_its_callers_bind():
    """The manifest that pays for the wait, read as the contract it is.

    One step, one command, `set -ue`, the workspace path as `KUBEMENTAT_HOME`, and the polling
    parameter feeding `--poll-interval`. Nothing is sourced and nothing loops: the step starts a run,
    waits for it, and reports what it saw, which is the whole reason the step exists.

    `cd ` is banned for the same reason the step has one command — a `cd` is a second job hiding in the
    script text — but the ban is only half the rule, and a reader who sees only this loop will "fix"
    the next line by putting the `cd` back. The step's directory is load-bearing and it is declared on
    the step itself with `workingDir`, which is Tekton's way and what `helmfile-cli.yml` and
    `terraform-cli.yml` already do. Both halves stay together: `cd` without `workingDir` is the old
    shape, `workingDir` alone is the current one. See
    `test_no_step_runs_kmt_from_a_directory_the_manifest_does_not_declare.py` for that rule across
    every Task, and for what uv does when the directory is undeclared.
    """
    import yaml

    manifest = yaml.safe_load(TASK.read_text(encoding="utf-8"))
    step = manifest["spec"]["steps"][0]
    script = step["script"]

    assert "set -ue" in script
    assert "kmt tekton run-pipeline" in script, "the step does not run the command"
    assert "--wait" in script, "the step starts the run and walks away from it"
    assert "--parallel" in script, "allow-parallel-run has nowhere to go"
    assert '--poll-interval "$(params.polling-sleep-time)"' in script, \
        "the polling param no longer feeds the wait"
    for left_behind in ("source run_pipeline.sh", "while true", "tkn pipelinerun describe",
                        "POLLING_SLEEP_TIME", "cd "):
        assert left_behind not in script, f"the step still has {left_behind!r} in it"
    assert script.count("uv run") == 1, "one command per step is the point"

    env = {entry["name"]: entry["value"] for entry in step["env"]}
    assert env["KUBEMENTAT_HOME"] == "$(params.path-to-configuration)", \
        "the configuration half of the old param was supposed to become the command's home"
    # The other half of the same param: the step runs its command from the directory it calls its
    # home. Where that rule lives for every Task is the guard file named in the docstring; this line
    # is the drift check, so the two uses of the param cannot be moved independently.
    assert step.get("workingDir") == env["KUBEMENTAT_HOME"], \
        "the step runs its command from a directory that is not the one it exports as its home"

    # The params are what a calling pipeline binds to by name, so the set is contract: adding one is a
    # caller's problem and removing one breaks a pipeline already committed. It is pinned here in full
    # rather than diffed against an older revision of the same file. `configuration-root` is the root
    # the command reads, which had to become a param of its own because that root is not necessarily a
    # path inside the checkout this step starts in; `cluster-kubeconfig` is where the work happens, and
    # a pod cannot find that answer out by itself either.
    assert [param["name"] for param in manifest["spec"]["params"]] == [
        "image", "environment", "team", "path-to-configuration", "configuration-root",
        "cluster-kubeconfig", "polling-sleep-time", "allow-parallel-run",
        "path-to-pipeline-run-file"]
    assert step["computeResources"], "the step ships its own limits, and they are part of it"
