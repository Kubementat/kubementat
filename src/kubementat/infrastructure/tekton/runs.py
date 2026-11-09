"""Starting a run, waiting for one, listing them, and tidying up afterwards.

The run half of the split: everything that answers "did this pipeline run, and is it over" — the
write that starts a `PipelineRun`, the loop that asks the controller until it answers, the four
listings, and the CronJob that prunes the runs a cluster has stopped needing. `preflight` is asked
before any of it touches a cluster, and `generation` is asked for the postfix and the placeholder
substitution the applied document is built out of; both edges point down, and nothing here imports
a module that would have to import this one back.

Methods lost `self` the way the rest of the split did, and what they read off the instance is a
parameter now: `kubernetes_utils` and `config` first, then the fields the constructor resolved off a
`Context` (`environment`) and the namespace it read out of `team_static` (`pipeline_namespace`)
where the function needs one. Where the instance value was a *default* rather than a value a caller
could hand over — the waiter's namespace — the read is written out at the point of use, because the
value belongs to `Config` and not to the object that used to exist.
Nothing else in any body changed.

The team refusal is this module's, in two of its functions, and it arrived here with `P4-6` because
that is what deleting the class made necessary. `TektonUtils.__init__` used to refuse a run that
named no team, before it had done anything at all, and when the class went the refusal had to land
where the team is actually read: `run_pipeline` reads the namespace out of `team_static`, and
`setup_cleanup_job` reads the registry credential out of `team_static_encrypted`. Both therefore
take the `Context` first and ask it for the team as their first statement, so a library caller
holding a team-less `Context` is refused before this module creates a namespace, writes a Secret,
or applies a document. Every other function here needs no `Context` for that reason: they list, wait
and prune through arguments that carry no team.

`time` is bound here as a module, and that is not decoration. The wait's clock is patched in the
tests by a dotted string (`…runs.time.monotonic`), and a string patch reaches the loop only if the
loop reads `monotonic` through this module: with `from time import monotonic` here, the patch would
land on a name nobody calls and the bound would be measured against a wall clock, so the test that
says "it stops at thirty minutes" would say nothing at all.

One name is placed here by decision rather than by the plan's own buckets. `list_available_pipeline_runs`
talks to no cluster at all — it globs the team's pipeline-run directory and the global one — and its
one caller is the run command itself, which falls back to listing when it was given no identifier.
That makes it the read-side twin of the `global/` rule `run_pipeline` resolves a bare name with, so
it lives beside the code it mirrors rather than in a bucket that never placed it.
"""

import logging
import time
from pathlib import Path

import yaml

from kubernetes import client

from kubementat import errors
# The split's other modules are reached through the module object and never by name: every seam on
# this path is patched by module object in the tests, and an imported name is a second binding that
# a patch on the module actually running cannot see. `preflight` answers the cluster question this
# path asks before it applies anything; `generation` supplies the postfix and the substitution the
# applied document is built out of.
from kubementat.infrastructure.tekton import generation, preflight


# What `--wait` asks the cluster, and for how long. The interval is the same number the Tekton
# Task's `polling-sleep-time` param carries, so the two are one knob and not two; the bound is the
# owner's thirty minutes, because the bash loop this replaces asked the same question forever, with
# no exit condition at all, which holds a pod open for a run that is never going to finish.
PIPELINE_RUN_POLL_INTERVAL_SECONDS = 3
PIPELINE_RUN_TIMEOUT_SECONDS = 1800


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
      `Config.tekton_team_pipeline_run_dir`, which is what the `kmt tekton generate-pipeline-run`
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


def list_pipelines(kubernetes_utils, config, pipeline_namespace):
    return _list_tekton_resource(kubernetes_utils, config, pipeline_namespace, "pipelines")


def list_tasks(kubernetes_utils, config, pipeline_namespace):
    return _list_tekton_resource(kubernetes_utils, config, pipeline_namespace, "tasks")


def list_taskruns(kubernetes_utils, config, pipeline_namespace):
    return _list_tekton_resource(kubernetes_utils, config, pipeline_namespace, "taskruns")


def list_pipeline_runs(kubernetes_utils, config, pipeline_namespace):
    return _list_tekton_resource(kubernetes_utils, config, pipeline_namespace, "pipelineruns")


def list_all_tekton_resources(kubernetes_utils, config, pipeline_namespace=None):
    """The four kinds, in the order `kmt tekton list` has always printed them.

    The namespace is the team's `PIPELINE_NAMESPACE` when the caller does not name one. That
    default is not a convenience: the class that used to sit in front of this call read the
    namespace off `self` and handed it down, so with the class gone a command that lists has to
    either read `team_static` in its own module — a namespace decision made in a second place,
    which is the defect every namespace row of the census recites — or leave the read here, beside
    the three functions that take it as an argument. It stays here, where `run_pipeline` already
    reads it, and the refusal a team-less `Context` gets is `Config`'s own, ahead of the first
    listing.
    """
    if pipeline_namespace is None:
        pipeline_namespace = config.get('team_static').get('PIPELINE_NAMESPACE')
    list_pipelines(kubernetes_utils, config, pipeline_namespace)
    list_tasks(kubernetes_utils, config, pipeline_namespace)
    list_pipeline_runs(kubernetes_utils, config, pipeline_namespace)
    list_taskruns(kubernetes_utils, config, pipeline_namespace)
    return None


def setup_cleanup_job(ctx, kubernetes_utils, config, environment) -> None:
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

    The `Context` is asked for the team before a single cluster object is touched, and the reason
    it is asked so early is the pull Secret below. That read is the only thing on this path that
    wants a team, and until `P4-6` dissolved `TektonUtils.__init__` it was the only refusal the
    path had -- and it came one operation in, after `create_namespace` had already been asked. So a
    run with no team did not merely fail late, it failed holding a namespace it had just made.
    Asking first costs this command nothing (the `TEAM` line it prints is the same line, read off
    the same `Context`) and takes that object back.

    Raises:
        kubementat.errors.CleanupJobImageNotConfiguredError: If the CI image cannot be named from configuration.
        KeyError: If `cluster-cronjobs` is not a kubementat component (a typo, not a rename).
        kubementat.errors.CommandExecutionError: Never by this method itself — it calls no
            `kubectl`.
        kubernetes.client.exceptions.ApiException: If the cluster refuses an object.
        kubementat.errors.TeamNotSuppliedError: If this `Context` was given no team.
    """
    # Asked first, before the component lookup below, for the reason written in the docstring: the
    # pull Secret is this path's one team read, it is the next thing this function asks `Config`
    # for, and the namespace it is addressed at is asked before it.
    team = ctx.require_team("the credential the cleanup job's pods pull their image with")

    # `cluster-cronjobs` is the component, not the answer: the namespace itself is what the
    # environment's static.json gives back, with `constants.DEFAULT_NAMESPACES` underneath.
    namespace = config.component_namespace("cluster-cronjobs")
    image = cleanup_job_image(config.get('env_static'))

    logging.info("#########################")
    logging.info("Setting up the pipelinerun cleanup job from platform_config ...")
    logging.info(f"ENVIRONMENT: {environment}")
    logging.info(f"TEAM: {team}")
    logging.info(f"CRONJOB_NAMESPACE: {namespace}")
    logging.info(f"CI_IMAGE: {image}")
    logging.info(f"SCHEDULE: {CLEANUP_JOB_SCHEDULE}")
    logging.info(f"NUMBER_OF_JOBS_TO_KEEP: {CLEANUP_JOBS_TO_KEEP}")
    logging.info("#########################")

    # Namespace first, and created rather than checked for: `create_namespace` treats a 409 as
    # the state it wants, so this is a no-op on a cluster where the component is already there
    # and a real creation on one where it is not. bash needed `|| true` to say the same thing.
    kubernetes_utils.create_namespace(namespace)
    has_pull_secret = _setup_cleanup_job_pull_secret(kubernetes_utils, config, namespace)

    logging.info(f"Configuring {CLEANUP_JOB_SERVICE_ACCOUNT_NAME} in namespace "
                 f"{namespace} ...")
    kubernetes_utils.create_or_replace_service_account(
        namespace, CLEANUP_JOB_SERVICE_ACCOUNT_NAME, labels=dict(CLEANUP_JOB_LABELS))

    # The Secret is only half of it: what makes a kubelet use it for this job's pods is the
    # ServiceAccount pointing at it, which is the `patch serviceaccount` the script ran
    # immediately after it created the account. It goes after the account for the same reason.
    if has_pull_secret:
        kubernetes_utils.patch_service_account_with_pull_secrets(
            namespace, CLEANUP_JOB_SERVICE_ACCOUNT_NAME,
            [{"name": CLEANUP_JOB_PULL_SECRET_NAME}])

    logging.info(f"Setting up {CLEANUP_JOB_CLUSTER_ROLE_NAME} ...")
    kubernetes_utils.create_or_replace_cluster_role(
        CLEANUP_JOB_CLUSTER_ROLE_NAME, cleanup_job_cluster_role())

    logging.info(f"Binding {CLEANUP_JOB_CLUSTER_ROLE_NAME} to "
                 f"{CLEANUP_JOB_SERVICE_ACCOUNT_NAME} ...")
    kubernetes_utils.create_or_replace_cluster_role_binding(
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
    kubernetes_utils.create_or_replace_custom_resource(
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


def _setup_cleanup_job_pull_secret(kubernetes_utils, config, namespace: str) -> bool:
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
    credentials = config.get('team_static_encrypted') or {}
    block = None
    for entry in credentials.get('DOCKER_REGISTRY_CREDENTIALS') or []:
        if isinstance(entry, dict) and entry.get('NAME') == CLEANUP_JOB_PULL_SECRET_NAME:
            block = entry
            break
    if block is None:
        logging.info(
            f"No DOCKER_REGISTRY_CREDENTIALS entry named {CLEANUP_JOB_PULL_SECRET_NAME} in "
            f"{config.team_static_encrypted_config_path}, so the cleanup job's pods get "
            f"no image pull secret from this command. The job still runs against a registry "
            f"that needs no credential; if this cluster's registry does, the pods will say so."
        )
        return False
    logging.info(f"Configuring {CLEANUP_JOB_PULL_SECRET_NAME} for cleanup job pods in "
                 f"namespace {namespace} ...")
    kubernetes_utils.create_or_replace_docker_secret(
        namespace=namespace,
        name=CLEANUP_JOB_PULL_SECRET_NAME,
        auth_url=block.get('DOCKER_REGISTRY_AUTH_URL'),
        username=block.get('DOCKER_REGISTRY_USERNAME'),
        password=block.get('DOCKER_REGISTRY_PASSWORD'),
        email=block.get('DOCKER_REGISTRY_EMAIL'),
    )
    return True


def _list_tekton_resource(kubernetes_utils, config, namespace, plural_identifier):
    preflight._preflight_tekton_api_version(kubernetes_utils, config)
    objects = kubernetes_utils.list_custom_resources(
        namespace=namespace,
        group=config.TEKTON_API_GROUP,
        version=config.TEKTON_API_VERSION,
        plural_identifier=plural_identifier
    )
    logging.info(f"### Listing {plural_identifier} in namespace: {namespace}")

    if len(objects['items']) < 1:
        logging.info("No resources found.")
        return None

    for object in objects['items']:
        logging.info(f"    {object['metadata']['name']} - created at: {object['metadata']['creationTimestamp']}")

    return objects


def cleanup_pipeline_runs(kubernetes_utils, config, pipeline_namespace=None, filter=None):
    """Prune the runs of one team's namespace, with the same namespace default `list` uses."""
    if pipeline_namespace is None:
        pipeline_namespace = config.get('team_static').get('PIPELINE_NAMESPACE')
    if filter is None:
        filter = "succeeded"
    runs = list_pipeline_runs(kubernetes_utils, config, pipeline_namespace)
    logging.info(f"Cleaning up pipeline runs in namespace: {pipeline_namespace} with filter: {filter}")

    if runs is None:
        logging.info("No pipeline runs found.")
        return None

    for run in runs['items']:
        run_name = run['metadata']['name']
        cleanup_pipeline_run(kubernetes_utils, config, pipeline_namespace, run_name, filter, run_object=run)
    return None


def cleanup_pipeline_run(kubernetes_utils, config, pipeline_namespace, run_name: str,
                         filter="all", run_object=None):
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
    kubernetes_utils.delete_custom_resource(resource_name=run_name,
        namespace=pipeline_namespace,
        group=config.TEKTON_API_GROUP,
        version=config.TEKTON_API_VERSION,
        plural_identifier="pipelineruns"
    )
    return None


def list_available_pipeline_runs(config, team) -> None:
    """List available pipeline run files for the given team."""
    logging.info(f"Team '{team}' pipeline runs:")
    team_dir = Path(f"{config.tekton_team_pipeline_run_dir}")
    team_pipeline_files = list(team_dir.glob("*.yml"))
    if team_dir.exists() and len(team_pipeline_files) > 0:
        for yml_file in team_pipeline_files:
            logging.info(f"  {Path(yml_file).name}")
    else:
        logging.info(f"  No team-specific pipeline runs found for {team}")

    logging.info("##############")
    logging.info("Global pipeline runs:")
    global_dir = Path(config.tekton_global_pipeline_run_dir)
    if global_dir.exists():
        for yml_file in global_dir.glob("*.yml"):
            logging.info(f"  global/{Path(yml_file).name}")
    else:
        logging.info("  No global pipeline runs found")
    logging.info("##############")


def check_running_pipelines(kubernetes_utils, config, pipeline_namespace, pipeline_name: str):
    """Check if there are any running instances of the given pipeline."""
    pipeline_runs = list_pipeline_runs(kubernetes_utils, config, pipeline_namespace)
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


def run_pipeline(ctx, kubernetes_utils, config, environment, pipeline_run_file_identifier: str,
                 allow_parallel_run: bool = False):
    """
    Run a Tekton pipeline using the specified parameters.

    Args:
        ctx: The `Context` the command resolved. It is asked for the team as this function's first
            statement, because the namespace this run is applied into is the team's
            `PIPELINE_NAMESPACE`, and a run that named no team has no such namespace to run in. That
            is the refusal `TektonUtils.__init__` performed until `P4-6` dissolved the class, and it
            belongs ahead of the file read below rather than behind it.
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
    # The team before the configuration, the configuration before the cluster. This is the one
    # function of the split that reads a namespace out of `team_static` on its way to an `apply`, so
    # it is the function the constructor's refusal had to survive in -- and the only user-visible
    # difference the dissolution makes: the run now says "no team" as its first sentence instead of
    # as the first thing the class did on the way here.
    team = ctx.require_team("the namespace the Tekton objects live in")

    # Load configuration values
    env_static = config.get('env_static')
    team_static = config.get('team_static')

    pipeline_namespace = team_static.get('PIPELINE_NAMESPACE')

    # Print configuration information
    logging.info("#########################")
    logging.info("Loading configuration from platform_config ...")
    logging.info(f"ENVIRONMENT: {environment}")
    logging.info(f"TEAM: {team}")
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
                                                             config)
    logging.info(f"Running file: {pipeline_run_file_path}")

    # Read the pipeline run file
    try:
        with open(pipeline_run_file_path, 'r') as f:
            pipeline_run_content = f.read()
            pipeline_run_yaml = yaml.safe_load(pipeline_run_content)
    except Exception as e:
        logging.error(f"Error reading pipeline run file: {e}")
        logging.error("A run file is either a path or an identifier: a bare name in "
                      f"{config.tekton_team_pipeline_run_dir}, or global/<name>.yml in "
                      f"{config.tekton_global_pipeline_run_dir}")
        return None

    # Extract pipeline information
    name_in_file = pipeline_run_yaml['metadata']['name']
    pipeline_name = pipeline_run_yaml['spec']['pipelineRef']['name']
    logging.info(f"Pipeline run name: {name_in_file}")
    logging.info(f"Pipeline run target pipeline: {pipeline_name}")

    # Check for running pipelines
    if not allow_parallel_run:
        running_pipelines = check_running_pipelines(kubernetes_utils, config, pipeline_namespace, pipeline_name)
        if running_pipelines:
            logging.info("##############################")
            logging.info(f"There are running tasks for pipeline {pipeline_name} within the namespace {pipeline_namespace}")
            logging.info(f"Running pipelines: {', '.join(running_pipelines)}")
            logging.info("##############################")
            logging.info("Listing runs for reference")
            pipeline_runs = list_pipeline_runs(kubernetes_utils, config, pipeline_namespace)
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

    generation._replace_placeholders_in_dict(config, pipeline_run_yaml)

    # Display the pipeline run
    logging.info("#########################")
    logging.info("Applying pipeline run:")
    logging.info(yaml.dump(pipeline_run_yaml))
    logging.info("#########################")

    # Fail here, once, if this cluster cannot speak v1 at all, rather than after the
    # YAML has been loaded and printed for it.
    preflight._preflight_tekton_api_version(kubernetes_utils, config)

    # Apply the pipeline run using KubernetesUtils
    try:
        kubernetes_utils.create_or_replace_custom_resource(
            group=config.TEKTON_API_GROUP,
            version=config.TEKTON_API_VERSION,
            namespace=pipeline_namespace,
            kind="PipelineRun",
            body=pipeline_run_yaml,
            plural_identifier="pipelineruns"
        )

        # The listing that answers the question the apply just raised, through the same
        # `list_pipeline_runs` the `tekton list` command uses.
        logging.info("Pipeline runs in namespace:")
        list_pipeline_runs(kubernetes_utils, config, pipeline_namespace)
        logging.info("######################")
        logging.info("You can open a tunnel to the tekton ui using:")
        logging.info("uv run kmt tunnel tekton")
        logging.info("######################")
        return pipeline_run_name
    except Exception as e:
        logging.error(f"Error applying pipeline run: {e}")
        return None


def wait_for_pipeline_run(kubernetes_utils, config, pipeline_run_name: str, namespace: str = None,
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
    namespace = (namespace if namespace is not None
                 else config.get('team_static').get('PIPELINE_NAMESPACE'))
    sleep_seconds = sleep if sleep is not None else time.sleep
    started = time.monotonic()
    deadline = started + timeout_seconds
    logging.info(f"Waiting for pipeline-run {pipeline_run_name} in namespace {namespace}: "
                 f"asking every {poll_interval_seconds}s, for up to {timeout_seconds}s")

    while True:
        run = _pipeline_run_or_none(kubernetes_utils, config, pipeline_run_name, namespace)
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


def _pipeline_run_or_none(kubernetes_utils, config, pipeline_run_name: str, namespace: str):
    """One read of one PipelineRun, and `None` for the one status a poll cannot report on.

    A 404 is not a condition the controller writes and not something waiting helps with; every
    other status is passed back untouched, because a permissions failure or an API path that went
    away mid-wait is a problem the operator has to be shown rather than one this loop guesses at.
    """
    try:
        return kubernetes_utils.get_custom_resource(
            group=config.TEKTON_API_GROUP,
            version=config.TEKTON_API_VERSION,
            namespace=namespace,
            name=pipeline_run_name,
            plural_identifier="pipelineruns")
    except client.exceptions.ApiException as error:
        if error.status == 404:
            return None
        raise
