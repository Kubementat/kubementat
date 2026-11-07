"""Parity: the three cluster-read scripts, against the commands that replace them.

`analyze_deployment.sh`, `retrieve_deployment_docker_image_versions.sh` and
`cluster_capacity.sh` are deleted by this commit. They are still in the history —
`git show 3aac5cb:scripts/utilities/kubernetes/<name>.sh` prints each — and this file runs
that blob, not a copy, against the shims, then runs the new command on the same fixture and
asks `cluster_parity.compare` whether the two sides touched the same cluster.

The declared differences are all of them, and they are declared because the harness treats
an undeclared one as the bug it is:

* the logs half of `analyze_deployment.sh` asked for pods by
  `app.kubernetes.io/name`; the replacement selects a deployment the way every other
  kubementat command does, by its release name in `app.kubernetes.io/instance`
  (`infrastructure/helm.py`'s `RELEASE_INSTANCE_LABEL`), so the two selectors do not mean the same pods.
* the replacement reads the deployment object by name to answer "does this exist", which
  bash never did; without it, "no logs" could be a wrong deployment name or a quiet pod.
* logs on the Python side arrive per pod (`read_namespaced_pod_log`) where bash asked for
  everything matching a selector in one `kubectl logs -l`; what is compared is the set of
  pods, not how many argvs it took.

And one addition on the capacity side: the metrics API. The script never asked, because it
read the percentages out of `kubectl describe node`; the replacement asks the metrics API
for actual usage and falls back, with a message, when metrics-server is not installed. A
read the script never performed is declared as Python-only, not hidden.

The `jq` in the images script is not a cluster tool and never enters the shim log: the
filter runs against the kubectl shim's stdout on a host that has `jq`, which is how the
script's own one-liners ran. What is compared is what each side asked the cluster for, plus
— for the images — the values that came back, because the images are the answer.
"""

import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest
from kubernetes.client import (V1Container, V1Node, V1NodeStatus, V1ObjectMeta, V1Pod,
                               V1PodSpec, V1PodStatus, V1ResourceRequirements)
from cluster_parity import (ClusterOperation, cluster_named, compare,
                            operations_from_calls)

from conftest import FIXTURE_KUBE_CONTEXT, FIXTURE_TARGET, run_bash

# The cluster both sides of the comparisons here address: the fixture's, named by the context its
# `static.json` declares and by the `current-context` of the kubeconfig it points at. The
# target is `conftest.FIXTURE_TARGET`, whose reason for existing is that reason, and the bash
# side is declared with the same value: a deleted script addressed its cluster through the
# environment, and the environment's current-context is this fixture's.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)


REPO_ROOT = Path(__file__).resolve().parent.parent

# The commit the scripts were still in. They are deleted here, so this is a record, not a run.
BLOB_REVISION = "3aac5cb"
SCRIPTS = {
    "analyze": "scripts/utilities/kubernetes/analyze_deployment.sh",
    "images": "scripts/utilities/kubernetes/retrieve_deployment_docker_image_versions.sh",
    "capacity": "scripts/utilities/kubernetes/cluster_capacity.sh",
}

NAMESPACE = "analyze-me"
DEPLOYMENT = "web"


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


def pod(name, namespace=NAMESPACE, phase="Running", node="node-a", images=("example.test/web:1.0",),
        instance=None, requests=None):
    """One pod, whole enough for every read in this file.

    `instance` is what the deployment selector matches; the first container carries
    `requests`, because that is the half `cluster_capacity` reads.
    """
    labels = {} if instance is None else {f"app.kubernetes.io/instance": instance}
    labels.setdefault("app.kubernetes.io/name", instance or name)
    resources = V1ResourceRequirements(requests=requests) if requests else None
    containers = [V1Container(name=f"c{index}", image=image, resources=resources)
                  for index, image in enumerate(images)]
    return V1Pod(
        metadata=V1ObjectMeta(name=name, namespace=namespace, labels=labels),
        spec=V1PodSpec(node_name=node, containers=containers),
        status=V1PodStatus(phase=phase))


def pod_list(*items):
    return SimpleNamespace(items=list(items))


def allocatable_node(name, cpu, memory):
    return V1Node(metadata=V1ObjectMeta(name=name),
                  status=V1NodeStatus(allocatable={"cpu": cpu, "memory": memory}))


# ---------------------------------------------------------------------------
# analyze_deployment.sh -> kmt analyze-deployment
# ---------------------------------------------------------------------------
def test_the_deployment_analysis_asks_the_cluster_for_the_pods_and_their_logs(
        tmp_path, fixture_repo, fake_binaries, dry_run_env, recorded_api):
    """`kubectl get pods -n NS` and `kubectl logs -n NS -l …`, and what the Python reads instead.

    The bash side is one `get` and one selector log; the Python side answers with the same
    two lists and a per-pod log. The selector difference is declared below and explained by
    the release-name convention: a diagnostic that disagreed with every other command about
    which pods the deployment *was* would be the bug this comparison exists to catch.
    """
    script = deleted_script(tmp_path, "analyze")
    fake_binaries.respond("kubectl", "NAME      READY   STATUS    AGE\nweb-1     1/1     Running   3m\n",
                          subcommand="get")
    fake_binaries.respond("kubectl", "starting up\nlistening on 8080\n", subcommand="logs")
    bash_result = run_bash(script, args=[NAMESPACE, DEPLOYMENT], env=dry_run_env)
    assert bash_result.returncode == 0, bash_result.stderr
    bash_ops = operations_from_calls(fake_binaries.take_calls(), cluster=CLUSTER)

    selected = pod("web-1", instance=DEPLOYMENT)
    recorder = recorded_api
    recorder.answer("list_namespaced_pod", lambda **kwargs: pod_list(selected))
    recorder.answer("read_namespaced_deployment", lambda **kwargs: object())
    recorder.answer("read_namespaced_pod_log", "starting up\nlistening on 8080\n")

    from kubementat.infrastructure.kubernetes import KubernetesUtils

    result = KubernetesUtils(FIXTURE_TARGET,
                               api_client=recorded_api.for_target(FIXTURE_TARGET))\
        .analyze_deployment(NAMESPACE, DEPLOYMENT)
    assert result["logs"] == {"web-1": "starting up\nlistening on 8080\n"}
    assert result["message"] is None

    compare(
        bash_ops, recorder.operations(),
        bash_only=[ClusterOperation("read", "log", NAMESPACE, None,
                                    (("selector", f"app.kubernetes.io/name={DEPLOYMENT}"),), cluster=CLUSTER)],
        python_only=[
            ClusterOperation("read", "pod", NAMESPACE, None,
                             (("selector", f"app.kubernetes.io/instance={DEPLOYMENT}"),), cluster=CLUSTER),
            # The existence check the script never ran.
            ClusterOperation("read", "deployment", NAMESPACE, None, (), cluster=CLUSTER),
            # Logs are read per pod, so the pod bash selected by selector is named here.
            ClusterOperation("read", "podlog", NAMESPACE, None, (), cluster=CLUSTER),
        ],
        context="analyze_deployment.sh versus KubernetesUtils.analyze_deployment")


# ---------------------------------------------------------------------------
# retrieve_deployment_docker_image_versions.sh -> kmt pod-images
# ---------------------------------------------------------------------------
def test_reading_the_used_images_reads_the_pods_of_the_namespace_once(
        tmp_path, fixture_repo, fake_binaries, dry_run_env, recorded_api):
    """The script read the pod list twice (`jq` for names, `jq` for images); Python reads it once.

    That is not just fewer calls: the two reads could straddle a rollout and print names and
    images from two different moments. The operations comparison allows the second read on
    the bash side (the harness compares sets, so it collapses anyway), and the values are
    asserted directly, because a silently missing image would line up on operations alone.
    """
    pods_json = json.dumps({"items": [
        {"metadata": {"name": "web-1"}, "spec": {"containers": [{"image": "example.test/web:1.0"}]}},
        {"metadata": {"name": "web-2"}, "spec": {"containers": [
            {"image": "example.test/web:1.1"}, {"image": "example.test/side:2.2"}]}},
    ]})
    script = deleted_script(tmp_path, "images")
    fake_binaries.respond("kubectl", pods_json)
    bash_result = run_bash(script, args=[NAMESPACE], env=dry_run_env)
    assert bash_result.returncode == 0, bash_result.stderr
    bash_ops = operations_from_calls(fake_binaries.take_calls(), cluster=CLUSTER)

    recorder = recorded_api
    recorder.answer("list_namespaced_pod", lambda **kwargs: pod_list(
        pod("web-1", images=("example.test/web:1.0",)),
        pod("web-2", images=("example.test/web:1.1", "example.test/side:2.2"))))

    from kubementat.infrastructure.kubernetes import KubernetesUtils

    result = KubernetesUtils(FIXTURE_TARGET,
                                api_client=recorded_api.for_target(FIXTURE_TARGET)).list_pod_images(NAMESPACE)

    bash_images = sorted(json.loads(line) for line in bash_result.stdout.splitlines()
                         if line.startswith('"') and "/" in line)
    assert sorted(result["images"]) == bash_images, "bash and Python saw different images"
    assert set(result["pods"]) == {"web-1", "web-2"}, "the pod half of the script's output"

    compare(bash_ops, recorder.operations(),
            context="retrieve_deployment_docker_image_versions.sh versus "
                    "KubernetesUtils.list_pod_images (bash reads the pod list twice; the "
                    "second read is the same set, collapsed by the comparison)")


# ---------------------------------------------------------------------------
# cluster_capacity.sh -> kmt cluster-capacity
# ---------------------------------------------------------------------------
# The half of `kubectl describe node` the script parsed: two whitespace, "Requests", and
# the two lines below it holding `cpu … (40%) …` and `memory … (10%) …`.
NODE_A_DESCRIBE = """Name:         node-a
Labels:       kubernetes.io/hostname=node-a
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests          Limits
  --------           --------          ------
  cpu                800m (40%)        1 (50%)
  memory             600Mi (10%)       1Gi (18%)
Events:              <none>
"""


def test_the_capacity_report_reads_the_nodes_and_the_pods_that_ask_for_them(
        tmp_path, fixture_repo, fake_binaries, dry_run_env, recorded_api):
    """`get nodes`, `describe node`, `get pod --all-namespaces`, `describe nodes`.

    bash assembled the percentages by grepping describe's table; the replacement does the
    same arithmetic from the pods' own `resources.requests` against `status.allocatable`,
    which is what that table prints. The metrics read is new on the Python side and is
    declared as such; what must match is that both sides read the nodes and every pod.
    """
    script = deleted_script(tmp_path, "capacity")
    fake_binaries.respond("kubectl", "node-a\nnode-b\n", subcommand="get.nodes")
    fake_binaries.respond("kubectl", NODE_A_DESCRIBE, subcommand="describe.node")
    fake_binaries.respond("kubectl", "NAME    NAMESPACE   STATUS    NODE\nweb-1   analyze-me  Running   node-a\n",
                          subcommand="get.pod")
    fake_binaries.respond("kubectl", NODE_A_DESCRIBE, subcommand="describe.nodes")
    bash_result = run_bash(script, env=dry_run_env)
    assert bash_result.returncode == 0, bash_result.stderr
    bash_ops = operations_from_calls(fake_binaries.take_calls(), cluster=CLUSTER)
    assert "Average usage" in bash_result.stdout, "the script's arithmetic must actually run"

    recorder = recorded_api
    recorder.answer("list_node", lambda **kwargs: pod_list(
        allocatable_node("node-a", "2", "6Gi"), allocatable_node("node-b", "1", "2Gi")))
    recorder.answer("list_pod_for_all_namespaces", lambda **kwargs: pod_list(
        pod("web-1", node="node-a", requests={"cpu": "800m", "memory": "600Mi"})))
    recorder.answer("list_cluster_custom_object",
                    lambda **kwargs: {"items": [{"metadata": {"name": "node-a"},
                                                 "containers": [{"usage": {"cpu": "1",
                                                                           "memory": "3Gi"}}]}]})

    from kubementat.infrastructure.kubernetes import KubernetesUtils

    result = KubernetesUtils(FIXTURE_TARGET,
                                api_client=recorded_api.for_target(FIXTURE_TARGET)).cluster_capacity()

    # The same cluster the bash side was handed: 800m of 2 CPU asked for, usage at 50%.
    assert result["metrics"]["available"] is True
    assert result["nodes"][0]["requested_percent"] == {"cpu": 40.0, "memory": 9.8}
    assert result["nodes"][0]["usage_percent"] == {"cpu": 50.0, "memory": 50.0}

    compare(
        bash_ops, recorder.operations(),
        python_only=[ClusterOperation("read", "node", None, None,
                                      (("group", "metrics.k8s.io"), ("version", "v1beta1")), cluster=CLUSTER)],
        context="cluster_capacity.sh versus KubernetesUtils.cluster_capacity")
