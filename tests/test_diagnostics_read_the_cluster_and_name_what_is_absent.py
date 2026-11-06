"""The cluster diagnostics: pods, images, and capacity, read the way the scripts read them.

`kmt analyze-deployment`, `kmt pod-images` and `kmt cluster-capacity` replace three bash
scripts (see `test_parity_kubernetes_diagnostics.py` for the comparison against the recorded
blobs). What the scripts did with `kubectl` piped through `jq` these do through the client,
and the part that makes the swap safe lives here:

* a namespace that was never created gets its name in a message, not an `ApiException`;
* a deployment that is not installed is reported in the result, because showing the operator
  the namespace's other pods is the useful answer;
* metrics-server is a service a cluster need not have, and its absence is a sentence in the
  capacity report rather than a traceback.

Fixture values echo the recorded scripts where it matters: the same 800m/600Mi pod requests
`cluster_capacity.sh` read out of `kubectl describe node`, so the two files can be read
against one cluster.
"""

from types import SimpleNamespace

import pytest
from kubernetes.client import (V1Container, V1Node, V1NodeStatus, V1ObjectMeta, V1Pod,
                               V1PodSpec, V1PodStatus, V1ResourceRequirements)
from kubernetes.client.exceptions import ApiException

from kubementat.errors import NamespaceNotFoundError
from kubementat.infrastructure.kubernetes import KubernetesUtils
from cluster_fixture import TARGET_DEV

NAMESPACE = "analyze-me"
DEPLOYMENT = "web"


def api_error(status, reason="not found"):
    return ApiException(status=status, reason=reason)


def pod(name, namespace=NAMESPACE, phase="Running", node="node-a", image="example.test/web:1.0",
        instance=None, requests=None, init_image=None):
    """One pod, whole enough for every read in this file.

    `instance` is what the deployment selector matches, `requests` goes onto the first
    container, and the labels exist because the selector is answered by them on a real cluster.
    """
    labels = {f"app.kubernetes.io/instance": instance} if instance is not None else {}
    labels.setdefault("app.kubernetes.io/name", instance or name)
    resources = (V1ResourceRequirements(requests=requests) if requests else None)
    containers = [V1Container(name="main", image=image, resources=resources)]
    init_containers = ([V1Container(name="init", image=init_image)] if init_image else None)
    return V1Pod(
        metadata=V1ObjectMeta(name=name, namespace=namespace, labels=labels),
        spec=V1PodSpec(node_name=node, containers=containers, init_containers=init_containers),
        status=V1PodStatus(phase=phase))


def node(name="node-a", cpu="2", memory="6Gi"):
    return V1Node(metadata=V1ObjectMeta(name=name),
                  status=V1NodeStatus(allocatable={"cpu": cpu, "memory": memory}))


def pod_list(*pods):
    return SimpleNamespace(items=list(pods))


def node_list(*nodes):
    return SimpleNamespace(items=list(nodes))


# ---------------------------------------------------------------------------
# analyze-deployment
# ---------------------------------------------------------------------------
def test_analyzing_a_deployment_shows_the_namespace_and_reads_the_logs_it_selects(recorded_api):
    """`kubectl get pods` plus `kubectl logs -l` is the old script; the result is the new shape.

    The pods of the namespace arrive as evidence, and the logs arrive for the pods the
    deployment actually owns — a pod without the instance label is not the deployment's,
    no matter what it is called.
    """
    selected = pod("web-7f9c", instance=DEPLOYMENT)
    other = pod("sidecar-3a", instance="other")
    recorder = recorded_api
    recorder.answer("list_namespaced_pod", lambda **kwargs:
                    pod_list(selected) if kwargs.get("label_selector") else pod_list(selected, other))
    recorder.answer("read_namespaced_pod_log", "starting up\nlistening on 8080\n")

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).analyze_deployment(NAMESPACE, DEPLOYMENT)

    assert {p["name"] for p in result["pods"]} == {"web-7f9c", "sidecar-3a"}
    assert [p["name"] for p in result["matched_pods"]] == ["web-7f9c"]
    assert result["logs"] == {"web-7f9c": "starting up\nlistening on 8080\n"}
    assert result["message"] is None
    assert result["selector"] == f"app.kubernetes.io/instance={DEPLOYMENT}"


def test_analyzing_reports_a_deployment_that_is_not_installed_without_failing_the_read(recorded_api):
    """The namespace still answers, so a missing deployment is a sentence and not an exception.

    A typo in the deployment name is the common case. The pods of the namespace still come
    back — they are the evidence that the namespace is the right one — and the message says
    which of the two answers did not arrive.
    """
    recorder = recorded_api
    recorder.answer("list_namespaced_pod", lambda **kwargs:
                    pod_list() if kwargs.get("label_selector")
                    else pod_list(pod("other-1", instance="other")))
    recorder.answer("read_namespaced_deployment", api_error(404))

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).analyze_deployment(NAMESPACE, DEPLOYMENT)

    assert [p["name"] for p in result["matched_pods"]] == []
    assert result["logs"] == {}
    assert f"Deployment '{DEPLOYMENT}' does not exist in namespace '{NAMESPACE}'." == result["message"]


def test_analyzing_names_the_namespace_that_was_never_created(recorded_api):
    """A namespace that does not exist is the operator's typo, and the error says so.

    The old bash printed kubectl's raw `ApiException` text; the replacement names the
    namespace it looked for, because that is the part of the message an act on.
    """
    recorded_api.answer("list_namespaced_pod", api_error(404))

    with pytest.raises(NamespaceNotFoundError) as error:
        KubernetesUtils(TARGET_DEV, api_client=recorded_api).analyze_deployment("no-such-space", DEPLOYMENT)

    assert "no-such-space" in str(error.value)


def test_a_pod_that_is_gone_while_logs_are_read_is_reported_and_not_an_error(recorded_api):
    """A pod that vanishes between listing and log-reading is a race, not an error.

    CrashLooping pods reschedule while this command reads them. The report must not fall
    over halfway through for one pod that is no longer there.
    """
    recorder = recorded_api
    recorder.answer("list_namespaced_pod", lambda **kwargs: pod_list(pod("web-1", instance=DEPLOYMENT)))
    recorder.answer("read_namespaced_pod_log", api_error(404))

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).analyze_deployment(NAMESPACE, DEPLOYMENT)

    assert result["logs"]["web-1"].startswith("<the pod is gone")


# ---------------------------------------------------------------------------
# pod-images
# ---------------------------------------------------------------------------
def test_pod_images_answers_each_image_once_across_the_namespace(recorded_api):
    """`kubectl get pods -o json | jq .images` is the script; this is the same two answers.

    Rollouts leave pods of two tags of the same image side by side, and an init container
    pulls from the registry too, so the set is what "used images" means — a list with
    duplicates would read as three problems where there are two.
    """
    recorder = recorded_api
    recorder.answer("list_namespaced_pod", lambda **kwargs: pod_list(
        pod("web-1", image="example.test/web:1.0", init_image="example.test/init:0.9"),
        pod("web-2", image="example.test/web:1.1"),
        pod("web-3", image="example.test/web:1.0")))

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pod_images(NAMESPACE)

    assert result["pods"] == ["web-1", "web-2", "web-3"]
    assert result["images"] == ["example.test/init:0.9", "example.test/web:1.0",
                                "example.test/web:1.1"]


def test_pod_images_names_the_namespace_that_was_never_created(recorded_api):
    recorded_api.answer("list_namespaced_pod", api_error(404))

    with pytest.raises(NamespaceNotFoundError) as error:
        KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pod_images("no-such-space")

    assert "no-such-space" in str(error.value)


# ---------------------------------------------------------------------------
# cluster-capacity
# ---------------------------------------------------------------------------
def metrics_absent():
    return api_error(404, reason="not found")


def test_capacity_reports_requested_against_allocatable_for_every_node(recorded_api):
    """800m requested on a 2-CPU node is 40% — the same number `kubectl describe node` prints.

    Memory is read as a quantity, not a string: 600Mi against 6Gi is 9.8%, the same number
    `kubectl describe node` prints. The finished pods are not counted (the script's own
    source is describe-node's Requests section, which counts only what is running), and the
    average is across nodes, not across pods.
    """
    recorder = recorded_api
    recorder.answer("list_node", lambda **kwargs: node_list(
        node("node-a"), node("node-b", cpu="1", memory="2Gi")))
    recorder.answer("list_pod_for_all_namespaces", lambda **kwargs: pod_list(
        pod("web-1", node="node-a", requests={"cpu": "800m", "memory": "600Mi"}),
        pod("done-1", node="node-b", phase="Succeeded",
            requests={"cpu": "5", "memory": "1Gi"}),
        pod("unscheduled-1", node=None)))
    recorder.answer("list_cluster_custom_object", lambda **kwargs: {"items": [
        {"metadata": {"name": "node-a"}, "containers": [
            {"usage": {"cpu": "1", "memory": "3Gi"}}]}]})

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).cluster_capacity()

    assert [n["name"] for n in result["nodes"]] == ["node-a", "node-b"]
    a, b = result["nodes"]
    assert a["requested_percent"] == {"cpu": 40.0, "memory": 9.8}
    assert b["requested_percent"] == {"cpu": 0.0, "memory": 0.0}
    assert a["usage_percent"] == {"cpu": 50.0, "memory": 50.0}
    assert b["usage_percent"] is None
    assert result["average_requested_percent"] == {"cpu": 20.0, "memory": 4.9}
    assert result["metrics"] == {"available": True, "message": None}
    assert {"name": "unscheduled-1", "node": None, "status": "Running"} in [
        {k: d[k] for k in ("name", "node", "status")} for d in result["pod_distribution"]]


def test_capacity_explains_that_metrics_server_is_absent_and_still_answers(recorded_api):
    """A cluster without metrics-server is an ordinary cluster, not a traceback.

    The metrics API answers 404 when no service is mapped for it, and the script it replaces
    never asked at all — so its absence may not sink the node list. The message must say
    what is missing and what is still being reported.
    """
    recorder = recorded_api
    recorder.answer("list_node", lambda **kwargs: node_list(node("node-a")))
    recorder.answer("list_pod_for_all_namespaces", lambda **kwargs: pod_list(
        pod("web-1", node="node-a", requests={"cpu": "800m", "memory": "600Mi"})))
    recorder.answer("list_cluster_custom_object", metrics_absent())

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).cluster_capacity()

    assert result["metrics"]["available"] is False
    assert "metrics-server" in result["metrics"]["message"]
    assert result["nodes"][0]["requested_percent"] == {"cpu": 40.0, "memory": 9.8}
    assert result["nodes"][0]["usage_percent"] is None
    assert result["average_requested_percent"] == {"cpu": 40.0, "memory": 9.8}


def test_a_failing_metrics_endpoint_is_not_reported_as_an_absent_one(recorded_api):
    """404 is "not installed". Anything else from that endpoint is a real cluster failure.

    Swallowing a 500 the way a 404 is swallowed would make the report lie about why usage
    is missing, so only the absence statuses are graceful.
    """
    recorder = recorded_api
    recorder.answer("list_node", lambda **kwargs: node_list(node("node-a")))
    recorder.answer("list_pod_for_all_namespaces", lambda **kwargs: pod_list())
    recorder.answer("list_cluster_custom_object", api_error(500, reason="server error"))

    with pytest.raises(ApiException):
        KubernetesUtils(TARGET_DEV, api_client=recorded_api).cluster_capacity()
