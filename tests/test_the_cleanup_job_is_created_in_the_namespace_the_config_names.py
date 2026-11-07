"""The cleanup job goes where the environment says, and the prune is the one the script wrote.

`setup_tekton_pipelinerun_cleanup_job.sh` is deleted by this commit. What it asked the cluster for
is still reachable — `git show 6d55f7d:scripts/automation/tekton/setup_tekton_pipelinerun_cleanup_job.sh`
prints the file, and the heredocs below are read out of that blob at test time rather than copied
into this file, so the record cannot drift away from the script it came from. A checkout without
history says so instead of passing quietly.

The behaviour this locks down is the one that was broken: the namespace. bash carried
`CRONJOB_NAMESPACE="cluster-cronjobs"` as a constant of its own, so after a namespace rename the
job was created in one place and looked for in another — which is why the harness's
`assert:cleanup-cronjob-exists` and `assert:cleanup-cronjob-scheduled` reported a cluster with no
cronjobs on it. The replacement reads the same key the install deploys into and the uninstall
sweeps, so a rename moves the job, the asserts and the sweep together.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from kubernetes.client.exceptions import ApiException

from cluster_parity import ClusterOperation, cluster_named, compare, operations_from_calls
from conftest import FIXTURE_ENVIRONMENT, RecordedCall
from kubementat.errors import CleanupJobImageNotConfiguredError
# The cleanup job's names and the reader that answers its image moved out of `tekton_utils.py`
# (P4-3) into the module that builds the job, which is where a patch of them has to land.
from kubementat.infrastructure.tekton.runs import (CLEANUP_JOB_CLUSTER_ROLE_BINDING_NAME,
                                                   CLEANUP_JOB_CLUSTER_ROLE_NAME, CLEANUP_JOB_NAME,
                                                   CLEANUP_JOB_SERVICE_ACCOUNT_NAME,
                                                   cleanup_job_image)
from cluster_fixture import configuration_only_cluster, configuration_only_context

# The cluster both sides of this comparison address. The Python side gets it from the `Context` it
# is handed, and the record of the deleted script is declared with the same value: the script
# addressed its cluster through `KUBECONFIG` in the environment, so its argv carried nothing, and
# the record still has to say which cluster it meant.
CLUSTER = configuration_only_cluster(FIXTURE_ENVIRONMENT)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The commit the script was still in. The file is deleted here, so this is a record and not a run.
BLOB_REVISION = "6d55f7d"
SCRIPT_IN_HISTORY = "scripts/automation/tekton/setup_tekton_pipelinerun_cleanup_job.sh"

NAMESPACE = "fixture-cronjobs"
TEAM = "fixture"
CI_IMAGE = "registry.example.test/ci-runner:1.2.3"

# The variables bash expanded into those heredocs. The fixture's `static.json` is written to answer
# exactly these values, so both sides of every comparison below see one configuration.
BASH_VARIABLES = {
    "CRONJOB_NAMESPACE": NAMESPACE,
    "SCHEDULE": "0 23 * * *",
    "NUMBER_OF_JOBS_TO_KEEP": "20",
    "CI_IMAGE": CI_IMAGE,
    "DOCKER_REGISTRY_SECRET_NAME": "docker-registry-secret",
}

# Kinds that live outside a namespace, so bash applied them without `-n`.
CLUSTER_SCOPED = {"clusterrole", "clusterrolebinding"}

_HEREDOC = re.compile(r"<<-?EOF\n(.*?)EOF\n", re.S)


def deleted_script():
    """The script's own text, out of git history. Skips, loudly, if the history is not there."""
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{SCRIPT_IN_HISTORY}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the cleanup script's record is not reachable in this checkout: git show "
                    f"{BLOB_REVISION}:{SCRIPT_IN_HISTORY} exited {result.returncode} "
                    f"({result.stderr.strip()}). The parity below is a comparison, and without "
                    f"the other side of it there is nothing to compare.")
    return result.stdout


def expand_bash_heredoc(text):
    r"""What bash wrote into the heredoc, as text.

    An unquoted `<<EOF` expands `$VAR` and leaves `\$VAR` alone — the escaped ones being the
    variables the *job* expands at run time, inside its own container. Longest names first, so
    `$NUMBER_OF_JOBS_TO_KEEP` does not eat the `$…_PLUS_1` that contains it.
    """
    guard = "\x00DOLLAR\x00"
    text = text.replace("\\$", guard)
    for name in sorted(BASH_VARIABLES, key=len, reverse=True):
        text = text.replace(f"${{{name}}}", BASH_VARIABLES[name])
        text = text.replace(f"${name}", BASH_VARIABLES[name])
    return text.replace(guard, "$")


def the_script_asks():
    """The whole record: namespace, secret, account, patch, roles, job, in that order.

    The Secret arrives as one call carrying its own arguments. bash wrote it through a pipe —
    `create secret … --dry-run=client -o yaml | kubectl apply -f -` — where the first half never
    touches the cluster and the second is the write. One operation is what that is: counting two
    would say bash wrote the Secret twice and Python once, which is a fact about how many lines of
    kubectl ran and not about what the cluster holds.

    The `describe` and the `get` at the end are kept too. They show a cronjob to a human; the
    comparison declares them as bash-only rather than pretend they never happened.
    """
    calls = [RecordedCall("kubectl", ["create", "namespace", NAMESPACE], cwd="/")]
    calls.append(RecordedCall("kubectl", [
        "-n", NAMESPACE, "create", "secret", "docker-registry", "docker-registry-secret",
        "--docker-server=https://registry.example.test",
        "--docker-username=deployer",
        "--docker-password=not-a-password",
        "--docker-email=team@example.test",
        "--dry-run=client", "-o", "yaml"], cwd="/"))

    for document in _HEREDOC.findall(deleted_script()):
        expanded = expand_bash_heredoc(document)
        kind = str(yaml.safe_load(expanded).get("kind", "")).lower()
        argv = ["apply"]
        if kind not in CLUSTER_SCOPED:
            argv += ["-n", NAMESPACE]
        calls.append(RecordedCall("kubectl", argv + ["-f", "-"], cwd="/", stdin=expanded))
        # The account is patched with the pull secret straight after it is created, and that is a
        # separate operation: `apply` of the manifest does not carry `imagePullSecrets`.
        if kind == "serviceaccount":
            calls.append(RecordedCall("kubectl", [
                "-n", NAMESPACE, "patch", "serviceaccount", CLEANUP_JOB_SERVICE_ACCOUNT_NAME,
                "-p", '{"imagePullSecrets":[{"name":"docker-registry-secret"}]}'], cwd="/"))

    return calls


def the_script_reports():
    """The two reads bash made to show its work, reduced the same way everything else is."""
    return operations_from_calls([
        RecordedCall("kubectl", ["-n", NAMESPACE, "describe", "serviceaccount",
                                 CLEANUP_JOB_SERVICE_ACCOUNT_NAME], cwd="/"),
        RecordedCall("kubectl", ["-n", NAMESPACE, "get", "cronjob", CLEANUP_JOB_NAME,
                                 "-o=yaml"], cwd="/"),
    ], cluster=CLUSTER)


def configured_fixture(fixture_repo, namespace=NAMESPACE):
    """The fixture's configuration, with the two keys this job reads spelled the way a team's are.

    The credential block is named `docker-registry-secret` because that is the name bash selected
    on; the fixture's default entry is named something else, and the Python side (unlike bash, which
    sent `null` for a block it did not find) would have skipped the Secret entirely.
    """
    fixture_repo.environment_static(CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE=namespace)
    fixture_repo.team_static_encrypted(DOCKER_REGISTRY_CREDENTIALS=[{
        "NAME": "docker-registry-secret",
        "DOCKER_REGISTRY_AUTH_URL": "https://registry.example.test",
        "DOCKER_REGISTRY_EMAIL": "team@example.test",
        "DOCKER_REGISTRY_PASSWORD": "not-a-password",
        "DOCKER_REGISTRY_USERNAME": "deployer",
    }])


def the_python_setup(fixture_repo, namespace=NAMESPACE):
    from kubementat.infrastructure.tekton import runs
    from kubementat.tekton_utils import TektonUtils

    configured_fixture(fixture_repo, namespace)
    # The same `Context` and the same seven cluster operations, handed over as the arguments the
    # function now takes: what it used to read off the instance it is given. The class still
    # resolves the client and still refuses a run with no team, so it is still built here.
    utils = TektonUtils(configuration_only_context(fixture_repo.environment, fixture_repo.team))
    runs.setup_cleanup_job(utils.kubernetes_utils, utils.config, utils.environment, utils.team)
    return None


def without_self_named_body(operations):
    """One namespace, one claim: this name exists. Both sides then say it the same way.

    bash asked with `kubectl create namespace NAME`, which carries no body to reduce; the client
    asks with a `V1Namespace` whose whole body *is* that name, so it arrives with an extra fact that
    repeats the `name=` the operation already has. Nothing in the harness is claiming two namespaces
    are different because one of them spelled its own name in two places.
    """
    normalised = []
    for operation in operations:
        if operation.kind == "namespace":
            operation = ClusterOperation(operation.verb, operation.kind, operation.namespace,
                                         operation.name, (), cluster=operation.cluster)
        normalised.append(operation)
    return normalised


def test_the_rbac_and_the_job_the_python_writes_are_the_ones_the_script_wrote(fixture_repo,
                                                                              dry_run_env,
                                                                              recorded_api):
    """Nine operations, same kinds, same namespaces, same payloads, same order.

    Order is part of the comparison because these depend on each other: the namespace before
    anything addressed at it, the pull Secret before the account that names it, the account before
    the binding that binds it, and the whole RBAC before the CronJob that starts a pod as it.

    The CronJob payload is compared whole, which is the interesting half: `schedule`, both history
    limits, `concurrencyPolicy: Forbid`, the pod labels, the image and the prune script itself all
    have to agree character for character, because a cleanup job that prunes in a different order
    deletes the wrong runs.
    """
    the_python_setup(fixture_repo)
    compare(without_self_named_body(operations_from_calls(the_script_asks(), cluster=CLUSTER)),
            without_self_named_body(recorded_api.operations()),
            bash_only=the_script_reports(),
            python_only=[ClusterOperation("read", "serviceaccount", NAMESPACE, None, (), cluster=CLUSTER)],
            context="what setup_tekton_pipelinerun_cleanup_job.sh asked the cluster, against what "
                    "`runs.setup_cleanup_job()` asks it now")


def test_the_namespace_comes_from_the_environment_config_and_nowhere_else(fixture_repo,
                                                                          dry_run_env,
                                                                          recorded_api):
    """The job, the account and the binding all land in the one namespace the config names.

    `cluster-cronjobs` is what the template ships, so it is also what the default answers with —
    that is the fallback and not the answer. The next test renames it.
    """
    the_python_setup(fixture_repo)
    namespaced = [operation for operation in recorded_api.operations()
                  if operation.namespace is not None]

    assert namespaced, "the cleanup job set up nothing in a namespace"
    assert {operation.namespace for operation in namespaced} == {NAMESPACE}, namespaced


def test_renaming_the_namespace_moves_the_job_with_it(fixture_repo, dry_run_env, recorded_api):
    """The whole point of reading the key: a rename changes where the job is.

    This is the failure that was red. bash never asked, so it kept creating the job in the
    namespace it had memorised while every assert — and the uninstall sweep — looked in the one the
    operator had just renamed it to.
    """
    the_python_setup(fixture_repo, namespace="nightly-prune")
    written = recorded_api.operations()

    assert {operation.namespace for operation in written if operation.namespace} == \
           {"nightly-prune"}, written
    assert "nightly-prune" in [operation.namespace for operation in written
                               if operation.kind == "cronjob"], written
    assert not [operation for operation in written if operation.namespace == "cluster-cronjobs"], \
        "the job still lands in a namespace the code remembered rather than the one it read"

    # And it is the same accessor every other component uses — there is no second answer here.
    from kubementat.core.config.config import Config

    assert Config(fixture_repo.environment, fixture_repo.team,
                  preload_configs=False).component_namespace(
                      "cluster-cronjobs") == "nightly-prune"


def test_the_job_exists_and_is_the_one_that_prunes(fixture_repo, dry_run_env, recorded_api):
    """One CronJob, named, scheduled, and pointed at the CI image the environment config names."""
    the_python_setup(fixture_repo)
    cron_jobs = [operation for operation in recorded_api.operations()
                 if operation.kind == "cronjob"]

    assert len(cron_jobs) == 1, cron_jobs
    job = cron_jobs[0]
    assert job.verb == "ensure" and job.name == CLEANUP_JOB_NAME and job.namespace == NAMESPACE
    body = json.loads(dict(job.facts)["body"])
    spec = body["spec"]
    assert spec["schedule"] == "0 23 * * *"
    assert spec["successfulJobsHistoryLimit"] == 1
    assert spec["failedJobsHistoryLimit"] == 2
    assert spec["concurrencyPolicy"] == "Forbid"
    template = spec["jobTemplate"]["spec"]["template"]
    assert template["spec"]["serviceAccount"] == CLEANUP_JOB_SERVICE_ACCOUNT_NAME
    assert template["spec"]["containers"][0]["image"] == CI_IMAGE
    assert template["spec"]["containers"][0]["command"][:2] == ["/bin/bash", "-c"]
    assert 'NUMBER_OF_JOBS_TO_KEEP="20"' in template["spec"]["containers"][0]["command"][2]
    assert template["spec"]["serviceAccount"] == CLEANUP_JOB_SERVICE_ACCOUNT_NAME
    assert template["spec"]["containers"][0]["image"] == CI_IMAGE


def test_an_already_existing_job_is_replaced_and_not_reported_as_a_failure(fixture_repo,
                                                                          dry_run_env,
                                                                          recorded_api):
    """The state a re-run finds: everything is already there. That is not an error, and it is not
    a different run.

    Every create answers 409, which is what a cluster that has been running this command since last
    night says. The answer to it is the same object written over the one that is there — the log
    line that says `already exists` is the only difference, and the replaces are what got asked for.
    """
    for method in ("create_namespace", "create_namespaced_secret",
                   "create_namespaced_service_account", "create_cluster_role",
                   "create_cluster_role_binding", "create_namespaced_custom_object"):
        recorded_api.answer(method, ApiException(status=409, reason="AlreadyExists"))

    the_python_setup(fixture_repo)

    written = recorded_api.operations()
    replaces = [operation for operation in written if operation.verb == "ensure"]
    assert {operation.kind for operation in replaces} >= {"cronjob", "serviceaccount",
                                                          "clusterrole", "clusterrolebinding",
                                                          "secret", "namespace"}, replaces


def test_a_missing_image_key_is_an_error_that_names_the_key(fixture_repo):
    """No image, no job — and one sentence saying which of the three keys to go add.

    bash printed `null` for a key that was not configured, made a CronJob whose image was
    `null/null:null`, reported success, and the failure showed up days later as a cronjob history
    that had never had a pod in it.
    """
    with pytest.raises(CleanupJobImageNotConfiguredError) as error:
        cleanup_job_image({"DOCKER_REGISTRY_BASE_URL": "registry.example.test",
                           "TEKTON_CI_IMAGE_TAG": "1.2.3"})

    assert "TEKTON_CI_IMAGE_NAME" in str(error.value)


def test_no_namespace_is_written_into_the_package_that_creates_the_job():
    """The component is named in the code; the namespace is not. That is the whole defect.

    A literal here would be a second answer, and the second answer is what makes a rename only
    half happen.
    """
    # The rule is the package's now, not one file's: the job is created in `runs.py` (P4-3) and the
    # module that used to create it has no job code left in it, so reading that file would be a
    # guard about nothing. Both halves — the bound literal and the accessor — are checked over every
    # module, and a hit names the file it came from.
    from kubementat.core import constants

    namespaces = set(constants.DEFAULT_NAMESPACES.values())
    bound = []
    sources = []
    for module in sorted((REPO_ROOT / "src/kubementat/infrastructure/tekton").glob("*.py")):
        source = module.read_text(encoding="utf-8")
        sources.append(source)
        for line in source.splitlines():
            if line.strip().startswith("#"):
                continue
            literal = re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[\"']([^\"']+)[\"']\s*$", line)
            if literal and literal.group(1) in namespaces:
                bound.append(f"{module.name}: {line.strip()}")

    assert bound == [], "a namespace written into the package that creates this job is a second answer to rename"
    assert "component_namespace" in "\n".join(sources), "it has to come from the same accessor every other "\
        "component namespace comes from"
