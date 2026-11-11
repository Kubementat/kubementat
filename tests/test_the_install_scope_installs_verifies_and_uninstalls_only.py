"""`--scope install` is the default run: config init, install, verify, uninstall, destroy the VM.

`--scope full` keeps everything else - the pipeline run, the rerun idempotency check, the
features, the uninstall confirmation probes and the reinstall-after-uninstall cycle. These tests
pin which stages each scope runs and what the shorter teardown still checks, so the default can
not quietly lose its residue assert or grow the reinstall cycle back.
"""

import json

import pytest

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()
config = stages.config


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    monkeypatch.setattr(stages, "NS_GONE_WAIT_S", 0)
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)


def a_runner(scope):
    guest = FakeGuest()
    recording = Recording()
    options = type("Options", (), {"env": {}, "scope": scope,
                                   "stage_timeout": lambda self, name, seconds: seconds})()
    runner = stages.LaneRunner(config.LANES["smoke"], options, FakeArtifacts(), None, {},
                               recording, lambda message: None)
    runner.guest = guest
    runner.cluster = stages.asserts.Cluster(guest)
    runner.stage = "99-teardown"
    # The release snapshots `no-kubementat-releases` subtracts. Nothing was installed and the
    # baseline is recorded as empty, so that row is vacuous for this fixture -- it has to be
    # recorded, not red, because the point of these tests is which stages ran, and a red the fixture
    # never looks at would be a finding a later reader could not tell apart from a real one.
    runner.state.update(baseline_namespaces=["default"], post_install_pvcs=[],
                        helm_releases=[], helm_baseline_ids=[],
                        baseline_cluster_scoped={kind: [] for kind in config.CLUSTER_SCOPED_RESIDUE_KINDS})
    guest.respond("get ns", lambda command: Answer(0, json.dumps({"items": [{"metadata": {"name": "default"}}]}), ""))
    guest.respond("helm list", "[]")
    guest.respond("get ", lambda command: Answer(0, '{"items": []}', ""))
    return runner, guest, recording


def stage_names(scope):
    runner, _guest, _recording = a_runner(scope)
    return [name for name, _function, _timeout in runner.stages()]


def test_install_is_the_default_scope():
    assert config.DEFAULT_SCOPE == "install"
    assert stages.LaneRunner(config.LANES["smoke"], type("Options", (), {})(), None, None, {},
                             None, None).scope == "install"


def test_the_install_scope_provisions_installs_and_tears_down_and_nothing_else():
    assert stage_names("install") == ["01-provision", "02-install", "99-teardown"]


def test_the_full_scope_runs_every_stage_in_order():
    assert stage_names("full") == ["01-provision", "02-install", "03-run", "04-rerun",
                                   "05-features", "99-teardown"]


def test_the_install_scope_uninstalls_once_and_still_checks_for_residue():
    runner, guest, recording = a_runner("install")

    runner.stage_teardown()

    names = [entry["name"] for entry in recording.entries]
    assert "cap:uninstall" in names
    assert "assert:no-extra-namespaces" in names
    assert "assert:no-extra-cluster-scoped:clusterroles" in names
    assert not [name for name in names if "reinstall" in name or "second-pass" in name
                or "confirmation" in name]
    assert len(guest.commands_matching("kmt uninstall")) == 1


def test_the_full_scope_keeps_the_reinstall_cycle():
    runner, _guest, recording = a_runner("full")

    runner.stage_teardown()

    names = [entry["name"] for entry in recording.entries]
    assert "cap:uninstall-second-pass" in names
    assert "cap:reinstall-after-uninstall" in names
    assert "assert:confirmation-rejected-changes-nothing" in names
