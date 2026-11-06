"""Tests for the reporting and debugging helpers: exec, logs, pods, nodes, api-resources.

`kmt deploy-ssh`, `kmt deploy-logs`, `kmt pvc-usage` and the uninstall's "here is the
cluster you are about to wipe" prompt all read through these, so what they replace is a
hand-written `kubectl` command line per script. The contract that makes that safe is the
one these tests pin:

* the command line is argv, with a pod name or a label selector that could contain a space
  arriving as one argument;
* something that streams, streams (`capture=False`) — capturing `logs -f` or an
  interactive shell is what made the old tunnel look frozen;
* a helper that only needs a verdict from the API goes through the client, not a shell.

The `kubectl` boundary is recorded where it is called. That nothing else in `src/` starts a
program is asserted by `test_command_execution_policy.py`.
"""

from types import SimpleNamespace

import pytest

from kubementat import shell
from kubementat.kubernetes_utils import KubernetesUtils
from cluster_fixture import TARGET_DEV
from kubementat.core.identity.target import kubectl_target_args


class RecordedRun:
    """What `shell.run` was asked to do, in place of running anything."""

    def __init__(self, stdout="", raises=None):
        self.argv = None
        self.kwargs = None
        self.stdout = stdout
        self.raises = raises

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return shell.CommandResult(argv, 0, stdout=self.stdout)


def make_utils(monkeypatch, **replacements):
    """A `KubernetesUtils` with no cluster: no kubeconfig, no real `kubectl`, no API.

    `replacements` maps `shell.run` (to a `RecordedRun`) and the API attributes to fakes. There is
    nothing left to neutralise here: building the object against `TARGET_DEV` reads nothing,
    because the clients are built when they are asked for and every one of them is being replaced
    below before anything asks.
    """
    utils = KubernetesUtils(TARGET_DEV)
    if "run" in replacements:
        monkeypatch.setattr("kubementat.kubernetes_utils.shell.run", replacements["run"])
    for attribute, api in replacements.items():
        if attribute != "run":
            setattr(utils, attribute, api)
    return utils


class FakeApi:
    def __init__(self, raises=None, returns=None):
        self.calls = []
        self.raises = dict(raises or {})
        self.returns = dict(returns or {})

    def __getattr__(self, method):
        def call(**kwargs):
            self.calls.append((method, kwargs))
            answer = self.returns.get(method)
            return answer(**kwargs) if callable(answer) else answer

        return call

    def called_as(self, method):
        matching = [kwargs for name, kwargs in self.calls if name == method]
        return matching[-1] if matching else None


# ---------------------------------------------------------------------------
# exec, in a container, as its own arguments
# ---------------------------------------------------------------------------
def test_an_interactive_shell_gets_a_stdin_and_a_tty(monkeypatch):
    run = RecordedRun()
    utils = make_utils(monkeypatch, run=run)

    utils.exec_in_pod("dev1", "sso-login-service-7d9-abc", ["/bin/bash"])

    assert run.argv == [
        "kubectl", *kubectl_target_args(TARGET_DEV), "--namespace", "dev1", "exec",
        "--stdin", "--tty", "sso-login-service-7d9-abc", "--", "/bin/bash",
    ]


def test_the_container_is_selected_before_the_command_it_runs(monkeypatch):
    run = RecordedRun()
    utils = make_utils(monkeypatch, run=run)

    utils.exec_in_pod("dev1", "nginx-0", ["env"], container="sidecar", interactive=False)

    assert run.argv == ["kubectl", *kubectl_target_args(TARGET_DEV), "--namespace", "dev1", "exec",
                        "nginx-0", "--container", "sidecar", "--", "env"]


def test_an_argument_with_a_space_stays_one_argument(monkeypatch):
    """The old shell path re-parsed what the operator typed; argv cannot split it."""
    run = RecordedRun()
    utils = make_utils(monkeypatch, run=run)

    utils.exec_in_pod("dev1", "nginx-0", ["sh", "-c", "cat /etc/hosts; echo hi"], interactive=False)

    assert run.argv[-3:] == ["sh", "-c", "cat /etc/hosts; echo hi"]


def test_a_command_run_for_a_verdict_is_captured_and_a_shell_is_not(monkeypatch):
    run = RecordedRun(stdout="")
    utils = make_utils(monkeypatch, run=run)

    utils.exec_in_pod("dev1", "nginx-0", ["df", "-h"], capture=True)

    assert run.kwargs["capture"] is True
    assert run.argv[-2:] == ["df", "-h"]


# ---------------------------------------------------------------------------
# logs: one helper that answers, one that keeps going
# ---------------------------------------------------------------------------
def test_logs_that_are_followed_are_never_captured(monkeypatch):
    """`logs -f` ends when the operator interrupts it; capturing it shows them nothing."""
    run = RecordedRun()
    utils = make_utils(monkeypatch, run=run)

    utils.stream_logs("dev1", "app.kubernetes.io/instance=nginx-example")

    assert run.kwargs["capture"] is False
    assert run.argv == [
        "kubectl", *kubectl_target_args(TARGET_DEV), "--namespace", "dev1", "logs",
        "--selector", "app.kubernetes.io/instance=nginx-example",
        "--timestamps=true", "--follow",
    ]


def test_following_logs_can_narrow_to_one_container_and_a_short_tail(monkeypatch):
    run = RecordedRun()
    utils = make_utils(monkeypatch, run=run)

    utils.stream_logs("dev1", "app.kubernetes.io/instance=nginx", container="nginx", tail_lines=10)

    assert run.argv[-4:] == ["--container", "nginx", "--tail", "10"]


def test_reading_logs_once_goes_to_the_api_rather_than_starting_a_command(monkeypatch):
    core = FakeApi(returns={"read_namespaced_pod_log": "1:1:1\n"})
    run = RecordedRun()
    utils = make_utils(monkeypatch, core_api=core, run=run)

    assert utils.get_pod_logs("dev1", "nginx-0", container="nginx", tail_lines=100) == "1:1:1\n"

    assert core.called_as("read_namespaced_pod_log") == {
        "namespace": "dev1", "name": "nginx-0", "container": "nginx", "tail_lines": 100, "timestamps": False,
    }
    assert run.argv is None  # no kubectl for a plain read


# ---------------------------------------------------------------------------
# pods, nodes, api-resources
# ---------------------------------------------------------------------------
def test_pods_are_selected_by_their_labels_and_come_back_empty_when_nothing_matches(monkeypatch):
    pods = [type("Pod", (), {"metadata": type("M", (), {"name": "nginx-0"})})()]
    core = FakeApi(returns={"list_namespaced_pod": lambda **kwargs: type("L", (), {"items": pods if kwargs["label_selector"] else []})()})
    utils = make_utils(monkeypatch, core_api=core)

    assert utils.list_pods("dev1", "app.kubernetes.io/instance=nginx") == pods
    assert utils.list_pods("dev1") == []


def test_the_nodes_report_their_addresses_and_capacity_for_the_uninstall_prompt(monkeypatch):
    def node(name, address, cpu):
        return type("Node", (), {
            "metadata": type("M", (), {"name": name})(),
            "status": type("S", (), {"addresses": [{"type": "InternalIP", "address": address}], "capacity": {"cpu": cpu}})(),
        })()

    core = FakeApi(returns={"list_node": lambda **kwargs: type("L", (), {"items": [node("k3s-server-1", "10.0.0.5", "4")]})()})
    utils = make_utils(monkeypatch, core_api=core)

    nodes = utils.get_nodes()

    assert nodes[0].status.addresses[0]["address"] == "10.0.0.5"
    assert nodes[0].status.capacity["cpu"] == "4"


def test_the_resource_types_a_cluster_serves_come_back_parsed(monkeypatch):
    """This was converted to discovery, and the table it returns is unchanged.

    `DynamicClient.resources` is keyed by `(group, version)` and then by resource name, and the
    old `kubectl api-resources --output=json` table was assembled from exactly that. So the read
    moves and the answer's shape does not: one entry per resource type, ordered by name, with
    `name` carrying the group suffix and `verbs` the verbs discovery reported.
    """
    discovery = SimpleNamespace(resources={
        ("tekton.dev", "v1beta1"): {"pipelines": {
            "singularName": "pipeline", "namespaced": True, "kind": "Pipeline", "verbs": ["*"]}},
    })
    utils = make_utils(monkeypatch, dynamic_client=discovery)

    assert utils.get_api_resources() == [
        {"name": "pipelines.tekton.dev", "singularName": "pipeline", "namespaced": True,
         "kind": "Pipeline", "verbs": ["*"]}]
