"""The kubelet read behind `kmt kubernetes pvc-usage`: what answered, from which node, and what did
not.
"""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from cluster_fixture import TARGET_DEV
from kubernetes.client import V1Node, V1ObjectMeta
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes
from kubementat.infrastructure.kubernetes import KubernetesUtils

GIB = 1024 ** 3


def claim(name, capacity, used, available):
    """One volume entry as a kubelet lists it: a `pvcRef` and the three byte figures."""
    return {"name": "mount", "pvcRef": {"name": name}, "capacityBytes": capacity,
            "usedBytes": used, "availableBytes": available}


def ephemeral(name, used=4096):
    """A volume entry with no `pvcRef` -- an emptyDir, a secret, a config map. Not a claim."""
    return {"name": name, "usedBytes": used}


def summary(*volume_lists, **node_stats):
    """A `stats/summary` body: one pod per volume list, volumes where kubelet puts them."""
    document = {"pods": [{"volume": list(volumes)} for volumes in volume_lists]}
    document.update(node_stats)
    return document


def node(*names):
    return SimpleNamespace(items=[V1Node(metadata=V1ObjectMeta(name=name)) for name in names])


def kubelet_answers(recorded_api, bodies):
    """Have the node-proxy call answer each node from `bodies`, which is keyed by node name."""
    def connect(name=None, path=None, **kwargs):
        if name not in bodies:
            raise ApiException(status=0, reason="Failed to establish a new connection")
        return bodies[name]

    recorded_api.answer("connect_get_node_proxy_with_path", connect)
    return recorded_api


def sizes(rows):
    return [(row["name"], row["node"], row["capacity_bytes"], row["used_bytes"],
             row["available_bytes"], row["percentage_used"]) for row in rows]


# ---------------------------------------------------------------------------
# the library read
# ---------------------------------------------------------------------------
def test_a_two_node_cluster_answers_one_row_per_claim_without_starting_anything(
        recorded_api, monkeypatch):
    """Two kubelets answering, and the only programs started are none."""
    started = []
    monkeypatch.setattr("kubementat.infrastructure.kubernetes.exec.shell.run",
                        lambda argv, **kwargs: started.append(list(argv)))
    recorded_api.answer("list_node", lambda **kwargs: node("node-b", "node-a"))
    kubelet_answers(recorded_api, {
        # The real client returns the proxy body as text, so the first node is answered that way.
        "node-a": json.dumps({"pods": [
            {"volume": [claim("data-postgres-0", 10 * GIB, int(4.25 * GIB), 6 * GIB)]},
            {"volume": [ephemeral("var-log"), claim("shared-logs", 2 * GIB, 512 * 1024 ** 2,
                                                   1024 * (1024 * 1024 - 512))]},
        ]}),
        "node-b": {"pods": [{"volume": [claim("shared-logs", 2 * GIB, 1024 ** 3,
                                             1024 ** 3)]}]},
    })

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pvc_usage()

    assert started == [], f"a cluster read that starts a program: {started}"
    assert recorded_api.methods() == ["list_node",
                                      "connect_get_node_proxy_with_path",
                                      "connect_get_node_proxy_with_path"], recorded_api.as_str()
    assert result["nodes"] == ["node-a", "node-b"], "read in name order, whatever the API returned"
    assert result["messages"] == []
    assert sizes(result["pvcs"]) == [
        ("data-postgres-0", "node-a", 10 * GIB, int(4.25 * GIB), 6 * GIB, 42.5),
        ("shared-logs", "node-a", 2 * GIB, 512 * 1024 ** 2, 1024 * (1024 * 1024 - 512), 25.0),
        ("shared-logs", "node-b", 2 * GIB, 1024 ** 3, 1024 ** 3, 50.0),
    ]
    assert not any(row["name"] == "var-log" for row in result["pvcs"]), \
        "an entry with no pvcRef is a directory on the node, not a claim"


def test_a_kubelet_that_answers_nothing_is_a_message_and_not_a_traceback(recorded_api):
    """One silent node of two: the other half of the answer still arrives, with its name."""
    recorded_api.answer("list_node", lambda **kwargs: node("node-a", "node-b"))
    kubelet_answers(recorded_api, {
        "node-a": {"pods": [{"volume": [claim("data-a", GIB, int(0.5 * GIB), int(0.5 * GIB))]}]},
    })

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pvc_usage()

    assert [row["name"] for row in result["pvcs"]] == ["data-a"]
    assert len(result["messages"]) == 1
    assert "node-b" in result["messages"][0]
    assert "answered nothing" in result["messages"][0]
    assert result["nodes"] == ["node-a", "node-b"], "the silent node is still a node that was read"


def test_a_kubelet_that_answers_without_a_pod_list_says_which_half_is_missing(recorded_api):
    """An answer that is a body and not a silence is still not an answer about pods."""
    recorded_api.answer("list_node", lambda **kwargs: node("node-a"))
    kubelet_answers(recorded_api, {"node-a": {"node": {"nodeName": "node-a", "runtime": {}}}})

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pvc_usage()

    assert result["pvcs"] == []
    assert len(result["messages"]) == 1
    assert "no volume entries" in result["messages"][0]

    # A kubelet that is running and has nothing scheduled answers zero rows and no message.
    recorded_api.answers.clear()
    recorded_api.answer("list_node", lambda **kwargs: node("node-a"))
    kubelet_answers(recorded_api, {"node-a": {"pods": []}})
    assert KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pvc_usage()["messages"] == []


def test_a_volume_that_lists_no_bytes_still_reads_as_a_claim_with_zeroes(recorded_api):
    """A `pvcRef` with no figures is a claim the kubelet has not measured, not a missing one."""
    recorded_api.answer("list_node", lambda **kwargs: node("node-a"))
    kubelet_answers(recorded_api, {"node-a": {"pods": [{"volume": [
        {"name": "mount", "pvcRef": {"name": "half-a-answer"}}]}]}})

    assert sizes(KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pvc_usage()["pvcs"]) == [
        ("half-a-answer", "node-a", 0, 0, 0, None)]


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------
@pytest.fixture
def two_node_cluster(recorded_api):
    """A cluster the command can be invoked against: one claim per node, one node silent."""
    recorded_api.answer("list_node", lambda **kwargs: node("node-a", "node-b"))
    kubelet_answers(recorded_api, {
        "node-a": {"pods": [{"volume": [claim("data-postgres-0", 10 * GIB, 5 * GIB, 5 * GIB)]}]},
    })
    return recorded_api


def invoking_the_command(two_node_cluster, *arguments):
    """The command as a user runs it, with its clients reduced to the recorder's answers."""
    result = CliRunner(mix_stderr=False).invoke(kubernetes.pvc_usage, list(arguments))
    assert result.exception is None, f"{result.stdout}{result.stderr}\n{result.exception}"
    return result


def test_the_human_flag_shortens_the_sizes_and_is_not_help(two_node_cluster):
    """`-h` is the script's `-h`: human-readable units, not `--help`."""
    params = {param.name: param for param in kubernetes.pvc_usage.params}
    assert params["human"].opts == ["--human", "-h"]
    assert "help" not in {param.name for param in kubernetes.pvc_usage.params}, (
        "-h was taken by help, so the script's -h does not mean what the script meant")

    plain = invoking_the_command(two_node_cluster, "dev")
    human = invoking_the_command(two_node_cluster, "dev", "--human")
    short = invoking_the_command(two_node_cluster, "dev", "-h")

    assert "10Gi" in human.output and "5Gi" in human.output
    assert "10737418240" not in human.output, "the flag left a byte count in a human column"
    assert human.output == short.output, "-h and --human are one flag"
    assert "10737418240" in plain.output, "the plain form is the one that kept the bytes"


def test_the_environment_names_the_read_and_changes_nothing(two_node_cluster, resolvable_environment):
    """`dev` and `prod` print their own name and address their own cluster."""
    from cluster_fixture import (
        DEV_SERVER,
        PROD_SERVER,
        write_environment,
        write_kubeconfig,
    )

    write_kubeconfig(resolvable_environment / "kubeconfig",
                     {"fixture": DEV_SERVER, "prod": PROD_SERVER})
    write_environment(resolvable_environment, "prod", context="prod")

    dev = invoking_the_command(two_node_cluster, "dev")
    other = invoking_the_command(two_node_cluster, "prod")

    assert f"cluster dev: {DEV_SERVER}" in dev.stderr, dev.stderr
    assert f"cluster prod: {PROD_SERVER}" in other.stderr, other.stderr
    assert [line for line in dev.output.splitlines() if line.startswith("environment:")][0] == \
        "environment: dev"
    assert [line for line in other.output.splitlines() if line.startswith("environment:")][0] == \
        "environment: prod"
    assert [line for line in dev.output.splitlines() if "data-postgres-0" in line] == \
        [line for line in other.output.splitlines() if "data-postgres-0" in line]


def test_a_cluster_with_no_claims_says_so_instead_of_printing_an_empty_table(recorded_api):
    """Nothing scheduled and nothing bound: the answer is a sentence, not blank space."""
    recorded_api.answer("list_node", lambda **kwargs: node("node-a"))
    kubelet_answers(recorded_api, {"node-a": {"pods": [{"volume": [ephemeral("scratch")]}]}})

    result = CliRunner().invoke(kubernetes.pvc_usage, ["dev"])

    assert result.exit_code == 0
    assert "(no kubelet reported a claim)" in result.output


@pytest.fixture(autouse=True)
def _an_environment_the_command_can_resolve(resolvable_environment):
    """`kmt kubernetes pvc-usage` now resolves the cluster it reads, so this module declares one."""
    return resolvable_environment
