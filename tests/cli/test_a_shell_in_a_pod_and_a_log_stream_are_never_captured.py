"""A shell inside a pod and a log stream: what they ask kubectl, and how they hand over."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from cluster_fixture import TARGET_DEV
from conftest import FIXTURE_ENVIRONMENT
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes
from kubementat.errors import NamespaceNotFoundError
from kubementat.infrastructure import shell
from kubementat.infrastructure.kubernetes import KubernetesUtils

NAMESPACE = "deploy-me"
DEPLOYMENT = "web"
POD = "web-7d9f4-abcde"
SELECTOR = f"app.kubernetes.io/instance={DEPLOYMENT}"


class RecordingShell:
    """A `shell.run` that records what it was handed, so the streaming rule is assertable."""

    def __init__(self, pod_names=(POD,)):
        self.pod_names = list(pod_names)
        self.calls = []

    def run(self, argv, capture=False, check=True, **kwargs):
        argv = list(argv)
        self.calls.append({"argv": argv, "capture": capture, "check": check})
        return shell.CommandResult(argv, 0)

    @property
    def last(self):
        return self.calls[-1]

    def call_for(self, verb):
        """The most recent call that ran *verb*, so a test does not read a call by index."""
        for call in reversed(self.calls):
            if verb in call["argv"]:
                return call
        raise AssertionError(f"nothing called {verb!r}; ran {self.calls}")

    @property
    def verbs(self):
        return [call["argv"][0:4] for call in self.calls]


@pytest.fixture
def cluster_with_a_web_pod(recorded_api, monkeypatch):
    """A cluster that answers, and a shell recorder in place of every child process."""
    recorder = RecordingShell()
    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))
    # The pod lookup is a client read, so it answers through the same recorder the
    # namespace read does -- and `pod_names` stays the one dial that decides what it finds.
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(items=[
        SimpleNamespace(metadata=SimpleNamespace(name=name)) for name in recorder.pod_names]))
    monkeypatch.setattr(shell, "run", recorder.run)
    return recorder


def test_the_session_streams_and_is_never_captured(cluster_with_a_web_pod, recorded_api):
    """The whole reason this command can hang a test: a captured shell is a hung shell."""
    KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell(NAMESPACE, DEPLOYMENT)

    assert cluster_with_a_web_pod.call_for("exec")["capture"] is False


def test_a_namespace_that_was_never_created_says_so_before_a_pod_is_seeked(recorded_api,
                                                                           monkeypatch):
    """Both commands, and neither of them starts by hunting for pods that cannot exist."""
    recorder = RecordingShell()
    recorded_api.answer("read_namespace", ApiException(status=404, reason="not found"))
    monkeypatch.setattr(shell, "run", recorder.run)

    for action in (KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell,
                   KubernetesUtils(TARGET_DEV, api_client=recorded_api).stream_deployment_logs):
        with pytest.raises(NamespaceNotFoundError) as error:
            action(NAMESPACE, DEPLOYMENT)
        assert NAMESPACE in str(error.value)
    assert recorder.calls == [], "the pod lookup ran before the namespace was confirmed"


# ---------------------------------------------------------------------------
# the CLI surface: what an operator sees when the cluster answers no
# ---------------------------------------------------------------------------
def test_the_cli_answers_a_missing_namespace_in_one_line(recorded_api):
    """No traceback for a namespace that was never created, and no exit code that says 0."""
    recorded_api.answer("read_namespace", ApiException(status=404, reason="not found"))

    for command in (kubernetes.deployment_logs, kubernetes.deployment_ssh):
        result = CliRunner().invoke(command, [FIXTURE_ENVIRONMENT, NAMESPACE, DEPLOYMENT])
        assert result.exit_code == 1, result.output
        assert "does not exist" in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit), result.output


def test_a_kubectl_that_fails_on_the_way_in_is_answered_as_an_error(fake_binaries, recorded_api):
    """The distroless case: the pod is fine, the binary is not, and kubectl says so."""
    fake_binaries.respond("kubectl", "error: unable to dohand /bin/bash: no such file\n",
                          exit_code=1, subcommand="exec")
    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(
        items=[SimpleNamespace(metadata=SimpleNamespace(name=POD))]))

    result = CliRunner().invoke(kubernetes.deployment_ssh,
                                [FIXTURE_ENVIRONMENT, NAMESPACE, DEPLOYMENT])

    assert result.exit_code == 1, result.output
    assert "bin/bash" in result.output or "exec" in result.output
    assert "Traceback" not in result.output


def test_the_log_command_takes_a_container_and_a_tail_without_losing_either(fake_binaries,
                                                                           recorded_api):
    """The arguments are the script's arguments, and one of them is an option."""
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(
        items=[SimpleNamespace(metadata=SimpleNamespace(name=POD))]))
    fake_binaries.respond("kubectl", "", subcommand="logs")
    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))

    result = CliRunner().invoke(kubernetes.deployment_logs,
                                [FIXTURE_ENVIRONMENT, NAMESPACE, DEPLOYMENT, "sidecar",
                                 "--tail", "25"])

    assert result.exit_code == 0, result.output
    calls = fake_binaries.take_calls()
    logs = [call for call in calls if call.program == "kubectl" and "logs" in call.args]
    assert logs, calls
    argv = logs[-1].args
    assert argv[argv.index("--tail") + 1] == "25"
    assert argv[argv.index("--container") + 1] == "sidecar"
    # ...and neither value is a joined token a shell would have to split.
    assert all(" " not in argument for argument in argv)


@pytest.fixture(autouse=True)
def _an_environment_the_command_can_resolve(resolvable_environment):
    """A session that streams still resolves first, so this module declares a cluster."""
    return resolvable_environment
