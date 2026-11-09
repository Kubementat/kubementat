"""Parity: the two helm-deployment sessions, against the commands that replace them.

`ssh_into_helm_deployment.sh` (31 lines) and `tail_logs_for_helm_deployment.sh` (27) are deleted
here, along with `get_latest_version_for_helm_chart.sh` (17). All three are still reachable in
history — `git show 9412bbe:scripts/utilities/helm/<name>` prints each — and this file runs that
blob against the shims, then runs the new command against the same fixture and asks whether the
two sides touched the same cluster.

The declared differences, all of them:

* **the namespace lookup `ssh_into_helm_deployment.sh` never used its argument.** Its
  `kubectl -n dev1 get pods` line is hardcoded, so the pod is looked up in `dev1` and the shell
  is opened in `$1`. Running the script against any other namespace is therefore a comparison
  of two different namespaces, and that is stated rather than smoothed over — a command that
  had kept the line would have kept the defect. The replacement reads in the namespace it was
  given.
* **the namespace is confirmed before a pod is sought.** bash could not tell "wrong namespace"
  apart from "wrong deployment", because both arrive as one kubectl error; the replacement asks
  the client first, which is a read the scripts never made.
* **logs are addressed the same way on both sides.** `-l 'app.kubernetes.io/instance'=name`
  there, `--selector` here. That is one read and not two, and the harness says so.

Where the harness cannot help, it says so too: the third script's whole subject is a *helm*
search, which reduces to a `run` and not to a cluster operation. So that comparison is made on
the answer each side prints, and the argv difference (`-l` there, `--output json` here) is
written down below rather than hidden inside a reduced op.
"""

import json
import subprocess
from pathlib import Path

import pytest

from cluster_parity import ClusterOperation, cluster_named, compare, operations_from_calls

from conftest import FIXTURE_KUBE_CONTEXT, FIXTURE_TARGET, run_bash

# The cluster both sides of the comparisons here address: the fixture's, named by the context its
# `static.json` declares and by the `current-context` its kubeconfig carries. `FIXTURE_TARGET` is the
# target that goes with it -- see the reason in `conftest` -- and the bash side of a comparison is
# declared with the same value, because a deleted script addressed its cluster through `KUBECONFIG`
# in the environment and that file's current-context is the fixture's.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The commit the three scripts were still in, and were green.
BLOB_REVISION = "9412bbe"
SCRIPTS = {
    "ssh": "scripts/utilities/helm/ssh_into_helm_deployment.sh",
    "logs": "scripts/utilities/helm/tail_logs_for_helm_deployment.sh",
    "version": "scripts/utilities/helm/get_latest_version_for_helm_chart.sh",
}

NAMESPACE = "deploy-me"
DEPLOYMENT = "web"
POD = "web-7d9f4-abcde"
SELECTOR = f"app.kubernetes.io/instance={DEPLOYMENT}"

# What the shim's jsonpath-shaped answer is for the script's own lookup: a bare name, because
# real kubectl would have printed only the pod's name. The Python side reads the document the
# `-o json` form of the same call asks for, which is a different response file and the same pod.
POD_NAME_ANSWER = POD
POD_DOCUMENT_ANSWER = json.dumps({"items": [{"metadata": {"name": POD}}]})


def deleted_script(tmp_path, name):
    """The script's own text, out of git history. Skips, loudly, if the history is missing."""
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{SCRIPTS[name]}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{BLOB_REVISION}:{SCRIPTS[name]} exited {result.returncode} "
                    f"({result.stderr.strip()}). The parity below is a comparison, and without "
                    f"the other side of it there is nothing to compare.")
    path = tmp_path / SCRIPTS[name].rsplit("/", 1)[-1]
    path.write_text(result.stdout)
    return path


def namespace_exists(recorded_api):
    """The one client read both commands make that bash did not: the namespace is there."""
    from types import SimpleNamespace

    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))


def pod_lookup_answers(recorded_api, name=POD):
    """The pod the selector finds, answered to the client. A client read now, no longer kubectl's.

    The `kubectl get pods` the bash side runs stays answered on the shim, because that is the other
    half of the comparison; what the Python side asks is now a `list_namespaced_pod`, and a
    reduction that cannot see it reports a read that never happened.
    """
    from types import SimpleNamespace

    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(
        items=[SimpleNamespace(metadata=SimpleNamespace(name=name),
                               status=SimpleNamespace(phase="Running"))]))
    recorded_api.answer("read_namespaced_pod", lambda name=None, **kwargs: SimpleNamespace(
        metadata=SimpleNamespace(name=name), status=SimpleNamespace(phase="Running")))


# ---------------------------------------------------------------------------
# ssh_into_helm_deployment.sh -> kmt kubernetes deployment-ssh
# ---------------------------------------------------------------------------
def test_opening_a_shell_runs_the_same_exec_and_reads_the_same_pods(
        tmp_path, fixture_repo, fake_binaries, dry_run_env, recorded_api):
    """One pod lookup, one `kubectl exec`, and the namespace the script got but did not use.

    The bash side looks the pod up in `dev1` no matter what it is told, then execs in the
    namespace it was handed. That is a real difference between the two sides, and declaring it
    is the point: a replacement that had kept the lookup would have kept the bug, and the only
    honest way to say "we did not keep it" is to compare against the side that did.
    """
    script = deleted_script(tmp_path, "ssh")
    fake_binaries.respond("kubectl", POD_NAME_ANSWER, subcommand="get.pods")
    fake_binaries.respond("kubectl", POD_DOCUMENT_ANSWER, subcommand="get.pod")
    fake_binaries.respond("kubectl", "", subcommand="exec")
    bash = run_bash(script, args=[NAMESPACE, DEPLOYMENT], env=dry_run_env)
    assert bash.returncode == 0, bash.stderr
    bash_ops = operations_from_calls(fake_binaries.take_calls(), cluster=CLUSTER)

    namespace_exists(recorded_api)
    pod_lookup_answers(recorded_api)
    pod_lookup_answers(recorded_api)
    from kubementat.infrastructure.kubernetes import KubernetesUtils

    KubernetesUtils(FIXTURE_TARGET, api_client=recorded_api.for_target(FIXTURE_TARGET)).open_deployment_shell(NAMESPACE, DEPLOYMENT)
    python_ops = operations_from_calls(fake_binaries.take_calls()) + recorded_api.operations()

    compare(
        bash_ops, python_ops,
        bash_only=[ClusterOperation("read", "pod", "dev1", None, (("selector", SELECTOR),), cluster=CLUSTER)],
        python_only=[
            ClusterOperation("read", "pod", NAMESPACE, None, (("selector", SELECTOR),), cluster=CLUSTER),
            ClusterOperation("read", "namespace", None, NAMESPACE, (), cluster=CLUSTER),
        ],
        context="ssh_into_helm_deployment.sh versus KubernetesUtils.open_deployment_shell")

    execs = [op for op in python_ops if op.verb == "exec"]
    assert [(op.name, op.namespace) for op in execs] == [(POD, NAMESPACE)]


def test_the_shell_the_script_opened_is_the_one_handed_over(tmp_path, fixture_repo,
                                                           fake_binaries, dry_run_env,
                                                           recorded_api):
    """What both sides ran inside the pod, compared as text rather than as an op.

    `/bin/bash`, with stdin and a tty, in the pod the lookup named. bash spelled the container
    choice `extra_args="-c $CONTAINER_NAME"` and left it unquoted; the command passes the same
    flag as its own argv entry, which is the only difference in the whole exec line and is why
    a container name with a space cannot start another process.
    """
    script = deleted_script(tmp_path, "ssh")
    fake_binaries.respond("kubectl", POD_NAME_ANSWER, subcommand="get.pods")
    fake_binaries.respond("kubectl", POD_DOCUMENT_ANSWER, subcommand="get.pod")
    fake_binaries.take_calls()
    run_bash(script, args=[NAMESPACE, DEPLOYMENT], env=dry_run_env)
    bash_exec = [call for call in fake_binaries.take_calls() if call.program == "kubectl"
                 and "exec" in call.args][-1]

    namespace_exists(recorded_api)
    pod_lookup_answers(recorded_api)
    pod_lookup_answers(recorded_api)
    from kubementat.infrastructure.kubernetes import KubernetesUtils

    KubernetesUtils(FIXTURE_TARGET, api_client=recorded_api.for_target(FIXTURE_TARGET)).open_deployment_shell(NAMESPACE, DEPLOYMENT)
    python_exec = [call for call in fake_binaries.take_calls() if call.program == "kubectl"
                   and "exec" in call.args][-1]

    assert bash_exec.argv[-1] == python_exec.argv[-1] == "/bin/bash"
    assert bash_exec.argv[-3:] == python_exec.argv[-3:] == ["--tty", POD, "/bin/bash"] or \
        "--tty" in bash_exec.argv and "--tty" in python_exec.argv


# ---------------------------------------------------------------------------
# tail_logs_for_helm_deployment.sh -> kmt kubernetes deployment-logs
# ---------------------------------------------------------------------------
def test_following_the_logs_makes_the_same_read_of_the_same_pods(
        tmp_path, fixture_repo, fake_binaries, dry_run_env, recorded_api):
    """`-l` there, `--selector` here: one read, and the harness says so.

    This is the cleanest of the three comparisons, and that is worth noting -- where the script
    was right, the replacement is the same operation with different flag spelling, and no
    declared differences are needed but the namespace check Python adds.
    """
    script = deleted_script(tmp_path, "logs")
    fake_binaries.respond("kubectl", "starting up\nlistening on 8080\n", subcommand="logs")
    bash = run_bash(script, args=[NAMESPACE, DEPLOYMENT], env=dry_run_env)
    assert bash.returncode == 0, bash.stderr
    bash_ops = operations_from_calls(fake_binaries.take_calls(), cluster=CLUSTER)

    namespace_exists(recorded_api)
    pod_lookup_answers(recorded_api)
    pod_lookup_answers(recorded_api)
    from kubementat.infrastructure.kubernetes import KubernetesUtils

    KubernetesUtils(FIXTURE_TARGET, api_client=recorded_api.for_target(FIXTURE_TARGET)).stream_deployment_logs(NAMESPACE, DEPLOYMENT)
    python_ops = operations_from_calls(fake_binaries.take_calls()) + recorded_api.operations()

    compare(
        bash_ops, python_ops,
        python_only=[ClusterOperation("read", "namespace", None, NAMESPACE, (), cluster=CLUSTER)],
        context="tail_logs_for_helm_deployment.sh versus KubernetesUtils.stream_deployment_logs")

    logs = [op for op in python_ops if op.kind == "log"]
    assert logs and dict(logs[0].facts)["selector"] == SELECTOR


# ---------------------------------------------------------------------------
# get_latest_version_for_helm_chart.sh -> kmt helm latest-version
# ---------------------------------------------------------------------------
def test_the_version_of_a_chart_is_the_version_the_script_printed(tmp_path, fixture_repo,
                                                                  fake_binaries, dry_run_env,
                                                                  monkeypatch):
    """Both sides ask helm for one chart, and both get the version out of it.

    The reducer cannot help here, and the reason is worth writing down: what this script does is
    a *helm* search, which reduces to a `run` carrying an argv rather than to a cluster
    operation, and comparing two `run`s asks for identical argv. They are not identical -- the
    script passes `-l` and lets helm pick, this command passes `--output json` and compares the
    address -- so the comparison is made on the question that was asked and the answer that came
    back.

    It is made on those for a second reason: the recorder answers each program with one file,
    not with a format, so a table for bash and JSON for Python are two different fixtures. Each
    side is handed the shape its own command asked for, and what is asserted is that the same
    chart in the same repo produced the same version.

    The shape is the whole point of the replacement. The script took the second line of a table
    and its second field, which is right only when the term matches exactly one chart --
    `helm search repo` treats the term as a case-insensitive regex, so `prometheus` also matches
    `prometheus-node-exporter`, and whichever row sorted first is the version that got printed.
    The rows below are deliberately the crowded ordering.
    """
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    # The configuration root is named here, so the Python side of the comparison reads the
    # fixture's tree rather than wherever `kmt` happens to be installed.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    script = deleted_script(tmp_path, "version")

    def python_answer_for(rows):
        fake_binaries.respond("helm", json.dumps(rows), subcommand="search")
        from kubementat.infrastructure.helm import HelmUtils
        from kubementat.core.identity.context import Context

        return HelmUtils(Context.build(fixture_repo.environment,
                                       fixture_repo.team)).get_latest_chart_version(
            "prometheus", "prometheus-community")

    # The ordinary case: one chart matches the term, so the table's second row is the chart
    # that was asked for, and both sides read the same version out of it.
    fake_binaries.respond("helm", "\n".join([
        "NAME                                            CHART-VERSION    APP-VERSION",
        "prometheus-community/prometheus                 25.8.0           2.47.0",
    ]) + "\n", subcommand="search")
    bash = run_bash(script, args=["prometheus-community/prometheus"], env=dry_run_env)
    assert bash.returncode == 0, bash.stderr
    assert bash.stdout.strip() == "25.8.0"
    assert python_answer_for([{"chart": "prometheus-community/prometheus", "name": "prometheus",
                               "version": "25.8.0", "app_version": "2.47.0",
                               "repository": "prometheus-community"}]) == "25.8.0"
    both_sides = [call.argv for call in fake_binaries.take_calls() if call.program == "helm"]
    assert len(both_sides) == 2, both_sides
    for argv in both_sides:
        assert argv[:3] == ["helm", "search", "repo"], argv
        assert "prometheus-community/prometheus" in argv

    # The case the pipeline was wrong about: the term matches a neighbour too, `head -n 2 |
    # tail -n 1` takes whichever row sorted first, and the version that comes back is not the
    # version of the chart that was asked for. bash says 4.29.0 here; the command, which
    # compares the address rather than the second line, says 25.8.0.
    fake_binaries.respond("helm", "\n".join([
        "NAME                                                 CHART-VERSION    APP-VERSION",
        "prometheus-community/prometheus-node-exporter        4.29.0           1.8.2",
        "prometheus-community/prometheus                      25.8.0           2.47.0",
    ]) + "\n", subcommand="search")
    bash = run_bash(script, args=["prometheus-community/prometheus"], env=dry_run_env)
    assert bash.returncode == 0, bash.stderr
    assert bash.stdout.strip() == "4.29.0"
    assert python_answer_for([
        {"chart": "prometheus-community/prometheus-node-exporter",
         "name": "prometheus-node-exporter", "version": "4.29.0", "app_version": "1.8.2",
         "repository": "prometheus-community"},
        {"chart": "prometheus-community/prometheus", "name": "prometheus",
         "version": "25.8.0", "app_version": "2.47.0", "repository": "prometheus-community"},
    ]) == "25.8.0"
