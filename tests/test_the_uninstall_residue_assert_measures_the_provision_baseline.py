"""The uninstall-residue assert has to be able to fail.

It used to be compared against the namespaces recorded *after* `kmt install`, which put every
namespace kubementat created inside the set it was allowed to leave behind. The last full lane
shows what that buys: six namespaces on the machine, `cert-manager` and `nginx-ingress` still
present after uninstall, and the detail reading "namespaces beyond baseline: none". A green that
was manufactured from the wrong baseline is worse than a red lane, because it ends the search.

The baseline is now what the cluster looked like before kubementat touched it, so these three
cases are the whole point of the change: residue is red, a clean teardown is green, and a run
that never recorded a baseline gets no green for free.

`nginx-ingress` is in the residue list below because it was in the lane's own snapshot, which is
what the numbers are taken from and not what a cluster gets today: kubementat stopped installing
the controller on 2026-10-28. The assert cannot tell a namespace the product shipped from a
namespace the product left behind, which is exactly why the fixture is allowed to use either.

The same rule is now extended to helm releases, where the baseline is not a list of namespaces but a
list of *releases* -- and kubementat's Traefik makes that one matter: its release is `traefik` in the
namespace `traefik`, and k3s runs a `traefik` in `kube-system`. Two objects, one name. Comparing
names across the three snapshots stopped being the same question the moment both were on the
cluster, so they are compared as `namespace/name` and the row below that shares the baseline rule
with the namespace rows gets the same tests: residue is red, a clean teardown is green, and a
baseline that was never taken earns no green.
"""

import json

import pytest

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    """Stop a fake cluster from being waited on.

    The harness polls for up to three minutes for a namespace to drain, which is what a real
    cluster needs and what a fake that never changes its answer makes fatal: this file spent three
    minutes per residue assertion before this was pinned. Zeroing the ceiling keeps the decision
    being tested -- present, therefore residue -- and discards only the waiting, which has nothing
    here to wait against.
    """
    monkeypatch.setattr(stages, "NS_GONE_WAIT_S", 0)
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)

PROVISIONED = ["default", "kube-node-lease", "kube-public", "kube-system"]
# What the cluster actually looked like when the lane's teardown ran, taken from the run's own
# snapshot rather than from a hypothesis about what uninstall should have removed.
RESIDUE = sorted(PROVISIONED + ["cert-manager", "nginx-ingress"])


def a_runner(namespaces, baseline=None, helm=(), installed=(), baseline_ids=()):
    """A teardown stage whose cluster is whatever the fake guest is told to list.

    `helm` is what is on the cluster when teardown looks; `installed` is the post-install snapshot
    the run recorded, and `baseline_ids` the `namespace/name` set stage 01 recorded before kubementat
    touched anything. `baseline_ids=None` means that baseline was never recorded, which is a
    different run than an empty one: with no set to subtract there is no such thing as ours.
    """
    guest = FakeGuest()
    recording = Recording()
    lane = stages.config.LANES["full"]
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(lane, options, FakeArtifacts(), None, {}, recording, lambda message: None)
    runner.guest = guest
    runner.cluster = stages.asserts.Cluster(guest)
    runner.stage = "99-teardown"
    runner._recording = recording
    runner.state["baseline_namespaces"] = baseline if baseline is not None else list(PROVISIONED)
    runner.state["post_install_pvcs"] = []
    runner.state["helm_releases"] = list(installed)
    if baseline_ids is not None:
        runner.state["helm_baseline_ids"] = list(baseline_ids)

    guest.respond("get ns", lambda command: Answer(0, json.dumps(
        {"items": [{"metadata": {"name": name}} for name in namespaces]}), ""))
    guest.respond("get crd", lambda command: Answer(0, '{"items": []}', ""))
    guest.respond("get clusterrole", lambda command: Answer(0, '{"items": []}', ""))
    guest.respond("get pvc", lambda command: Answer(0, '{"items": []}', ""))
    guest.respond("helm list", lambda command: Answer(0, json.dumps(list(helm)), ""))
    return runner


def test_a_namespace_that_outlived_uninstall_is_residue_and_not_the_baseline():
    runner = a_runner(RESIDUE)

    runner._expect_no_residue()

    entry = runner._recording.entry("assert:no-extra-namespaces")
    assert entry["passed"] is False, \
        "cert-manager and nginx-ingress survived uninstall; the detail must say so: %s" % entry["detail"]
    assert "cert-manager" in entry["detail"] and "nginx-ingress" in entry["detail"], \
        "a residue assert that does not name the namespace is not actionable: %s" % entry["detail"]


def test_a_teardown_back_to_the_provision_baseline_is_clean():
    runner = a_runner(list(PROVISIONED))

    runner._expect_no_residue()

    entry = runner._recording.entry("assert:no-extra-namespaces")
    assert entry["passed"] is True, "nothing beyond the baseline is not residue: %s" % entry["detail"]
    assert "none" in entry["detail"]


def test_a_run_without_a_provision_baseline_gets_no_green_for_free():
    # An empty baseline used to mean "everything is allowed to survive". Without the set that
    # `extra` is measured against there is no such thing as residue, so the honest answer is red.
    runner = a_runner(list(PROVISIONED), baseline=[])

    runner._expect_no_residue()

    entry = runner._recording.entry("assert:no-extra-namespaces")
    assert entry["passed"] is False, "a green invented from a missing baseline is the old bug again"


# ------------------------------------------------------------------ release identity
# The same assert has the same problem about helm releases, and kubementat's Traefik is the object
# that makes it true: its release is `traefik` in the namespace `traefik`, and k3s runs a `traefik`
# in `kube-system`. Two objects, one name. Subtracting and comparing names is therefore not the
# same question as asking which *releases* were installed and which of them are still there.

# k3s's own controller, read off the cluster: same release name, another namespace, another chart.
K3S_TRAEFIK = {"name": "traefik", "namespace": "kube-system", "status": "deployed",
               "chart": "traefik-41.6.2", "revision": 3}
# kubementat's, which is what an uninstall is supposed to have taken with it.
OURS_TRAEFIK = {"name": "traefik", "namespace": "traefik", "status": "deployed",
                "chart": "traefik-42.1.3", "revision": 1}


def helm_list_output(*releases):
    """What the guest answers for `helm list -A -o json`: helm calls the release field `name`."""
    return [{"name": release["name"], "namespace": release["namespace"], "status": release["status"],
             "chart": release["chart"], "revision": release["revision"]} for release in releases]


def snapshot(*releases):
    """The same cluster read as the run records it, which is `asserts.Cluster.helm_list`'s shape.

    Worth spelling out twice rather than reusing one dict: `helm_releases` in the run state is what
    `helm_list` returned, keyed `release`, and a fixture that hands it helm's own `name` key would
    be testing a shape no stage ever holds.
    """
    return [{"namespace": release["namespace"], "release": release["name"], "status": release["status"],
             "chart": release["chart"], "revision": release["revision"]} for release in releases]


def test_a_release_that_survives_under_its_namespace_is_residue_however_many_of_its_name_are_left():
    """k3s's `kube-system/traefik` must not be able to speak for kubementat's `traefik/traefik`.

    Both releases are on the cluster, the baseline accounts for k3s's, and only ours was supposed to
    go. Comparing names subtracted ours with k3s's release and reported a clean teardown while a
    controller and its namespace were still installed -- a green that no amount of reading
    `result.json` would let anyone doubt.
    """
    runner = a_runner(list(PROVISIONED),
                      helm=helm_list_output(K3S_TRAEFIK, OURS_TRAEFIK),
                      installed=snapshot(K3S_TRAEFIK, OURS_TRAEFIK),
                      baseline_ids=["kube-system/traefik", "kube-system/traefik-crd"])

    runner._expect_no_residue()

    entry = runner._recording.entry("assert:no-kubementat-releases")
    assert entry["passed"] is False, \
        "ours is still installed in the namespace we made; the name alone said it was gone: %s" % entry["detail"]
    assert "traefik/traefik" in entry["detail"], \
        "residue that does not say which namespace is the object cannot be acted on: %s" % entry["detail"]
    assert "kube-system/traefik" not in entry["detail"], \
        "k3s's own controller is not what survived, and naming it here would send a reader off by one object: %s" % entry["detail"]


def test_the_controller_the_cluster_was_already_running_is_not_kubementat_s_residue():
    """What the change must not break: a release that predates the run cannot be blamed on it.

    Names alone would call this green too -- the same string on both sides of the subtraction. That
    is why the row is here, and not because the old compare got the wrong answer: the verdict was
    never the same question, and this is the case where asking it correctly costs nothing.
    """
    runner = a_runner(list(PROVISIONED),
                      helm=helm_list_output(K3S_TRAEFIK),
                      installed=snapshot(K3S_TRAEFIK),
                      baseline_ids=["kube-system/traefik", "kube-system/traefik-crd"])

    runner._expect_no_residue()

    entry = runner._recording.entry("assert:no-kubementat-releases")
    assert entry["passed"] is True, "the cluster ran this before kubementat did: %s" % entry["detail"]
    assert "none" in entry["detail"]


def test_a_run_that_recorded_no_namespaced_release_baseline_cannot_call_anything_ours():
    """The same rule the namespace row learned, applied to the release row.

    A baseline that was never taken is not an empty one, and subtracting "nothing" from what the run
    installed leaves a set that reads as residue when it is really just unmeasured. Red says that.
    """
    runner = a_runner(list(PROVISIONED), helm=helm_list_output(OURS_TRAEFIK),
                      installed=snapshot(OURS_TRAEFIK), baseline_ids=None)

    runner._expect_no_residue()

    entry = runner._recording.entry("assert:no-kubementat-releases")
    assert entry["passed"] is False, "an unrecorded baseline is not evidence of a clean uninstall: %s" % entry["detail"]


def test_the_release_rows_helm_list_hands_back_still_carry_the_what_and_the_how_many():
    """`helm_list` is one read of the cluster, and the two keys it used to drop are the cheap half.

    Existing callers read namespace, release and status; nothing may have to go back to the guest
    for a second read to learn which chart a release is, or which revision it is on. That is what
    "nothing was overwritten" and "which of these two objects is it" are both decided from.
    """
    guest = FakeGuest()
    guest.respond("helm list", json.dumps([{"name": "traefik", "namespace": "kube-system",
                                            "status": "deployed", "chart": "traefik-41.6.2",
                                            "revision": 3}]))

    releases = stages.asserts.Cluster(guest).helm_list()

    assert releases == [{"namespace": "kube-system", "release": "traefik", "status": "deployed",
                         "chart": "traefik-41.6.2", "revision": 3}]
