"""A service account's token is a read a command can do, not a grep an operator types."""

import base64
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from conftest import FIXTURE_ENVIRONMENT
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes

NAMESPACE = "kubernetes-dashboard"
SERVICE_ACCOUNT = "kubernetes-dashboard-read-only-cluster-user"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.a-real-token.c-0nc34t3n4t3"

def token_secret(name, *, for_service_account=SERVICE_ACCOUNT, token=TOKEN):
    """A token Secret as the client returns it — type and annotation, which is how it is found."""
    return SimpleNamespace(
        type="kubernetes.io/service-account-token",
        metadata=SimpleNamespace(name=name, annotations=(
            {"kubernetes.io/service-account.name": for_service_account}
            if for_service_account is not None else {})),
        data={"token": base64.b64encode(token.encode())} if token is not None else None)


def answered(*secrets):
    return lambda **kwargs: SimpleNamespace(items=list(secrets))


def read(*, namespace=NAMESPACE, service_account=SERVICE_ACCOUNT, stdout_only=False):
    """The command an operator runs, as a result. stdout is the token, nothing else."""
    return CliRunner(mix_stderr=not stdout_only).invoke(
        kubernetes.service_account_token, [FIXTURE_ENVIRONMENT, namespace, service_account])


def asked_to_list(recorded_api):
    return [kwargs for method, kwargs in recorded_api.calls if method == "list_namespaced_secret"]


def test_the_token_is_printed_and_is_the_only_thing_the_command_prints(recorded_api):
    """One Secret, one answer, and no preamble on stdout."""
    recorded_api.answer("list_namespaced_secret",
                        answered(token_secret("dashboard-reader-token-xyz12")))

    result = read(stdout_only=True)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == TOKEN
    assert asked_to_list(recorded_api) == [{"namespace": NAMESPACE}]
    # The other thing a run says is on the other stream, and it says which cluster was read.
    assert "cluster dev:" in result.stderr, result.stderr


def test_a_token_secret_named_by_a_different_convention_is_still_the_right_one(recorded_api):
    """The token secret is found by its service-account annotation, not by its name."""
    recorded_api.answer("list_namespaced_secret",
                        answered(token_secret("credentials-for-the-dashboard")))

    result = read(stdout_only=True)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == TOKEN


def test_a_service_account_with_no_token_secret_says_so_instead_of_printing_nothing(recorded_api):
    """Absence is a message now, not an empty `Token for service account X:` and exit 0."""
    recorded_api.answer("list_namespaced_secret",
                        answered(token_secret("someone-else-token-777",
                                              for_service_account="someone-else")))

    result = read()

    assert result.exit_code != 0, "a token that was never minted must not exit 0"
    message = result.output
    assert SERVICE_ACCOUNT in message and NAMESPACE in message
    assert "create_or_replace_service_account_token_secret" in message
    assert "Traceback" not in message, message


def test_a_namespace_that_was_never_created_names_the_namespace(recorded_api):
    """A 404 from the listing is a namespace, not a missing service account."""
    recorded_api.answer("list_namespaced_secret", ApiException(status=404, reason="not found"))

    result = read(namespace="never-made-one")

    assert result.exit_code != 0
    assert "never-made-one" in result.output
    assert "does not exist" in result.output
    assert SERVICE_ACCOUNT not in result.output, "the namespace is what was missing here"


def test_the_command_is_on_the_surface_once_and_under_one_name():
    """`kmt --help` is the truth about reachability, and there is exactly one route to a token."""
    from cli_help import commands_from_help, help_output, run_help

    names = commands_from_help(help_output())
    group = commands_from_help(run_help("kmt", "kubernetes", "--help"))

    assert "kubernetes" in names, sorted(names)
    assert "service-account-token" in group, sorted(group)
    assert [name for name in names if "token" in name] == [], sorted(names)
    assert [name for name in group if "token" in name] == ["service-account-token"], sorted(group)


@pytest.fixture(autouse=True)
def _an_environment_the_command_can_resolve(resolvable_environment):
    """The command resolves a cluster before it reads, so this module needs one declared."""
    return resolvable_environment
