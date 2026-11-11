"""A service account's token is a read a command can do, not a grep an operator types.

`scripts/secret_management/retrieve_token_for_service_account.sh` is deleted here. Its record is:

    git show 19b97d0:scripts/secret_management/retrieve_token_for_service_account.sh

and the comparison below reads that blob at test time rather than copying it, so the record cannot
drift from the file it came from. What the blob is: `kubectl get secret` piped through
`grep "<service-account>-token"`, then `jsonpath={.data.token} | base64 --decode`. It read the name
as the convention, and the convention is only half the story — the annotation is what says whose
token a Secret is, and since 1.24 a cluster mints one only for a Secret that declares it.

That difference is the parity this file proves, and so is the part bash got wrong quietly. The
script's failure modes were silence and exit 0:

* no Secret at all -> `token_name` was empty, the `get secret` still exited, and the operator got
  `Token for service account X:` over nothing;
* a Secret the token controller has not filled in yet -> the same, with `token:` empty.

Both now answer in one line that names what is missing and says what fixes it, because the command
is the difference between those two states and a kubeconfig built on an empty token.
"""

import base64
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes
from conftest import FIXTURE_ENVIRONMENT

NAMESPACE = "kubernetes-dashboard"
SERVICE_ACCOUNT = "kubernetes-dashboard-read-only-cluster-user"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.a-real-token.c-0nc34t3n4t3"

BLOB_REVISION = "19b97d0"
SCRIPT_IN_HISTORY = "scripts/secret_management/retrieve_token_for_service_account.sh"


def deleted_script():
    """The script's own text, out of git history. Skips, loudly, if the history is not there."""
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{SCRIPT_IN_HISTORY}"],
                            cwd=str(Path(__file__).resolve().parent.parent),
                            capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{BLOB_REVISION}:{SCRIPT_IN_HISTORY} exited {result.returncode} "
                    f"({result.stderr.strip()}). The parity below is a comparison, and without "
                    f"the other side of it there is nothing to compare.")
    return result.stdout


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


def not_minted_yet(*_args, **_kwargs):
    """A Secret of that type the controller has not filled in: `data` is absent, not empty."""
    return token_secret(f"{SERVICE_ACCOUNT}-token-abcde", token=None)


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


def test_a_token_secret_the_controller_has_not_filled_in_says_that_and_not_that_it_is_missing(
        recorded_api):
    """The two states an operator acts on differently, told apart.

    "There is no token" and "the token has not been issued yet" look the same to a grep over `kubectl
    get secret`. The second one means wait and ask again; the first means the Secret was never
    requested, and an operator following the first piece of advice would wait forever.
    """
    recorded_api.answer("list_namespaced_secret", answered(not_minted_yet()))

    result = read()

    assert result.exit_code != 0
    assert "has not filled" in result.output or "carries no token" in result.output, result.output
    assert "request it again" in result.output, result.output


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

    Flat, like every command on this surface except `users`: no `kmt secrets` subgroup would be
    consistent with what is already there, and no alias would be a second place to agree about what
    the command is called.
    """
    from test_cli_surface import commands_from_help, help_output

    names = commands_from_help(help_output())

    assert "service-account-token" in names, sorted(names)
    assert [name for name in names if "token" in name] == ["service-account-token"], sorted(names)


def test_the_script_found_the_token_by_a_name_and_this_finds_it_by_an_annotation():
    """The comparison, with the other side read out of git.

    Both sides end at the same bytes — `data.token`, base64-decoded. The difference is which Secret
    each one is willing to believe, and it is visible in the one line of the script that does the
    finding.
    """
    script = deleted_script()

    # The finding half, verbatim: a grep over `kubectl get secret` for `<service-account>-token`.
    assert re.search(r'grep\s+"\$\{SERVICE_ACCOUNT_NAME\}-token"', script), script
    # ...and the decode, which is the one thing both sides do identically.
    assert "jsonpath={.data.token}" in script and "base64" in script, script
    # No `kubernetes.io/service-account.name` anywhere: bash never asked who the token belonged to.
    assert "service-account.name" not in script, script


def test_the_script_had_no_answer_for_a_token_that_is_not_there():
    """The failure modes as facts about the deleted file, so they cannot be re-introduced.

    Empty `token_name`, then `get secret "" --output=jsonpath=...` failing or printing nothing, and
    the script echoing the header anyway. `set -eu` is on *after* the usage check, which is why a
    missing token still exited 0: the `token_name` assignment is not the line that fails, `get
    secret ""` is, and by then the header has been printed.
    """
    script = deleted_script()

    assert 'echo "Found token: $token_name"' in script
    assert 'echo "Token for service account $SERVICE_ACCOUNT_NAME:"' in script
    assert "exit 1" not in script.split("set -eu")[1], "the failure half never failed"


@pytest.fixture(autouse=True)
def _an_environment_the_command_can_resolve(resolvable_environment):
    """The command resolves a cluster before it reads, so this module needs one declared.

    `@kmt_context` runs ahead of the body and a token read is a cluster read whatever the
    script that preceded it believed. Nothing here uses the answer — the namespace is the argument
    the operator typed — so the fixture that declares a cluster and nothing else is the right size.
    """
    return resolvable_environment
