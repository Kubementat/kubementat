"""Parity: the tunnels, against the `kmt tunnel ...` commands that replaced them.

A grouping step folded four of them into `kmt tunnel ...` (`kmt tunnel grafana`); the two that
stayed flat, `tunnel-polaris`
and `tunnel-mysql`, were retired, so what is compared below is the four that survive. The
arguments are stated in the form the CLI answers to now, which is the other half of what these tests
compare.

The `open_*_tunnel.sh` scripts were all marked *already migrated* or *delete last* in the plan's
deletion ledger. One ticket wrote the comparison that proved the first four and the mysql half,
and a later one wrote the last, so the bash half of this file is a **record** rather than a run:
`SCRIPT_ASKED` is what each script sent to `kubectl`, recorded from the scripts while they were
still in the tree. (The commits they were deleted from are `ccb6976` for the first four and
`609e757` for the mysql pair — `git show ccb6976:scripts/utilities/tunneling/open_grafana_tunnel.sh`
and `git show 609e757:scripts/utilities/tunneling/open_mysql_tunnel.sh` are two of them, so a
failure can be checked against the script that caused it.)

The Python half is still executed for real: a `kmt` subprocess, the fixture checkout for its
configuration, and the fake `kubectl` for the cluster. So this is still a comparison, not a
monologue — a command that starts forwarding to the wrong namespace, the wrong selector, or the
wrong port fails here even though nothing bash is left to compare against.

MySQL is the one record whose command is gone: the script's own text is still read out of git (that
is what the ledger says), and so is the fact that it forwarded what a human typed. What is not
compared any more is the command that replaced it, because row 25 retired `kmt tunnel-mysql` and a
comparison against a command that no longer exists is a test of nothing.

* `open_pod_tunnel.sh` was `source`d, not executed, so the namespace and the selector the caller
  resolved are what reached `kubectl`. That is the fact these records pin, and the reason the
  not-installed branch below could stay invisible for so long.
* The record's pod name is the fixture's — the same `the-pod-under-test` the shims hand the CLI.
  It is a recorded run, not a claim about a cluster.
"""

import json
import subprocess
from pathlib import Path

import pytest
from cluster_parity import (API_PROGRAM, ClusterOperation, cluster_named, compare,
                            operations_from_calls)

from conftest import ENVIRONMENT_STATIC, FIXTURE_KUBE_CONTEXT, run_kmt

# The cluster both sides of these comparisons address, in the record's own spelling. The
# scripts read it out of `KUBECONFIG` their `kubectl` found in the environment, and the commands read
# it out of the fixture's `static.json` — the same fixture, so the same context, and a run that
# drifted to another one is a difference this harness now sees.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)

POD_LIST = json.dumps({"items": [{"metadata": {"name": "the-pod-under-test"}}]})

# The tunnels' bash-only partner: `open_pod_tunnel.sh` printed the current kubectl context
# before it forwarded, and nothing in Python does that. It is a read of the local kubeconfig —
# it does not touch the cluster, and the Python path decided it was not worth an exec. The
# script is gone now (it was deleted with the pair it was `source`d by), so the record is
# history that has to be declared rather than a live difference; the four tunnels that used it
# still carry it, because that is what their scripts asked for. It carries no cluster: `kubectl
# config` reads the file on the machine and asks the cluster nothing, and that is the one shape of
# operation here that is allowed not to name one.
CONTEXT_READ = ClusterOperation("read", "kubeconfig", None, None,
                                (("subcommand", "current-context"),))

# What each script asked the cluster to do, in the order it asked for it. Recorded from the
# scripts themselves while they were still in `scripts/utilities/tunneling/`; the local ports are
# the ones the script hardcoded, which is why the CLI below is handed them explicitly rather
# than left to its own defaults (see `the_command_defaults_are_the_ports_the_scripts_carried`).
SCRIPT_ASKED = {
    "grafana": [
        ClusterOperation("read", "pod", "grafana", None,
                         (("selector", "app.kubernetes.io/instance=grafana,"
                                       "app.kubernetes.io/name=grafana"),), cluster=CLUSTER),
        ClusterOperation("port-forward", "pod", "grafana", "the-pod-under-test",
                         (("address", "0.0.0.0"), ("ports", "3101:3000")), cluster=CLUSTER),
    ],
    "tekton": [
        ClusterOperation("read", "pod", "tekton-pipelines", None,
                         (("selector", "app=tekton-dashboard"),), cluster=CLUSTER),
        ClusterOperation("port-forward", "pod", "tekton-pipelines", "the-pod-under-test",
                         (("address", "0.0.0.0"), ("ports", "9097:9097")), cluster=CLUSTER),
    ],
    # The secret manager, recorded under the name it carries today. `open_vault_ui_tunnel.sh` asked
    # for `vault` because that is what the component was called, and a recorded run whose facts are
    # keyed to a component moves with the component: what the record compares is *the component's
    # pod, in the component's namespace, on its own API port*, not the string `vault`. The port pair
    # is the part that belonged to bash rather than to the component, and it is unchanged.
    "openbao": [
        ClusterOperation("read", "pod", "openbao", None,
                         (("selector", "app.kubernetes.io/instance=openbao,"
                                       "app.kubernetes.io/name=openbao"),), cluster=CLUSTER),
        ClusterOperation("port-forward", "pod", "openbao", "the-pod-under-test",
                         (("address", "0.0.0.0"), ("ports", "8200:8200")), cluster=CLUSTER),
    ],
}

# One entry per former script, with the arguments the CLI takes. The ports are stated so a
# default that drifts is a decision and not an accident.
TUNNELS = [
    pytest.param("grafana", ["tunnel", "grafana", "dev", "--local-port", "3101",
                             "--remote-port", "3000"],
                 id="grafana"),
    pytest.param("tekton", ["tunnel", "tekton", "dev", "--local-port", "9097",
                            "--remote-port", "9097"],
                 id="tekton-dashboard"),
    pytest.param("openbao", ["tunnel", "openbao-ui", "dev", "--local-port", "8200",
                             "--remote-port", "8200"],
                 id="openbao"),
]


def run_command(dry_run_env, fake_binaries, kmt_arguments):
    """The command, run the way an operator runs it, with what it asked for.

    Subprocess rather than in-process because the point is what the whole command reaches: the
    shims have to be found through `PATH` the same way the real binaries would be.
    """
    result = run_kmt(kmt_arguments, dry_run_env)
    assert result.returncode == 0, f"kmt {kmt_arguments[0]} failed:\n{result.stdout}\n{result.stderr}"
    return fake_binaries.take_calls()


@pytest.mark.parametrize("component,kmt_arguments", TUNNELS)
def test_a_tunnel_asks_the_cluster_for_the_same_connection(
        fixture_repo, fake_binaries, dry_run_env, component, kmt_arguments):
    """Same pod, same namespace, same selector, same port mapping, same `kubectl` arguments.

    The namespace is what a renamed component would change (it moved to
    `*_DEPLOYMENT_NAMESPACE` in `static.json`), and the selector is what decides whether the
    tunnel reaches the workload or nothing — the two facts the scripts held in literals and the
    command reads from configuration.
    """
    fake_binaries.respond("kubectl", POD_LIST, subcommand="get")

    command_calls = run_command(dry_run_env, fake_binaries, kmt_arguments)

    compare(SCRIPT_ASKED[component], operations_from_calls(command_calls),
            bash_only=[CONTEXT_READ], context=f"kmt {' '.join(kmt_arguments[:1])}")


@pytest.mark.parametrize("component,kmt_arguments", TUNNELS)
def test_the_pod_found_by_the_selector_is_the_pod_the_tunnel_forwards_to(
        fixture_repo, fake_binaries, dry_run_env, component, kmt_arguments):
    """The pod name must come from the selector's own query, not from a query that looks similar.

    A selector that drifts can still resolve *a* pod on a real cluster, and then the tunnel
    forwards to the wrong workload. So the name is asserted directly, and it is the name the
    recorded script asked for — which is the only way to notice the selector and the forward
    disagreeing with each other.
    """
    fake_binaries.respond("kubectl", POD_LIST, subcommand="get")

    command_calls = run_command(dry_run_env, fake_binaries, kmt_arguments)

    forwarded = [operation.name
                 for operation in operations_from_calls(command_calls)
                 if operation.verb == "port-forward"]
    asked_for = [operation.name
                 for operation in SCRIPT_ASKED[component]
                 if operation.verb == "port-forward"]
    assert forwarded == asked_for == ["the-pod-under-test"]


def test_grafana_tunnel_reads_the_selector_from_configuration(fixture_repo, fake_binaries,
                                                              dry_run_env):
    """Namespace and selector come from `static.json`, so the record needs both to mean anything.

    The script hardcoded `grafana` and the label pair; the command reads them out of the fixture.
    Setting the fixture's values to the same strings is what makes the comparison say something:
    if a change moved the selector back into code, the values in the fixture would stop being the
    ones used.
    """
    fake_binaries.respond("kubectl", POD_LIST, subcommand="get")
    namespace = ENVIRONMENT_STATIC["GRAFANA_DEPLOYMENT_NAMESPACE"]

    command_calls = run_command(dry_run_env, fake_binaries,
                                ["tunnel", "grafana", "dev", "--local-port", "3101",
                                 "--remote-port", "3000"])

    # The lookup is a client read now, so "who was asked" is a separate record from the
    # `kubectl` the shim saw. Both halves come from the same run, and the namespace is the fact —
    # where it was asked from is not what this test is about.
    looked_up = [call for call in command_calls
                 if "get" in call.args or call.program == API_PROGRAM]
    assert looked_up, "nobody looked for the pod"
    assert namespace in " ".join(str(argument) for argument in looked_up[0].argv), \
        "the tunnel looked in a namespace nobody configured"
    assert {operation.namespace for operation in operations_from_calls(command_calls)} == {namespace}
    assert {operation.namespace for operation in SCRIPT_ASKED["grafana"]} == {namespace}


def test_tunneling_a_component_that_is_not_installed_fails_as_a_tunnel_failure(
        fixture_repo, fake_binaries, dry_run_env):
    """The branch that used to be broken, and what the replacement owes instead.

    No pod matches, so there is nothing to forward. bash could not tell: `jq -r
    '.items[0].metadata.name'` returns the string `null` for a namespace with nothing in it,
    `set -eu` has nothing to object to, and the script opened a `port-forward` to `pod/null` and
    exited 0. So the claim here is not "both fail" — bash did not fail — but "the command does not
    open a tunnel to a pod that is not there, and says so once", which is what `kmt` half of that
    pair still has to keep.
    """
    fake_binaries.respond("kubectl", json.dumps({"items": []}), subcommand="get")

    result = run_kmt(["tunnel", "grafana", "dev"], dry_run_env)
    command_ops = operations_from_calls(fake_binaries.take_calls())

    assert result.returncode == 1, result.stdout
    assert "Is the component installed?" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert not [op for op in command_ops if op.verb == "port-forward"], \
        "a tunnel was opened to nothing"


def test_the_command_defaults_are_the_ports_the_scripts_carried():
    """The ports a user got, stated as numbers rather than inherited from a flag.

    Two of the old scripts forwarded local ports 8080 (polaris) and 8200 (the component that is
    OpenBao now) — their *service* ports — while the CLI's own defaults are 8082 and 8205, which is
    what you want for a local tunnel but is a visible change for anybody who used the scripts. The
    comparison above pins both sides to the same arguments, so it cannot hide this; this is where the
    CLI's answer is written down, and where it has to be fixed if someone decides the scripts were
    right. Polaris is not on this list: row 25 retired its command, so its 8082 default went with
    it and the record above no longer has a command to compare against.
    """
    from cli.commands import tunnels

    defaults = lambda command: {parameter.name: parameter.default for parameter in command.params}
    assert defaults(tunnels.tunnel_openbao_ui)["local_port"] == "8205"
    assert defaults(tunnels.tunnel_grafana)["local_port"] == "3001"
    assert defaults(tunnels.tunnel_tekton)["local_port"] == "9097"


# ---------------------------------------------------------------------------------------------
# MySQL — the last pair of tunnel scripts, and the command that replaced them is retired
# ---------------------------------------------------------------------------------------------

# The mysql script's half of the ledger: `open_pod_tunnel.sh` was `source`d by it, so the pair
# is deleted together and one comparison covers both.
MYSQL_SCRIPT = "scripts/utilities/tunneling/open_mysql_tunnel.sh"
POD_TUNNEL_SCRIPT = "scripts/utilities/tunneling/open_pod_tunnel.sh"
# The commit the pair was last green in. Everything about the bash side is read out of here.
BLOB_REVISION = "609e757"

# What `open_mysql_tunnel.sh` asked the cluster is stated once, from the blob read below, and is
# not compared against a command any more: the namespace was whoever typed the first argument,
# the pod whoever typed the second, and everything after that a symmetric forward on all
# interfaces. Row 25 retired `kmt tunnel-mysql`, so the bash side is a record to read and not a
# run to compare — that read is `test_the_script_that_was_deleted_resolved_nothing_it_forwarded`.


def deleted_script(name):
    """The script's own text, out of git history. Skips, loudly, if the history is missing."""
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{name}"],
                            cwd=str(Path(__file__).resolve().parent.parent),
                            capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{BLOB_REVISION}:{name} exited {result.returncode} "
                    f"({result.stderr.strip()}). This is a comparison, and without the bash side "
                    f"there is nothing to compare it against.")
    return result.stdout


def test_the_script_that_was_deleted_resolved_nothing_it_forwarded():
    """The bash side, read from the blob rather than remembered.

    Three things this record has to keep saying, because they are what the replacement owes:
    the port was symmetric (local and remote were one variable), the address was `0.0.0.0`, and
    **nothing in the script asked the cluster who the pod was** — no `kubectl get`, no `jq`, no
    selector. Both facts it forwarded were typed by a human, which is why a command that reads
    them out of `static.json` is the migration and not a rewrite.
    """
    script = deleted_script(MYSQL_SCRIPT)

    assert 'source open_pod_tunnel.sh' in script, "the pair was one script, not two"
    assert '"$PORT" "$PORT" "0.0.0.0"' in script, "the forward was symmetric on 0.0.0.0"
    assert 'PORT="3306"' in script, "the default port the command carries"
    assert "kubectl get" not in script and "jq " not in script, \
        "bash resolved no namespace and no pod; that is the difference being compared"
    assert '$1' in script and '$2' in script, "both were arguments, not configuration"


def test_the_two_tunnel_scripts_stay_deleted():
    """The ledger says delete last, and *last* was this ticket.

    `open_pod_tunnel.sh` outlived the four scripts that `source`d it because
    `open_mysql_tunnel.sh` still needed it. Nothing is left to need it, so a reappearance is a
    second way to open the same tunnel — and the second copy is always the one that drifts.
    """
    root = Path(__file__).resolve().parent.parent
    for path in (MYSQL_SCRIPT, POD_TUNNEL_SCRIPT):
        assert not (root / path).exists(), f"{path} is back in the tree"
    assert not (root / "scripts" / "utilities" / "tunneling").exists(), \
        "the directory outlives its scripts when a file is dropped back into it"
