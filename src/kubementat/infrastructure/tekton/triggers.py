"""What one team's webhook triggers need from the cluster, and the RBAC that lets them run.

The fifth and last module of the Tekton split. Three groups live here: the seven RBAC objects a
team's EventListeners are granted through, the two webhook Secrets read from
`static.encrypted.json`, and the team's own generated manifests, read out of the directory the
generator writes them to. Nothing here installs a release, runs a pipeline, or asks the cluster
which API it serves — the first is `install`'s, the second `runs`' and `generation`'s, the third
`preflight`'s, and the only edge this module follows into the package is the one call to
`generation._refuse_obsolete_team_trigger_dir`, which is why `generation.py` imports nothing here.

**Why this module takes a `Context` and the other four did not.** `_apply_team_trigger_resources`
builds `KubernetesUtils(ctx)` itself on its way into `apply.apply_resources` — it has always done
that, from a `Context` and not from the client the constructor handed out, because the primitive
wants a client and the directory walk owns nothing else. So the entry point carries `ctx` as well
as the state the constructor used to read (`kubernetes_utils`, `config`, `environment`).
Handing over the raw `Context` is what keeps that construction the same object it was: a client
built from the caller's own `Context` addresses the caller's own cluster. A `kubernetes_utils`-
only signature would have had to build the second client out of something else — the one that was
already handed in, or a `ClusterTarget` reconstructed for the purpose — and that changes which
cluster the trigger manifests land in, which is exactly the kind of seam a move must not move.
The `team` that used to travel beside the `Context` is gone: `P4-6` deleted the constructor that
produced it, and a value that arrives from `ctx.require_team` cannot also be an argument, because
then a caller could hand in a team the `Context` says the run was not given.

Two bindings carry weight. `generation` and `apply` are reached module-qualified, never by name:
the suite patches by module object, and the `:1417` guard call is the one cross-module edge here,
so a frozen local binding is a second copy of the guard nobody runs. `client` is the Kubernetes
client library, used only to build the RBAC documents.
"""

from kubementat import errors
from kubementat.infrastructure.kubernetes import KubernetesUtils, apply
# `generation` is bound as a module and called as `generation.<name>` — see the note above. It is
# the package's sink, so this edge is the only one this module follows, and nothing in
# `generation.py` imports back.
from kubementat.infrastructure.tekton import generation
import base64
import logging
from kubernetes import client
from pathlib import Path

# The names the trigger controller and its webhook-setup job run as, and the RBAC they are
# granted through. They are product-visible strings: an EventListener's pod asks for
# `tekton-triggers-sa` by name, so renaming these is a change to the cluster's state and not to
# this file. They were spelled inline in the deleted script's heredocs, which is why two of them
# appeared twice each there (`tekton-triggers-sa` is both the subject and the name of the thing
# being bound) and why the ClusterRoleBinding's name had no owner at all.
TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME = "tekton-triggers-sa"
TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME = "tekton-triggers-createwebhook-sa"
TRIGGER_CONTROLLER_ROLE_NAME = "tekton-triggers-role"
TRIGGER_WEBHOOK_JOB_ROLE_NAME = "tekton-triggers-createwebhook-role"
TRIGGER_CONTROLLER_ROLE_BINDING_NAME = "tekton-triggers-sa-to-role-binding"
TRIGGER_WEBHOOK_JOB_ROLE_BINDING_NAME = "tekton-triggers-createwebhook-rolebinding"
BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME = \
    "tekton-triggers-bindings-and-interceptors-clusterrole"


def webhook_job_cluster_role_binding_name(team: str) -> str:
    """The ClusterRoleBinding that gives one team's trigger SA the cluster-wide reads.

    The name carries the team because that is what makes it one object per team: every team's
    `tekton-triggers-sa` is bound to the same ClusterRole, and a single cluster-scoped object
    owned by several teams is how a second team's setup would delete the first one's binding.
    """
    return ("tekton-triggers-sa-to-"
            f"{BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME}-binding-{team}")


# Which Secret is written from which `static.encrypted.json` key. Order is the deleted script's
# (gitlab first), and so is the pair of names: the EventListener templates refer to these Secrets
# by name, so they are cluster contract and not configuration. The `secretKey` those templates
# ask for is `secret`, while the key this writes is `secretToken`; that disagreement predates
# this code and is deliberately untouched, because changing the key a Secret stores its token
# under is a migration and not a port.
WEBHOOK_SECRETS = (("gitlab-trigger-webhook-secret", "GITLAB_WEBHOOK_SECRET"),
                   ("github-trigger-webhook-secret", "GITHUB_WEBHOOK_SECRET"))


def _trigger_labels(team: str, team_scoped: bool = True) -> dict:
    """The labels every trigger resource carries.

    `managed-by: kubementat` is kubementat's claim on the object; `team` is on the namespaced
    objects and not on the ClusterRole, exactly as the heredocs had it. That asymmetry is not
    decoration: it is what says which of these objects one team's setup owns and which one the
    whole cluster shares.

    This function is the rule's home, and five places restate the pair instead of importing it:
    `automations/uninstall/residue.py`, `infrastructure/kubernetes/constants.py`,
    `cli/commands/kubernetes.py`, `tests/test_uninstall_sweeps_everything_kubementat_owns.py` and
    `tests/test_a_secret_is_created_from_the_keys_of_a_json_file.py`. None of them are importers —
    the uninstaller reads the label off the object it is sweeping and the CLI writes its own
    annotation — so nothing here is wired to them and no import changed when this function moved.
    What moves with the rule is the sentence a later reader has to update, and it is this one.
    """
    if team_scoped:
        return {"managed-by": "kubementat", "team": team}
    return {"managed-by": "kubementat"}


def bindings_and_interceptors_cluster_role():
    """The cluster-wide read every EventListener needs, shared by all teams.

    EventListeners resolve `ClusterTriggerBinding`s and interceptors, which live outside any
    namespace, so without this the controller starts and then fails on the first webhook with a
    forbidden error naming a resource the operator cannot see from inside the EventListener.
    """
    return client.V1ClusterRole(
        api_version="rbac.authorization.k8s.io/v1",
        kind="ClusterRole",
        metadata=client.V1ObjectMeta(
            name=BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME,
            labels=_trigger_labels("", team_scoped=False),
        ),
        rules=[client.V1PolicyRule(
            api_groups=["triggers.tekton.dev"],
            resources=["clustertriggerbindings", "clusterinterceptors", "interceptors"],
            verbs=["get", "list", "watch"],
        )],
    )


def trigger_controller_role(team: str):
    """What an EventListener's own pod is allowed to do inside the pipeline namespace.

    Read its own trigger resources and configmaps, create the runs a TriggerTemplate asks for,
    impersonate the ServiceAccount it creates them as, and record events. The `podsecuritypolicies`
    rule is read only by clusters old enough to still serve that API; on a modern one it is inert
    and harmless, and dropping it would be a silent behaviour change for exactly those clusters.
    """
    return client.V1Role(
        api_version="rbac.authorization.k8s.io/v1",
        kind="Role",
        metadata=client.V1ObjectMeta(name=TRIGGER_CONTROLLER_ROLE_NAME,
                                     labels=_trigger_labels(team)),
        rules=[
            client.V1PolicyRule(api_groups=["triggers.tekton.dev"],
                                resources=["eventlisteners", "triggerbindings",
                                           "triggertemplates", "triggers"],
                                verbs=["get", "list", "watch"]),
            # configmaps: the trigger controller's own logging configuration.
            client.V1PolicyRule(api_groups=[""], resources=["configmaps"],
                                verbs=["get", "list", "watch"]),
            # What a TriggerTemplate is allowed to create in the namespace it runs in.
            client.V1PolicyRule(api_groups=["tekton.dev"],
                                resources=["pipelineruns", "pipelineresources", "taskruns"],
                                verbs=["create"]),
            client.V1PolicyRule(api_groups=[""], resources=["serviceaccounts"],
                                verbs=["impersonate"]),
            client.V1PolicyRule(api_groups=["policy"], resources=["podsecuritypolicies"],
                               resource_names=["tekton-triggers"], verbs=["use"]),
            client.V1PolicyRule(api_groups=[""], resources=["events"],
                                verbs=["create", "patch"]),
        ],
    )


def trigger_webhook_job_role(team: str):
    """What the create-webhook job writes: its Secret, its EventListener, its Ingress.

    The apiGroup the ingress rule names is `extensions`, which a modern API server no longer
    serves. It is kept because that is what the script sent, and because a rule that grants
    nothing is not the same kind of bug as a rule that grants the wrong thing: the job that
    needed it would have to be migrated separately, and this ticket is a port.
    """
    return client.V1Role(
        api_version="rbac.authorization.k8s.io/v1",
        kind="Role",
        metadata=client.V1ObjectMeta(name=TRIGGER_WEBHOOK_JOB_ROLE_NAME,
                                     labels=_trigger_labels(team)),
        rules=[
            client.V1PolicyRule(api_groups=[""], resources=["secrets"],
                                verbs=["get", "list", "create", "update", "delete"]),
            client.V1PolicyRule(api_groups=["triggers.tekton.dev"],
                                resources=["eventlisteners"],
                                verbs=["get", "list", "create", "update", "delete"]),
            client.V1PolicyRule(api_groups=["extensions"], resources=["ingresses"],
                                verbs=["create", "get", "list", "delete", "update"]),
        ],
    )


def setup_triggers(ctx, kubernetes_utils, config, environment) -> None:
    """Give one team's webhook triggers their RBAC, their webhook Secrets, and their resources.

    Three parts, in the order the deleted script ran them, because the order is what makes the
    last part able to run: an EventListener applied before `tekton-triggers-sa` exists is
    accepted by the API and then never starts a pod. Everything lands in one namespace, the
    team's `PIPELINE_NAMESPACE`, except the two cluster-scoped objects, which are shared by
    every team and named so that a second team's setup cannot overwrite the first one's.

    The three shapes this method exists to replace, all of them from the script:

    * `kubectl apply -f -` of a heredoc, nine times. The client writes those objects instead,
      so "it was already there" is a replacement and not a line of `kubectl` output — which
      is what makes a second, fifth and twentieth run of this command do the same thing.
    * `jq -r` of two keys from `static.encrypted.json`. A key that is not configured skips its
      Secret. bash sent the string `null` instead, base64-encoded, and created a Secret whose
      token was four characters long; that is the one place this is deliberately not the same.
    * `kubectl apply -n <ns> -f <dir>` of the team's generated trigger files, where the
      directory is `Config.tekton_team_trigger_dir` —
      `platform_config/{env}/{team}/tekton/triggers/` — and no longer
      `tekton_ci/triggers/{TEAM}/`. That is decision 2 of the migration: the
      generated files are team configuration, and configuration lives in `platform_config`.
      The generator *templates* they are generated from are product data and resolve through
      `Config.tekton_trigger_template_dir`.

    Raises:
        kubementat.errors.TeamNotSuppliedError: If this `Context` was given no team.
        kubementat.errors.PipelineNamespaceNotConfiguredError: If the team's static config has no namespace.
        kubementat.errors.ResourceWithoutNameError: If a trigger manifest has no name.
        kubementat.errors.CommandExecutionError: Never, by this method itself — it calls no
            `kubectl`, which is what the RBAC primitives made unnecessary.
        kubernetes.client.exceptions.ApiException: If the cluster refuses an object.
    """
    # The team, asked of the `Context` and not handed in. `P4-5` moved this flow with a `team`
    # parameter beside the `Context` because the refusal that produced the value lived in
    # `TektonUtils.__init__` and the function had no business duplicating it; `P4-6` deleted the
    # class, so the sentence and the read belong here, as this function's first statement. The
    # `team_static` read below is the line that earns the guard: without a team there is no
    # `PIPELINE_NAMESPACE`, no ServiceAccount name to bind, and no directory to apply.
    team = ctx.require_team("the namespace the Tekton objects live in")

    namespace = config.get('team_static').get('PIPELINE_NAMESPACE')
    if not (isinstance(namespace, str) and namespace.strip()):
        raise errors.PipelineNamespaceNotConfiguredError(
            f"PIPELINE_NAMESPACE is not set in "
            f"{config.team_static_config_path}, so kubementat has no namespace to put "
            f"the trigger ServiceAccounts, Roles and bindings in. Set it to the namespace the "
            f"team's pipelines run in and run `kmt tekton setup-triggers` again."
        )
    namespace = namespace.strip()

    logging.info("#########################")
    logging.info("Setting up tekton triggers from platform_config ...")
    logging.info(f"ENVIRONMENT: {environment}")
    logging.info(f"TEAM: {team}")
    logging.info(f"PIPELINE_NAMESPACE: {namespace}")
    logging.info(f"TRIGGER_DIRECTORY: {config.tekton_team_trigger_dir}")
    logging.info("#########################")

    _setup_trigger_rbac(kubernetes_utils, namespace, team)
    _setup_webhook_secrets(kubernetes_utils, config, namespace, team)
    _apply_team_trigger_resources(ctx, config, namespace, team)
    logging.info("Finished setting up tekton triggers.")

def _setup_trigger_rbac(kubernetes_utils, namespace: str, team) -> None:
    """The seven objects, in the script's order: role, then the accounts bound to it.

    Nothing here branches on the cluster's state. Every one of these primitives is
    create-or-replace, so a namespace that already has them gets the same objects written
    over them, which is what an operator expects from a command that says `setup`.
    """
    logging.info("Setting up roles for tekton triggers ...")

    logging.info(f"Configuring {BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME} ...")
    kubernetes_utils.create_or_replace_cluster_role(
        BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME,
        bindings_and_interceptors_cluster_role(),
    )

    logging.info(f"Setting up {TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME} in namespace "
                 f"{namespace} ...")
    kubernetes_utils.create_or_replace_service_account(
        namespace, TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME,
        labels=_trigger_labels(team))

    logging.info(f"Setting up {TRIGGER_CONTROLLER_ROLE_NAME} in namespace {namespace} ...")
    kubernetes_utils.create_or_replace_role(
        namespace, TRIGGER_CONTROLLER_ROLE_NAME, trigger_controller_role(team))

    logging.info(f"Binding {TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME} to "
                 f"{TRIGGER_CONTROLLER_ROLE_NAME} in namespace {namespace} ...")
    kubernetes_utils.create_or_replace_role_binding(
        namespace, TRIGGER_CONTROLLER_ROLE_BINDING_NAME,
        client.V1RoleBinding(
            api_version="rbac.authorization.k8s.io/v1",
            kind="RoleBinding",
            metadata=client.V1ObjectMeta(name=TRIGGER_CONTROLLER_ROLE_BINDING_NAME,
                                         labels=_trigger_labels(team)),
            subjects=[client.RbacV1Subject(
                kind="ServiceAccount", name=TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME)],
            role_ref=client.V1RoleRef(kind="Role", name=TRIGGER_CONTROLLER_ROLE_NAME,
                                      api_group="rbac.authorization.k8s.io"),
        ))

    cluster_binding_name = webhook_job_cluster_role_binding_name(team)
    logging.info(f"Binding {TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME} to "
                 f"{BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME} for team {team} ...")
    kubernetes_utils.create_or_replace_cluster_role_binding(
        cluster_binding_name,
        client.V1ClusterRoleBinding(
            api_version="rbac.authorization.k8s.io/v1",
            kind="ClusterRoleBinding",
            metadata=client.V1ObjectMeta(name=cluster_binding_name,
                                         labels=_trigger_labels(team)),
            subjects=[client.RbacV1Subject(
                kind="ServiceAccount", name=TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME,
                namespace=namespace)],
            role_ref=client.V1RoleRef(kind="ClusterRole",
                                      name=BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME,
                                      api_group="rbac.authorization.k8s.io"),
        ))

    logging.info(f"Setting up {TRIGGER_WEBHOOK_JOB_ROLE_NAME} in namespace {namespace} ...")
    kubernetes_utils.create_or_replace_role(
        namespace, TRIGGER_WEBHOOK_JOB_ROLE_NAME, trigger_webhook_job_role(team))

    logging.info(f"Setting up {TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME} in namespace "
                 f"{namespace} ...")
    kubernetes_utils.create_or_replace_service_account(
        namespace, TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME,
        labels=_trigger_labels(team))

    logging.info(f"Binding {TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME} to "
                 f"{TRIGGER_WEBHOOK_JOB_ROLE_NAME} in namespace {namespace} ...")
    kubernetes_utils.create_or_replace_role_binding(
        namespace, TRIGGER_WEBHOOK_JOB_ROLE_BINDING_NAME,
        client.V1RoleBinding(
            api_version="rbac.authorization.k8s.io/v1",
            kind="RoleBinding",
            metadata=client.V1ObjectMeta(name=TRIGGER_WEBHOOK_JOB_ROLE_BINDING_NAME,
                                         labels=_trigger_labels(team)),
            subjects=[client.RbacV1Subject(
                kind="ServiceAccount", name=TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME)],
            role_ref=client.V1RoleRef(kind="Role", name=TRIGGER_WEBHOOK_JOB_ROLE_NAME,
                                      api_group="rbac.authorization.k8s.io"),
        ))

def _setup_webhook_secrets(kubernetes_utils, config, namespace: str, team) -> None:
    """The two webhook Secrets, and only for the providers that have a token configured.

    Read from the team's `static.encrypted.json`, which is where a generated token lands
    (`config set --generate`) and where an operator puts a real one. An absent, empty or `null` key
    means that
    provider is not in use: the Secret is not created, the run continues, and the log says so
    — a Secret holding the tokenless result of a missing key is how bash failed this, and an
    EventListener that never validates a webhook fails quietly too.
    """
    encrypted = config.get('team_static_encrypted') or {}
    for secret_name, config_key in WEBHOOK_SECRETS:
        token = encrypted.get(config_key)
        if not (isinstance(token, str) and token.strip()):
            logging.info(f"No {config_key} configured for team {team}, not creating "
                         f"{secret_name} in namespace {namespace}")
            continue
        logging.info(f"Configuring {secret_name} from {config_key} in namespace "
                     f"{namespace} ...")
        kubernetes_utils.create_or_replace_secret(
            namespace=namespace,
            secret_name=secret_name,
            key_type="Opaque",
            # The token is the Secret's only data item, and it goes in `data` as base64 —
            # the same wire format `kubectl apply` of the heredoc produced, which is what
            # lets a Secret this wrote and a Secret that script wrote be the same object.
            raw_data={"secretToken": base64.b64encode(token.encode()).decode()},
            labels=_trigger_labels(team),
        )

def _apply_team_trigger_resources(ctx, config, namespace: str, team) -> None:
    """The team's own trigger manifests, from the directory the generator writes them to.

    A directory that is not there is a team that has not generated any triggers yet, and that
    is a state a freshly prepared tree is in, not an error: the RBAC above is still correct and
    still all of it is needed. A directory that *is* there goes through the same
    `apply_resources` primitive every other directory does, so `*.yaml`, subdirectories and
    the `templates/` exclusion all apply here too — including the rule that a manifest with no
    name is refused rather than applied to nothing.

    The *old* directory is none of those cases: `<team>/triggers/` standing next to the path
    this reads is an upgraded tree whose generated manifests would be silently applied
    nowhere, so it is refused by name before the new directory is even looked at.
    """
    generation._refuse_obsolete_team_trigger_dir(config)
    trigger_dir = Path(config.tekton_team_trigger_dir)
    if not trigger_dir.is_dir():
        logging.info(f"No trigger directory at {trigger_dir}, so no triggers are configured "
                     f"for team {team}. Generate some with "
                     f"`kmt tekton-generate-triggers`, or apply manifests here by hand.")
        return

    # The primitive walks the directory and answers for every document in it; the object it
    # hands the cluster is built here, which is why no `Automation` is needed to reach it.
    logging.info(f"Setting up configured triggers for team {team} ...")
    apply.apply_resources(KubernetesUtils(ctx), trigger_dir, namespace, kind=None,
                              group=config.TRIGGERS_API_GROUP,
                              version=config.TRIGGERS_API_VERSION)
