"""The cluster-scoped residue assert excuses what it *names*, and nothing else.

`869aeb8` made the uninstall sweep ask the cluster who owns a ClusterRole instead of carrying a
name list, and by that rule two objects deliberately survive: `tekton-triggers-eventlistener-roles`
and `tekton-triggers-eventlistener-clusterroles` are bound by the EventListener controller at run
time, so no binding in the pinned release references them and they carry no `managed-by` label for
the sweep to claim them with. `src/kubementat/automation.py`'s
`_delete_cluster_scoped_rbac_kubementat_owns` says exactly that in advance, and refuses to hard-code
upstream role names -- a name list moved into that method would undo the rule it is protecting, so
the list lives in `config.CLUSTER_SCOPED_EXPECTED_RESIDUE`, which is the harness that measures the
leftovers.

The assert has to survive the move in both directions: the two known objects read as expected
residue rather than as a lane that is red for a fact, and everything else still reads as residue.
That second half is what these rows are for -- the value of naming the exception is that it is
subtractable, and a fixture that cannot fail is worse than the red it replaced. (Row 27, owner
decision 2026-10-30: name the expected residue instead of accepting a permanent red, and do not
sweep it by name in the product.)
"""

import json

import pytest

from e2e_probe_stub import FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()
config = stages.config

# What the cluster looked like before kubementat touched it. Deliberately the k3s default set: the
# residue is measured against this, so whatever a fixture plants is extra by construction.
PROVISIONED = ["default", "kube-node-lease", "kube-public", "kube-system"]

# The two objects the sweep is written not to reach, spelled as the cluster reports them.
EVENTLISTENER_ROLES = ["tekton-triggers-eventlistener-roles",
                       "tekton-triggers-eventlistener-clusterroles"]
# Something that is not on the declared list: a leftover with no excuse, which is what the assert
# exists to find.
STRAY = "kubementat-leftover-clusterrole"


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    """The polling that lets a real namespace drain has nothing to wait against here.

    Zeroing the ceiling keeps the decision under test -- present beyond the baseline, therefore
    residue -- and drops the sleep, which would cost a minute per assertion for no reason.
    """
    monkeypatch.setattr(stages, "NS_GONE_WAIT_S", 0)
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)


def items(*names):
    return json.dumps({"items": [{"metadata": {"name": name}} for name in names]})


def a_runner(clusterroles=()):
    """A teardown stage whose cluster carries `clusterroles` and nothing else beyond the baseline.

    One planted kind is enough to decide every row here: the assert walks kinds, so whatever is not
    planted reads back empty and cannot quietly add itself to the verdict. The cluster-scoped
    baseline is recorded as empty for every kind, which is what makes a planted name extra rather
    than already-accounted-for; `helm_releases`/`helm_baseline_ids` are recorded so the release row
    is vacuous rather than red for a reason this file is not testing.
    """
    guest = FakeGuest(default='{"items": []}')
    recording = Recording()
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(config.LANES["full"], options, FakeArtifacts(), None, {},
                              recording, lambda message: None)
    runner.guest = guest
    runner.cluster = stages.asserts.Cluster(guest)
    runner.stage = "99-teardown"
    runner.state.update(baseline_namespaces=list(PROVISIONED),
                        post_install_pvcs=[],
                        helm_releases=[], helm_baseline_ids=[],
                        baseline_cluster_scoped={kind: [] for kind in config.CLUSTER_SCOPED_RESIDUE_KINDS})
    guest.respond("get ns", items(*PROVISIONED))
    guest.respond("helm list", "[]")
    # `cluster_scoped_names("clusterroles")` and `clusterroles()` issue two different reads, and
    # both have to answer with the same list or the detail and the helm check would be measuring
    # two different clusters.
    if clusterroles:
        guest.respond("get clusterroles ", items(*clusterroles))
        guest.respond("get clusterrole ", items(*clusterroles))
    return runner, recording


def detail_of(recording, name):
    return recording.entry("assert:%s" % name)["detail"]


def names_labelled(detail, label):
    """The names the detail puts behind `unexpected-` / `expected-`, as a list.

    Format-coupled on purpose. The claim being pinned is that one line tells a reader which objects
    are excused and which are residue, so reading the two lists out of it is the test.
    """
    listed = detail.split(" %s-" % label, 1)[1].split("(", 1)[1].split(")", 1)[0]
    return [] if listed == "none" else [name.strip() for name in listed.split(",")]


def test_the_leftovers_the_lane_has_measured_read_as_expected_and_not_as_a_failure():
    """The whole reason for the change: a known leftover is named, and the row is green.

    Before this the detail said `clusterroles beyond the post-provision baseline: <two names>` and
    the row was red, so the first thing every reader had to know was that the red meant nothing.
    """
    runner, recording = a_runner(EVENTLISTENER_ROLES)

    runner._expect_no_residue()

    entry = recording.entry("assert:no-extra-cluster-scoped:clusterroles")
    assert entry["passed"] is True, \
        "these two are what the sweep is written to leave alone, so red is a wrong answer: %s" % entry["detail"]
    assert names_labelled(entry["detail"], "unexpected") == []
    assert names_labelled(entry["detail"], "expected") == sorted(EVENTLISTENER_ROLES), \
        "an excuse that does not say which objects it covers is a reader with nothing to check: %s" % entry["detail"]
    assert "run time" in entry["detail"], \
        "naming the objects without the reason sends the reader off to find it: %s" % entry["detail"]


def test_a_clusterrole_beyond_the_two_the_lane_names_is_residue_and_goes_red():
    """The part that makes the exception worth having: the two named objects cannot cover for a
    third. Subtracting a set is not the same operation as passing a kind, and this is where the
    difference is made visible.
    """
    runner, recording = a_runner(sorted(EVENTLISTENER_ROLES + [STRAY]))

    runner._expect_no_residue()

    entry = recording.entry("assert:no-extra-cluster-scoped:clusterroles")
    assert entry["passed"] is False, \
        "the planted object is not on the declared list, so its own lane is red: %s" % entry["detail"]
    assert names_labelled(entry["detail"], "unexpected") == [STRAY], \
        "the residue line may name one object and nothing else: %s" % entry["detail"]
    assert names_labelled(entry["detail"], "expected") == sorted(EVENTLISTENER_ROLES), \
        "excusing the planted object would hide which two are still expected: %s" % entry["detail"]


def test_an_extra_clusterrole_the_lane_expected_nothing_of_is_residue_by_itself():
    """The declared set is not a kind-wide amnesty.

    With neither expected object on the cluster, `expected-0` is the honest count -- and the one
    extra object must still be found. A fixture that only ever exercises the exception would let a
    regression that excuses a whole kind pass as a green lane.
    """
    runner, recording = a_runner([STRAY])

    runner._expect_no_residue()

    entry = recording.entry("assert:no-extra-cluster-scoped:clusterroles")
    assert entry["passed"] is False, \
        "nothing was declared for this leftover, so nothing excuses it: %s" % entry["detail"]
    assert names_labelled(entry["detail"], "unexpected") == [STRAY]
    assert names_labelled(entry["detail"], "expected") == []


def test_a_kind_the_lane_declares_no_exception_for_excuses_nothing():
    """Only one kind is measured to be expected to leave anything. The others were measured too,
    and came back clean, so their set is empty and an object beyond the baseline is residue.
    """
    runner, recording = a_runner()
    runner.guest.respond("get clusterrolebindings ", items("tekton-triggers-webhook-admin"))

    runner._expect_no_residue()

    assert recording.entry("assert:no-extra-cluster-scoped:clusterroles")["passed"] is True
    entry = recording.entry("assert:no-extra-cluster-scoped:clusterrolebindings")
    assert entry["passed"] is False, \
        "no exception is declared for bindings, so this is residue: %s" % entry["detail"]
    assert names_labelled(entry["detail"], "unexpected") == ["tekton-triggers-webhook-admin"]


def test_naming_the_expected_residue_leaves_the_other_teardown_rows_alone():
    """The asserts this row had no business touching still answer for themselves.

    `no-tekton-crds` and `no-helm-deployer-clusterrole` are green on the lane this decision was
    taken from and have to stay green; the exception is inside the per-kind loop and nowhere else.
    """
    runner, recording = a_runner(EVENTLISTENER_ROLES + ["helm-deployer-cluster-role"])

    runner._expect_no_residue()

    assert recording.passed("assert:no-tekton-crds") is True
    entry = recording.entry("assert:no-helm-deployer-clusterrole")
    assert entry["passed"] is False, \
        "that object is the product's own and has no excuse in this row: %s" % entry["detail"]
