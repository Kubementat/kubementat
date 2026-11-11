"""Every lane assert here used to be green on a cluster where the product had done nothing.

`helm-releases-present` counted k3s's own traefik. `secret-round-trip` passed on any Secret in a
namespace `setup-secrets` never writes to. `cleanup-runs-filtered` passed on `before=0 after=0`.
`config-scope-ignores-foreign-team` looked for a config value in output that never prints one.
`uninstall-independent-of-tree` asserted that `uv run` fails outside a uv project. The rerun
check compared names only, so a regenerated password was "idempotent".

Each test below builds the cluster state the product would leave if it had failed, and asserts
the row goes red; and the state it leaves when it works, and asserts green. A row that cannot be
shown red in pytest is not measuring anything in the lane either.
"""

import json

import pytest

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness, shown

stages = load_harness()
asserts = stages.asserts

HELMFILE = """\
repositories:
- name: grafana
  url: https://grafana.github.io/helm-charts
releases:
- name: prometheus
  labels:
    group: standard
- name: grafana
  labels:
    group: standard
# - name: polaris
#   labels:
#     group: standard
- name: cert-manager
  labels:
    group: routing
- name: openbao
  labels:
    group: extra
"""

SSH_KEY = {"NAME": "deploy", "PRIVATE_KEY_BASE64": "c2VjcmV0", "PUBLIC_KEY": "",
           "TARGET_NAMESPACE": "smoke-pipelines", "TARGET_SECRET_NAME": "deploy-ssh"}


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    monkeypatch.setattr(stages, "NS_GONE_WAIT_S", 0)
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(stages.time, "sleep", lambda seconds: None)


def a_runner(stage="02-install"):
    guest = FakeGuest()
    recording = Recording()
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(stages.config.LANES["full"], options, FakeArtifacts(), None, {},
                               recording, lambda message: None)
    runner.guest = guest
    runner.cluster = asserts.Cluster(guest)
    runner.stage = stage
    runner._recording = recording
    return runner, guest, recording


def items(*objects):
    return Answer(0, json.dumps({"items": list(objects)}), "")


# ------------------------------------------------------------------ helm releases

def test_only_the_releases_the_installed_groups_declare_are_expected():
    assert stages._releases_in_groups(HELMFILE, ["standard"]) == ["grafana", "prometheus"]
    assert stages._releases_in_groups(HELMFILE, ["standard", "routing"]) == \
        ["cert-manager", "grafana", "prometheus"]


def test_k3s_own_releases_do_not_count_as_an_install():
    runner, guest, recording = a_runner()
    guest.respond("helmfile.yaml.gotmpl", HELMFILE)
    guest.respond("helm list", json.dumps([{"name": "traefik", "namespace": "kube-system", "status": "deployed"}]))

    runner._expect_release_set(["standard"])

    assert not recording.passed("assert:helm-releases-present")
    assert "grafana" in recording.detail("assert:helm-releases-present")


def test_a_release_that_failed_to_deploy_is_named():
    runner, guest, recording = a_runner()
    guest.respond("helmfile.yaml.gotmpl", HELMFILE)
    guest.respond("helm list", json.dumps([
        {"name": "prometheus", "namespace": "prometheus", "status": "deployed"},
        {"name": "grafana", "namespace": "grafana", "status": "failed"}]))

    runner._expect_release_set(["standard"])

    assert not recording.passed("assert:helm-releases-present")
    assert "grafana=failed" in recording.detail("assert:helm-releases-present")


def test_every_declared_release_deployed_is_green():
    runner, guest, recording = a_runner()
    guest.respond("helmfile.yaml.gotmpl", HELMFILE)
    guest.respond("helm list", json.dumps([
        {"name": "prometheus", "namespace": "prometheus", "status": "deployed"},
        {"name": "grafana", "namespace": "grafana", "status": "deployed"}]))

    runner._expect_release_set(["standard"])

    assert recording.passed("assert:helm-releases-present")


# ------------------------------------------------------------------ secrets

def test_a_declared_deploy_key_with_no_secret_is_red():
    runner, guest, recording = a_runner()
    guest.respond("SSH_DEPLOY_KEYS", json.dumps([SSH_KEY]))
    guest.respond("get secret deploy-ssh", Answer(1, "", 'secrets "deploy-ssh" not found'))

    runner._expect_secret_round_trip()

    assert not recording.passed("assert:secret-round-trip")
    assert "smoke-pipelines/deploy-ssh: missing" in recording.detail("assert:secret-round-trip")


def test_a_secret_whose_bytes_differ_from_the_config_is_red_without_printing_them():
    runner, guest, recording = a_runner()
    guest.respond("SSH_DEPLOY_KEYS", json.dumps([SSH_KEY]))
    guest.respond("get secret deploy-ssh", json.dumps(
        {"type": "kubernetes.io/ssh-auth", "data": {"ssh-privatekey": "b3RoZXI="}}))

    runner._expect_secret_round_trip()

    detail = recording.detail("assert:secret-round-trip")
    assert not recording.passed("assert:secret-round-trip")
    assert "c2VjcmV0" not in detail and "b3RoZXI=" not in detail


def test_a_team_that_declares_no_deploy_keys_proves_nothing():
    runner, guest, recording = a_runner()
    guest.respond("SSH_DEPLOY_KEYS", "")

    runner._expect_secret_round_trip()

    assert not recording.passed("assert:secret-round-trip")


def test_the_secret_the_config_describes_is_green():
    runner, guest, recording = a_runner()
    guest.respond("SSH_DEPLOY_KEYS", json.dumps([SSH_KEY]))
    guest.respond("get secret deploy-ssh", json.dumps(
        {"type": "kubernetes.io/ssh-auth", "data": {"ssh-privatekey": "c2VjcmV0"}}))

    runner._expect_secret_round_trip()

    assert recording.passed("assert:secret-round-trip")


# ------------------------------------------------------------------ team scope

def test_a_secret_written_from_another_teams_config_is_a_leak():
    runner, guest, recording = a_runner("05-features")
    guest.respond("config show", shown(PIPELINE_NAMESPACE="smoke-pipelines"))
    guest.respond("get secret", "smoke-pipelines kmt-e2e-foreign-ssh-secret\n")

    runner._expect_setup_secrets_stays_in_its_team()

    assert not recording.passed("assert:config-scope-ignores-foreign-team")
    assert guest.saw("rm -rf", "kmt-e2e-foreign"), "the foreign team must not outlive the check"


def test_setup_secrets_that_reads_only_its_own_team_is_green():
    runner, guest, recording = a_runner("05-features")
    guest.respond("config show", shown(PIPELINE_NAMESPACE="smoke-pipelines"))
    guest.respond("get secret", "smoke-pipelines deploy-ssh\n")

    runner._expect_setup_secrets_stays_in_its_team()

    assert recording.passed("assert:config-scope-ignores-foreign-team")


# ------------------------------------------------------------------ cleanup filter

def run_with(name, succeeded):
    return {"metadata": {"name": name},
            "status": {"conditions": [{"type": "Succeeded", "status": succeeded}]}}


def a_cleanup_cluster(guest, before, after):
    """`get pipelinerun` answers `before` until the cleanup command ran, `after` from then on."""
    guest.respond("config show", shown(PIPELINE_NAMESPACE="smoke-pipelines"))
    guest.respond("tekton cleanup-pipeline-runs", lambda command: guest.state.update(cleaned=True) or "")
    guest.respond("get pipelinerun", lambda command: items(*(after if guest.state.get("cleaned") else before)))
    guest.respond("get pods -A", items())


def test_a_filter_with_nothing_to_remove_proves_nothing():
    runner, guest, recording = a_runner("05-features")
    failing = run_with(stages.FAILING_RUN, "False")
    a_cleanup_cluster(guest, [failing], [failing])

    runner._expect_cleanup_removes_only_succeeded_runs()

    assert not recording.passed("assert:cleanup-runs-filtered")


def test_a_filter_that_removes_the_failed_run_too_is_red():
    runner, guest, recording = a_runner("05-features")
    a_cleanup_cluster(guest, [run_with("hello-world-pipeline-run-abc", "True"),
                              run_with(stages.FAILING_RUN, "False")], [])

    runner._expect_cleanup_removes_only_succeeded_runs()

    assert not recording.passed("assert:cleanup-runs-filtered")


def test_removing_the_succeeded_run_and_keeping_the_failed_one_is_green():
    runner, guest, recording = a_runner("05-features")
    failing = run_with(stages.FAILING_RUN, "False")
    a_cleanup_cluster(guest, [run_with("hello-world-pipeline-run-abc", "True"), failing], [failing])

    runner._expect_cleanup_removes_only_succeeded_runs()

    assert recording.passed("assert:cleanup-runs-filtered")
    assert guest.saw("delete", "pipelinerun/%s" % stages.FAILING_RUN)


# ------------------------------------------------------------------ rerun content

def test_a_rerun_that_rewrites_a_secret_under_the_same_name_is_a_change():
    guest = FakeGuest()
    cluster = asserts.Cluster(guest)
    guest.respond("get secret", lambda command: items(
        {"metadata": {"namespace": "grafana", "name": "grafana"}, "type": "Opaque",
         "data": {"admin-password": guest.state.get("password", "b25l")}}))
    guest.respond("get configmap", items())
    before = asserts.content_digests(cluster, ["secret", "configmap"])
    guest.state["password"] = "dHdv"
    after = asserts.content_digests(cluster, ["secret", "configmap"])

    assert asserts.changed_content(before, after) == {"secret": ["grafana/grafana"]}
    assert asserts.changed_content(before, before) == {}


# ------------------------------------------------------------------ teardown

def test_cluster_scoped_objects_the_uninstall_forgot_are_residue():
    runner, guest, recording = a_runner("99-teardown")
    # The two release snapshots the residue rows subtract. Nothing was installed and the baseline
    # was empty, so `no-kubementat-releases` is a row this fixture is not testing -- it just must not
    # be red for a reason unrelated to what is being asserted, which an unrecorded baseline would
    # have made it.
    runner.state.update(baseline_namespaces=["default"], post_install_pvcs=[],
                        helm_releases=[], helm_baseline_ids=[],
                        baseline_cluster_scoped={kind: [] for kind in stages.config.CLUSTER_SCOPED_RESIDUE_KINDS})
    guest.respond("get ns", items({"metadata": {"name": "default"}}))
    guest.respond("get pvc", items())
    guest.respond("helm list", "[]")
    guest.respond("get crd ", items())
    guest.respond("get clusterrole ", items())
    guest.respond("get customresourcedefinitions", items({"metadata": {"name": "certificates.cert-manager.io"}}))
    guest.respond("get clusterrolebindings", items({"metadata": {"name": "tekton-triggers-smoke"}}))
    guest.respond("get ", items())

    runner._expect_no_residue()

    assert not recording.passed("assert:no-extra-cluster-scoped:customresourcedefinitions")
    assert "certificates.cert-manager.io" in recording.detail("assert:no-extra-cluster-scoped:customresourcedefinitions")
    assert not recording.passed("assert:no-extra-cluster-scoped:clusterrolebindings")
    assert recording.passed("assert:no-extra-cluster-scoped:mutatingwebhookconfigurations")


def test_a_process_still_running_out_of_the_checkout_means_the_tree_is_still_needed():
    runner, guest, recording = a_runner("99-teardown")
    guest.respond("ps -eo", "4242 kubectl port-forward svc/grafana\n4242 cwd=/var/tmp/kmt-e2e (deleted)\n")
    guest.respond("get pods,pv", items())

    runner._expect_tree_independent()

    assert not recording.passed("assert:uninstall-independent-of-tree")
    assert "4242" in recording.detail("assert:uninstall-independent-of-tree")


def test_a_tunnel_wrapper_beside_the_checkout_is_not_inside_it():
    runner, guest, recording = a_runner("99-teardown")
    guest.respond("ps -eo", "17 bash /var/tmp/kmt-e2e-tunnel-grafana.sh\n17 cwd=/\n")
    guest.respond("get pods,pv", items())

    runner._expect_tree_independent()

    assert recording.passed("assert:uninstall-independent-of-tree")
