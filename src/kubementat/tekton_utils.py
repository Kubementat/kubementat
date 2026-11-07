from kubementat import errors
from kubementat.infrastructure import shell
from kubementat.infrastructure.kubernetes import KubernetesUtils, apply
# The generation group moved out (P4-1) and the preflight group with it (P4-2), so what this file
# still calls reaches them through their modules. Never `from … import` a function: the suite patches
# by module object and a name imported here is a frozen second binding nobody runs. `preflight` is
# not on this line any more — the two places that asked it, `_list_tekton_resource` and
# `run_pipeline`, are `runs.py`'s business since P4-3, and an import kept "for symmetry" is the
# second binding this rule is written against.
from kubementat.infrastructure.tekton import generation
import base64
import click
import logging
from kubernetes import client
from pathlib import Path
import time


# Where upstream serves the release manifests kubementat installs, spelled once because the
# base URL and the pins move together: the bucket this URL used to point at serves only what it
# happened to keep (`v1.4.0` answers, anything from 1.15 on 404s), so a pin bumped without this
# string moving is an install failure on the very first `kubectl apply`.
TEKTON_RELEASE_BASE_URL = "https://infra.tekton.dev/tekton-releases"

# The ClusterRole every helm deploy is authorised through. kubementat creates it, so kubementat
# also has to be able to name it on the way out (`Automation.uninstall_kubementat`): it is
# cluster-scoped, which means no namespace delete takes it with it.
HELM_DEPLOYER_CLUSTER_ROLE_NAME = "helm-deployer-cluster-role"

# The Service the Pipelines webhook answers on. It is written here rather than derived because it
# is upstream's own name, fixed by the release manifests kubementat applies and spelled in the error
# the API server prints when it cannot be reached. The wait that reads it is a control-plane
# question, not a namespace question: it has no business inventing a name.
PIPELINE_WEBHOOK_SERVICE_NAME = "tekton-pipelines-webhook"

# What an install applies, as data: the upstream release directory, the `static.json` key its
# version comes from, and the files it serves. Order is part of the product here, not cosmetics
# — Pipelines goes first, because the dashboard and the trigger controller both read from its
# API — and `interceptors.yaml` is not optional: the trigger controller reads
# `config-triggers-core-interceptors` from it and fails to start without that ConfigMap.
RELEASE_MANIFESTS = (
    ("pipeline", "TEKTON_VERSION_PIPELINE", ("release.yaml",)),
    ("dashboard", "TEKTON_VERSION_DASHBOARD", ("release-full.yaml",)),
    ("triggers", "TEKTON_VERSION_TRIGGERS", ("release.yaml", "interceptors.yaml")),
)

# The rules kubementat has always granted the helm deployer, and said it granted: one line that
# says "everything, everywhere" is what the deleted script's heredoc said, so that is what the
# code says too. `extensions` is obsolete on a modern API server and does no harm; dropping it
# would be a behaviour change nobody asked for.
HELM_DEPLOYER_RULE_API_GROUPS = ("", "rbac.authorization.k8s.io", "networking.k8s.io", "batch",
                                 "extensions", "apps", "autoscaling", "tekton.dev",
                                 "resolution.tekton.dev")


def _release_manifest_url(component, version, filename) -> str:
    """One release manifest's address, built identically for all three components.

    The `/previous/v<version>/` segment is why the pins and the base URL have to agree: it is how
    upstream addresses a *specific* release, so a version that is not published there resolves
    to nothing rather than to whatever is newest.
    """
    return f"{TEKTON_RELEASE_BASE_URL}/{component}/previous/v{version}/{filename}"


def release_manifests_to_apply(env_static) -> list:
    """The manifests this build applies, in the order it applies them.

    Args:
        env_static: The environment-level `static.json`, as a mapping.

    Returns:
        list: `(component, url, address_in_namespace)` per manifest. The Pipelines release is not
        addressed at a namespace (it brings the control plane, not an app into one); the other
        two land inside the tekton namespace.

    Raises:
        kubementat.errors.TektonReleaseVersionNotConfiguredError: If a pinned version is absent or empty.
    """
    manifests = []
    for component, version_key, filenames in RELEASE_MANIFESTS:
        value = (env_static or {}).get(version_key)
        # Whitespace is what a template left behind by a `REPLACEME` edit looks like, and a URL
        # that contains it is still a 404 — so an unset key and a blank one are the same failure.
        version = value.strip() if isinstance(value, str) else value
        if not version:
            raise errors.TektonReleaseVersionNotConfiguredError(
                f"{version_key} is not set in the environment's static.json, so kubementat cannot "
                f"build the URL for the {component} release. Add \"{version_key}\": "
                f"\"<version>\" to platform_config/<environment>/static.json. What is refused here is "
                f"a key that was declared and answered nothing, and it is refused rather than "
                f"defaulted: kubementat's own default for this key is the template's value in "
                f"`constants.DEFAULT_STATIC_ANSWERS`, and a version the tool picked instead of the "
                f"configuration is how a pin stops meaning anything."
            )
        for filename in filenames:
            manifests.append((component, _release_manifest_url(component, version, filename),
                              component != "pipeline"))
    return manifests


def helm_deployer_cluster_role():
    """The ClusterRole that lets the helm deployer touch what it deploys.

    Returns:
        client.V1ClusterRole: Ready to hand to `KubernetesUtils.create_or_replace_cluster_role`.
    """
    return client.V1ClusterRole(
        api_version="rbac.authorization.k8s.io/v1",
        kind="ClusterRole",
        metadata=client.V1ObjectMeta(
            name=HELM_DEPLOYER_CLUSTER_ROLE_NAME,
            labels={"managed-by": "kubementat"},
        ),
        rules=[client.V1PolicyRule(
            api_groups=list(HELM_DEPLOYER_RULE_API_GROUPS),
            resources=["*"],
            verbs=["*"],
        )],
    )


# How long an install may take for the Pipelines webhook to become reachable, and how often it is
# asked. `kubectl apply` has returned by the time the next manifest goes in, and the webhook is
# already registered as a control-plane hook by then -- so a controller pod that is not yet
# serving turns the *next* apply into somebody else's error. This is the same reason the discovery
# wait exists, aimed one step further along: discovery asks whether the API path opened, this asks
# whether the socket behind the webhook resolves.
#
# Two minutes is not a guess picked for its round number. A single-node k3s pod pulls an image and
# reaches ready in tens of seconds on a cold cache, and the ceiling only has to bound the wait a
# healthy cluster never makes: short enough that a genuinely stuck webhook is reported inside one
# `kmt install`, long enough that a slow node is not mistaken for one. Polled every two seconds for
# the same reason `PIPELINE_RUN_POLL_INTERVAL_SECONDS` is: the answer changes on pod-scale time, and
# asking faster spends API reads for nothing.
WEBHOOK_READY_TIMEOUT_SECONDS = 120.0
WEBHOOK_READY_POLL_SECONDS = 2.0


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


class TektonUtils:
    def __init__(self, ctx):
        # One `Context` and nothing else, the shape the other service classes have.
        # `self.ctx` is kept because it is what a second KubernetesUtils object is built from --
        # the same object the old `Automation(self.ctx)` built -- and building a second `Context`
        # to hand over would resolve "which cluster" a second time for one method, which is what
        # one `Context` is there to prevent.
        self.ctx = ctx
        self.kubernetes_utils = KubernetesUtils(ctx)
        self.environment = ctx.environment
        # The team is not a path segment here, it is the namespace every one of this class's
        # objects is written into: `PIPELINE_NAMESPACE` is read out of `team_static` two lines
        # below, and `require_team` is what says a run without a team has no such namespace.
        self.team = ctx.require_team("the namespace the Tekton objects live in")
        self.config = ctx.config
        self.pipeline_namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')

    def install(self) -> None:
        """Install or upgrade the three Tekton releases kubementat runs on, plus its ClusterRole.

        This is what the installer script used to do, and it is the same shape: one
        `kubectl apply` per release manifest, addressed at the namespace that release installs
        into, and a ClusterRole created on the way through. Nothing here branches on whether the
        cluster already has Tekton: `apply` is the branch — a fresh cluster creates the objects,
        an existing one gets the same manifests written over them, which is why an upgrade is a
        version bump in `static.json` and not a procedure. `preflight_installed_tekton_api_versions`
        is what turns "the applies succeeded" into "this cluster now serves what kubementat
        speaks"; it is called by `Automation.install_kubementat` after this returns, because the
        controller takes a moment to open its API path.

        The two things that differ from bash are deliberate:

        * A missing `TEKTON_VERSION_*` key is an error naming that key. `jq -r` printed `null`
          into the URL instead and the install failed as a 404 on somebody else's log.
        * The ClusterRole arrives through the client, so a role that is already there is
          replaced rather than announced by `kubectl`'s `configured` line — and it is the same
          object whether the cluster was empty or not.

        Raises:
            kubementat.errors.TektonReleaseVersionNotConfiguredError: If a pinned release version is not configured.
            kubementat.errors.CommandExecutionError: If `kubectl` refuses a manifest.
            kubernetes.client.exceptions.ApiException: If the ClusterRole cannot be written.
        """
        env_static = self.config.get('env_static')
        namespace = self.config.component_namespace("tekton")
        logging.info("#########################")
        logging.info("Installing Tekton from platform_config ...")
        logging.info(f"ENVIRONMENT: {self.environment}")
        logging.info(f"TEKTON_NAMESPACE: {namespace}")
        for key in ("TEKTON_VERSION_PIPELINE", "TEKTON_VERSION_TRIGGERS",
                    "TEKTON_VERSION_DASHBOARD"):
            logging.info(f"{key}: {env_static.get(key)}")
        logging.info(f"TEKTON_RELEASE_BASE_URL: {TEKTON_RELEASE_BASE_URL}")
        logging.info("#########################")

        for component, url, address_in_namespace in release_manifests_to_apply(env_static):
            # The first deliberate exception, kept in the tree rather than only written down: this
            # applies a
            # multi-document upstream manifest by URL, where server-side apply through
            # `DynamicClient` *is* available and converting means owning the download and the
            # document splitting. The reason it stays is not a client inability, and not a claim
            # someone once wrote down against it --
            # it is that a split that gets the order wrong or skips a status subresource is a worse
            # failure than a binary this project already declares. And it still carries the
            # cluster, because an unaddressed `kubectl apply` is the bug this design is built on.
            argv = ["kubectl", "apply", *self.kubernetes_utils.kubectl_args]
            if address_in_namespace:
                argv += ["-n", namespace]
            shell.run(argv + ["-f", url])
            if component == "pipeline":
                # The ClusterRole goes in immediately after the control plane it authorises, exactly
                # where the script put it, so a run that dies partway never leaves a controller with
                # nothing to deploy through.
                self._install_helm_deployer_cluster_role()
                # ...and the dashboard and Triggers releases do not go in until the webhook the
                # apply just registered can actually be called. See `TektonWebhookNotReachableError`.
                self._wait_for_pipeline_webhook(namespace)

    def _install_helm_deployer_cluster_role(self) -> None:
        """Write the ClusterRole the helm deployer's bindings point at.

        Fresh install and upgrade are the two branches of one call: create, and if the API says it
        already exists, replace. The alternative — the `kubectl describe` the script ran afterwards
        to show its work — proved nothing except that the role was readable, which a successful
        write here already says more reliably.
        """
        logging.info(f"Configuring {HELM_DEPLOYER_CLUSTER_ROLE_NAME} ...")
        self.kubernetes_utils.create_or_replace_cluster_role(
            HELM_DEPLOYER_CLUSTER_ROLE_NAME,
            helm_deployer_cluster_role(),
        )

    def _wait_for_pipeline_webhook(self, namespace: str) -> None:
        """Hold the remaining releases until the API server can reach the webhook the last apply registered.

        `kubectl apply` of the Pipelines release returns when the objects are accepted, and among
        those objects is the webhook configuration that tells the API server to call
        `<service>.<namespace>.svc` before it will create anything the controller cares about. From
        that moment the control plane is holding a hook for a pod that is still being scheduled, and
        the next apply — the Triggers release, whose objects are validated by that hook — fails as a
        webhook call rather than as an apply. Nothing is wrong with the Triggers release. This reads
        the same question the operator would: is there anything behind the Service yet?

        Endpoints, not `readyReplicas`, is the read: the API server dials what the Endpoints object
        advertises, so an empty list is exactly the state that fails, and a rolled-out Deployment
        whose pod is not passing readiness would still be a broken webhook.

        The wait is bounded so a stuck webhook is reported rather than survived. When it expires,
        the error names the Service, the namespace and the ceiling, because "the cluster was not
        ready" is not an answer anyone can act on.

        Args:
            namespace: The namespace the control plane was just applied into.

        Raises:
            kubementat.errors.TektonWebhookNotReachableError: If no endpoint is reachable within
                WEBHOOK_READY_TIMEOUT_SECONDS.
        """
        deadline = time.time() + WEBHOOK_READY_TIMEOUT_SECONDS
        while True:
            addresses = self.kubernetes_utils.service_endpoint_addresses(
                namespace, PIPELINE_WEBHOOK_SERVICE_NAME)
            if addresses:
                logging.info(
                    f"{PIPELINE_WEBHOOK_SERVICE_NAME}.{namespace} is reachable from "
                    f"{len(addresses)} endpoint(s); applying the releases that call its webhook")
                return
            if time.time() >= deadline:
                raise errors.TektonWebhookNotReachableError(
                    f"{PIPELINE_WEBHOOK_SERVICE_NAME} in namespace {namespace} served no endpoints "
                    f"for {WEBHOOK_READY_TIMEOUT_SECONDS}s, so the releases that are validated through "
                    "it were not applied. The Pipelines control plane is installed but not serving: "
                    f"check that its pod is Running and passing readiness in {namespace} "
                    f"(`kubectl -n {namespace} get pods -l app=tekton-pipelines-webhook`, and the "
                    "events on the pod), then re-run `kmt install`. The releases that were applied "
                    f"before {PIPELINE_WEBHOOK_SERVICE_NAME} stayed applied, and re-running is safe: "
                    "every apply here is a write of the same object, not an increment.")
            logging.info(
                f"{PIPELINE_WEBHOOK_SERVICE_NAME}.{namespace} has no ready endpoints yet, asking "
                f"again in {WEBHOOK_READY_POLL_SECONDS}s")
            time.sleep(WEBHOOK_READY_POLL_SECONDS)

    def setup_triggers(self) -> None:
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
            kubementat.errors.PipelineNamespaceNotConfiguredError: If the team's static config has no namespace.
            kubementat.errors.ResourceWithoutNameError: If a trigger manifest has no name.
            kubementat.errors.CommandExecutionError: Never, by this method itself — it calls no
                `kubectl`, which is what the RBAC primitives made unnecessary.
            kubernetes.client.exceptions.ApiException: If the cluster refuses an object.
        """
        namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')
        if not (isinstance(namespace, str) and namespace.strip()):
            raise errors.PipelineNamespaceNotConfiguredError(
                f"PIPELINE_NAMESPACE is not set in "
                f"{self.config.team_static_config_path}, so kubementat has no namespace to put "
                f"the trigger ServiceAccounts, Roles and bindings in. Set it to the namespace the "
                f"team's pipelines run in and run `kmt tekton setup-triggers` again."
            )
        namespace = namespace.strip()

        logging.info("#########################")
        logging.info("Setting up tekton triggers from platform_config ...")
        logging.info(f"ENVIRONMENT: {self.environment}")
        logging.info(f"TEAM: {self.team}")
        logging.info(f"PIPELINE_NAMESPACE: {namespace}")
        logging.info(f"TRIGGER_DIRECTORY: {self.config.tekton_team_trigger_dir}")
        logging.info("#########################")

        self._setup_trigger_rbac(namespace)
        self._setup_webhook_secrets(namespace)
        self._apply_team_trigger_resources(namespace)
        logging.info("Finished setting up tekton triggers.")

    def _setup_trigger_rbac(self, namespace: str) -> None:
        """The seven objects, in the script's order: role, then the accounts bound to it.

        Nothing here branches on the cluster's state. Every one of these primitives is
        create-or-replace, so a namespace that already has them gets the same objects written
        over them, which is what an operator expects from a command that says `setup`.
        """
        logging.info("Setting up roles for tekton triggers ...")

        logging.info(f"Configuring {BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME} ...")
        self.kubernetes_utils.create_or_replace_cluster_role(
            BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME,
            bindings_and_interceptors_cluster_role(),
        )

        logging.info(f"Setting up {TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME} in namespace "
                     f"{namespace} ...")
        self.kubernetes_utils.create_or_replace_service_account(
            namespace, TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME,
            labels=_trigger_labels(self.team))

        logging.info(f"Setting up {TRIGGER_CONTROLLER_ROLE_NAME} in namespace {namespace} ...")
        self.kubernetes_utils.create_or_replace_role(
            namespace, TRIGGER_CONTROLLER_ROLE_NAME, trigger_controller_role(self.team))

        logging.info(f"Binding {TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME} to "
                     f"{TRIGGER_CONTROLLER_ROLE_NAME} in namespace {namespace} ...")
        self.kubernetes_utils.create_or_replace_role_binding(
            namespace, TRIGGER_CONTROLLER_ROLE_BINDING_NAME,
            client.V1RoleBinding(
                api_version="rbac.authorization.k8s.io/v1",
                kind="RoleBinding",
                metadata=client.V1ObjectMeta(name=TRIGGER_CONTROLLER_ROLE_BINDING_NAME,
                                             labels=_trigger_labels(self.team)),
                subjects=[client.RbacV1Subject(
                    kind="ServiceAccount", name=TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME)],
                role_ref=client.V1RoleRef(kind="Role", name=TRIGGER_CONTROLLER_ROLE_NAME,
                                          api_group="rbac.authorization.k8s.io"),
            ))

        cluster_binding_name = webhook_job_cluster_role_binding_name(self.team)
        logging.info(f"Binding {TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME} to "
                     f"{BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME} for team {self.team} ...")
        self.kubernetes_utils.create_or_replace_cluster_role_binding(
            cluster_binding_name,
            client.V1ClusterRoleBinding(
                api_version="rbac.authorization.k8s.io/v1",
                kind="ClusterRoleBinding",
                metadata=client.V1ObjectMeta(name=cluster_binding_name,
                                             labels=_trigger_labels(self.team)),
                subjects=[client.RbacV1Subject(
                    kind="ServiceAccount", name=TRIGGER_CONTROLLER_SERVICE_ACCOUNT_NAME,
                    namespace=namespace)],
                role_ref=client.V1RoleRef(kind="ClusterRole",
                                          name=BINDINGS_AND_INTERCEPTORS_CLUSTER_ROLE_NAME,
                                          api_group="rbac.authorization.k8s.io"),
            ))

        logging.info(f"Setting up {TRIGGER_WEBHOOK_JOB_ROLE_NAME} in namespace {namespace} ...")
        self.kubernetes_utils.create_or_replace_role(
            namespace, TRIGGER_WEBHOOK_JOB_ROLE_NAME, trigger_webhook_job_role(self.team))

        logging.info(f"Setting up {TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME} in namespace "
                     f"{namespace} ...")
        self.kubernetes_utils.create_or_replace_service_account(
            namespace, TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME,
            labels=_trigger_labels(self.team))

        logging.info(f"Binding {TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME} to "
                     f"{TRIGGER_WEBHOOK_JOB_ROLE_NAME} in namespace {namespace} ...")
        self.kubernetes_utils.create_or_replace_role_binding(
            namespace, TRIGGER_WEBHOOK_JOB_ROLE_BINDING_NAME,
            client.V1RoleBinding(
                api_version="rbac.authorization.k8s.io/v1",
                kind="RoleBinding",
                metadata=client.V1ObjectMeta(name=TRIGGER_WEBHOOK_JOB_ROLE_BINDING_NAME,
                                             labels=_trigger_labels(self.team)),
                subjects=[client.RbacV1Subject(
                    kind="ServiceAccount", name=TRIGGER_WEBHOOK_JOB_SERVICE_ACCOUNT_NAME)],
                role_ref=client.V1RoleRef(kind="Role", name=TRIGGER_WEBHOOK_JOB_ROLE_NAME,
                                          api_group="rbac.authorization.k8s.io"),
            ))

    def _setup_webhook_secrets(self, namespace: str) -> None:
        """The two webhook Secrets, and only for the providers that have a token configured.

        Read from the team's `static.encrypted.json`, which is where a generated token lands
        (`config set --generate`) and where an operator puts a real one. An absent, empty or `null` key
        means that
        provider is not in use: the Secret is not created, the run continues, and the log says so
        — a Secret holding the tokenless result of a missing key is how bash failed this, and an
        EventListener that never validates a webhook fails quietly too.
        """
        encrypted = self.config.get('team_static_encrypted') or {}
        for secret_name, config_key in WEBHOOK_SECRETS:
            token = encrypted.get(config_key)
            if not (isinstance(token, str) and token.strip()):
                logging.info(f"No {config_key} configured for team {self.team}, not creating "
                             f"{secret_name} in namespace {namespace}")
                continue
            logging.info(f"Configuring {secret_name} from {config_key} in namespace "
                         f"{namespace} ...")
            self.kubernetes_utils.create_or_replace_secret(
                namespace=namespace,
                secret_name=secret_name,
                key_type="Opaque",
                # The token is the Secret's only data item, and it goes in `data` as base64 —
                # the same wire format `kubectl apply` of the heredoc produced, which is what
                # lets a Secret this wrote and a Secret that script wrote be the same object.
                raw_data={"secretToken": base64.b64encode(token.encode()).decode()},
                labels=_trigger_labels(self.team),
            )

    def _apply_team_trigger_resources(self, namespace: str) -> None:
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
        generation._refuse_obsolete_team_trigger_dir(self.config)
        trigger_dir = Path(self.config.tekton_team_trigger_dir)
        if not trigger_dir.is_dir():
            logging.info(f"No trigger directory at {trigger_dir}, so no triggers are configured "
                         f"for team {self.team}. Generate some with "
                         f"`kmt tekton-generate-triggers`, or apply manifests here by hand.")
            return

        # The primitive walks the directory and answers for every document in it; the object it
        # hands the cluster is built here, which is why no `Automation` is needed to reach it.
        logging.info(f"Setting up configured triggers for team {self.team} ...")
        apply.apply_resources(KubernetesUtils(self.ctx), trigger_dir, namespace, kind=None,
                                  group=self.config.TRIGGERS_API_GROUP,
                                  version=self.config.TRIGGERS_API_VERSION)
