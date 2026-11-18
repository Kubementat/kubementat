"""A service account's token is a read a command can do, not a grep an operator types.

`kmt kubernetes service-account-token` reads the token off the annotation
`kubernetes.io/service-account.name` rather than off a `<name>-token` naming convention, because the
annotation is what says whose token a Secret is — and since 1.24 a cluster mints a Secret only for a
service account that declares it, so a name-based read finds nothing exactly when the cluster is
newest.

The two empty answers are the reason the failure modes are named. A service account with no token
Secret and a Secret the token controller has not filled in yet both used to print a header over
nothing and exit 0; each now answers in one line that names what is missing and says what fixes it,
because an operator cannot tell "no token yet" from "a kubeconfig built on an empty token".
"""

import base64
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes
from conftest import FIXTURE_ENVIRONMENT

NAMESPACE = "kubernetes-dashboard"
SERVICE_ACCOUNT = "kubernetes-dashboard-read-only-cluster-user"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.a-real-token.c-0nc34t3n4t3"

def token_secret(name, *, for_service_account=SERVICE_ACCOUNT, token=TOKEN):
    """A token Secret as the client returns it — type and annotation, which is how it is found.

    `data.token` is carried base64-encoded, the same way `jsonpath={.data.token}` hands it to
    `base64 --decode` on the bash side.
    """
    return SimpleNamespace(
        type="kubernetes.io/service-account-token",
        metadata=SimpleNamespace(name=name, annotations=(
            {"kubernetes.io/service-account.name": for_service_account}
            if for_service_account is not None else {})),
        data={"token": base64.b64encode(token.encode())} if token is not None else None)


def answered(*secrets):
    return lambda **kwargs: SimpleNamespace(items=list(secrets))


def read(*, namespace=NAMESPACE, service_account=SERVICE_ACCOUNT, stdout_only=False):
    """The command an operator runs, as a result. stdout is the token, nothing else.

    `stdout_only` separates the streams, which is the only way to assert the rule rather than just
    the value: the resolution report is stderr, so the credential's stdout stays one line.
    The environment is the first argument and is passed as one, because a credential read says which
    cluster it came from before it says anything else.
    """
    return CliRunner(mix_stderr=not stdout_only).invoke(
        kubernetes.service_account_token, [FIXTURE_ENVIRONMENT, namespace, service_account])


def asked_to_list(recorded_api):
    return [kwargs for method, kwargs in recorded_api.calls if method == "list_namespaced_secret"]


def test_the_token_is_printed_and_is_the_only_thing_the_command_prints(recorded_api):
    """One Secret, one answer, and no preamble on stdout.

    The script printed four lines of framing around the token; a command whose stdout is the
    credential has to be redirectable (`--output` is not needed when the token is the output), so
    framing is not free. The namespace is what the read is addressed at, which is why it is in the
    recorded calls and not inferred.
    """
    recorded_api.answer("list_namespaced_secret",
                        answered(token_secret("dashboard-reader-token-xyz12")))

    result = read(stdout_only=True)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == TOKEN
    assert asked_to_list(recorded_api) == [{"namespace": NAMESPACE}]
    # The other thing a run says is on the other stream, and it says which cluster was read.
    assert "cluster dev:" in result.stderr, result.stderr


def test_a_token_secret_named_by_a_different_convention_is_still_the_right_one(recorded_api):
    """The name is a convention; the annotation is the fact. The half of the parity that matters.

    The script matched on `<service-account>-token`, so a Secret that mints correctly but is named
    anything else was invisible to it. This one is named for something else and is found here —
    and the blob says, in the next test, that the script would have found nothing.
    """
    recorded_api.answer("list_namespaced_secret",
                        answered(token_secret("credentials-for-the-dashboard")))

    result = read(stdout_only=True)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == TOKEN


def test_a_service_account_with_no_token_secret_says_so_instead_of_printing_nothing(recorded_api):
    """Absence is a message now, not an empty `Token for service account X:` and exit 0.

    This is the failure that mattered: the operator pasted the blank into a kubeconfig, and the
    cluster told them nothing was wrong until the first request 401'd.
    """
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
    """A 404 from the listing is a namespace, not a missing service account.

    Without the conversion the command would either say "no token secret for X" — pointing at the
    wrong thing — or raise. The same conversion every other client-side read in this file's module
    makes, for the same reason.
    """
    recorded_api.answer("list_namespaced_secret", ApiException(status=404, reason="not found"))

    result = read(namespace="never-made-one")

    assert result.exit_code != 0
    assert "never-made-one" in result.output
    assert "does not exist" in result.output
    assert SERVICE_ACCOUNT not in result.output, "the namespace is what was missing here"


def test_the_command_is_on_the_surface_once_and_under_one_name():
    """`kmt --help` is the truth about reachability, and there is exactly one route to a token.

    The verb sits on the `kubernetes` group, which is where everything that addresses a cluster
    already lives; no `kmt secrets` subgroup would be consistent with the surface, and no alias would
    be a second place to agree about what the command is called. What this checks is that the move
    left no route behind: no name printed at the top level reads like a token, and the group holds the
    verb once.
    """
    from test_cli_surface import commands_from_help, help_output, run_help

    names = commands_from_help(help_output())
    group = commands_from_help(run_help("kmt", "kubernetes", "--help"))

    assert "kubernetes" in names, sorted(names)
    assert "service-account-token" in group, sorted(group)
    assert [name for name in names if "token" in name] == [], sorted(names)
    assert [name for name in group if "token" in name] == ["service-account-token"], sorted(group)


@pytest.fixture(autouse=True)
def _an_environment_the_command_can_resolve(resolvable_environment):
    """The command resolves a cluster before it reads, so this module needs one declared.

    `@kmt_context` runs ahead of the body and a token read is a cluster read. Nothing here uses the
    answer — the namespace is the argument the operator typed — so the fixture that declares a
    cluster and nothing else is the right size.
    """
    return resolvable_environment
