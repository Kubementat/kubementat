"""What one team's trigger setup asks the cluster for, and what it refuses to ask.

`triggers.setup_triggers()` writes one team's trigger RBAC, their webhook Secrets, and the
generated manifests, in that order. The order is the contract: an EventListener applied before the
service account it runs as exists is accepted and then never runs, and the shared ClusterRole has to
precede the binding that points at it.

What a team can actually get wrong is the half this file is for — the object that is already there,
the namespace that is not configured, the token that was never set, the directory that holds nothing —
because each of those is a branch the setup has to answer rather than an exception on the way out.

Four things here are not inherited from bash and are stated where they are:

* the objects arrive through the client, so "already exists" is a branch and not an error;
* a namespace that is not configured stops the run before the first write, where `jq -r` answered
  the string `null` and eight operations went to a namespace that does not exist;
* a webhook token that is not configured skips its Secret, where `jq -r` answered `null` and the
  Secret was created holding the four characters `bnVsbAo=`;
* the generated manifests are read from `platform_config/{env}/{team}/tekton/triggers/`, not from
  the checkout: they live under `tekton/` in the configuration tree and not beside the code, so a
  checkout is not a configuration directory any more — and a tree that still has the pre-move
  directory is refused by name rather than silently applied nowhere.
"""

import base64

import pytest
from cluster_fixture import configuration_only_context

from kubementat.errors import (
    PipelineNamespaceNotConfiguredError,
    ResourceWithoutNameError,
)
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import triggers

# The team-level trigger directory: platform_config, under `tekton/`,
# not the checkout. The old path is spelled once too, because refusing it is half the subject.
TRIGGER_SUBDIRECTORY = ("platform_config", "dev", "fixture", "tekton", "triggers")
OBSOLETE_TRIGGER_SUBDIRECTORY = ("platform_config", "dev", "fixture", "triggers")

EVENT_LISTENER = """\
apiVersion: triggers.tekton.dev/v1beta1
kind: EventListener
metadata:
  name: hello-gitlab-fixture-event-listener
spec:
  triggers:
    - name: push-trigger
      template:
        ref: hello-gitlab-fixture-trigger-template
"""

INGRESS = """\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: hello-gitlab-fixture-event-listener-ingress
spec:
  ingressClassName: nginx
"""


def trigger_dir(fixture_repo):
    """The directory `Config.tekton_team_trigger_dir` resolves to for the fixture."""
    path = fixture_repo.root
    for part in TRIGGER_SUBDIRECTORY:
        path = path / part
    return path


def obsolete_trigger_dir(fixture_repo):
    """The old trigger directory: what an upgraded tree can still have standing."""
    path = fixture_repo.root
    for part in OBSOLETE_TRIGGER_SUBDIRECTORY:
        path = path / part
    return path


def write_triggers(fixture_repo, manifests):
    """Generated trigger manifests, filed the way the generator files them: one per app."""
    for name, text in manifests.items():
        path = trigger_dir(fixture_repo) / "hello" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return trigger_dir(fixture_repo)


def run_the_setup(fixture_repo, recorded_api, **team_static_overrides):
    """`setup_triggers()`, the way `kmt install` runs it: real config, fake cluster.

    The config is the fixture's `platform_config`, read through `Config` the same way the CLI's
    `kmt tekton setup-triggers` reads it, and the cluster is the recording client. The `Context` is
    handed to `triggers.setup_triggers` twice over -- once as itself, once inside the client built
    from it -- the same way the two call sites in `src/` hand it over, so a test that reaches a
    namespace the fixture did not write is testing the code and not a mock. The team is not a
    parameter any more: `setup_triggers` asks the `Context` for it, which is the refusal it carried
    on the class until P4-6 deleted it.
    """
    fixture_repo.team_static(**team_static_overrides)
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    triggers.setup_triggers(context, KubernetesUtils(context), context.config, context.environment)
    return recorded_api.operations()


def test_the_setup_writes_the_rbac_a_webhook_needs_and_nothing_else(fixture_repo, dry_run_env,
                                                                    recorded_api):
    """The seven objects, in the order they must arrive.

    The order is not decoration: an EventListener applied before `tekton-triggers-sa` exists is
    accepted and then never runs, and the shared ClusterRole has to be there before the binding
    that points at it. It is spelled as tuples rather than counted so a failure says which object
    moved.
    """
    fixture_repo.team_static()

    run_the_setup(fixture_repo, recorded_api)

    operations = recorded_api.operations()
    cluster_scope = [(op.kind, op.name) for op in operations
                     if op.namespace is None and op.kind in ("clusterrole", "clusterrolebinding")]
    assert cluster_scope == [
        ("clusterrole", triggers.BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME),
        ("clusterrolebinding", triggers.webhook_job_cluster_role_binding_name(fixture_repo.team)),
    ], cluster_scope
    assert [(op.verb, op.kind, op.namespace, op.name) for op in operations if op.namespace][:6] == [
        ("ensure", "serviceaccount", fixture_repo.pipeline_namespace,
         triggers.TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME),
        ("ensure", "role", fixture_repo.pipeline_namespace, triggers.TRIGGER_CONTROLLER_ROLE_NAME),
        ("ensure", "rolebinding", fixture_repo.pipeline_namespace,
         triggers.TRIGGER_CONTROLLER_ROLE_BINDING_NAME),
        ("ensure", "role", fixture_repo.pipeline_namespace, triggers.TRIGGER_WEBHOOK_JOB_ROLE_NAME),
        ("ensure", "serviceaccount", fixture_repo.pipeline_namespace,
         triggers.TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME),
        ("ensure", "rolebinding", fixture_repo.pipeline_namespace,
         triggers.TRIGGER_WEBHOOK_JOB_ROLE_BINDING_NAME),
    ], recorded_api.as_str()
    # Everything else this flow writes is a Secret or a trigger manifest, asserted elsewhere.
    assert {op.kind for op in operations} == {"clusterrole", "clusterrolebinding", "role",
                                              "rolebinding", "serviceaccount"}


def test_a_team_without_a_pipeline_namespace_is_refused_before_the_first_write(
        fixture_repo, dry_run_env, recorded_api):
    """`jq -r` answered `null`; the answer here names the file and the key, and writes nothing.

    The failure is not interesting, the *silence* is: eight objects aimed at a namespace called
    `null` is a run that reports nine errors without ever naming the one line that is wrong.
    """
    with pytest.raises(PipelineNamespaceNotConfiguredError):
        run_the_setup(fixture_repo, recorded_api, PIPELINE_NAMESPACE=None)

    assert recorded_api.calls == [], "something was written to a namespace kubementat never read"


def test_the_shared_role_is_labelled_once_and_the_team_objects_twice(
        fixture_repo, dry_run_env, recorded_api):
    """Which of these objects one team's setup owns, read off the objects themselves.

    The ClusterRole is shared and says only `managed-by: kubementat`; everything namespaced in the
    team's namespace also says which team. A ClusterRole carrying a `team` label would be a
    different object for every team, which is exactly what the ClusterRoleBinding's name is for.
    """
    run_the_setup(fixture_repo, recorded_api)
    # Only RBAC rules survive the harness's reduction to facts, so the labels that decide ownership
    # are read off the objects that were actually sent, by name.
    sent = {}
    for _method, kwargs in recorded_api.calls:
        body = kwargs.get("body")
        document = body if isinstance(body, dict) else (body.to_dict() if body else None)
        if document and document.get("metadata"):
            sent[document["metadata"]["name"]] = document["metadata"].get("labels") or {}

    assert sorted(sent[triggers.BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME]) == ["managed-by"], \
        "the one ClusterRole is shared, so it names no team"
    for name in (triggers.TRIGGER_CONTROLLER_ROLE_NAME,
                 triggers.TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME,
                 triggers.TRIGGER_WEBHOOK_JOB_ROLE_NAME,
                 triggers.TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME,
                 triggers.TRIGGER_CONTROLLER_ROLE_BINDING_NAME,
                 triggers.TRIGGER_WEBHOOK_JOB_ROLE_BINDING_NAME,
                 triggers.webhook_job_cluster_role_binding_name(fixture_repo.team)):
        assert sorted(sent[name]) == ["managed-by", "team"], (name, sent[name])


def test_the_secret_that_replaced_a_heredoc_holds_the_same_bytes(
        fixture_repo, dry_run_env, recorded_api):
    """The wire format is what the heredoc's `echo -n | base64` produced, and nothing else.

    `data` and `string_data` are not the same field to the API server, and the EventListener's
    `secretRef` reads one key, so this is the half of the Secret that a migration can get wrong
    without anything noticing until a webhook is rejected.
    """
    fixture_repo.team_static()
    fixture_repo.team_static_encrypted(GITLAB_WEBHOOK_SECRET="s3cret-token",
                                       GITHUB_WEBHOOK_SECRET="another-token")

    run_the_setup(fixture_repo, recorded_api)

    written = {op.name: op for op in recorded_api.operations() if op.kind == "secret"}
    body = [kwargs for method, kwargs in recorded_api.calls
            if method == "create_namespaced_secret"]
    encoded = [body_entry["body"] for body_entry in body]
    for name, token in (("gitlab-trigger-webhook-secret", "s3cret-token"),
                        ("github-trigger-webhook-secret", "another-token")):
        assert name in written, recorded_api.as_str()
        payload = next(entry for entry in encoded
                       if entry.metadata.name == name)
        assert payload.type == "Opaque"
        assert payload.data == {"secretToken": base64.b64encode(token.encode()).decode()}
        assert payload.string_data is None, "the field changed, which changes how the API reads it"


def test_the_old_trigger_directory_is_refused_by_name_and_nothing_is_applied(
        fixture_repo, dry_run_env, recorded_api):
    """A tree that still has the old `triggers/` directory: refused, not silently skipped.

    The new directory is read and the old one is read by nothing, so a tree holding both would
    apply the new half and drop what the old one holds without a word. The reader refuses at the
    point of reading, names both paths, and — measured here — not one manifest from either
    directory reaches the cluster. The RBAC above the read still ran: it is create-or-replace of
    the same objects, and the refusal is about the manifests, not about the accounts.
    """
    from kubementat.errors import ObsoleteTriggerDirectoryError

    obsolete = obsolete_trigger_dir(fixture_repo)
    (obsolete / "hello").mkdir(parents=True)
    (obsolete / "hello" / "hello-gitlab-event-listener.yml").write_text(EVENT_LISTENER)
    fixture_repo.team_static()

    with pytest.raises(ObsoleteTriggerDirectoryError) as error:
        run_the_setup(fixture_repo, recorded_api)

    message = str(error.value)
    assert str(obsolete) in message, message
    assert str(trigger_dir(fixture_repo)) in message, message
    applied = recorded_api.operations()
    assert not [op for op in applied if op.kind in ("eventlistener", "ingress")], \
        recorded_api.as_str()


def test_the_generated_manifests_are_read_from_platform_config_and_not_the_checkout(
        fixture_repo, dry_run_env, recorded_api):
    """Decision 2, measured. The manifests under `platform_config/.../tekton/triggers/` are the ones.

    The same two documents filed under `tekton_ci/triggers/{TEAM}/` are ignored — that path is not
    configuration and is not read by anything now, which is the point of moving the location and
    the point of this test.
    """
    write_triggers(fixture_repo, {"hello-gitlab-event-listener.yml": EVENT_LISTENER,
                                  "hello-gitlab-event-listener-ingress.yml": INGRESS})
    (fixture_repo.root / "tekton_ci" / "triggers" / fixture_repo.team / "stale").mkdir(parents=True)
    (fixture_repo.root / "tekton_ci" / "triggers" / fixture_repo.team / "stale" /
     "old-event-listener.yml").write_text(EVENT_LISTENER.replace("hello-gitlab", "stale"))
    fixture_repo.team_static()

    operations = run_the_setup(fixture_repo, recorded_api)

    applied = {(op.kind, op.name) for op in operations if op.kind in
               ("eventlistener", "ingress")}
    assert applied == {("eventlistener", "hello-gitlab-fixture-event-listener"),
                       ("ingress", "hello-gitlab-fixture-event-listener-ingress")}, \
        recorded_api.as_str()
    assert "stale" not in str(operations)


def test_a_manifest_that_cannot_be_named_stops_the_setup_and_says_which_one(
        fixture_repo, dry_run_env, recorded_api, tmp_path):
    """`apply_resources` refuses a document with no name; this refuses a document with no kind.

    Both are the same class of failure — a manifest somebody wrote that cannot become an object —
    and in both cases the answer is the file and the document, not a Python internal.
    """
    write_triggers(fixture_repo, {"broken.yml": "apiVersion: v1\nmetadata:\n  name: no-kind\n"})
    fixture_repo.team_static()

    with pytest.raises(ResourceWithoutNameError):
        run_the_setup(fixture_repo, recorded_api)


def test_the_command_the_script_had_is_now_the_python_path(fixture_repo, dry_run_env, fake_binaries,
                                                           recorded_api):
    """`kmt tekton setup-triggers` no longer executes anything.

    The command's only promise after the migration is that the same flow runs: RBAC through the
    client, and no bash. `fake_binaries` is the evidence — nothing is recorded under `PATH`, so
    no script, no `kubectl` and no `jq` took part in this run.
    """
    from click.testing import CliRunner

    from cli.commands import tekton

    fixture_repo.team_static()

    result = CliRunner().invoke(tekton.setup_triggers,
                                [fixture_repo.environment, fixture_repo.team])

    assert result.exit_code == 0, result.output
    assert "create_cluster_role" in recorded_api.methods(), recorded_api.as_str()
    assert fake_binaries.calls() == [], "the command still reaches for a binary"
