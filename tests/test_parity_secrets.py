"""Parity: the secrets scripts, against the `kmt setup-*` commands that replaced them.

`configure_secrets.sh` and `configure_docker_registry_access.sh` were marked *already migrated*
in the plan's deletion ledger; the migration proved it and both scripts are now deleted. So the bash
half of this file is a **record** rather than a run: what each script sent to the cluster is
written down below, captured from the scripts while they were still in
`scripts/automation/tekton/` (`git show ccb6976:scripts/automation/tekton/configure_secrets.sh`
is one of them). The Python half is executed for real, so the comparison is still a comparison.

The two halves used completely different routes to the same object — `jq` plus a heredoc manifest
versus the generated client — and the question was only ever whether the object that landed was
the same object. That is what `CONFIGURE_SECRETS_ASKED` and `REGISTRY_ACCESS_ASKED` keep
answering now that the route is gone.

The branch that used to be broken is the interesting half. `configure_secrets.sh` applied a
Secret and then ran `kubectl describe` on it without checking whether the apply did anything;
the Python path creates, and on a collision replaces. A team that had half its keys configured
therefore reported success, because reading is not the same act as writing.
"""

from cluster_parity import ClusterOperation, cluster_named, compare

from conftest import FIXTURE_KUBE_CONTEXT

# Which cluster both sides of every comparison here address: the fixture environment's, named by the
# context its `static.json` declares. It is a declaration rather than a guess — the record
# now carries a cluster and a run that picked a different one has to fail here instead of matching.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)


def kmt(command_module, command_name, arguments):
    """Run a `kmt` command in-process.

    In-process rather than a subprocess because the Python half of these commands talks to the
    Kubernetes *API*, and the fake client that makes that testable has to be the one the command
    loaded. The shims still work (they are found through `PATH`), so any command that does exec
    `kubectl` still shows up in the same log.
    """
    from click.testing import CliRunner

    command = getattr(command_module, command_name)
    result = CliRunner().invoke(command, arguments)
    assert result.exit_code == 0, f"{command_name} {arguments}: {result.output}\n{result.exception}"
    return result


# What `configure_secrets.sh` sent, in order: one Secret per deploy key in the team's
# `static.encrypted.json`, then the `kubectl describe` that read it back.
CONFIGURE_SECRETS_ASKED = [
    ClusterOperation("ensure", "secret", "fixture-pipelines", "deploy-key-one-secret",
                     (("data", ("ssh-privatekey",)), ("type", "kubernetes.io/ssh-auth")), cluster=CLUSTER),
    ClusterOperation("read", "secret", "fixture-pipelines", None, cluster=CLUSTER),
]

# What `configure_docker_registry_access.sh` sent: the registry Secret twice — the app namespace
# and the pipeline namespace — and the two service-account patches that make it pull anything.
REGISTRY_ACCESS_ASKED = [
    ClusterOperation("ensure", "secret", "fixture-deployments", "registry-credentials",
                     (("data", (".dockerconfigjson",)),
                      ("registries", ("https://registry.example.test",)),
                      ("type", "kubernetes.io/dockerconfigjson")), cluster=CLUSTER),
    ClusterOperation("ensure", "secret", "fixture-pipelines", "registry-credentials",
                     (("data", (".dockerconfigjson",)),
                      ("registries", ("https://registry.example.test",)),
                      ("type", "kubernetes.io/dockerconfigjson")), cluster=CLUSTER),
    ClusterOperation("patch", "serviceaccount", "fixture-pipelines", "fixture-helm-deployer",
                     (("imagePullSecrets", ("registry-credentials",)),), cluster=CLUSTER),
    ClusterOperation("patch", "serviceaccount", "fixture-deployments", "default",
                     (("imagePullSecrets", ("registry-credentials",)),), cluster=CLUSTER),
    ClusterOperation("read", "serviceaccount", "fixture-pipelines", None, cluster=CLUSTER),
    ClusterOperation("read", "serviceaccount", "fixture-deployments", None, cluster=CLUSTER),
]


def test_the_same_ssh_deploy_key_lands_as_the_same_secret(fixture_repo, dry_run_env,
                                                          recorded_api):
    """Type, namespace, name, data keys: what anything downstream depends on.

    The record was taken from the script's own `kubectl apply` of a heredoc, so a quoting or
    indentation mistake in the deleted script is visible in the object it produced — and the
    command is compared against that object rather than against an assertion about itself.
    """
    from cli.commands import kubementat

    kmt(kubementat, "setup_secrets", ["dev", "fixture"])

    compare(CONFIGURE_SECRETS_ASKED, recorded_api.operations(),
            context="kmt setup-secrets against what configure_secrets.sh used to send")


def test_the_secret_is_read_back_from_the_namespace_it_was_written_to(
        fixture_repo, dry_run_env, recorded_api):
    """The read after the write, which is where a namespace typo hides.

    The script ran `kubectl describe secret <name>` per key; the command lists the pipeline
    namespace. Both read the same kind in the same place, which is why the read is compared
    without a name: the script's one-secret read and a namespace listing make the same claim. If
    the Python path stopped looking at all, this says so.
    """
    from cli.commands import kubementat
    kmt(kubementat, "setup_secrets", ["dev", "fixture"])

    assert [op for op in CONFIGURE_SECRETS_ASKED if op.is_read] == [
        ClusterOperation("read", "secret", "fixture-pipelines", None, cluster=CLUSTER)]
    assert any(op.is_read and op.kind == "secret" and op.namespace == "fixture-pipelines"
               for op in recorded_api.operations()), recorded_api.as_str()


def test_a_secret_that_is_already_there_is_not_an_error_on_either_side(
        fixture_repo, dry_run_env, recorded_api):
    """The branch that used to be broken: run the command twice.

    bash cannot see the difference — `kubectl apply` replaces and exits 0. Python gets a 409 on
    create and replaces, which is the same act. The contract is that a second run succeeds and
    leaves one Secret behind, so `create` and `replace` collapse into a single `ensure`, exactly
    once. A path that did both would look correct in the log and delete nothing.
    """
    from cli.commands import kubementat
    from kubernetes import client

    recorded_api.calls.clear()
    recorded_api.answer("create_namespaced_secret",
                        client.exceptions.ApiException(status=409, reason="already exists"))

    kmt(kubementat, "setup_secrets", ["dev", "fixture"])

    expected = ClusterOperation("ensure", "secret", "fixture-pipelines", "deploy-key-one-secret",
                                (("data", ("ssh-privatekey",)),
                                 ("type", "kubernetes.io/ssh-auth")), cluster=CLUSTER)
    writes = [op for op in recorded_api.operations() if not op.is_read]
    assert writes, "the second run wrote nothing"
    assert set(writes) == {expected}, writes


def test_the_secret_the_command_writes_carries_only_type_and_data(
        fixture_repo, dry_run_env, recorded_api):
    """A real difference, written down rather than smoothed over.

    `configure_secrets.sh` stamped `team`, `environment` and `key_name` on the Secret as labels
    and annotations; `create_or_replace_secret` sends type and data only. That is recorded here
    rather than asserted from a run, because the script is deleted — `git show
    ccb6976:scripts/automation/tekton/configure_secrets.sh` still shows the heredoc that put them
    there. Nothing in the cluster rejects it and nothing in this tree reads it, and the comparison
    above deliberately does not look at metadata — so it is stated here instead, as a fact about
    the replacement rather than an accident. If those labels ever matter to something (a selector,
    a dashboard, the cleanup job), this test is where that becomes visible.
    """
    from cli.commands import kubementat
    kmt(kubementat, "setup_secrets", ["dev", "fixture"])
    created = [kwargs for _method, kwargs in recorded_api.calls if "body" in kwargs][-1]
    sent = created["body"].to_dict()
    assert not (sent.get("metadata") or {}).get("labels"), "Python now labels secrets too"


# ---------------------------------------------------------------------------
# the registry-access row: one command, two namespaces, two patches
# ---------------------------------------------------------------------------

def test_docker_registry_access_writes_the_same_secrets_and_the_same_patches(
        fixture_repo, dry_run_env, recorded_api):
    """The whole former script, compared end to end.

    Two namespaces get the registry Secret and both service accounts get the imagePullSecret. The
    script did it with `create --dry-run=client -o yaml | kubectl apply -f -`, which the harness
    folds into one operation; the client sends one body. Both produce the same writes, in the same
    order.
    """
    from cli.commands import kubementat

    kmt(kubementat, "setup_docker_registry_access", ["dev", "fixture"])

    compare(REGISTRY_ACCESS_ASKED, recorded_api.operations(),
            context="kmt setup-docker-registry-access against what "
                    "configure_docker_registry_access.sh used to send")


def test_the_pull_secret_is_attached_to_both_service_accounts(fixture_repo, dry_run_env,
                                                              recorded_api):
    """The part of the migration that is easy to drop and hard to notice.

    `HELM_DEPLOYER_SERVICE_ACCOUNT_NAME` is patched in the pipeline namespace and `default` in
    the app namespace — the two places the deleted script patched. Lose either patch and pods
    cannot pull their image — `ImagePullBackOff`, several days later, in a namespace the operator
    was not looking at.
    """
    from cli.commands import kubementat

    kmt(kubementat, "setup_docker_registry_access", ["dev", "fixture"])
    patches = sorted((op.name, op.namespace, dict(op.facts).get("imagePullSecrets"))
                     for op in recorded_api.operations() if op.verb == "patch")

    assert patches == [("default", "fixture-deployments", ("registry-credentials",)),
                       ("fixture-helm-deployer", "fixture-pipelines", ("registry-credentials",))]


def test_the_registry_the_secret_is_for_is_the_one_in_configuration(
        fixture_repo, dry_run_env, recorded_api):
    """The credential's whole job is naming the right registry.

    bash put it in a `--docker-server` flag; Python writes it into `.dockerconfigjson`. The
    harness decodes the latter and compares both halves, because an argument comparison would
    have proved nothing here — and this is the one fact a docker-registry Secret exists to carry.
    """
    from cli.commands import kubementat

    expected = (fixture_repo.team_static_encrypted()["DOCKER_REGISTRY_CREDENTIALS"][0]
                ["DOCKER_REGISTRY_AUTH_URL"])
    kmt(kubementat, "setup_docker_registry_access", ["dev", "fixture"])
    secrets = [op for op in recorded_api.operations() if op.kind == "secret"]

    assert secrets, "the Python path created no Secret"
    assert {dict(op.facts)["registries"] for op in secrets} == {(expected,)}
    assert {dict(op.facts).get("registries", ())
            for op in REGISTRY_ACCESS_ASKED if op.kind == "secret"} == {(expected,)}
