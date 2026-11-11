"""Every name `result.json` reports for a written file is a path the lane actually wrote to.

`config_adjustments` is the block a reader uses to answer "what did the harness change and where",
and it is assembled one site at a time by the stages that changed something. When the name is spelled
out as a literal string instead of derived from the root the lane reads, the block keeps telling the
truth it told when every lane wrote in the same place -- and stops being true the moment a lane
prepares its own directory. A bootstrap lane writes to `…-config`, reports `platform_config/...`, and
the report is wrong in exactly the field whose whole job is to be checked by a human.

This is the same defect the capability rows had, one level up: thirteen of them said
`{workdir}/platform_config` inside their argv, and every lane that existed agreed, so nothing noticed
until a lane wanted a root of its own. The difference is that a wrong argv is visible the first time a
command runs, while a wrong name in a report is invisible until somebody trusts it.
"""

import json
import posixpath

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()
config = stages.config
asserts = stages.asserts


def a_runner(lane_name="bootstrap", stage="02-bootstrap", state=None):
    guest = FakeGuest()
    recording = Recording()
    state = state if state is not None else {"config_adjustments": []}
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(config.LANES[lane_name], options, FakeArtifacts(), None, state,
                               recording, lambda message: None)
    runner.guest = guest
    runner.cluster = asserts.Cluster(guest)
    runner.stage = stage
    return runner, guest, recording, state


def the_reported_names(state):
    return [entry["file"] for entry in state.get("config_adjustments", [])]


def the_paths_a_writer_used():
    """Every file one of the reporting stages touched, found without reading its source.

    The point is that the name and the write cannot disagree, so the check has to see both: the name
    the stage filed, and the path the stage actually passed to the tool that wrote it.
    """
    return {
        "the cluster": ("_declare_the_cluster", ["config set", "CLUSTER_KUBE_CONTEXT"]),
        "the namespaces": ("_declare_the_team_namespaces", ["config set", "--team"]),
    }


def test_a_bootstrap_lane_reports_the_root_it_prepared_and_not_the_one_it_borrowed():
    """The report is derived, so a lane that wrote somewhere else cannot report an old path.

    Driving `_declare_the_cluster` is what makes this a test and not a grep: the reported name comes
    out of the write path rather than out of a string that happens to sit near it. Every answer the
    helper needs is scripted, because a fixture that let the helper fail halfway would report a
    missing name and read like a clean result.
    """
    runner, guest, recording, state = a_runner()
    kubeconfig = "/home/ubuntu/.kube/config"
    root = runner.lane.config_dir
    static = posixpath.join(root, config.ENVIRONMENT, "static.json")
    guest.respond("config view -o json", Answer(
        0, json.dumps({"clusters": [{"name": "k3s", "cluster": {"server": "https://10.0.2.15:6443"}}],
                        "contexts": [{"name": "k3s", "context": {"cluster": "k3s", "user": "k3s"}}]}), ""))
    guest.respond("printf", Answer(0, kubeconfig, ""))
    guest.respond("jq -r", Answer(0, "present\nnull\n", ""))
    guest.respond("config set", Answer(0, "", ""))
    guest.respond("config show", Answer(0,
                                        "%s = k3s   (declared in %s)\n" % (config.CLUSTER_KUBE_CONTEXT_KEY,
                                                                           static), ""))
    # The variable that is not exported; answered last of the shell-shaped reads so it cannot win a
    # pattern the earlier reads also contain.
    guest.respond('p="$', Answer(0, "unset", ""))

    runner._declare_the_cluster()

    names = the_reported_names(state)
    assert names, "the cluster was declared, so the report has to carry it: %s" % \
        recording.detail("assert:config-init-written-the-cluster-key-as-null")
    for name in names:
        assert name.startswith(root), \
            "%r is not inside the root this lane writes to (%s): the report is spelling an old path" \
            % (name, root)
        assert "platform_config/" not in name, \
            "%r still says the checkout's own root, which this lane does not write into" % name


def test_an_existing_lane_still_reports_the_path_a_reader_of_the_docs_would_expect():
    """Deriving the name is not allowed to change what a working lane says about itself.

    Every lane but bootstrap writes into the guest copy's `platform_config`, which is what the suite's
    own documentation names, so a derivation that quietly rewrites that sentence would be a change to
    the report's meaning caught here rather than discovered in a run.
    """
    runner, _guest, _recording, _state = a_runner(lane_name="full", stage="02-install")
    reported = runner._config_report(config.ENVIRONMENT, "static.json")
    assert reported == "platform_config/%s/static.json" % config.ENVIRONMENT, \
        "the shared root is still named the way every report a human has read names it, and a "\
        "derivation that renamed it would be a silent change to what the suite says about itself: %r" \
        % reported
    assert runner._config_report(config.ENVIRONMENT, "static.json") == \
        posixpath.join(posixpath.basename(config.LANES["full"].config_dir),
                       config.ENVIRONMENT, "static.json")


def test_the_lane_never_names_a_root_it_did_not_read_for_the_reads_it_does_read():
    """A stage that reads a file through a helper has to pass the same root the capability would use.

    The two ways of reaching the tree are the same fact and have to agree; the check is that the
    helper's answer is inside the lane's root for a lane whose root is not the default.
    """
    for lane_name in config.LANES:
        runner, guest, _recording, _state = a_runner(lane_name=lane_name, state={})
        path = runner._config_path(config.ENVIRONMENT, "static.json")
        assert path == posixpath.join(config.LANES[lane_name].config_dir, config.ENVIRONMENT,
                                      "static.json"), \
            "the accessor disagrees with the lane shape for %s" % lane_name
