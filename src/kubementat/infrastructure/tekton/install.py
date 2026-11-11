"""Installing the three pinned Tekton releases, and the ClusterRole that deploys through them.

The install half of the split: what a `kmt install` sends to the cluster, the address each release
is fetched from, the role every helm deploy is authorised through, and the one pause that keeps the
next release from failing as a webhook call. Nothing here decides *whether* the cluster is ready to
be spoken to — `preflight` answers that, both before a run and after this returns — and nothing here
generates a manifest, which is `generation`'s. So this module has no outgoing edge inside the
package, and that is the property the DAG wants: the module that writes cluster objects does not
need the module that answers cluster questions.

Methods lost `self` the way the rest of the split did, and what they read off the instance is a
parameter now: `kubernetes_utils` and `config`, plus `environment` for the log line the constructor
used to print. The ClusterRole write and the webhook wait take the client they actually need and
nothing else, because both of them asked one question of one object before.

Two bindings here are load-bearing and not style. `shell` is bound as a module, never `from
…shell import run`: the test that says the role is written after the control plane it authorises
patches `shell.run` through a dotted string, and that string reaches the loop only if this module
looks the name up at call time. `time` is bound the same way for the same reason the wait's clock
is shortened in a test rather than slept out.

The two wait numbers are read here as module globals and owned here. `preflight.py`'s discovery
numbers are its own default arguments and stay there — that is a different question, asked of a
different thing, on a different clock.
"""

from kubementat import errors
from kubementat.infrastructure import shell
from kubernetes import client
import logging
import time


# Where upstream serves the release manifests kubementat installs, spelled once because the
# base URL and the pins move together: the bucket this URL used to point at serves only what it
# happened to keep (`v1.4.0` answers, anything from 1.15 on 404s), so a pin bumped without this
# string moving is an install failure on the very first `kubectl apply`.
TEKTON_RELEASE_BASE_URL = "https://infra.tekton.dev/tekton-releases"

# The ClusterRole every helm deploy is authorised through. kubementat creates it, so kubementat
# also has to be able to name it on the way out. `uninstall_kubementat` in
# `automations/uninstall/kubementat.py` is that way out, and it names this constant rather than
# rebuilding the manifest it came from: it is
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
                f"\"<version>\" to `<environment>/static.json` of the configuration directory. What is refused here is "
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


def install(kubernetes_utils, config, environment) -> None:
    """Install or upgrade the three Tekton releases kubementat runs on, plus its ClusterRole.

    This is what the installer script used to do, and it is the same shape: one
    `kubectl apply` per release manifest, addressed at the namespace that release installs
    into, and a ClusterRole created on the way through. Nothing here branches on whether the
    cluster already has Tekton: `apply` is the branch — a fresh cluster creates the objects,
    an existing one gets the same manifests written over them, which is why an upgrade is a
    version bump in `static.json` and not a procedure. `preflight_installed_tekton_api_versions`
    is what turns "the applies succeeded" into "this cluster now serves what kubementat
    speaks"; it is called by `install_kubementat` in `automations/install/kubementat.py` after this
    returns, because the
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
    env_static = config.get('env_static')
    namespace = config.component_namespace("tekton")
    logging.info("#########################")
    logging.info("Installing Tekton from platform_config ...")
    logging.info(f"ENVIRONMENT: {environment}")
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
        argv = ["kubectl", "apply", *kubernetes_utils.kubectl_args]
        if address_in_namespace:
            argv += ["-n", namespace]
        shell.run(argv + ["-f", url])
        if component == "pipeline":
            # The ClusterRole goes in immediately after the control plane it authorises, exactly
            # where the script put it, so a run that dies partway never leaves a controller with
            # nothing to deploy through.
            _install_helm_deployer_cluster_role(kubernetes_utils)
            # ...and the dashboard and Triggers releases do not go in until the webhook the
            # apply just registered can actually be called. See `TektonWebhookNotReachableError`.
            _wait_for_pipeline_webhook(kubernetes_utils, namespace)

def _install_helm_deployer_cluster_role(kubernetes_utils) -> None:
    """Write the ClusterRole the helm deployer's bindings point at.

    Fresh install and upgrade are the two branches of one call: create, and if the API says it
    already exists, replace. The alternative — the `kubectl describe` the script ran afterwards
    to show its work — proved nothing except that the role was readable, which a successful
    write here already says more reliably.
    """
    logging.info(f"Configuring {HELM_DEPLOYER_CLUSTER_ROLE_NAME} ...")
    kubernetes_utils.create_or_replace_cluster_role(
        HELM_DEPLOYER_CLUSTER_ROLE_NAME,
        helm_deployer_cluster_role(),
    )

def _wait_for_pipeline_webhook(kubernetes_utils, namespace: str) -> None:
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
        addresses = kubernetes_utils.service_endpoint_addresses(
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
