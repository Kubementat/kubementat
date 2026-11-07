from kubementat import errors
from kubementat.infrastructure import shell
from kubementat.infrastructure.kubernetes import KubernetesUtils, apply
# The generation group moved out (P4-1), so the calls that still belong to this file's remaining
# work reach it through the module. Never `from … import`: the suite patches by module object and a
# name imported here is a frozen second binding nobody runs.
from kubementat.infrastructure.tekton import generation
import base64
import click
import logging
from kubernetes import client
from pathlib import Path
import time
import yaml


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


# What `--wait` asks the cluster, and for how long. The interval is the same number the Tekton
# Task's `polling-sleep-time` param carries, so the two are one knob and not two; the bound is the
# owner's thirty minutes, because the bash loop this replaces asked the same question forever, with
# no exit condition at all, which holds a pod open for a run that is never going to finish.
PIPELINE_RUN_POLL_INTERVAL_SECONDS = 3
PIPELINE_RUN_TIMEOUT_SECONDS = 1800


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


def preflight_tekton_api_version(kubernetes_utils, config) -> None:
    """Stop here if this cluster does not serve the `tekton.dev` version kubementat uses.

    A cluster whose Tekton controller only serves `tekton.dev/v1beta1` is the failure this
    guards: every manifest would be rejected one by one, and the forty-first 404 reads
    nothing like the one-line answer. Nothing falls back - kubementat writes `v1`, so an
    older controller is simply not supported here, and the message says which knob turns
    into a supported cluster.

    Args:
        kubernetes_utils: The utilities that own the API client doing the discovery.
        config: The configuration supplying TEKTON_API_GROUP/TEKTON_API_VERSION.

    Raises:
        kubementat.errors.TektonControllerWithoutConfiguredApiError: If the cluster serves nothing under
            that group/version.
    """
    if kubernetes_utils.api_version_is_served(config.TEKTON_API_GROUP, config.TEKTON_API_VERSION):
        return

    raise errors.TektonControllerWithoutConfiguredApiError(
        f"The cluster serves no resources under {config.TEKTON_API_GROUP}/{config.TEKTON_API_VERSION}, "
        "and kubementat speaks only that API version. Nothing was applied. Install or upgrade "
        f"Tekton Pipelines with `kmt install <environment> <team>`, or set "
        f"TEKTON_VERSION_PIPELINE in platform_config/<environment>/static.json to a version that "
        f"serves {config.TEKTON_API_GROUP}/{config.TEKTON_API_VERSION} (Pipelines 1.0 and newer). "
        f"The manifests kubementat ships, and the ones under examples/tekton_ci/, are written for "
        f"it, and so is this code."
    )


# How many times, and how far apart, the post-install assertion asks the discovery question.
# `kubectl apply` returns when the object is accepted, not when the API server has opened the
# `/apis/<group>/<version>` path in front of it, so the first request straight after an install
# can answer 404 on a cluster that is about to serve it. Waiting is not a fallback: the same
# answer is demanded at the end of the wait as at the start of it.
DISCOVERY_ATTEMPTS = 6
DISCOVERY_DELAY_SECONDS = 5.0

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


def _served_within_a_bounded_wait(kubernetes_utils, group, version, attempts, delay_seconds) -> bool:
    """Ask the discovery question until it says yes or the attempts run out."""
    for attempt in range(attempts):
        if kubernetes_utils.api_version_is_served(group, version):
            return True
        if attempt + 1 < attempts:
            logging.info(
                f"No {group}/{version} on this cluster yet, retrying discovery "
                f"in {delay_seconds}s (attempt {attempt + 1} of {attempts})"
            )
            time.sleep(delay_seconds)
    return False


def preflight_installed_tekton_api_versions(kubernetes_utils, config,
                                           attempts=DISCOVERY_ATTEMPTS,
                                           delay_seconds=DISCOVERY_DELAY_SECONDS) -> None:
    """Stop the install if what was just applied does not serve what kubementat speaks.

    The install ends with `kubectl apply` having exited 0, which is a claim about the manifests
    it sent and not about the controller that is now running. A pin that is wrong, a release URL
    that 404s partway through, or an image that never rolls out all leave the install printing
    success and the first pipeline run printing 404s. So this asks the discovery question for both
    groups the install is supposed to have brought up, once per group, before anything else in the
    install runs.

    The message names the version the install tried to apply, because that is the half the
    operator cannot see from the cluster: the install builds its release URLs from the
    `TEKTON_VERSION_*` keys, so a mismatch is always either that pin or that controller.
    Nothing here falls back to another API version, and there is no environment variable that
    turns the assertion off.

    Args:
        kubernetes_utils: The utilities that own the API client doing the discovery.
        config: The configuration supplying the groups/versions and the pinned component versions.
        attempts: How many times to ask before believing the answer.
        delay_seconds: Seconds between attempts.

    Raises:
        kubementat.errors.TektonControllerWithoutConfiguredApiError: If the cluster serves nothing under a group/
            version kubementat needs, once the bounded wait has run out.
    """
    env_static = config.get('env_static')
    checked = [
        {
            "component": "Tekton Pipelines",
            "group": config.TEKTON_API_GROUP,
            "version": config.TEKTON_API_VERSION,
            "pin_key": "TEKTON_VERSION_PIPELINE",
            "pin": env_static.get("TEKTON_VERSION_PIPELINE"),
            "what_we_speak": "writes and reads only that API version",
            "supported": "Pipelines 1.0 and newer serve tekton.dev/v1",
        },
        {
            "component": "Tekton Triggers",
            "group": config.TRIGGERS_API_GROUP,
            "version": config.TRIGGERS_API_VERSION,
            "pin_key": "TEKTON_VERSION_TRIGGERS",
            "pin": env_static.get("TEKTON_VERSION_TRIGGERS"),
            "what_we_speak": "writes every trigger resource in that API version",
            "supported": "Triggers 0.37 serves triggers.tekton.dev/v1alpha1 and v1beta1 and no v1",
        },
    ]

    for entry in checked:
        group_version = f"{entry['group']}/{entry['version']}"
        if _served_within_a_bounded_wait(kubernetes_utils, entry['group'], entry['version'],
                                         attempts, delay_seconds):
            logging.info(f"The cluster serves {group_version}, as {entry['component']} should.")
            continue

        pinned = f"{entry['component']} {entry['pin']}" if entry["pin"] else \
            f"{entry['component']} at the version pinned in {entry['pin_key']}"
        raise errors.TektonControllerWithoutConfiguredApiError(
            f"The cluster serves no resources under {group_version} after installing "
            f"{pinned}, and kubementat {entry['what_we_speak']}. Nothing was configured. "
            f"Either the install did not land its manifests for {entry['component']}, or that "
            f"release does not serve {group_version} ({entry['supported']}). Install or upgrade "
            f"with `kmt install <environment> <team>`, or set {entry['pin_key']} in "
            f"platform_config/<environment>/static.json to a version that serves {group_version}, "
            f"then run the install again. There is no fallback API version and no override."
        )


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


# Which component's namespace the cleanup job lands in is spelled at the call site and nowhere
# else, because `src/` may not bind a namespace to a literal (the guard that says so is
# `test_no_module_under_src_binds_a_namespace_to_a_literal`, and it cannot tell a component name
# that happens to equal a namespace away from a namespace). What is read is
# `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE` in the environment's `static.json` — the same key the
# install deploys into and the uninstall sweeps. The bash hardcoded
# `CRONJOB_NAMESPACE="cluster-cronjobs"`, which is the defect `TODOs.md` item 2 records: a renamed
# namespace moved the job out from under the asserts that looked for it in the old place.

# The four names the cleanup job is made of. These are cluster contract: the CronJob's pod
# template names the ServiceAccount by hand, the ClusterRoleBinding names both, and the
# pull-secret name is the default one kubelet looks for.
CLEANUP_JOB_SERVICE_ACCOUNT_NAME = "tekton-cleaner"
CLEANUP_JOB_CLUSTER_ROLE_NAME = "tekton-cleaner-clusterrole"
CLEANUP_JOB_CLUSTER_ROLE_BINDING_NAME = "tekton-cleaner-clusterrole-binding"
CLEANUP_JOB_NAME = "cleanup-tekton-pipelineruns"
CLEANUP_JOB_PULL_SECRET_NAME = "docker-registry-secret"

# How much history the job keeps, and how old a run has to be before it counts. The prune age is
# not a duration here: the job's whole rule is "keep the newest N completed runs, delete the
# rest", so `NUMBER_OF_JOBS_TO_KEEP` is the retention policy, spelled in runs.
CLEANUP_JOBS_TO_KEEP = 20
CLEANUP_JOB_SCHEDULE = "0 23 * * *"
CLEANUP_JOB_SUCCESSFUL_HISTORY_LIMIT = 1
CLEANUP_JOB_FAILED_HISTORY_LIMIT = 2

# The labels the bash heredocs put on these objects. `tekton-cleanup: cleanup` on the pod template
# is what a `kubectl get pods -l tekton-cleanup` finds the runs by, so it is not decoration.
CLEANUP_JOB_LABELS = {"managed-by": "kubementat"}
CLEANUP_JOB_POD_LABELS = {"managed-by": "kubementat", "tekton-cleanup": "cleanup"}

# What the cleaner is allowed to do: see every PipelineRun in the cluster and delete them. The
# rule is cluster-scoped because the job lists `--all-namespaces`; a namespaced Role here would
# have left it able to see nothing, and then to report success anyway.
def cleanup_job_cluster_role():
    """The ClusterRole the cleanup job's ServiceAccount is bound to.

    `delete` alongside the three reads is not an oversight: a cleaner that cannot list is a
    cleaner that deletes nothing, and the verbs are exactly the ones the deleted heredoc sent.
    """
    return client.V1ClusterRole(
        api_version="rbac.authorization.k8s.io/v1",
        kind="ClusterRole",
        metadata=client.V1ObjectMeta(name=CLEANUP_JOB_CLUSTER_ROLE_NAME,
                                     labels=dict(CLEANUP_JOB_LABELS)),
        rules=[client.V1PolicyRule(api_groups=["tekton.dev"],
                                   resources=["pipelineruns"],
                                   verbs=["delete", "get", "watch", "list"])],
    )


def cleanup_job_command(jobs_to_keep: int = CLEANUP_JOBS_TO_KEEP) -> list:
    """The container command that prunes, spelled once.

    It is `/bin/bash -c` of a script because that is what the CronJob has always contained, and
    because the prune is a pipeline over `kubectl get` rather than an API call — it sorts by the
    completion time the API server wrote, keeps the newest `jobs_to_keep`, and deletes the tail.
    The deletion is per-item `|| true`, so a run that vanished between listing and deleting is not
    a failed job.
    """
    return ["/bin/bash", "-c", _CLEANUP_JOB_SCRIPT.replace(
        'NUMBER_OF_JOBS_TO_KEEP="20"', f'NUMBER_OF_JOBS_TO_KEEP="{jobs_to_keep}"')]


# The script inside that command, verbatim out of the CronJob heredoc. Nothing here is rewritten:
# this is a port, and a cleanup job whose sort order changed is a cleanup job that deletes the
# wrong runs.
_CLEANUP_JOB_SCRIPT = """set -e

NUMBER_OF_JOBS_TO_KEEP="20"
let NUMBER_OF_JOBS_TO_KEEP_PLUS_1=$NUMBER_OF_JOBS_TO_KEEP+1
echo "NUMBER_OF_JOBS_TO_KEEP_PLUS_1: $NUMBER_OF_JOBS_TO_KEEP_PLUS_1"

ALL_COMPLETED_JOBS="$(kubectl get pipelinerun --all-namespaces -o jsonpath='{range .items[?(@.status.completionTime)]}{.status.completionTime}{" "}{.metadata.name}{" -n"}{.metadata.namespace}{"\\n"}{end}' | sort -s)"
echo "All completed jobs in all namespaces:"
echo "$ALL_COMPLETED_JOBS"
all_count="$(echo "$ALL_COMPLETED_JOBS" | wc -l | tr -d ' ')"
echo "All completed job count: $all_count"

if [[ "$all_count" -lt "$NUMBER_OF_JOBS_TO_KEEP" ]]; then
  echo "Found less than $NUMBER_OF_JOBS_TO_KEEP pipelineruns. Skipping execution."
  exit 0
fi

echo ""
TO_DELETE="$(echo "$ALL_COMPLETED_JOBS" | tail -n +$NUMBER_OF_JOBS_TO_KEEP_PLUS_1 | awk '{ print $2 " " $3 }')"
echo "Full deletion list:"
echo "$TO_DELETE"
echo "To delete job count: $(echo "$TO_DELETE" | wc -l | tr -d ' ')"

echo "$TO_DELETE" | while IFS= read -r delete_item ; do 
  echo "Deleting pipelinerun: ${delete_item}"
  kubectl delete pipelinerun ${delete_item} || true
done
"""


def cleanup_job_image(env_static) -> str:
    """The CI image the prune runs in, built from the same three keys as everywhere else.

    Args:
        env_static: The environment-level `static.json`, as a mapping.

    Returns:
        str: `<registry>/<ci image>:<tag>`, exactly what `kubectl` was handed before.

    Raises:
        kubementat.errors.CleanupJobImageNotConfiguredError: If any of the three keys is declared and answers nothing,
            or if the registry is not configured. The name and the tag are also answered
            by `constants.DEFAULT_STATIC_ANSWERS` for a tree that declares nothing, so an *absent*
            key no longer reaches this — a mapping that is missing one is the same fact as an
            operator's file that declares it as `""`, and both are refused.
    """
    missing = []
    for key in ("DOCKER_REGISTRY_BASE_URL", "TEKTON_CI_IMAGE_NAME", "TEKTON_CI_IMAGE_TAG"):
        value = (env_static or {}).get(key)
        if not (isinstance(value, str) and value.strip()):
            missing.append(key)
    if missing:
        raise errors.CleanupJobImageNotConfiguredError(
            f"{' and '.join(missing)} {'are' if len(missing) > 1 else 'is'} not answered in the "
            f"environment's static.json, so kubementat cannot name the image the cleanup job runs "
            f"in. Add them to platform_config/<environment>/static.json. What is refused here is a "
            f"key that answers nothing, and kubementat will not point a job at an image it made up: "
            f"that is created successfully and then fails in silence on the schedule. The name and "
            f"the tag do have kubementat's own default (`ubuntu-ci-minimal:latest`, the template's "
            f"value), which is what a tree that declared nothing runs on."
        )
    base = env_static["DOCKER_REGISTRY_BASE_URL"].strip().rstrip("/")
    return f"{base}/{env_static['TEKTON_CI_IMAGE_NAME'].strip()}:{env_static['TEKTON_CI_IMAGE_TAG'].strip()}"


def cleanup_pipelinerun_cron_job(image: str,
                                 jobs_to_keep: int = CLEANUP_JOBS_TO_KEEP) -> dict:
    """The CronJob itself, as a manifest-shaped dict.

    It is a dict rather than a `V1CronJob` model because that is how `create_or_replace_custom_
    resource` speaks and because it keeps one answer to every field name: a client model would
    spell `jobTemplate` as `job_template`, and a CronJob whose template the API server cannot find
    is one that silently never schedules anything.

    Args:
        image: The container image the prune runs in, from `cleanup_job_image`.
        jobs_to_keep: How many completed PipelineRuns survive the prune.

    Returns:
        dict: The manifest, addressed at no namespace — the request carries that.
    """
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": CLEANUP_JOB_NAME, "labels": dict(CLEANUP_JOB_LABELS)},
        "spec": {
            # Every day at 23:00, with the 15-minute variant the script kept as a comment kept
            # here too, so the alternative is still legible rather than only remembered.
            "schedule": CLEANUP_JOB_SCHEDULE,
            "successfulJobsHistoryLimit": CLEANUP_JOB_SUCCESSFUL_HISTORY_LIMIT,
            "failedJobsHistoryLimit": CLEANUP_JOB_FAILED_HISTORY_LIMIT,
            "concurrencyPolicy": "Forbid",
            "jobTemplate": {
                "spec": {
                    "template": {
                        "metadata": {"labels": dict(CLEANUP_JOB_POD_LABELS)},
                        "spec": {
                            "restartPolicy": "OnFailure",
                            "serviceAccount": CLEANUP_JOB_SERVICE_ACCOUNT_NAME,
                            "containers": [{
                                "name": "tekton-cleaner-ci-image",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": cleanup_job_command(jobs_to_keep),
                            }],
                        },
                    },
                },
            },
        },
    }


def _resolve_pipeline_run_file_path(identifier: str, config) -> Path:
    """Where a pipeline-run file lives, given whichever way a caller wrote it down.

    Three conventions reach this argument, and all three have to keep working, because they belong
    to callers this file does not own:

    * a **bare file name** (`foo-run.yml`) is a team identifier and resolves under
      `Config.tekton_team_pipeline_run_dir`, which is what the `kmt tekton-generate-pipeline-run`
      output and `list_available_pipeline_runs` both use;
    * a **`global/` prefix** resolves under `tekton_ci/pipeline-runs`, the long-standing way of
      pointing at a run that belongs to no team;
    * a **path** is used as it was given. That is what `tekton_ci/tasks/run-tekton-pipeline.yml`
      hands over -- `$(workspaces.workspace.path)/<repo>/platform_config/<env>/<team>/tekton/
      pipeline-runs/foo-run.yml`, a string the deleted bash script `cat`ed without asking what it
      meant -- and an absolute path is a path anywhere in it, so no caller has to be rewritten to a
      convention it cannot read.

    A relative argument that contains a separator is ambiguous between the last two, and the rule is
    stated in the command's own `--help`: it is a path if it is there, and a team-relative
    identifier (`smoke/foo-run.yml`) if it is not. Nothing in this tree writes a run into a
    subdirectory of the team's pipeline-run directory, so the fallback exists for the documented
    identifier shape and for nothing else; `global/` is an identifier and never a path.
    """
    given = Path(identifier)
    if given.is_absolute():
        return given
    if identifier.startswith("global/"):
        return Path(config.tekton_global_pipeline_run_dir) / identifier[len("global/"):]
    if len(given.parts) > 1:
        if given.exists():
            return given.resolve()
        team_relative = Path(config.tekton_team_pipeline_run_dir) / identifier
        if team_relative.exists():
            return team_relative
        # Neither place had it. Say so as a path rather than let `open` choose the story to tell.
        logging.info(f"{identifier} is neither a file here nor a run in "
                     f"{config.tekton_team_pipeline_run_dir}; trying it as a path relative to "
                     f"{Path.cwd()}")
        return given.resolve()
    return Path(config.tekton_team_pipeline_run_dir) / identifier


def _first_status_condition(resource) -> dict:
    """`status.conditions[0]`, without the `IndexError` a just-created object answers with.

    A `PipelineRun` has no conditions in the instant the controller has accepted it and none of its
    Tasks have started, and bash's `jq -r '.status.conditions[0]'` read that as `null` rather than
    crashing. Reading an empty list out of `conditions` is the same answer, so the two callers of
    this do not have to agree on which moment they caught the run at.
    """
    conditions = ((resource or {}).get('status') or {}).get('conditions') or []
    return conditions[0] if conditions else {}


class TektonUtils:
    # Whether this process already knows the cluster serves the version it is about to use.
    # A class attribute so an instance built without the constructor still answers for
    # itself, and an instance writes its own copy the first time the check passes.
    tekton_api_verified = False

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

    def _preflight_tekton_api_version(self):
        """Check once per process that the controller serves what this class is about to use."""
        if self.tekton_api_verified:
            return
        preflight_tekton_api_version(self.kubernetes_utils, self.config)
        self.tekton_api_verified = True

    def list_pipelines(self):
        return self._list_tekton_resource(self.pipeline_namespace, "pipelines")

    def list_tasks(self):
        return self._list_tekton_resource(self.pipeline_namespace, "tasks")

    def list_taskruns(self):
        return self._list_tekton_resource(self.pipeline_namespace, "taskruns")

    def list_pipeline_runs(self):
        return self._list_tekton_resource(self.pipeline_namespace, "pipelineruns")

    def list_all_tekton_resources(self):
        self.list_pipelines()
        self.list_tasks()
        self.list_pipeline_runs()
        self.list_taskruns()
        return None

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

    def setup_cleanup_job(self) -> None:
        """Set up the CronJob that prunes completed PipelineRuns, in the namespace config names.

        This is what `setup_tekton_pipelinerun_cleanup_job.sh` did, in one place instead of two
        hundred lines of bash. Seven cluster operations, in the order the script ran them, because
        that order is what makes the job able to run: the namespace before anything addressed at
        it, the pull Secret before the ServiceAccount that refers to it, the ServiceAccount before
        the ClusterRoleBinding that names it, the ClusterRole before the CronJob that starts a pod
        as it.

        The namespace is read from configuration and not remembered: `Config.component_namespace
        ("cluster-cronjobs")` answers with `CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE`, the same key
        the install deploys into and the uninstall sweeps. The bash carried its own copy of that
        answer, which is the whole reason the `assert:cleanup-cronjob-exists` and
        `assert:cleanup-cronjob-scheduled` stages were red after a namespace rename: the job was
        created, and looked for, in two different places.

        Both of the states a real cluster is in are ordinary here. A cluster that has never had
        this job gets the objects created; a cluster that has had it since last night gets the
        same objects written over the ones that are there, which is what makes a command that says
        `setup` safe to re-run on a Friday. A resource that is not there is what the create is for.

        Two things are deliberately not the same as bash, and both are defects rather than
        preferences: a `static.json` key that is not configured is an error naming the key instead
        of a CronJob whose image is `null/null:null`, and a team whose encrypted config has no
        `docker-registry-secret` block gets no Secret and a log line instead of one whose every
        field is the string `null`.

        Raises:
            kubementat.errors.CleanupJobImageNotConfiguredError: If the CI image cannot be named from configuration.
            KeyError: If `cluster-cronjobs` is not a kubementat component (a typo, not a rename).
            kubementat.errors.CommandExecutionError: Never by this method itself — it calls no
                `kubectl`.
            kubernetes.client.exceptions.ApiException: If the cluster refuses an object.
        """
        # `cluster-cronjobs` is the component, not the answer: the namespace itself is what the
        # environment's static.json gives back, with `constants.DEFAULT_NAMESPACES` underneath.
        namespace = self.config.component_namespace("cluster-cronjobs")
        image = cleanup_job_image(self.config.get('env_static'))

        logging.info("#########################")
        logging.info("Setting up the pipelinerun cleanup job from platform_config ...")
        logging.info(f"ENVIRONMENT: {self.environment}")
        logging.info(f"TEAM: {self.team}")
        logging.info(f"CRONJOB_NAMESPACE: {namespace}")
        logging.info(f"CI_IMAGE: {image}")
        logging.info(f"SCHEDULE: {CLEANUP_JOB_SCHEDULE}")
        logging.info(f"NUMBER_OF_JOBS_TO_KEEP: {CLEANUP_JOBS_TO_KEEP}")
        logging.info("#########################")

        # Namespace first, and created rather than checked for: `create_namespace` treats a 409 as
        # the state it wants, so this is a no-op on a cluster where the component is already there
        # and a real creation on one where it is not. bash needed `|| true` to say the same thing.
        self.kubernetes_utils.create_namespace(namespace)
        has_pull_secret = self._setup_cleanup_job_pull_secret(namespace)

        logging.info(f"Configuring {CLEANUP_JOB_SERVICE_ACCOUNT_NAME} in namespace "
                     f"{namespace} ...")
        self.kubernetes_utils.create_or_replace_service_account(
            namespace, CLEANUP_JOB_SERVICE_ACCOUNT_NAME, labels=dict(CLEANUP_JOB_LABELS))

        # The Secret is only half of it: what makes a kubelet use it for this job's pods is the
        # ServiceAccount pointing at it, which is the `patch serviceaccount` the script ran
        # immediately after it created the account. It goes after the account for the same reason.
        if has_pull_secret:
            self.kubernetes_utils.patch_service_account_with_pull_secrets(
                namespace, CLEANUP_JOB_SERVICE_ACCOUNT_NAME,
                [{"name": CLEANUP_JOB_PULL_SECRET_NAME}])

        logging.info(f"Setting up {CLEANUP_JOB_CLUSTER_ROLE_NAME} ...")
        self.kubernetes_utils.create_or_replace_cluster_role(
            CLEANUP_JOB_CLUSTER_ROLE_NAME, cleanup_job_cluster_role())

        logging.info(f"Binding {CLEANUP_JOB_CLUSTER_ROLE_NAME} to "
                     f"{CLEANUP_JOB_SERVICE_ACCOUNT_NAME} ...")
        self.kubernetes_utils.create_or_replace_cluster_role_binding(
            CLEANUP_JOB_CLUSTER_ROLE_BINDING_NAME,
            client.V1ClusterRoleBinding(
                api_version="rbac.authorization.k8s.io/v1",
                kind="ClusterRoleBinding",
                metadata=client.V1ObjectMeta(name=CLEANUP_JOB_CLUSTER_ROLE_BINDING_NAME,
                                             labels=dict(CLEANUP_JOB_LABELS)),
                subjects=[client.RbacV1Subject(kind="ServiceAccount",
                                              name=CLEANUP_JOB_SERVICE_ACCOUNT_NAME,
                                              namespace=namespace)],
                role_ref=client.V1RoleRef(kind="ClusterRole", name=CLEANUP_JOB_CLUSTER_ROLE_NAME,
                                          api_group="rbac.authorization.k8s.io"),
            ))

        logging.info(f"Configuring {CLEANUP_JOB_NAME} in namespace {namespace} ...")
        self.kubernetes_utils.create_or_replace_custom_resource(
            group="batch",
            version="v1",
            namespace=namespace,
            kind="CronJob",
            body=cleanup_pipelinerun_cron_job(image),
            plural_identifier="cronjobs",
        )
        logging.info(f"Cleanup job {CLEANUP_JOB_NAME} is set up in namespace {namespace}; it "
                     f"runs on the schedule {CLEANUP_JOB_SCHEDULE} and keeps the newest "
                     f"{CLEANUP_JOBS_TO_KEEP} completed pipelineruns.")

    def _setup_cleanup_job_pull_secret(self, namespace: str) -> bool:
        """The registry credential the cleaner's pod pulls its image with, if the team has one.

        The job's container comes from the same private registry the pipelines run in, so the pod
        needs a pull secret in its own namespace. bash created it unconditionally from four `jq -r`
        reads of one credentials block, which is how a team with no `DOCKER_REGISTRY_CREDENTIALS`
        entry named `docker-registry-secret` got a Secret that said `null` four times and a pod
        that was rejected by the registry with a message naming none of those things.

        Here the block is looked up, and no block means no Secret and one log line that says which
        file to look in. The caller does the other half — the ServiceAccount that points at the
        Secret cannot be patched before it exists — which is why this answers with what it did.

        Returns:
            bool: Whether a Secret was written, i.e. whether the ServiceAccount has a pull secret
            to be told about.
        """
        credentials = self.config.get('team_static_encrypted') or {}
        block = None
        for entry in credentials.get('DOCKER_REGISTRY_CREDENTIALS') or []:
            if isinstance(entry, dict) and entry.get('NAME') == CLEANUP_JOB_PULL_SECRET_NAME:
                block = entry
                break
        if block is None:
            logging.info(
                f"No DOCKER_REGISTRY_CREDENTIALS entry named {CLEANUP_JOB_PULL_SECRET_NAME} in "
                f"{self.config.team_static_encrypted_config_path}, so the cleanup job's pods get "
                f"no image pull secret from this command. The job still runs against a registry "
                f"that needs no credential; if this cluster's registry does, the pods will say so."
            )
            return False
        logging.info(f"Configuring {CLEANUP_JOB_PULL_SECRET_NAME} for cleanup job pods in "
                     f"namespace {namespace} ...")
        self.kubernetes_utils.create_or_replace_docker_secret(
            namespace=namespace,
            name=CLEANUP_JOB_PULL_SECRET_NAME,
            auth_url=block.get('DOCKER_REGISTRY_AUTH_URL'),
            username=block.get('DOCKER_REGISTRY_USERNAME'),
            password=block.get('DOCKER_REGISTRY_PASSWORD'),
            email=block.get('DOCKER_REGISTRY_EMAIL'),
        )
        return True

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

    def _list_tekton_resource(self, namespace, plural_identifier):
        self._preflight_tekton_api_version()
        objects = self.kubernetes_utils.list_custom_resources(
            namespace=namespace,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier=plural_identifier
        )
        logging.info(f"### Listing {plural_identifier} in namespace: {namespace}")

        if len(objects['items']) < 1:
            logging.info("No resources found.")
            return None

        for object in objects['items']:
            logging.info(f"    {object['metadata']['name']} - created at: {object['metadata']['creationTimestamp']}")

        return objects

    def cleanup_pipeline_runs(self, filter: str):
        runs = self.list_pipeline_runs()
        logging.info(f"Cleaning up pipeline runs in namespace: {self.pipeline_namespace} with filter: {filter}")

        if runs is None:
            logging.info("No pipeline runs found.")
            return None

        for run in runs['items']:
            run_name = run['metadata']['name']
            self.cleanup_pipeline_run(run_name, filter, run_object=run)
        return None

    def cleanup_pipeline_run(self, run_name: str, filter="all", run_object=None):
        should_be_deleted=False
        if run_object is None:
            should_be_deleted = True
        if filter == "all":
            should_be_deleted = True

        if not should_be_deleted and run_object is not None:
            run_condition_type = run_object['status']['conditions'][0]['type']
            run_condition_status = run_object['status']['conditions'][0]['status']
            if run_condition_type == "Succeeded" and run_condition_status == "True":
                should_be_deleted = True
                logging.info(f"Pipeline run {run_name} is in a successful state, deleting it")

        if not should_be_deleted:
            return None

        logging.info(f"Deleting pipeline run {run_name}")
        self.kubernetes_utils.delete_custom_resource(resource_name=run_name,
            namespace=self.pipeline_namespace,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier="pipelineruns"
        )
        return None


    def list_available_pipeline_runs(self) -> None:
        """List available pipeline run files for the given team."""
        logging.info(f"Team '{self.team}' pipeline runs:")
        team_dir = Path(f"{self.config.tekton_team_pipeline_run_dir}")
        team_pipeline_files = list(team_dir.glob("*.yml"))
        if team_dir.exists() and len(team_pipeline_files) > 0:
            for yml_file in team_pipeline_files:
                logging.info(f"  {Path(yml_file).name}")
        else:
            logging.info(f"  No team-specific pipeline runs found for {self.team}")

        logging.info("##############")
        logging.info("Global pipeline runs:")
        global_dir = Path(self.config.tekton_global_pipeline_run_dir)
        if global_dir.exists():
            for yml_file in global_dir.glob("*.yml"):
                logging.info(f"  global/{Path(yml_file).name}")
        else:
            logging.info("  No global pipeline runs found")
        logging.info("##############")

    def check_running_pipelines(self, pipeline_name: str):
        """Check if there are any running instances of the given pipeline."""
        pipeline_runs = self.list_pipeline_runs()
        if not pipeline_runs or 'items' not in pipeline_runs:
            return []

        running_pipelines = []
        for run in pipeline_runs['items']:
            spec = run.get('spec', {})
            pipeline_ref = spec.get('pipelineRef', {})
            status = run.get('status', {}).get('conditions', [{}])[0]

            if (pipeline_ref.get('name') == pipeline_name and
                status.get('reason') == "Running"):
                running_pipelines.append(run['metadata']['name'])

        return running_pipelines

    def run_pipeline(self, pipeline_run_file_identifier: str, allow_parallel_run: bool = False):
        """
        Run a Tekton pipeline using the specified parameters.

        Args:
            pipeline_run_file_identifier: Either a path to the run YAML file (`platform_config/dev/dev1/
                tekton/pipeline-runs/foo-run.yml`) or an identifier for it: a bare name in the team's
                directory (`foo-run.yml`, or `smoke/foo-run.yml`), or `global/` plus a name in the
                global directory. See `_resolve_pipeline_run_file_path`.
            allow_parallel_run: Whether to allow parallel runs of the same pipeline

        Returns:
            The generated `PipelineRun` name on the way out -- what `wait_for_pipeline_run` is asked
            to wait for, and the only record of which run this call started -- or `None` when nothing
            was started: a file that could not be read, a pipeline that is already Running, or a
            cluster that refused the object. The name is generated here and never derived again, so a
            caller that needs it takes it from the return value and never re-computes a postfix.
        """

        # Load configuration values
        env_static = self.config.get('env_static')
        team_static = self.config.get('team_static')

        pipeline_namespace = team_static.get('PIPELINE_NAMESPACE')

        # Print configuration information
        logging.info("#########################")
        logging.info("Loading configuration from platform_config ...")
        logging.info(f"ENVIRONMENT: {self.environment}")
        logging.info(f"TEAM: {self.team}")
        logging.info(f"PIPELINE_NAMESPACE: {pipeline_namespace}")
        logging.info(f"TEKTON_KUBERNETES_STORAGE_CLASS: {env_static.get('TEKTON_KUBERNETES_STORAGE_CLASS')}")
        logging.info("")
        logging.info(f"DOCKER_REGISTRY_BASE_URL: {env_static.get('DOCKER_REGISTRY_BASE_URL')}")
        logging.info(f"TEKTON_CI_IMAGE_NAME: {env_static.get('TEKTON_CI_IMAGE_NAME')}")
        logging.info(f"TEKTON_CI_IMAGE_TAG: {env_static.get('TEKTON_CI_IMAGE_TAG')}")
        logging.info("")
        logging.info(f"AUTOMATION_GIT_URL: {env_static.get('AUTOMATION_GIT_URL')}")
        logging.info(f"AUTOMATION_GIT_PROJECT_NAME: {env_static.get('AUTOMATION_GIT_PROJECT_NAME')}")
        logging.info(f"AUTOMATION_GIT_REVISION: {env_static.get('AUTOMATION_GIT_REVISION')}")
        logging.info(f"AUTOMATION_GIT_SERVER_HOST: {env_static.get('AUTOMATION_GIT_SERVER_HOST')}")
        logging.info(f"AUTOMATION_GIT_SERVER_PORT: {env_static.get('AUTOMATION_GIT_SERVER_PORT')}")
        logging.info(f"AUTOMATION_GIT_SERVER_SSH_USER: {env_static.get('AUTOMATION_GIT_SERVER_SSH_USER')}")
        logging.info("")
        logging.info(f"HELM_DEPLOYER_SERVICE_ACCOUNT_NAME: {team_static.get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')}")
        logging.info("")

        logging.info("#########################")
        logging.info(f"Running pipeline run file {pipeline_run_file_identifier} in {pipeline_namespace} namespace ...")

        pipeline_run_file_path = _resolve_pipeline_run_file_path(pipeline_run_file_identifier,
                                                                 self.config)
        logging.info(f"Running file: {pipeline_run_file_path}")

        # Read the pipeline run file
        try:
            with open(pipeline_run_file_path, 'r') as f:
                pipeline_run_content = f.read()
                pipeline_run_yaml = yaml.safe_load(pipeline_run_content)
        except Exception as e:
            logging.error(f"Error reading pipeline run file: {e}")
            logging.error("A run file is either a path or an identifier: a bare name in "
                          f"{self.config.tekton_team_pipeline_run_dir}, or global/<name>.yml in "
                          f"{self.config.tekton_global_pipeline_run_dir}")
            return None

        # Extract pipeline information
        name_in_file = pipeline_run_yaml['metadata']['name']
        pipeline_name = pipeline_run_yaml['spec']['pipelineRef']['name']
        logging.info(f"Pipeline run name: {name_in_file}")
        logging.info(f"Pipeline run target pipeline: {pipeline_name}")

        # Check for running pipelines
        if not allow_parallel_run:
            running_pipelines = self.check_running_pipelines(pipeline_name)
            if running_pipelines:
                logging.info("##############################")
                logging.info(f"There are running tasks for pipeline {pipeline_name} within the namespace {pipeline_namespace}")
                logging.info(f"Running pipelines: {', '.join(running_pipelines)}")
                logging.info("##############################")
                logging.info("Listing runs for reference")
                pipeline_runs = self.list_pipeline_runs()
                if pipeline_runs and 'items' in pipeline_runs:
                    for run in pipeline_runs['items']:
                        status_reason = "Unknown"
                        if run.get('status', {}).get('conditions'):
                            status_reason = run['status']['conditions'][0].get('reason', 'Unknown')
                        logging.info(f"  {run['metadata']['name']} - Status: {status_reason}")
                logging.info("##############################")
                logging.info("CANCELLED TO AVOID PARALLEL RUN!")
                logging.info("Nothing was applied. Re-run with --parallel if a second concurrent run "
                             "of this pipeline is what you meant.")
                return None

        # Generate pipeline run name with random postfix
        random_postfix = generation.generate_random_postfix()
        pipeline_run_name = f"{name_in_file}-{random_postfix}"
        logging.info(f"Generated pipeline-run name: {pipeline_run_name}")

        # Update the name in the YAML
        pipeline_run_yaml['metadata']['name'] = pipeline_run_name

        generation._replace_placeholders_in_dict(self.config, pipeline_run_yaml)

        # Display the pipeline run
        logging.info("#########################")
        logging.info("Applying pipeline run:")
        logging.info(yaml.dump(pipeline_run_yaml))
        logging.info("#########################")

        # Fail here, once, if this cluster cannot speak v1 at all, rather than after the
        # YAML has been loaded and printed for it.
        self._preflight_tekton_api_version()

        # Apply the pipeline run using KubernetesUtils
        try:
            self.kubernetes_utils.create_or_replace_custom_resource(
                group=self.config.TEKTON_API_GROUP,
                version=self.config.TEKTON_API_VERSION,
                namespace=pipeline_namespace,
                kind="PipelineRun",
                body=pipeline_run_yaml,
                plural_identifier="pipelineruns"
            )

            # List pipeline runs using TektonUtils
            logging.info("Pipeline runs in namespace:")
            self.list_pipeline_runs()
            logging.info("######################")
            logging.info("You can open a tunnel to the tekton ui using:")
            logging.info("uv run kmt tunnel tekton")
            logging.info("######################")
            return pipeline_run_name
        except Exception as e:
            logging.error(f"Error applying pipeline run: {e}")
            return None

    def wait_for_pipeline_run(self, pipeline_run_name: str, namespace: str = None,
                              poll_interval_seconds: float = PIPELINE_RUN_POLL_INTERVAL_SECONDS,
                              timeout_seconds: float = PIPELINE_RUN_TIMEOUT_SECONDS,
                              sleep=None) -> int:
        """Block until the named PipelineRun finishes, or until the bound says it has not.

        This is the half of `tekton_ci/tasks/run-tekton-pipeline.yml` that used to be a bash step:
        a loop that asked the Tekton CLI for the run's status as JSON, slept, and asked again, with no
        bound and no exit condition. It is the same question, asked through the API client instead of
        a binary that had to be installed in the pod, and the one structural improvement is that it
        stops: bash polled forever, and a wedged inner `PipelineRun` held a pod and a namespace
        hostage indefinitely. `timeout_seconds` is the bound (thirty minutes, the owner's number) and
        it exits non-zero when it runs out.

        It asks once per `poll_interval_seconds`, which is the Task's `polling-sleep-time` fed
        straight through. The answer is `status.conditions[0]`, the only thing the controller writes
        that says whether the run is over: `True` is success, `False` is failure and the reason and
        message are printed because they are the whole diagnosis an operator gets, and anything else
        means still running. A run that has been deleted is its own sentence rather than a traceback,
        because that is the one state where waiting is guaranteed to be wrong.

        The API version is not re-asserted here. The waiter is reached from `run_pipeline`, which has
        just written a `PipelineRun` through the same client and already asked the discovery question,
        and re-asking it once per poll buys nothing.

        Args:
            pipeline_run_name: The run to watch, normally what `run_pipeline` returned.
            namespace: Where it lives. Defaults to the team's `PIPELINE_NAMESPACE`.
            poll_interval_seconds: Seconds between reads.
            timeout_seconds: How long to keep asking. Past this, the run is reported as unfinished.
            sleep: What sleeps, for tests that want to drive the loop without a wall clock.
                `time.sleep` is reached through the module, so patching it there works too.

        Returns:
            `0` when the run finished successfully, `1` when it failed, was deleted, or ran past the
            bound.
        """
        namespace = namespace if namespace is not None else self.pipeline_namespace
        sleep_seconds = sleep if sleep is not None else time.sleep
        started = time.monotonic()
        deadline = started + timeout_seconds
        logging.info(f"Waiting for pipeline-run {pipeline_run_name} in namespace {namespace}: "
                     f"asking every {poll_interval_seconds}s, for up to {timeout_seconds}s")

        while True:
            run = self._pipeline_run_or_none(pipeline_run_name, namespace)
            if run is None:
                logging.error(f"PipelineRun {pipeline_run_name} is not in namespace {namespace}. It "
                              "was applied and is gone now, so there is nothing left to report on. "
                              "List what is there with `kmt tekton list`.")
                return 1

            condition = _first_status_condition(run)
            status = condition.get('status')
            reason = condition.get('reason')
            message = condition.get('message')
            elapsed = time.monotonic() - started

            if status == 'True':
                logging.info(f"Pipeline-run {pipeline_run_name} finished successfully after "
                             f"{elapsed:.0f}s: {reason or 'no reason given'}")
                logging.info(message or "")
                return 0
            if status == 'False':
                # `reason` and `message` are the whole diagnosis, so both are carried in the line the
                # operator is left with. `PipelineRunValidationFailed` and `Failed` are two different
                # stories that both say `False` without them.
                logging.error(f"Pipeline-run {pipeline_run_name} failed after {elapsed:.0f}s: "
                              f"{reason or 'no reason given'}: {message or 'no message given'}")
                return 1

            logging.info(f"Pipeline-run {pipeline_run_name} is still running after {elapsed:.0f}s "
                         f"({reason or 'no reason given'}): {message or 'no message given'}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logging.error(f"Pipeline-run {pipeline_run_name} in namespace {namespace} had not "
                              f"finished after {timeout_seconds}s, which is the bound this command "
                              "waits by. It is still running: find it, and what it is stuck on, with "
                              "`kmt tekton list`.")
                return 1
            sleep_seconds(min(poll_interval_seconds, remaining))

    def _pipeline_run_or_none(self, pipeline_run_name: str, namespace: str):
        """One read of one PipelineRun, and `None` for the one status a poll cannot report on.

        A 404 is not a condition the controller writes and not something waiting helps with; every
        other status is passed back untouched, because a permissions failure or an API path that went
        away mid-wait is a problem the operator has to be shown rather than one this loop guesses at.
        """
        try:
            return self.kubernetes_utils.get_custom_resource(
                group=self.config.TEKTON_API_GROUP,
                version=self.config.TEKTON_API_VERSION,
                namespace=namespace,
                name=pipeline_run_name,
                plural_identifier="pipelineruns")
        except client.exceptions.ApiException as error:
            if error.status == 404:
                return None
            raise

