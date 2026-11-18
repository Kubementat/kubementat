"""`kmt users ...` fails closed, on the team and environment it was given.

Three commands, one flow, and the whole point of the CLI surface is that the arguments decide
what gets edited: `--environment` says which `platform_config/{environment}` this is, `--team`
says which of its directories. Before D9 both were defaults hardcoded in the CLI, which is how
`tunnel-*` commands came to read `dev1` no matter what an operator typed, and how a team account
could be created for a team that environment does not configure.

The contract is the one `kmt uninstall` established, and these commands inherit it:

* without `--yes` and without a terminal to ask in, nothing is created and the message names the
  flag. A lane run is exactly this case; the bash's answer to it was EOF, `set -e`, and an exit
  code with no explanation.
* `--yes` answers the question. It does not change what the flow does, and it does not skip the
  checks that say whether this cluster will allow it at all (the ones in
  `test_a_team_account_run_names_the_cluster_and_checks_the_verbs_it_needs.py`).
* a team or environment that is not configured is an error that names the file, raised before
  the cluster is asked anything. Guessing is not an option when the action is "create a
  credential".
* the account the delete command removes is the whole account: the ServiceAccount, the token
  Secret that outlives it, and every RoleBinding that let it act, including the ones that let it
  tunnel into a component's namespace.
"""

import base64
import json

import pytest
from click.testing import CliRunner

from cli.commands import users
from conftest import is_a_write
import kubementat.infrastructure.user_management as user_management
from kubementat.consent import Consent
from kubementat.automations.accounts.accounts import create_team_account, delete_team_account
from cluster_fixture import configuration_only_context

ACCOUNT = "ops-account"

# `kmt users <name>` and the function behind it. Listed once, so the tests that run "all
# three" do not hand-assemble names out of a loop.
TEAM_ACCOUNT_COMMANDS = {
    "create-team-account": users.create_team_account,
    "create-team-account-read-only": users.create_team_account_read_only,
    "delete-team-account": users.delete_team_account,
}
SERVER = "https://the-cluster-under-test.example.test:6443"
AUTHORITY = "RlklWEFSVElKVFlURVNU="
ACCOUNT_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZlYXR1cmUifQ.payload.signature"

CLUSTER_NAMESPACES = [
    "default", "kube-system", "kube-public", "kube-node-lease",
    "tekton-pipelines", "grafana", "polaris", "ops-deployments", "ops-pipelines",
]


def cluster_holding(recorded_api, namespaces):
    recorded_api.answer("list_namespace", lambda **kwargs: type("List", (), {"items": [
        type("Namespace", (), {"metadata": type("M", (), {"name": n})})() for n in namespaces]})())


def token_secret_filled_in(recorded_api, service_account=ACCOUNT, namespace=None):
    def answer(namespace=None, **kwargs):
        return type("SecretList", (), {"items": [type("Secret", (), {
            "type": "kubernetes.io/service-account-token",
            "metadata": type("Meta", (), {
                "name": f"{service_account}-sa-secret",
                "annotations": {"kubernetes.io/service-account.name": service_account}})(),
            "data": {"token": base64.b64encode(ACCOUNT_TOKEN.encode())},
        })()]})()

    recorded_api.answer("list_namespaced_secret", answer)


@pytest.fixture
def cluster_ready_for_team_accounts(fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The cluster every command in this file is pointed at: verbs granted, token ready."""
    for verb, resource in user_management.CLUSTER_ACCESS_CHECKS:
        fake_binaries.respond("kubectl", "yes\n", subcommand=f"auth.can-i.{verb}.{resource}")
    fake_binaries.respond("kubectl", json.dumps({"clusters": [{
        "name": "under-test", "cluster": {"server": SERVER, "certificate-authority-data": AUTHORITY}}]}),
        subcommand="config.view")
    cluster_holding(recorded_api, CLUSTER_NAMESPACES)
    token_secret_filled_in(recorded_api)
    recorded_api.calls.clear()
    fake_binaries.take_calls()
    return fixture_repo


def client_writes(recorded_api):
    return [(method, kwargs) for method, kwargs in recorded_api.calls if is_a_write(method)]


def bindings_created(recorded_api):
    return {(kwargs.get("namespace"), (kwargs.get("body") or {}).get("metadata", {}).get("name"))
            for method, kwargs in recorded_api.calls if method == "create_namespaced_role_binding"}


def test_the_three_commands_are_the_same_gate_with_the_same_answer(
        cluster_ready_for_team_accounts, fake_binaries, recorded_api, fixture_repo):
    """`--yes` is not a mode for one of them: it is the scripted answer for all three.

    The duplicated bash script is what taught this the hard way -- the fix reached one copy and
    not the other, so the read-only variant and the delete variant still asked. Running all
    three through a non-terminal stdin and checking each one reaches the cluster is the
    cheapest version of the assertion the lane needs.
    """
    for command in TEAM_ACCOUNT_COMMANDS:
        recorded_api.calls.clear()
        fake_binaries.take_calls()

        result = CliRunner().invoke(TEAM_ACCOUNT_COMMANDS[command],
                                    [ACCOUNT, "--environment", fixture_repo.environment,
                                     "--team", fixture_repo.team, "--yes"])

        assert result.exit_code == 0, f"kmt users {command}: {result.output}"
        assert client_writes(recorded_api) or recorded_api.calls, f"kmt users {command} did nothing"


def test_a_team_configuration_that_is_not_json_is_refused_before_anything_is_written(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """The other unreadable file: valid namespaces cannot be read out of a broken document.

    Uninstall skips such a directory because deleting less is the safe direction. This command
    cannot skip it -- it has already been told which team to edit, and answering that question
    would mean a traceback of the tool's own JSON reader where the operator asked for a filename.
    """
    path = fixture_repo.platform_config / fixture_repo.environment / fixture_repo.team / "static.json"
    path.write_text('{"PIPELINE_NAMESPACE": "broken",\n')

    result = CliRunner().invoke(users.create_team_account,
                                [ACCOUNT, "--environment", fixture_repo.environment,
                                 "--team", fixture_repo.team, "--yes"])

    assert result.exit_code != 0, result.output
    assert "static.json" in result.output, result.output
    assert "Traceback" not in result.output, "a config problem arrived as a stack trace"
    assert client_writes(recorded_api) == []


def test_an_environment_that_was_never_initialized_is_named_by_the_file_that_is_missing(
        cluster_ready_for_team_accounts, fixture_repo, fake_binaries, recorded_api):
    """The other half of "which environment am I editing": a directory that is not there.

    Checked after the cluster's own permissions, which is the order the flow runs in, and
    before a single write. A missing environment file has to arrive as a message that names the
    path; anything else is a traceback where an operator expected a filename.
    """
    (fixture_repo.platform_config / fixture_repo.environment / "static.json").unlink()

    result = CliRunner().invoke(users.create_team_account,
                                [ACCOUNT, "--environment", fixture_repo.environment,
                                 "--team", fixture_repo.team, "--yes"])

    assert result.exit_code != 0
    assert "static.json" in result.output, (
        f"an uninitialized environment answered with {result.exception!r} instead of the file "
        "that is missing -- the same failure `kmt uninstall` reports by name")
    assert client_writes(recorded_api) == [], "it wrote before it knew which environment this was"


def test_deleting_an_account_leaves_the_roles_and_destroys_the_credential(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """A Role belongs to a namespace, not to the person who was bound to it.

    Two accounts of one team share `namespace-admin`; a delete that swept the Roles with the
    account would take the other one's access with it. Only the bindings, the account and its
    token go. The credential is the part that must not survive, because it is already sitting
    in somebody's `~/.kube/config`.
    """
    delete_team_account(configuration_only_context(fixture_repo.environment, fixture_repo.team), 
        ACCOUNT, consent=Consent.assumed())

    assert not [kwargs for method, kwargs in recorded_api.calls
                if method == "delete_namespaced_role"], "a delete swept Roles the team still needs"
    deleted_secret = [(method, kwargs) for method, kwargs in recorded_api.calls
                      if method == "delete_namespaced_secret"]
    assert deleted_secret, "the credential outlived the account"


def test_a_binding_for_a_tunnel_into_a_component_that_is_not_here_is_not_deleted(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """Nothing is claimed to be deleted in a namespace that was never in play.

    The tunnels that were skipped at create time have no bindings to delete; a delete that
    guessed by namespace name would report a cleanup that never happened.
    """
    cluster_holding(recorded_api, [ns for ns in CLUSTER_NAMESPACES if ns != "polaris"])
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    create_team_account(context, ACCOUNT, consent=Consent.assumed())
    recorded_api.calls.clear()

    delete_team_account(context, ACCOUNT, consent=Consent.assumed())

    assert not any(kwargs.get("namespace") == "polaris"
                   for method, kwargs in recorded_api.calls
                   if method == "delete_namespaced_role_binding")


def test_an_account_created_twice_is_the_same_account_and_not_two_of_them(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """Idempotence, because the lane runs the create twice and a rerun must not fight itself.

    The flow goes through create-or-replace primitives; running it a second time has to be a
    no-op on the shape of what is there, not an error and not a second set of bindings.
    """
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    create_team_account(context, ACCOUNT, consent=Consent.assumed())
    once = [repr(operation) for operation in recorded_api.operations()]
    recorded_api.calls.clear()

    create_team_account(context, ACCOUNT, consent=Consent.assumed())

    assert [repr(operation) for operation in recorded_api.operations()] == once
