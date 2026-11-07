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
import logging

import pytest
import yaml
from click.testing import CliRunner

from cli.commands import users
from cluster_fixture import STAGING_SERVER, write_kubeconfig
from conftest import is_a_write
import kubementat.infrastructure.user_management as user_management
from kubementat.automations.accounts.accounts import create_team_account, create_team_account_read_only, delete_team_account
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


def write_shapes(recorded_api):
    """Every write reduced to (verb, kind, namespace), in the order it happened.

    The names come out of the comparison on purpose: this asks whether the two variants touched
    the same places in the same order, which is a question about the flow and not about which
    Role it happened to name.
    """
    return [(operation.verb, operation.kind, operation.namespace)
            for operation in recorded_api.operations() if not operation.is_read]


def namespaces_written_to(recorded_api):
    return sorted({kwargs.get("namespace") for method, kwargs in recorded_api.calls
                   if method.startswith(("create_", "delete_", "replace_"))
                   and kwargs.get("namespace")})


def bindings_created(recorded_api):
    return {(kwargs.get("namespace"), (kwargs.get("body") or {}).get("metadata", {}).get("name"))
            for method, kwargs in recorded_api.calls if method == "create_namespaced_role_binding"}


def bindings_deleted(recorded_api):
    return {(kwargs.get("namespace"), kwargs.get("name"))
            for method, kwargs in recorded_api.calls if method == "delete_namespaced_role_binding"}


def kubeconfig_printed_in(caplog):
    """The text the command handed the operator, or None if it printed none.

    The document is what the product is, so it is read back out of the log rather than trusted
    to be there: the command logs the header and the text next to each other, which is what
    makes the header a reliable marker.
    """
    text = "\n".join(record.getMessage() for record in caplog.records)
    start = text.find("apiVersion: v1")
    if start == -1:
        return None
    return text[start:]


def test_every_team_account_command_refuses_a_run_that_cannot_answer(
        cluster_ready_for_team_accounts, fake_binaries, recorded_api, fixture_repo):
    """No terminal, no `--yes`, and three commands that all behave the same way about it.

    `CliRunner` hands a command a stream that is not a terminal, which is the lane's case.
    Nothing is asked of the cluster and nothing is written; the operator is told the flag.
    """
    for command in TEAM_ACCOUNT_COMMANDS:
        recorded_api.calls.clear()
        fake_binaries.take_calls()

        result = CliRunner().invoke(TEAM_ACCOUNT_COMMANDS[command],
                                    [ACCOUNT, "--environment", fixture_repo.environment,
                                     "--team", fixture_repo.team])

        assert result.exit_code != 0, f"kmt users {command} ran without confirmation"
        assert "--yes" in result.output, f"kmt users {command} did not say how to proceed"
        assert recorded_api.calls == [], f"kmt users {command} touched the cluster unasked"
        assert fake_binaries.calls() == [], f"kmt users {command} ran commands unasked"


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


def test_creating_an_account_prints_a_kubeconfig_for_the_namespace_the_team_owns(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api, caplog):
    """The product of `create-team-account` is text, and it is checked as text.

    Loads, names the account, addresses the cluster this run read, and points the context at
    the team's own namespace. Everything a person pastes is asserted here; the rest is what
    `test_a_team_account_kubeconfig_is_a_document_and_not_a_string.py` covers.
    """
    with caplog.at_level(logging.INFO):
        create_team_account(configuration_only_context(fixture_repo.environment, fixture_repo.team), 
            ACCOUNT, assume_yes=True)

    text = kubeconfig_printed_in(caplog)
    assert text is not None, "the command printed no kubeconfig"
    document = yaml.safe_load(text)
    context = document["contexts"][0]["context"]
    assert document["current-context"] == document["contexts"][0]["name"]
    assert context["namespace"] == fixture_repo.app_namespace
    assert context["user"] == document["users"][0]["name"] == ACCOUNT
    assert document["users"][0]["user"]["token"] == ACCOUNT_TOKEN
    assert document["clusters"][0]["cluster"]["server"] == SERVER, \
        "the address in the printed text is not the cluster this run spoke to"


def test_a_team_the_environment_does_not_configure_is_an_error_that_names_the_file(
        cluster_ready_for_team_accounts, fake_binaries, recorded_api, fixture_repo):
    """Typing the wrong `--team` must not create an account in somebody else's namespaces.

    A team directory that does not exist has no namespaces to name, and a flow that read
    `None` would hand the client a namespace of `None`. It says which file is missing, before
    it asks the cluster anything.
    """
    result = CliRunner().invoke(users.create_team_account,
                                [ACCOUNT, "--environment", fixture_repo.environment,
                                 "--team", "no-such-team", "--yes"])

    assert result.exit_code != 0
    assert "static.json" in result.output, result.output
    assert client_writes(recorded_api) == [], "an unknown team still got as far as the cluster"


def test_a_team_that_names_one_of_the_two_namespaces_is_still_askable(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """Uninstall tolerates a half-written team directory, and a team account does too.

    This used to be a refusal. `PIPELINE_NAMESPACE` is one of the answers a team's name determines, so
    a directory that names its applications namespace and nothing else has still named both: the
    second one follows from the team, and the account is bound in the namespace the rule computes
    exactly as it is bound in the one it was given. What did not change is that nothing is bound into
    a namespace no team and no file names: an environment that configures no such team still stops
    this command before the first write, which is the test just above this one.
    """
    (fixture_repo.platform_config / fixture_repo.environment
     / fixture_repo.team / "static.json").write_text(json.dumps(
        {"APP_DEPLOYMENT_NAMESPACE": fixture_repo.app_namespace,
         "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "fixture-helm-deployer"}))

    result = CliRunner().invoke(users.create_team_account,
                                [ACCOUNT, "--environment", fixture_repo.environment,
                                 "--team", fixture_repo.team, "--yes"])

    assert result.exit_code == 0, result.output
    namespaces = {namespace for _, _, namespace in write_shapes(recorded_api)}
    assert namespaces, "a team that named a team named nothing, and no namespace was bound"
    assert fixture_repo.app_namespace in namespaces, namespaces
    assert fixture_repo.pipeline_namespace in namespaces, \
        "the pipelines half was never bound: %s" % namespaces


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


def test_the_command_acted_on_the_team_it_was_handed_and_not_on_the_default_one(
        cluster_ready_for_team_accounts, fixture_repo, fake_binaries, recorded_api):
    """`--team` is the whole argument, and it has to reach the namespaces.

    A second team is configured with namespaces that cannot be mistaken for the fixture's. If
    the CLI passed a default through, or the flow resolved the team twice, the writes would land
    in `fixture-*` and the operator would get an account for a team they did not name.
    """
    other = "ops"
    (fixture_repo.platform_config / fixture_repo.environment / other).mkdir(parents=True,
                                                                             exist_ok=True)
    fixture_repo.write_json(
        fixture_repo.platform_config / fixture_repo.environment / other / "static.json",
        {"PIPELINE_NAMESPACE": "ops-pipelines", "APP_DEPLOYMENT_NAMESPACE": "ops-deployments",
         "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "ops-helm-deployer"})
    fixture_repo.write_json(
        fixture_repo.platform_config / fixture_repo.environment / other / "static.encrypted.json",
        {})
    recorded_api.calls.clear()

    result = CliRunner().invoke(users.create_team_account,
                               [ACCOUNT, "--environment", fixture_repo.environment,
                                "--team", other, "--yes"])

    assert result.exit_code == 0, result.output
    written = namespaces_written_to(recorded_api)
    assert "ops-deployments" in written, written
    assert "fixture-deployments" not in written and "fixture-pipelines" not in written, written


def test_the_environment_option_picks_the_cluster_the_namespaces_come_from(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """`--environment` is the other argument, and it changes what a namespace means.

    A second environment is configured for the same team, with namespaces that cannot be
    mistaken for the fixture's. Running against it proves the command reads
    `platform_config/{environment}` rather than the one this test run happens to have on disk
    -- and that a typo cannot silently run against whichever directory was there first.
    """
    root = fixture_repo.platform_config
    dev = root / fixture_repo.environment
    (root / "staging").mkdir(exist_ok=True)
    # Copied from the fixture's own environment files rather than written out: the only thing
    # that has to differ is where the namespaces point, and a second copy of the fixture's
    # values would be a second source of truth about what a namespace is.
    #
    # The cluster declaration cannot be copied. Two environments that address one cluster are a
    # collision is refused outright, so `staging` gets a context of its own in a file of its own —
    # otherwise this test would be asserting the collision message and not the environment it is
    # asking for.
    fixture_repo.write_json(root / "staging" / "static.json",
                            dict(json.loads((dev / "static.json").read_text()),
                                 TEKTON_DEPLOYMENT_NAMESPACE="staging-tekton",
                                 CLUSTER_KUBE_CONTEXT="staging",
                                 CLUSTER_KUBECONFIG_PATH=str(write_kubeconfig(
                                     root / "staging-kubeconfig", {"staging": STAGING_SERVER}))))
    fixture_repo.write_json(root / "staging" / "static.encrypted.json",
                            json.loads((dev / "static.encrypted.json").read_text()))
    (root / "staging" / fixture_repo.team).mkdir(parents=True, exist_ok=True)
    fixture_repo.write_json(root / "staging" / fixture_repo.team / "static.json",
                            {"PIPELINE_NAMESPACE": "staging-pipelines",
                             "APP_DEPLOYMENT_NAMESPACE": "staging-deployments",
                             "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "staging-helm-deployer"})
    fixture_repo.write_json(root / "staging" / fixture_repo.team / "static.encrypted.json", {})
    recorded_api.calls.clear()

    result = CliRunner().invoke(users.create_team_account,
                                [ACCOUNT, "--environment", "staging",
                                 "--team", fixture_repo.team, "--yes"])

    assert result.exit_code == 0, result.output
    written = namespaces_written_to(recorded_api)
    assert "staging-deployments" in written and "staging-pipelines" in written, written
    assert "fixture-deployments" not in written and "fixture-pipelines" not in written, written


def test_deleting_an_account_removes_the_service_account_and_the_token_that_outlives_it(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """The credential is what anyone holding the kubeconfig authenticates with.

    Deleting the ServiceAccount does not delete the Secret this flow created by hand, so the
    bash's `delete_team_account.sh` reported success while the kubeconfig kept working. The
    Secret goes with it.
    """
    delete_team_account(configuration_only_context(fixture_repo.environment, fixture_repo.team), 
        ACCOUNT, assume_yes=True)

    deleted = [(method, kwargs) for method, kwargs in recorded_api.calls
               if method.startswith("delete_")]
    assert ("delete_namespaced_service_account",
            {"namespace": fixture_repo.app_namespace, "name": ACCOUNT}) in deleted
    assert ("delete_namespaced_secret",
            {"namespace": fixture_repo.app_namespace, "name": f"{ACCOUNT}-sa-secret"}) in deleted


def test_deleting_an_account_leaves_the_roles_and_destroys_the_credential(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """A Role belongs to a namespace, not to the person who was bound to it.

    Two accounts of one team share `namespace-admin`; a delete that swept the Roles with the
    account would take the other one's access with it. Only the bindings, the account and its
    token go. The credential is the part that must not survive, because it is already sitting
    in somebody's `~/.kube/config`.
    """
    delete_team_account(configuration_only_context(fixture_repo.environment, fixture_repo.team), 
        ACCOUNT, assume_yes=True)

    assert not [kwargs for method, kwargs in recorded_api.calls
                if method == "delete_namespaced_role"], "a delete swept Roles the team still needs"
    deleted_secret = [(method, kwargs) for method, kwargs in recorded_api.calls
                      if method == "delete_namespaced_secret"]
    assert deleted_secret, "the credential outlived the account"


def test_deleting_an_account_unbinds_every_binding_the_create_wrote(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """The symmetry that makes a delete a delete.

    One flow creates the bindings and derives their names from the Role they bind; the delete
    derives them the same way, from the same table. Anything else leaves a live binding to a
    service account that no longer exists -- which reads as a success, and is the opposite of
    one.
    """
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    create_team_account(context, ACCOUNT, assume_yes=True)
    created = bindings_created(recorded_api)
    assert created, "the create wrote no bindings at all"
    recorded_api.calls.clear()

    delete_team_account(context, ACCOUNT, assume_yes=True)

    still_bound = created - bindings_deleted(recorded_api)
    assert still_bound == set(), f"left behind by delete: {sorted(still_bound)}"


def test_a_binding_for_a_tunnel_into_a_component_that_is_not_here_is_not_deleted(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """Nothing is claimed to be deleted in a namespace that was never in play.

    The tunnels that were skipped at create time have no bindings to delete; a delete that
    guessed by namespace name would report a cleanup that never happened.
    """
    cluster_holding(recorded_api, [ns for ns in CLUSTER_NAMESPACES if ns != "polaris"])
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    create_team_account(context, ACCOUNT, assume_yes=True)
    recorded_api.calls.clear()

    delete_team_account(context, ACCOUNT, assume_yes=True)

    assert not any(kwargs.get("namespace") == "polaris"
                   for method, kwargs in recorded_api.calls
                   if method == "delete_namespaced_role_binding")


def test_the_read_only_variant_is_a_full_account_save_for_the_role_name(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """Both variants create namespaces, account, token, tunnels and bindings.

    Anything other than the Role's name is a difference this file refuses to accept, because
    two implementations that drift by more than the one word they were supposed to differ by
    is the exact shape of the bug that made this command red for two lanes.
    """
    create_team_account_read_only(configuration_only_context(fixture_repo.environment, fixture_repo.team), 
        ACCOUNT, assume_yes=True)
    read_only_writes = write_shapes(recorded_api)
    recorded_api.calls.clear()

    create_team_account(configuration_only_context(fixture_repo.environment, fixture_repo.team), 
        ACCOUNT, assume_yes=True)
    admin_writes = write_shapes(recorded_api)

    assert read_only_writes == admin_writes


def test_an_account_created_twice_is_the_same_account_and_not_two_of_them(
        cluster_ready_for_team_accounts, fixture_repo, recorded_api):
    """Idempotence, because the lane runs the create twice and a rerun must not fight itself.

    The flow goes through create-or-replace primitives; running it a second time has to be a
    no-op on the shape of what is there, not an error and not a second set of bindings.
    """
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    create_team_account(context, ACCOUNT, assume_yes=True)
    once = [repr(operation) for operation in recorded_api.operations()]
    recorded_api.calls.clear()

    create_team_account(context, ACCOUNT, assume_yes=True)

    assert [repr(operation) for operation in recorded_api.operations()] == once
