"""The cluster is part of the record, and the record is what makes the wrong-cluster failure visible.

`tests/cluster_parity.py` reduces both channels a cluster operation can travel — the generated
client, bound to a `ClusterTarget`, and the binaries, given the cluster as an argument — to the same
`ClusterOperation`. Before the record named a cluster, that shape had no cluster in it, so these two asks looked identical:

    create namespace team-a        # on dev
    create namespace team-a        # on prod

and a parity test comparing them passed whatever the code had actually done. That is not a missing
detail, it is the specific failure the record exists to make visible: **the
namespace created on cluster A and the release applied on cluster B**, each half reporting success,
nothing in between saying so, and the pair surfacing at uninstall. A comparison that silently drops
the cluster is a parity test that can be green while the wrong cluster is edited, so the harness now
reads it from both sides (`cluster_parity.cluster_identity` for argv,
`conftest.ApiRecorder.for_target` for the client) and raises on any operation whose target it cannot
see — the module's existing rule pointed straight at this: an unaddressed cluster is exactly the
thing that must raise rather than be skipped.

One test that builds the divergence on purpose is worth more than ten that assert agreement, so the
first one below does exactly that, in both channels, against two real contexts in one fixture
kubeconfig. The second is the control that makes the first one mean something: the same two
channels, addressed the same way, do reduce to one operation.

* `ClusterTarget`/`Context` addressing itself is asserted by the addressing tests and is not
  re-asserted here.
* The `helm`/`helmfile` exemption table lives in `cluster_parity`, with
  `test_no_helm_call_is_addressed_by_the_process_any_more.py` ratcheting it against the tree.
"""

import pytest
from cluster_fixture import (DEV_SERVER, PROD_SERVER, TARGET_DEV, write_kubeconfig)
from cluster_parity import (ClusterOperation, ParityError, cluster_identity, cluster_named,
                            compare, operations_from_calls, parse_argv,
                            with_generated_run_name_normalized)
from conftest import ApiRecorder, RecordedCall

from kubementat.core.identity.target import ClusterTarget
from kubementat.kubernetes_utils import KubernetesUtils

NAMESPACE = "team-a"
RELEASE = "web"


def two_clusters(tmp_path):
    """One kubeconfig, two contexts, two addresses — the shape the failure needs.

    `write_kubeconfig` gives each context its own cluster entry, so `dev` and `prod` are genuinely
    two clusters here rather than two spellings of one: the address the 1:1 check compares differs,
    which is what makes "the wrong cluster" a fact and not a guess.
    """
    kubeconfig = write_kubeconfig(tmp_path / "kubeconfig",
                                  {"dev": DEV_SERVER, "prod": PROD_SERVER})
    return (kubeconfig,
            ClusterTarget.from_kubeconfig(str(kubeconfig), "dev", "dev"),
            ClusterTarget.from_kubeconfig(str(kubeconfig), "prod", "prod"))


def client_namespace_write(target, recorder):
    """The client half of the run: one namespace, written through the bound client.

    Production code rather than a hand-written operation, so what the record says about the cluster
    is what `KubernetesUtils` was told and not what this file believed.
    """
    KubernetesUtils(target, api_client=recorder.for_target(target)).create_namespace(NAMESPACE)
    return without_self_named_namespace(recorder.operations())


def without_self_named_namespace(operations):
    """A namespace's own name, counted once.

    `kubectl apply -f -` carries the manifest it applied and the client carries a `V1Namespace`, so
    the same write arrives with a body that either names the object twice or spells out the
    `apiVersion` nothing here is asking about. `test_the_cleanup_job_is_created_in_the_namespace_the_config_names.py`
    already normalises exactly this, for exactly that reason; this file borrows the rule rather than
    inventing a second one, because what is under test here is the *cluster* and nothing else.
    """
    return [ClusterOperation(operation.verb, operation.kind, operation.namespace, operation.name,
                             () if operation.kind == "namespace" else operation.facts,
                             operation.cluster)
            for operation in operations]


def kubectl_namespace_write(target, kubeconfig):
    """The `kubectl` half of the run: the same namespace, asked of `target`'s cluster.

    Delivered the way the scripts delivered it — a manifest on stdin, `apply -f -` — because that is
    the shape whose reduction has to match the client's down to the `body` fact. As
    `kubectl create namespace NAME` the two sides would differ for a reason the harness already
    documents elsewhere (a create carries no body to reduce, and a `V1Namespace` carries its own name
    in two places), and the point of *this* comparison is that nothing but the cluster differs.

    The flags are the ones `kubectl_target_args` puts on the line, so the reducer reads a real
    command and not an example.
    """
    argv = ["apply", "-f", "-"]
    if target.kubeconfig_path:
        argv += ["--kubeconfig", str(kubeconfig)]
    if target.context:
        argv += ["--context", target.context]
    manifest = ('{"apiVersion": "v1", "kind": "Namespace", '
                '"metadata": {"name": "' + NAMESPACE + '"}}')
    return without_self_named_namespace(
        operations_from_calls([RecordedCall("kubectl", argv, cwd="/", stdin=manifest)]))


def test_a_namespace_on_one_cluster_and_a_release_on_another_is_not_a_passing_comparison(tmp_path):
    """The failure, built here instead of avoided in prose.

    The namespace goes to `dev` through the client, the namespace goes to `prod` through `kubectl`,
    and every other field of the two operations agrees. A record with no cluster field called that
    one run; this asserts the comparison says no, and says it by naming both clusters — which is the
    only version of the message an operator can act on.

    It is worth being clear about what is under test: not that kubementat ever does this, but that
    the harness would notice the day it did. Before this change it would not have.
    """
    kubeconfig, dev, _prod = two_clusters(tmp_path)
    recorder = ApiRecorder()

    client_side = client_namespace_write(dev, recorder)
    binary_side = kubectl_namespace_write(_prod, kubeconfig)

    assert [op.cluster for op in client_side] == ["context dev"], client_side
    assert [op.cluster for op in binary_side] == ["context prod"], binary_side

    with pytest.raises(AssertionError) as raised:
        compare(binary_side, client_side, context="the same write, two clusters")

    message = str(raised.value)
    assert "context dev" in message and "context prod" in message, message
    # The diagnosis is stated, not left to the reader: with the cluster taken out the two columns
    # line up, and that is precisely why the field had to be added.
    assert "which cluster was addressed" in message, message


def test_the_same_write_through_both_channels_reduces_to_one_operation(tmp_path):
    """The control that makes the test above a measurement and not an artefact.

    Same namespace, same verb, same target on both sides: the two channels agree, so `compare` is
    satisfied. Without this half, the failure above could be an accident of a broken reducer rather
    than a difference in what the run asked for.
    """
    kubeconfig, dev, _prod = two_clusters(tmp_path)
    recorder = ApiRecorder()

    client_side = client_namespace_write(dev, recorder)
    binary_side = kubectl_namespace_write(dev, kubeconfig)

    compare(binary_side, client_side, context="both channels, one cluster")

    assert [op.cluster for op in client_side] == ["context dev"]
    assert {op.cluster for op in binary_side} == {"context dev"}


# ---------------------------------------------------------------------------
# half two: the reducer raises on an operation whose target it cannot see
# ---------------------------------------------------------------------------

def test_the_reducer_refuses_a_kubectl_that_names_no_cluster():
    """A read of a cluster nobody named is not a read of the cluster under test.

    This is the case the spec calls a bug and not a shortcut: every `kubectl` that stays in
    the tree is given `--context`/`--kubeconfig` by `kubectl_target_args`, so a call without them is
    either a regression in `src/` or a test that forgot to say what it ran against. Reducing it to
    `cluster=None` and carrying on would make that invisible in exactly the way the old record did,
    which is why it is the rule that raises and not a warning.
    """
    with pytest.raises(ParityError) as raised:
        operations_from_calls([RecordedCall("kubectl", ["get", "pods", "-n", NAMESPACE], cwd="/")])

    assert "cannot tell which one" in str(raised.value)


def test_the_reducer_refuses_a_helm_that_names_no_cluster():
    """helm is the half the record singles out, because that is where the release lands.

    The same rule in helm's flag spellings: `--kube-context`/`--kubeconfig`, and nothing in the
    environment. An unaddressed `helm upgrade` is the B in "namespace on A, release on B", so the
    reducer has to refuse it even though the argv is otherwise perfectly readable.
    """
    argv = ["upgrade", "--install", RELEASE, f"./{RELEASE}-1.0.0.tgz", "-n", NAMESPACE]

    with pytest.raises(ParityError) as raised:
        operations_from_calls([RecordedCall("helm", argv, cwd="/")])

    assert "cannot tell which one" in str(raised.value)

    # ...and addressed, it reduces. The refusal is about the missing target, not about helm.
    addressed = operations_from_calls([
        RecordedCall("helm", argv + ["--kube-context", "dev"], cwd="/")])
    assert [operation.cluster for operation in addressed] == ["context dev"], addressed


def test_a_client_call_recorded_without_a_target_raises():
    """The same rule on the other channel, where the target never appears in argv.

    An `ApiRecorder` that was never bound knows nothing about which cluster it answered for, and the
    log looks *cleaner* for that — a `(method, kwargs)` pair reads like a complete record. It is
    half a record, and reducing it into an operation with no cluster is how a comparison would end up
    comparing the wrong thing. So it raises, and says how to fix it.
    """
    recorder = ApiRecorder()

    with pytest.raises(ParityError) as raised:
        recorder.create_namespace(name=NAMESPACE)
        recorder.operations()

    assert "without a cluster" in str(raised.value)
    assert "for_target" in str(raised.value), "the message has to say what to do about it"


def test_reads_that_ask_nothing_of_a_cluster_still_reduce():
    """The rule has a far edge, and it is worth stating which side this is.

    `helm repo update` unpacks a chart cache and `kubectl config view` prints a local file. Neither
    can be answered about the wrong cluster because neither is answered about a cluster at all, so
    demanding a target from them would be the rule turned into noise — and a reducer that raises on
    everything has trained the next reader to declare the exception rather than read the failure.
    Both reduce, and both carry no cluster.
    """
    operations = operations_from_calls([
        RecordedCall("helm", ["repo", "update"], cwd="/"),
        RecordedCall("kubectl", ["config", "view"], cwd="/"),
    ])

    assert [operation.cluster for operation in operations] == [None, None], operations
    assert {operation.verb for operation in operations} == {"run", "read"}


def test_cluster_flags_are_parsed_as_values_and_not_as_verbs():
    """The one line in the shim that the record depends on.

    `--kube-context` takes a value, so if the parser mistook the value for a positional word then
    `helmfile --kube-context dev apply` would read as a call about `dev` and the cluster would be
    the thing the reducer never sees. The same shape appears in `--context=dev`, which is how some
    scripts write it, so both spellings are checked.
    """
    _namespace, flags, positional = parse_argv(["--kube-context", "dev", "apply"])
    assert positional == ["apply"], positional
    assert cluster_identity(flags) == "context dev"

    assert cluster_named(context="dev") == "context dev"
    assert cluster_named(kubeconfig="/tmp/operator/kubeconfig") == "kubeconfig /tmp/operator/kubeconfig"
    assert cluster_named(context="dev",
                         kubeconfig="/tmp/operator/kubeconfig") == "context dev"
    # A flag that arrived with nothing after it is not a target, and is not counted as one. The
    # shims record that shape as the literal `true`, which is why `cluster_named` looks for it.
    assert parse_argv(["--context"])[1] == {"context": "true"}
    assert cluster_named(**parse_argv(["--context"])[1]) is None
    assert cluster_named(context=None) is None


def test_the_recorder_keeps_every_call_with_the_cluster_that_asked():
    """The log's two halves stay in step, or attribution drifts into a false agreement.

    The recorder's cluster list is parallel to its log, so the risk is a mutation of the log that
    does not mutate the list: `recorded_api.calls.clear()` between two commands is the real one in
    this suite, and an old cluster attached to a new command's call would put a false label on the
    operation instead of a missing one. Here every mutation is exercised and the two lengths are
    compared, because "they are the same length" is the invariant, not a detail.
    """
    recorder = ApiRecorder()
    bound = recorder.for_target(TARGET_DEV)

    bound.create_namespace(name=NAMESPACE)
    assert len(recorder.calls) == len(recorder.calls.clusters) == 1
    assert [entry[2] for entry in recorder.entries()] == ["context dev"]

    recorder.calls.clear()
    assert recorder.calls == [] and recorder.calls.clusters == []

    bound.create_namespace(name=NAMESPACE)
    recorder.calls.pop()
    assert recorder.calls.clusters == [], "the cluster outlived the call it belonged to"

    recorder.calls.insert(0, ("read_namespace", {"name": NAMESPACE}))
    assert len(recorder.calls) == len(recorder.calls.clusters)
    assert recorder.entries()[0][2] is None, "an inserted call was not made through any client"

    bound.create_namespace(name=NAMESPACE)
    assert recorder.entries()[-1][2] == "context dev"


def test_an_operation_that_only_differs_by_cluster_is_called_out_in_the_failure(tmp_path):
    """The failure message has to survive the reader who did not read this file.

    Two columns that differ only in one bracket the eye slides past is how a wrong cluster gets
    merged, so the harness says the thing out loud when that is all there is. This is the assertion
    that the diagnosis belongs to the harness and not to the comment someone writes at 02:00.
    """
    kubeconfig, dev, prod = two_clusters(tmp_path)
    dev_write = kubectl_namespace_write(dev, kubeconfig)
    prod_write = kubectl_namespace_write(prod, kubeconfig)

    assert dev_write[0] != prod_write[0], "the two writes have to differ somewhere"
    with pytest.raises(AssertionError) as raised:
        compare(prod_write, dev_write)
    assert str(raised.value).count("which cluster was addressed") == 1


def test_a_target_that_names_neither_a_context_nor_a_file_refuses_to_be_bound():
    """The limit, stated rather than discovered by the next ticket.

    A `ClusterTarget` built from a connection document (the shape
    `from_service_account` builds) has a host and no context and no file, so there is nothing
    for the record to carry: the same question the argv answers for a binary has no answer here.
    It raises the same `ParityError` the reducer raises rather than inventing a second failure class
    for the same question, and it says which part is missing. `ClusterTarget.from_service_account`
    is what builds that shape today, and if a test needs to bind a recorder to a target shaped like
    that, this is
    where the gap has to be argued — the honest answer today is that the record cannot name it.
    """
    from cluster_fixture import target_cluster

    unaddressable = ClusterTarget(environment="library", context=None, kubeconfig_path=None,
                                  address="https://10.0.9.9:6443", source="connection")

    with pytest.raises(ParityError) as raised:
        target_cluster(unaddressable)
    assert "names neither" in str(raised.value)

    with pytest.raises(ParityError):
        ApiRecorder().for_target(unaddressable)


def test_the_record_carries_the_cluster_through_every_field_it_touches():
    """A new field that is part of the identity has to survive every normalisation.

    Folding a read's name away (`compare_key`) and folding a run's random suffix
    (`with_generated_run_name_normalized`) both rebuild the operation, and a rebuild that dropped the
    cluster would drop the only field this ticket added — silently, and only in the cases where the
    harness is doing something clever. Both are exercised here with a real operation.
    """
    written = ClusterOperation("ensure", "secret", NAMESPACE, "credentials",
                              (("type", "Opaque"),), cluster_named(context="dev"))

    assert ClusterOperation("read", "secret", NAMESPACE, "credentials",
                            (("type", "Opaque"),),
                            cluster_named(context="dev")) != written
    assert written.compare_key().cluster == "context dev"
    read = ClusterOperation("read", "pipelinerun", NAMESPACE, "hello-run-ab12cd", (),
                            cluster_named(context="dev"))
    folded = with_generated_run_name_normalized([read], "hello-run")
    assert folded[0].cluster == "context dev" and folded[0].name == "hello-run-<postfix>"
    assert hash(read) != hash(ClusterOperation("read", "pipelinerun", NAMESPACE, "hello-run-ab12cd",
                                               (), cluster_named(context="prod")))
