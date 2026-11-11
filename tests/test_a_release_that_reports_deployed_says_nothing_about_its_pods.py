"""A release that answers `deployed` has been reconciled, not started.

`helm-releases-present` reads the release record, and that record is written when helm hands the
manifests to the API. Nothing in the suite asked what the manifests *do*: a StatefulSet whose pods
sit in `CrashLoopBackOff` reports a `deployed` release and would have passed, and the group this
matters for -- `extra`, the secret-management release -- was installed by no lane at all, so every
claim about it came from a chart render on a host, where nothing can fail to start.

The two tests below are the whole reason `_expect_group_workloads` exists, and they are deliberately
the mirror pair: same release row, same namespace, one pod that came up and one that did not.

The second pair matters as much as the first: a group that declares releases and yields no rows, or
declares nothing because someone typoed a `group:` label, must be red rather than skipped, because an
empty group and a green lane are the same shape to a check that only looks for what is missing.
"""

import dataclasses
import json

import pytest

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()

# The shape these tests need, spelled out locally instead of read from `config.LANES["full"]`. The
# full lane's groups are a moving target -- `extra` joined it and came back out inside one day -- and a
# test that borrows that shape goes quiet when the lane does, reporting success for an assert that no
# longer runs. So the group list is pinned here and only the *harness* behaviour is under test.

# `openbao` in `group: extra`, and nothing else: what the real helmfile declares for the group that
# joined the full lane, kept small so a red row cannot come from a release this file forgot.
HELMFILE = """\
releases:
- name: grafana
  labels:
    group: standard
- name: openbao
  labels:
    group: extra
"""

OPENBAO_ROWS = [{"name": "grafana", "namespace": "grafana", "status": "deployed", "chart": "grafana-1",
                 "revision": 1},
                {"name": "openbao", "namespace": "openbao", "status": "deployed", "chart": "openbao-30",
                 "revision": 1}]


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    monkeypatch.setattr(stages, "NS_GONE_WAIT_S", 0)
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)
    # A window long enough for the predicate to be called, short enough that a red row does not spin
    # a unit test for the 180 seconds the lane is given.
    monkeypatch.setattr(stages, "WORKLOAD_TIMEOUT_S", 1)
    monkeypatch.setattr(stages.time, "sleep", lambda seconds: None)


def a_runner(pods, extra_declared=True, stage="02-install"):
    """Stage 02 with one question answered: what lives in the namespace the release landed in.

    Everything the stage needs to reach the assert is here. `pods` is the items array of
    `kubectl get pods -n openbao` -- that single knob is what makes the pair a pair.
    """
    guest = FakeGuest()
    recording = Recording()
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(dataclasses.replace(stages.config.LANES["full"],
                                                   install_groups=("standard", "extra")),
                               options, FakeArtifacts(), None, {}, recording, lambda message: None)
    runner.guest = guest
    runner.cluster = stages.asserts.Cluster(guest)
    runner.stage = stage
    runner._recording = recording
    guest.respond("get nodes", lambda command: Answer(
        0, json.dumps({"items": [{"metadata": {"name": "node-1"},
                                  "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]}), ""))
    guest.respond("get sa ", lambda command: Answer(0, '{"items": []}', ""))
    guest.respond("helmfile.yaml.gotmpl", HELMFILE if extra_declared else HELMFILE.split("- name: openbao")[0] + "- name: nothing\n  labels:\n    group: nowhere\n")
    guest.respond("helm list", json.dumps(OPENBAO_ROWS if extra_declared else OPENBAO_ROWS[:1]))
    guest.respond("get pods -n openbao", lambda command: Answer(0, json.dumps({"items": pods}), ""))
    # The cluster the stage installs into is declared before the first write (the `null` a fresh
    # by the rig), so the fake answers the reads that decision makes: the one context a kubeconfig
    # declares, the state of `CLUSTER_KUBE_CONTEXT` in the file initialize wrote and in the file the
    # rig edited, and whether `$KUBECONFIG` is doing any of the addressing. None of this is what the
    # file is testing -- `pods` below is -- but a stage that cannot reach its assert cannot be tested.
    guest.respond("config view -o json", lambda command: Answer(0, json.dumps(
        {"clusters": [{"name": "default", "cluster": {"server": "https://127.0.0.1:6443"}}],
         "contexts": [{"name": "default", "context": {"cluster": "default", "user": "default"}}]}), ""))
    guest.respond('printf %s "${KUBECONFIG', lambda command: Answer(0, "/home/ubuntu/.kube/config", ""))
    guest.respond("readlink -f", lambda command: Answer(0, "unset\n", ""))
    guest.respond('else "absent" end', lambda command: Answer(0, "present\nnull\n", ""))
    guest.respond(".[$key] // empty", lambda command: Answer(
        0, "grafana\n" if "GRAFANA" in command else "default\n", ""))
    guest.respond("kmt install", lambda command: Answer(
        0, "", "cluster dev: https://127.0.0.1:6443 (/var/tmp/kmt-e2e/platform_config/dev/static.json) "
             "— not verified: no other environment in this root declares a cluster\n"))
    # The all-namespaces read `wait` makes to tell a stalled pod off a cluster that cannot schedule.
    guest.respond("get pods -A", lambda command: Answer(0, json.dumps({"items": pods}), ""))
    return runner, guest, recording


def a_pod(name="openbao-0", phase="Running", ready=True):
    return {"metadata": {"name": name}, "status": {"phase": phase, "containerStatuses": [
        {"name": "openbao", "ready": ready, "restartCount": 0}]}}


def test_a_deployed_release_whose_pods_never_start_is_red():
    """The assert's reason to exist: `deployed` and CrashLoopBackOff are both true at once."""
    runner, guest, recording = a_runner([a_pod(phase="CrashLoopBackOff", ready=False)])

    runner.stage_install()

    entry = recording.entry("assert:workloads-running:extra")
    assert entry["passed"] is False, entry["detail"]
    assert "openbao-0" in entry["detail"] and "CrashLoopBackOff" in entry["detail"], entry["detail"]
    # And it is not confused with the release row: the release is there and `deployed`, which is
    # exactly why the suite needs a second question asked of a second object.
    assert recording.passed("assert:helm-releases-present"), recording.detail("assert:helm-releases-present")


def test_a_group_whose_pods_are_running_and_ready_is_green_and_says_which_ones():
    runner, guest, recording = a_runner([a_pod()])

    runner.stage_install()

    detail = recording.detail("assert:workloads-running:extra")
    assert recording.passed("assert:workloads-running:extra"), detail
    assert "openbao/openbao-0" in detail, "the row has to name the pods it looked at: %s" % detail


def test_running_but_not_ready_is_not_running():
    """A pod in `Running` whose container refuses to answer is the failure mode a phase hides."""
    runner, guest, recording = a_runner([a_pod(ready=False)])

    runner.stage_install()

    detail = recording.detail("assert:workloads-running:extra")
    assert not recording.passed("assert:workloads-running:extra"), detail
    assert "waiting on" in detail, detail


def test_a_group_that_declares_no_releases_is_red_rather_than_absent():
    """`group: extra` with nothing in it looks identical to success to a check that skips it.

    This is the shape a typo in a label makes, and the reason the assert does not treat a group with
    no rows as a no-op: a lane that reports nothing wrong here reports nothing happened either.
    """
    runner, guest, recording = a_runner([], extra_declared=False)

    runner.stage_install()

    entry = recording.entry("assert:workloads-running:extra")
    assert entry["passed"] is False, entry["detail"]
    assert "no release row names a namespace" in entry["detail"], entry["detail"]


def test_a_group_the_lane_did_not_install_produces_no_assert():
    """The check is scoped to what the configuration names, so a lane without `extra` stays quiet.

    Without this scoping the smoke lane would demand pods for a group it never installs, which is the
    same mistake in the other direction as the one the assert above exists to catch.
    """
    runner, guest, recording = a_runner([a_pod()])
    runner.lane = dataclasses.replace(runner.lane, install_groups=("standard",))

    runner.stage_install()

    names = [entry["name"] for entry in recording.entries]
    assert "assert:workloads-running:extra" not in names, names
