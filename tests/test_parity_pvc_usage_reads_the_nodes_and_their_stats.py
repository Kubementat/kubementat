"""Parity: the kubelet read, against the script that got there through a local proxy.

`scripts/utilities/kubernetes/list_pvc_usage.sh` is deleted, and it was 56 lines. This file runs the blob
at `git show 49c8739:scripts/utilities/kubernetes/list_pvc_usage.sh` through the shims, exactly as
the script ran, and asks whether the two sides went to the cluster for the same things. The blob is
the other side of the comparison and not a copy of it: it is also where the `curl` URL and the `jq`
filter below are read out of.

Both sides ask the cluster two questions and no others - the node list, and each node's
`/stats/summary`. That is the claim under test. Everything else this pair can do differently is
declared here rather than smuggled past the harness, because an undeclared difference is the bug
this harness exists to catch:

* **bash's transport is invisible to the harness.** `kubectl proxy` is recorded, because it is a
  kubectl verb and a session with the API. The reads that followed it were `curl`, and curl is not
  a cluster tool: it never enters the shim log. So the node list and the two per-node summaries are
  declared as Python-only operations, and the proxy session as bash-only - bash read those two
  questions, it just did it through a program this harness does not pretend is a cluster tool. The
  equivalence that is *not* declared is the address: bash built
  `127.0.0.1:8001/api/v1/nodes/<node>/proxy/stats/summary` out of its own `KUBEAPI` line, and the
  client is handed the node name plus `/stats/summary`, which is the same URL. `test_..._asked_for_
  the_same_path_the_script_curl` proves that off the blob's own text rather than asserting it.
* **formatting is not a cluster operation, and it is where the two visibly disagree.** The script's
  `defaultFormat` divided the sizes by 1024 and printed `PVC 1K-blocks Used Available Use%`;
  `humanFormat` ran the same three sizes through `numfmt --to=iec`; `column` padded what came out.
  The command prints the bytes the kubelet sent and chooses the binary unit itself - `5G` where
  `numfmt` writes `5.0G`, `121K` for 123456 - because `numfmt` is not a cluster tool, not a
  dependency, and not on a distroless host. The percentage column is `%.0f%%` there and one decimal
  here. None of that is a difference in what was asked of the cluster, so it is declared and not
  compared; the numbers behind it are compared in the second test.
* **bash divided by the capacity and died.** `percentageUsed: (.usedBytes / .capacityBytes * 100)`
  on a `capacityBytes: 0` document is `jq`'s `cannot be divided because the divisor is zero` and
  exit 5, which takes the whole report with it. The command answers that claim with no percentage
  and prints `n/a`. That is a difference in behaviour, deliberately, on the side that does not
  crash.

The `-h` flag is not a cluster question either, so it is not compared here; it is pinned in
`tests/test_pvc_usage_reads_the_kubelets_and_names_the_nodes_that_stay_silent.py`, which is where
the two forms of the table have to agree with each other.
"""

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cluster_parity import (ClusterOperation, cluster_named, compare,
                            operations_from_calls)
from conftest import FIXTURE_KUBE_CONTEXT, FIXTURE_TARGET, run_bash
from kubementat.kubernetes_utils import KubernetesUtils

# The cluster both sides of the comparisons here address: the fixture's, named by the context its
# `static.json` declares and by the `current-context` of the kubeconfig it points at. The
# target is `conftest.FIXTURE_TARGET`, whose reason for existing is that reason, and the bash
# side is declared with the same value: a deleted script addressed its cluster through the
# environment, and the environment's current-context is this fixture's.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/utilities/kubernetes/list_pvc_usage.sh"

# The commit the script was still in. `HEAD:` would be the natural thing to name, and it would be
# wrong: one commit after the deletion, `git show HEAD:scripts/...` finds nothing and this whole
# file skips itself out from under whoever reads it next. Same reason `cluster_parity`'s own record
# file names a sha and not a ref.
BLOB_REVISION = "49c8739"

NODE_STATS_PATH = "/stats/summary"
NODES = ("node-a", "node-b")

GIB = 1024 ** 3
MIB = 1024 ** 2

# The two `stats/summary` bodies this comparison runs on. The node that reports nothing interesting
# still has volumes - `emptyDir`s, a projected secret - because the claim filter is the half both
# sides have to get right.
SUMMARIES = {
    "node-a": {
        "node": {"nodeName": "node-a"},
        "pods": [
            {"volume": [{"name": "default-token", "usedBytes": 8192},
                        {"name": "data", "pvcRef": {"name": "data-postgres-0"},
                         "capacityBytes": 10 * GIB, "usedBytes": 5 * GIB,
                         "availableBytes": 5 * GIB}]},
            {"volume": [{"name": "scratch", "usedBytes": 4096}]},
        ],
    },
    "node-b": {
        "node": {"nodeName": "node-b"},
        "pods": [{"volume": [{"name": "logs", "pvcRef": {"name": "shared-logs"},
                              "capacityBytes": 2 * GIB, "usedBytes": 512 * MIB,
                              "availableBytes": 3 * GIB // 2}]}],
    },
}


def blob_text():
    """The script's own text, out of git history. Skips, loudly, if the history is missing."""
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{SCRIPT}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{BLOB_REVISION}:{SCRIPT} exited {result.returncode} "
                    f"({result.stderr.strip()}). Both halves of this comparison need it, so "
                    f"without it there is nothing to compare.")
    return result.stdout


def blob_script(tmp_path):
    """The blob written out, so `run_bash` can run it the way `kmt run-script` would."""
    path = tmp_path / "list_pvc_usage.sh"
    path.write_text(blob_text())
    return path


def answer_nodes(recorder):
    """The recorder answering `list_node` with one entry per name, and the summaries by name."""
    recorder.answer("list_node", lambda **kwargs:
                    SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name=name))
                                           for name in NODES]))
    recorder.answer("connect_get_node_proxy_with_path",
                    lambda name=None, path=None, **kwargs: SUMMARIES[name])
    return recorder


def test_the_two_sides_asked_the_cluster_the_same_two_questions(
        tmp_path, fixture_repo, fake_binaries, dry_run_env, recorded_api):
    """`kubectl proxy`, and then what each side read through it.

    The bash side is the blob, run whole: it starts the proxy, sleeps, curls (into nothing,
    because curl is not a cluster tool and no fixture answers it) and `pkill`s. What the shims
    recorded is therefore the session and nothing else, which is precisely the asymmetry this
    file declares: bash's two cluster questions went out through a program the harness does not
    log, and Python's two went out through the client, which is logged.
    """
    script = blob_script(tmp_path)
    bash_result = run_bash(script, env=dry_run_env)
    # The exit code of this script is `pkill`'s, not the read's: `kubectl-proxy-kill` is the last
    # command, and `pkill -f` exits 1 when it matched nothing. So the return code says nothing about
    # whether a cluster was read, which is its own finding -- and it is why the comparison below is
    # about the calls and not about the statuses.
    assert bash_result.returncode in (0, 1), bash_result.stderr
    assert "PVC" in bash_result.stdout, "the script did not even print its header"
    bash_ops = operations_from_calls(fake_binaries.take_calls(), cluster=CLUSTER)

    recorder = answer_nodes(recorded_api)
    KubernetesUtils(FIXTURE_TARGET,
                                api_client=recorded_api.for_target(FIXTURE_TARGET)).list_pvc_usage()

    assert fake_binaries.commands() == [], "the Python path started a program: shims should be quiet"
    compare(
        bash_ops, recorder.operations(),
        bash_only=[ClusterOperation("read", "proxy", None, None, (("port", "8001"),), cluster=CLUSTER)],
        python_only=[ClusterOperation("read", "node", None, None, (), cluster=CLUSTER)] + [
            ClusterOperation("read", "node", None, name, (("path", NODE_STATS_PATH),), cluster=CLUSTER)
            for name in NODES],
        context="list_pvc_usage.sh versus KubernetesUtils.list_pvc_usage: bash asked its two "
                "questions of `curl` behind `kubectl proxy` and Python asked them of the client")


def test_both_sides_asked_the_same_node_for_the_same_stats_path(tmp_path, recorded_api):
    """The URL bash curled and the path the client renders, compared as strings.

    This is the part of the migration that could have gone silently wrong: the endpoint. The
    script's `KUBEAPI` line and the string it appended are read out of the blob, assembled for one
    node, and lined up against the `name`/`path` the client was handed. If the node-proxy call ever
    stops meaning `/api/v1/nodes/{name}/proxy/{path}`, this is the test that says so.
    """
    blob = blob_text()
    api = re.search(r"^KUBEAPI=(127\.0\.0\.1:8001/api/v1/nodes)$", blob, re.M)
    assert api is not None, "the script's API root is not the one-line KUBEAPI assignment " \
                            "this comparison reads; the address equivalence below is unproven"
    suffix = re.search(r"\$KUBEAPI/\$node(/proxy/stats/summary)", blob)
    assert suffix is not None, "the script no longer asks for the stats summary through the " \
                               "node-proxy path, so there is no address here to line up against"

    # `curl -s $KUBEAPI/$node/proxy/stats/summary`, one node in, assembled the way bash did.
    curl_url = f"{api.group(1)}/{NODES[0]}{suffix.group(1)}"
    assert curl_url == "127.0.0.1:8001/api/v1/nodes/node-a/proxy/stats/summary"

    recorder = answer_nodes(recorded_api)
    KubernetesUtils(FIXTURE_TARGET,
                                api_client=recorded_api.for_target(FIXTURE_TARGET)).list_pvc_usage()
    asked = [(call["name"], call["path"]) for method, call in recorder.calls
             if method == "connect_get_node_proxy_with_path"]
    assert asked == [("node-a", NODE_STATS_PATH), ("node-b", NODE_STATS_PATH)], \
        "the client was not handed the nodes in name order: " + recorder.as_str()

    # What the client's node-proxy call resolves to, which is the only fact that has to agree.
    rendered = "/api/v1/nodes/node-a/proxy" + NODE_STATS_PATH
    assert curl_url.endswith(rendered), (curl_url, rendered)
    assert curl_url == "127.0.0.1:8001" + rendered


def test_the_claims_the_script_picked_are_the_claims_the_command_reports(tmp_path, recorded_api):
    """Same `stats/summary` bodies in, same claims out - the script's filter against the code's.

    The transport is not comparable, so the answer is compared instead: the blob's own `getPVCs`
    function, taken out of its text and run over the two documents Python is answered with. Nothing
    here emulates the cluster - `jq` is given a document, which is all that function ever was - and
    `jq` is on the same hosts the images parity test already requires it on.

    What comes back has to be the same list of claims, with the same byte counts, and the same
    percentage rounded to what this side prints. A claim dropped by one side, or an `emptyDir`
    counted as a claim by one side, fails here and not in a `--help` string.
    """
    body = re.search(r"function getPVCs\(\) \{(.*?)\n\}", blob_text(), re.S)
    assert body is not None, "the blob has no getPVCs function to run; the comparison is unproven"

    documents = "".join(json.dumps(SUMMARIES[name]) + "\n" for name in NODES)
    # Rebuilt in the blob's own shape rather than as a one-liner: bash wants the closing brace of a
    # function body at the start of a line, and the body's own line continuations end mid-line.
    command = "getPVCs() {" + body.group(1) + "\n}\ngetPVCs"
    bash = subprocess.run(["bash", "-c", command], input=documents, capture_output=True, text=True)
    assert bash.returncode == 0, f"{bash.stderr}\n{command}"

    answer_nodes(recorded_api)
    python_rows = KubernetesUtils(FIXTURE_TARGET,
                                api_client=recorded_api.for_target(FIXTURE_TARGET)).list_pvc_usage()["pvcs"]

    assert len(bash.stdout) > 0 and json.loads(bash.stdout), "the script's filter produced nothing"
    bash_rows = json.loads(bash.stdout)
    assert [row["name"] for row in bash_rows] == [row["name"] for row in python_rows], \
        f"the two sides disagree about which claims exist:\nbash: {bash_rows}\npython: {python_rows}"

    for bash_row, python_row in zip(bash_rows, python_rows):
        assert (bash_row["capacityBytes"], bash_row["usedBytes"], bash_row["availableBytes"]) == (
            python_row["capacity_bytes"], python_row["used_bytes"], python_row["available_bytes"])
        assert round(float(bash_row["percentageUsed"]), 1) == pytest.approx(
            python_row["percentage_used"]), bash_row
