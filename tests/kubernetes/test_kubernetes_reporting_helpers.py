"""Tests for the reporting and debugging helpers: exec, logs, pods, nodes, api-resources."""

from types import SimpleNamespace

from cluster_fixture import TARGET_DEV

from kubementat.core.identity.target import kubectl_target_args
from kubementat.infrastructure import shell
from kubementat.infrastructure.kubernetes import KubernetesUtils


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
    """A `KubernetesUtils` with no cluster: no kubeconfig, no real `kubectl`, no API."""
    utils = KubernetesUtils(TARGET_DEV)
    if "run" in replacements:
        monkeypatch.setattr("kubementat.infrastructure.kubernetes.exec.shell.run", replacements["run"])
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


def test_the_container_is_selected_before_the_command_it_runs(monkeypatch):
    run = RecordedRun()
    utils = make_utils(monkeypatch, run=run)

    utils.exec_in_pod("dev1", "nginx-0", ["env"], container="sidecar", interactive=False)

    assert run.argv == ["kubectl", *kubectl_target_args(TARGET_DEV), "--namespace", "dev1", "exec",
                        "nginx-0", "--container", "sidecar", "--", "env"]


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


def test_the_resource_types_a_cluster_serves_come_back_parsed(monkeypatch):
    """This was converted to discovery, and the table it returns is unchanged."""
    discovery = SimpleNamespace(resources={
        ("tekton.dev", "v1beta1"): {"pipelines": {
            "singularName": "pipeline", "namespaced": True, "kind": "Pipeline", "verbs": ["*"]}},
    })
    utils = make_utils(monkeypatch, dynamic_client=discovery)

    assert utils.get_api_resources() == [
        {"name": "pipelines.tekton.dev", "singularName": "pipeline", "namespaced": True,
         "kind": "Pipeline", "verbs": ["*"]}]
