"""A helmfile group is applied by one capability, whichever stage is applying it.

The routing group is applied by `kmt routing install`, which reads the cluster and declines when a
Traefik it does not own is already serving the node. `helm.helmfile-apply` cannot decline: a label
selector applies, and applies, and applies. So the guard only holds if *every* stage that applies
the group asks the same question.

That is what used to be untrue. Stage 02 had the loop inline and stage 04 rebuilt the later groups
as bare helmfile applies, so the lane's own rerun walked past the guard: the first pass skipped a
controller that was already there, the second installed a second one, and the idempotency line
compared a change the lane had just written down as something that would not happen. Both stages
now resolve `config.apply_capability_for_group`, and the tests below are that claim -- one stage at a
time, told by the commands the guest was given, plus the fallback for a group nobody has mapped.
"""

import dataclasses
import json

import pytest

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()
asserts = stages.asserts

# A helmfile with one release per group: what stage 02 expects to find, and nothing more.
HELMFILE = """\
releases:
- name: prometheus
  labels:
    group: standard
- name: cert-manager
  labels:
    group: routing
"""

ROUTING_INSTALL = "kmt routing install"


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    monkeypatch.setattr(stages, "NS_GONE_WAIT_S", 0)
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(stages.time, "sleep", lambda seconds: None)


def a_runner(lane=None, stage="02-install"):
    """A stage runner whose guest answers everything with success unless told otherwise.

    Stage 02 reads three cluster facts the fake has to be told the shape of, or it raises on the
    parse rather than run the stage: how many nodes are Ready, which service accounts exist, and
    what `helm list` says is deployed. Everything else the stage does can answer itself.
    """
    guest = FakeGuest()
    recording = Recording()
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(lane or stages.config.LANES["full"], options, FakeArtifacts(), None, {},
                               recording, lambda message: None)
    runner.guest = guest
    runner.cluster = asserts.Cluster(guest)
    runner.stage = stage
    runner._recording = recording
    guest.respond("get nodes", lambda command: Answer(
        0, json.dumps({"items": [{"metadata": {"name": "node-1"},
                                  "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]}), ""))
    guest.respond("get sa ", lambda command: Answer(0, '{"items": []}', ""))
    guest.respond("helmfile.yaml.gotmpl", HELMFILE)
    # Stage 02 declares its cluster before it installs (the `null` is filled in by the rig), so the
    # fake is told the shape of the four reads that makes: the contexts one kubeconfig declares, what
    # `CLUSTER_KUBE_CONTEXT` says in the file initialize just wrote and in the file the rig then
    # edited, and what `$KUBECONFIG` is doing in the guest. The `grafana` row is not in this file's
    # helmfile -- it is there because the cluster-declaration assert asks where one release landed,
    # and it is a cluster fact rather than a group fact.
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
    guest.respond("helm list", json.dumps([
        {"name": "prometheus", "namespace": "prometheus", "status": "deployed", "chart": "prometheus-1",
         "revision": 1},
        {"name": "cert-manager", "namespace": "cert-manager", "status": "deployed", "chart": "cert-manager-1",
         "revision": 1},
        {"name": "grafana", "namespace": "grafana", "status": "deployed", "chart": "grafana-14",
         "revision": 1}]))
    return runner, guest, recording


def test_stage_02_applies_the_routing_group_by_routing_install_and_keeps_naming_the_group():
    """The first pass: `kmt routing install`, and `group=routing` still in the record it leaves.

    The command is what is being checked; the record name and its detail prefix are what a reader of
    `result.json` has to keep so that a red `cap:...` row says which group it is talking about.
    """
    runner, guest, recording = a_runner()
    # Answering badly rather than well is deliberate: a failure is what proves the detail prefix
    # survived into the record, and applying a later group is not allowed to abort the stage.
    guest.respond(ROUTING_INSTALL, Answer(1, "", "a traefik the lane does not own is already there"))

    runner.stage_install()

    assert guest.saw("kmt routing install", "dev", "--yes"), guest.commands_matching("kmt routing")
    assert not guest.saw("kmt helm apply", "group=routing"), \
        "routing is applied by the command that can decline, not by a selector that cannot"

    entry = recording.entry("cap:routing.install:routing")
    assert entry["passed"] is False
    assert entry["detail"].startswith("group=routing"), entry["detail"]
    assert "exit=1" in entry["detail"]


def test_stage_04_re_runs_the_routing_group_through_the_same_capability_as_stage_02():
    """The second pass is the one that used to install a controller stage 02 had declined.

    `install` re-applies the first group only, so every later group has to be re-applied here --
    through the mapping, which is what this checks: the command the guest got, and not a single
    helmfile apply naming the routing group.
    """
    runner, guest, recording = a_runner(stage="04-rerun")
    guest.respond(ROUTING_INSTALL, Answer(0, "traefik already present, nothing to do", ""))

    runner.stage_rerun()

    assert guest.saw("kmt routing install", "dev", "--yes"), guest.commands_matching("kmt routing")
    assert not guest.saw("kmt helm apply", "group=routing"), \
        "the rerun applied the routing group with a selector: the guard is not on this path"
    # `idempotent:routing.install`, not `idempotent:helm.helmfile-apply:routing`: a capability that
    # takes no group placeholder has nothing to disambiguate, and the name is the assert.
    detail = recording.detail("assert:idempotent:routing.install")
    assert recording.passed("assert:idempotent:routing.install"), detail
    names = [entry["name"] for entry in recording.entries]
    assert "assert:idempotent:helm.helmfile-apply:routing" not in names, names


def test_a_group_the_mapping_does_not_name_is_applied_by_helmfile_apply_in_both_stages():
    """The fallback is what keeps a new group free of harness work, so it gets checked too.

    `extra` is a group that appears in no mapping entry. It must still be applied the way every
    later group was applied before routing existed -- by `helm.helmfile-apply` (`kmt helm apply`,
    the grouped name 02cd932 gave it) with its label filter -- in the first pass and in the rerun.
    """
    lane = dataclasses.replace(stages.config.LANES["full"], install_groups=("standard", "extra"))
    runner, guest, recording = a_runner(lane)

    runner.stage_install()
    runner.stage = "04-rerun"
    runner.stage_rerun()

    applies = [command for command in guest.commands
               if "kmt helm apply" in command and "group=extra" in command]
    assert len(applies) == 2, "one apply per stage, got %s" % applies
    assert not guest.saw(ROUTING_INSTALL), "a group with no mapping entry is not a routing command"
