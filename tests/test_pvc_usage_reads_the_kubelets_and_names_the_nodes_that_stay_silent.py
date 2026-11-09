"""The kubelet read behind `kmt kubernetes pvc-usage`: what answered, from which node, and what did not.

The `kmt kubernetes pvc-usage` command replaces `scripts/utilities/kubernetes/list_pvc_usage.sh`, which
reached a node's
`/stats/summary` by starting `kubectl proxy`, `sleep 2`, curling `127.0.0.1:8001` and then
`pkill -9`ing the proxy. The comparison against that script is
`tests/test_parity_pvc_usage_reads_the_nodes_and_their_stats.py`; what lives here is the shape the
replacement has to keep:

* the node list is read once and each node's summary is asked for by name — the same two questions,
  with no local server and no program started at all;
* only a volume entry that carries a `pvcRef` is a claim, so the `emptyDir`s and config maps that
  share the stats listing stay out of a report about claims;
* a kubelet that answers nothing, or one that answers without a `pods` half, is a message that
  names it and says what that leaves out. The rest of the cluster is still answered for;
* a claim with `capacityBytes: 0` gets no percentage. It is not divided by to find that out, which
  is the whole reason the row says `n/a`: bash's `jq` died on the same document with `cannot be
  divided because the divisor is zero` and exited 5;
* the command prints a table, its plain form is bytes, and `--human` (`-h`) shortens it the way the
  script's `-h` did — while `--help` is still help.

No configuration key is read on any of these paths, so none of these tests seeds one.
"""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from kubernetes.client import V1Node, V1ObjectMeta
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes
from kubementat.infrastructure.kubernetes import KubernetesUtils
from cluster_fixture import TARGET_DEV

# The endpoint, spelled out here rather than imported from the library: a test that reads the
# constant from the code under test cannot say the call asked for the right path.
STATS_PATH = "/stats/summary"

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
    """Have the node-proxy call answer each node from `bodies`, which is keyed by node name.

    A node missing from `bodies` answers with a connection failure, which is what a kubelet that
    is not listening looks like to the client (`ApiException`, status 0).
    """
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
    """Two kubelets answering, and the only programs started are none.

    `node-b` reports the claim `node-a` also reports, because that is what a `ReadWriteMany`
    volume looks like through this read and what the script's flat column hid.
    """
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


def test_the_path_each_node_is_asked_for_is_the_stats_summary_of_that_node(recorded_api):
    """Which node, and which path: the two facts the curl URL carried, now as keyword arguments.

    The script built its URL out of `$KUBEAPI/$node/proxy/stats/summary`. The client is handed the
    node name and the path separately, which is what makes the parity test able to say the two
    sides asked for one and the same path.
    """
    recorded_api.answer("list_node", lambda **kwargs: node("worker-1"))
    asked = kubelet_answers(recorded_api, {"worker-1": summary()})

    KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pvc_usage()

    assert asked.methods().count("connect_get_node_proxy_with_path") == 1
    _method, call = [c for c in asked.calls if c[0] == "connect_get_node_proxy_with_path"][0]
    assert call == {"name": "worker-1", "path": STATS_PATH}, (
        "the node-proxy call is the migration's one place where the URL still matters; the "
        f"client renders it as /api/v1/nodes/{call.get('name')}/proxy{call.get('path')}")


def test_a_kubelet_that_answers_nothing_is_a_message_and_not_a_traceback(recorded_api):
    """One silent node of two: the other half of the answer still arrives, with its name.

    A `curl` that found nothing to talk to was an empty body the script looped over in silence,
    so "which nodes did I not tell you about" is a question the old output could not answer at
    all.
    """
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
    """An answer that is a body and not a silence is still not an answer about pods.

    A kubelet serving a `/stats/summary` with no `pods` half is reporting its own numbers and none
    of its volumes, which is why the message says "there are no volume entries" rather than
    "nothing answered": the difference between those two sentences is what an operator does next.
    """
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


def test_a_claim_with_no_capacity_gets_no_percentage_and_no_zero_division(recorded_api):
    """`capacityBytes: 0` is reachable, so the percentage is not computed at all.

    It is the case the script died on: `jq` said `number (5) and number (0) cannot be divided
    because the divisor is zero` and exited 5, taking the whole report with it. A claim that is
    not attached yet, or a volume the kubelet has not measured, is an ordinary state and gets a
    row with `None` in it, which the command prints as `n/a`.
    """
    recorded_api.answer("list_node", lambda **kwargs: node("node-a"))
    kubelet_answers(recorded_api, {"node-a": {"pods": [{"volume": [
        claim("unattached", 0, 5, 0),
        claim("measured-later", 0, 0, 0),
        claim("normal", 1000, 250, 750),
    ]}]}})

    result = KubernetesUtils(TARGET_DEV, api_client=recorded_api).list_pvc_usage()

    assert sizes(result["pvcs"]) == [
        ("measured-later", "node-a", 0, 0, 0, None),
        ("normal", "node-a", 1000, 250, 750, 25.0),
        ("unattached", "node-a", 0, 5, 0, None),
    ]


def test_a_volume_that_lists_no_bytes_still_reads_as_a_claim_with_zeroes(recorded_api):
    """A `pvcRef` with no figures is a claim the kubelet has not measured, not a missing one.

    bash read those same fields straight out of the entry, so a missing `capacityBytes` reached
    `jq`'s division as `null`. Here it is 0, and the row that shows it is present so the claim is
    not silently dropped from a report about claims.
    """
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
    """The command as a user runs it, with its clients reduced to the recorder's answers.

    The streams are kept apart so `result.output` stays what the command's table is, exactly as it
    was before the resolution report was added — which is on stderr, asserted where it belongs.
    """
    result = CliRunner(mix_stderr=False).invoke(kubernetes.pvc_usage, list(arguments))
    assert result.exception is None, f"{result.stdout}{result.stderr}\n{result.exception}"
    return result


def test_the_command_prints_one_row_per_claim_and_then_the_nodes_that_stayed_silent(
        two_node_cluster):
    """The table, and the messages under it: what the whole command says.

    Sizes are the bytes the kubelet sent. The script's plain mode divided them by 1024 and said so
    in the header (`PVC 1K-blocks Used Available Use%`), which is a unit a pipe has to know about;
    the flag is what shortens them.
    """
    result = invoking_the_command(two_node_cluster, "dev")

    assert result.output.splitlines()[0] == "environment: dev"
    assert "PVC" in result.output and "USE%" in result.output
    row = [line for line in result.output.splitlines() if "data-postgres-0" in line][0]
    assert "node-a" in row
    assert "10737418240" in row and "5368709120" in row, "bytes, and the pipe can read them"
    assert "50.0%" in row
    assert "node-b" in result.output and "answered nothing" in result.output


def test_the_human_flag_shortens_the_sizes_and_is_not_help(two_node_cluster):
    """`-h` is the script's `-h`: human-readable units, not `--help`.

    That is deliberately kept as it was, because the flag is what the script's own usage line
    offered and a reader who already knows it should not have to relearn it; the help text says so,
    and `--help` still prints the help. The numbers themselves are the same ones, so the two runs
    have to agree on every claim.
    """
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


def test_the_command_documents_the_flag_it_offers():
    """`--help` prints the flag and still means help.

    A flag the help does not mention is a flag nobody finds, and a `-h` that prints the table
    instead of the help page is a trap. Both halves are checked off the real command object.
    """
    listing = CliRunner().invoke(kubernetes.pvc_usage, ["--help"])
    assert listing.exit_code == 0
    assert "--human" in listing.output
    assert "-h" in listing.output, "the script's flag has to be discoverable from the help page"


def test_the_environment_names_the_read_and_changes_nothing(two_node_cluster, resolvable_environment):
    """`dev` and `prod` print their own name and address their own cluster.

    The paragraph this replaces said there is no configuration key that routes a PVC read to a
    cluster and that the command will not invent one. The resolution order invented it on purpose, so
    the sentence
    that was true of the script is false of the command, and what is left for this test to hold is
    the half that did not change: the two runs read through the same primitive and get the same
    table. What they no longer share is the cluster, which is the assertion that replaced the
    comment — the invariant that two environments are two clusters, so two environments that printed
    the same
    address here would be a collision the check should have refused.
    """
    from cluster_fixture import DEV_SERVER, PROD_SERVER, write_environment, write_kubeconfig

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
    """`kmt kubernetes pvc-usage` now resolves the cluster it reads, so this module declares one.

    The read itself is per-node and asks no namespace question, so the fixture only has to make
    `environment: dev` mean something; what the test asserts stays the kubelet's answer.
    """
    return resolvable_environment
