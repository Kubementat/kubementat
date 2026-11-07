"""A shell inside a pod and a log stream: what they ask kubectl, and how they hand over.

`ssh_into_helm_deployment.sh` and `tail_logs_for_helm_deployment.sh` were a pod lookup and one
`kubectl` call each. That is all they did, and all of it is easy to get wrong in the direction
that does not fail a test: a captured `kubectl exec --stdin --tty` is a session nobody sees,
and a captured `kubectl logs -f` is a terminal that shows nothing until Ctrl+C. Both scripts
streamed, so both replacements must stream, and that is the assertion this file is built
around rather than an afterthought -- see `CiUtils` in `automations/ci.py`, which documents the same
rule for the
one interactive command that predates these two.

The other half is what both scripts answered *badly*, because bash had no way to tell a
namespace that does not exist apart from a deployment that is not installed:

* the namespace is asked of the client first, so a typo says "namespace X does not exist"
  before any pod lookup runs;
* the pod is found by the release label every other kubementat command uses
  (`app.kubernetes.io/instance`), not by a second selector invented near the exec call.

And one thing neither script did that the CLI does: a `kubectl` that fails on the way in
(a distroless image with no `/bin/bash`) is a one-line error, not a traceback.
"""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes
from conftest import FIXTURE_ENVIRONMENT
from kubementat.core.identity.target import kubectl_target_args
from kubementat.infrastructure import shell
from cluster_fixture import TARGET_DEV
from kubementat.errors import NamespaceNotFoundError, PodNotFoundError
from kubementat.infrastructure.kubernetes import KubernetesUtils

NAMESPACE = "deploy-me"
DEPLOYMENT = "web"
POD = "web-7d9f4-abcde"
SELECTOR = f"app.kubernetes.io/instance={DEPLOYMENT}"


class RecordingShell:
    """A `shell.run` that records what it was handed, so the streaming rule is assertable.

    An interactive call that is captured is indistinguishable in its argv from one that is
    not: the difference is `capture_output=True` on the child, and the symptom is a session
    that never returns. So the flag is what this recorder keeps, next to the argv.

    It answers nothing but the sessions themselves, because that is what is under test here;
    the pod lookup that comes before them goes through the client, so the fixture's
    `recorded_api` answers it. Everything else is a clean exit, which is what a session looks
    like when the operator has left.
    """

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
    """A cluster that answers, and a shell recorder in place of every child process.

    The namespace read is answered too, because the commands ask for it before they look for
    a pod: a fixture that left it unanswered would be testing the exception the client raises
    for an unconfigured method rather than the flow.
    """
    recorder = RecordingShell()
    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))
    # The pod lookup is a client read, so it answers through the same recorder the
    # namespace read does -- and `pod_names` stays the one dial that decides what it finds.
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(items=[
        SimpleNamespace(metadata=SimpleNamespace(name=name)) for name in recorder.pod_names]))
    monkeypatch.setattr(shell, "run", recorder.run)
    return recorder


def test_the_shell_is_handed_over_as_one_interactive_exec(cluster_with_a_web_pod, recorded_api):
    """`kubectl exec --stdin --tty <pod> -- /bin/bash`, with the bash a session expects.

    One argv, one call: the pod was found by a lookup that is not this command's business to
    re-derive, and what runs is `/bin/bash` as its own entry rather than a string a shell
    would have to parse.
    """
    KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell(NAMESPACE, DEPLOYMENT)

    call = cluster_with_a_web_pod.call_for("exec")
    # The target is in the argv, so a session that streams to the wrong cluster is
    # caught by the same assertion as a session that streams to no cluster at all.
    assert call["argv"] == ["kubectl", *kubectl_target_args(TARGET_DEV), "--namespace", NAMESPACE,
                            "exec", "--stdin", "--tty", POD, "--", "/bin/bash"]


def test_the_session_streams_and_is_never_captured(cluster_with_a_web_pod, recorded_api):
    """The whole reason this command can hang a test: a captured shell is a hung shell.

    `capture=False` is asserted here, not inferred from the argv. A session whose output is
    collected reaches the operator only when the child exits, which is when they typed
    `exit`, which is why an implementation that "worked" in a test is unusable in practice.
    """
    KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell(NAMESPACE, DEPLOYMENT)

    assert cluster_with_a_web_pod.call_for("exec")["capture"] is False


def test_the_pod_is_found_by_the_release_label_and_not_a_second_selector(cluster_with_a_web_pod, recorded_api):
    """The lookup is `get_pod_name_for_deployment`'s, addressed the way every command does it.

    The selector travels as one argument, which is what keeps a comma or a space inside a
    label out of the command line.
    """
    KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell(NAMESPACE, DEPLOYMENT)

    # The lookup is a client call, recorded on the API side rather than in argv.
    lookups = [(method, kwargs) for method, kwargs in recorded_api.calls
               if method == "list_namespaced_pod"]
    assert len(lookups) == 1, recorded_api.as_str()
    assert lookups[0][1]["namespace"] == NAMESPACE
    assert lookups[0][1]["label_selector"] == SELECTOR
    # The pod name that was read is the pod the exec was asked to enter.
    assert POD in cluster_with_a_web_pod.call_for("exec")["argv"]


def test_a_container_of_a_multi_container_pod_is_named_by_its_own_flag(cluster_with_a_web_pod, recorded_api):
    """`-c <container>` in the script, `--container <container>` here, and one entry each."""
    KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell(NAMESPACE, DEPLOYMENT, container="sidecar")

    argv = cluster_with_a_web_pod.call_for("exec")["argv"]
    assert argv[argv.index("--container") + 1] == "sidecar"
    # The container is a flag of `exec`, so it belongs before the `--` that ends kubectl's
    # own arguments and starts the command run inside the pod.
    assert argv.index("--container") < argv.index("--")


def test_logs_are_followed_with_timestamps_and_are_not_captured(cluster_with_a_web_pod, recorded_api):
    """The log half of the same rule: `-f` means the terminal, so nothing may collect it."""
    KubernetesUtils(TARGET_DEV, api_client=recorded_api).stream_deployment_logs(NAMESPACE, DEPLOYMENT)

    call = cluster_with_a_web_pod.call_for("logs")
    assert call["capture"] is False
    assert call["argv"][:6] == ["kubectl", *kubectl_target_args(TARGET_DEV), "--namespace",
                                NAMESPACE, "logs"]
    assert SELECTOR in call["argv"]
    assert "--timestamps=true" in call["argv"]
    assert "--follow" in call["argv"]


def test_a_tail_count_arrives_as_the_flag_it_names_and_not_as_a_default(cluster_with_a_web_pod, recorded_api):
    """Where to start is the one dial the script did not have and this command does.

    The script started at the beginning, which is what a rollout that failed an hour ago
    needs. `--tail` is the other question, and it is answered by kubectl rather than by a
    `tail -n` piped over a pipe.
    """
    KubernetesUtils(TARGET_DEV, api_client=recorded_api).stream_deployment_logs(NAMESPACE, DEPLOYMENT, container="sidecar",
                                             tail_lines=50)

    argv = cluster_with_a_web_pod.call_for("logs")["argv"]
    assert argv[argv.index("--tail") + 1] == "50"
    assert argv[argv.index("--container") + 1] == "sidecar"


def test_a_namespace_that_was_never_created_says_so_before_a_pod_is_seeked(recorded_api,
                                                                           monkeypatch):
    """Both commands, and neither of them starts by hunting for pods that cannot exist.

    The bash scripts had one failure shape for both questions and it named neither: kubectl
    printed an error about the namespace and exited. Here the read that can tell them apart
    comes first, so the answer the operator acts on is the one they get.
    """
    recorder = RecordingShell()
    recorded_api.answer("read_namespace", ApiException(status=404, reason="not found"))
    monkeypatch.setattr(shell, "run", recorder.run)

    for action in (KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell,
                   KubernetesUtils(TARGET_DEV, api_client=recorded_api).stream_deployment_logs):
        with pytest.raises(NamespaceNotFoundError) as error:
            action(NAMESPACE, DEPLOYMENT)
        assert NAMESPACE in str(error.value)
    assert recorder.calls == [], "the pod lookup ran before the namespace was confirmed"


def test_a_deployment_that_is_not_installed_names_the_selector_it_tried(recorded_api,
                                                                        monkeypatch):
    """Empty pod list, one message, and the fix in that message.

    A deployment that is not there is an ordinary state, so it is `PodNotFoundError` with the
    selector in it -- the same answer `get_pod_name_for_deployment` gives a tunnel that found
    nothing, rather than a second answer invented here.
    """
    recorder = RecordingShell(pod_names=())
    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))
    monkeypatch.setattr(shell, "run", recorder.run)

    with pytest.raises(PodNotFoundError) as error:
        KubernetesUtils(TARGET_DEV, api_client=recorded_api).open_deployment_shell(NAMESPACE, DEPLOYMENT)
    assert SELECTOR in str(error.value)


# ---------------------------------------------------------------------------
# the CLI surface: what an operator sees when the cluster answers no
# ---------------------------------------------------------------------------
def test_the_cli_answers_a_missing_namespace_in_one_line(recorded_api):
    """No traceback for a namespace that was never created, and no exit code that says 0.

    `click.ClickException` is the shape: one line of output, exit 1. The alternative is what
    an unhandled `ApiException` would have printed -- a stack, and a status code.
    """
    recorded_api.answer("read_namespace", ApiException(status=404, reason="not found"))

    for command in (kubernetes.deploy_logs, kubernetes.deploy_ssh):
        result = CliRunner().invoke(command, [FIXTURE_ENVIRONMENT, NAMESPACE, DEPLOYMENT])
        assert result.exit_code == 1, result.output
        assert "does not exist" in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit), result.output


def test_a_kubectl_that_fails_on_the_way_in_is_answered_as_an_error(fake_binaries, recorded_api):
    """The distroless case: the pod is fine, the binary is not, and kubectl says so.

    The scripts exited 1 with kubectl's text and no explanation. The command does the same
    thing more legibly: the failure is reported as a failure, and the stderr that names
    `/bin/bash` is in the message rather than two frames away from it.
    """
    fake_binaries.respond("kubectl", "error: unable to dohand /bin/bash: no such file\n",
                          exit_code=1, subcommand="exec")
    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(
        items=[SimpleNamespace(metadata=SimpleNamespace(name=POD))]))

    result = CliRunner().invoke(kubernetes.deploy_ssh,
                                [FIXTURE_ENVIRONMENT, NAMESPACE, DEPLOYMENT])

    assert result.exit_code == 1, result.output
    assert "bin/bash" in result.output or "exec" in result.output
    assert "Traceback" not in result.output


def test_the_log_command_takes_a_container_and_a_tail_without_losing_either(fake_binaries,
                                                                           recorded_api):
    """The arguments are the script's arguments, and one of them is an option.

    Positional `environment namespace deployment [container]`, then `--tail` -- which the script
    could only answer by editing itself. The environment is first because it says which cluster the
    session is opened against and the script could not say it; the rest stay where the script had
    them. Every value still travels as its own argv entry.
    """
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(
        items=[SimpleNamespace(metadata=SimpleNamespace(name=POD))]))
    fake_binaries.respond("kubectl", "", subcommand="logs")
    recorded_api.answer("read_namespace", SimpleNamespace(metadata=SimpleNamespace(
        name=NAMESPACE)))

    result = CliRunner().invoke(kubernetes.deploy_logs,
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
    """A session that streams still resolves first, so this module declares a cluster.

    `kubectl logs -f` and `kubectl exec` stay kubectl and still carry the target that
    `Context` resolved, which is why a test of their error handling now needs an environment that
    answers. It is not asked about: the failure under test is the pod or the namespace.
    """
    return resolvable_environment
