"""The cleanup cronjob goes where the environment says, and it prunes what it is told to prune.

The job's namespace is read from the same key the install deploys into and the uninstall sweeps, so
renaming it moves the job, the lane's `cleanup-cronjob-exists` assert and the teardown together. A
namespace carried as a constant in the job's own definition is the bug this file exists for: the job
was created in one place and looked for in another, and the lane reported a cluster with no cronjobs
on it.
"""

import json
import re
from pathlib import Path

import pytest
from kubernetes.client.exceptions import ApiException

from kubementat.errors import CleanupJobImageNotConfiguredError
# The cleanup job's names and the reader that answers its image moved out of `tekton_utils.py`
# (P4-3) into the module that builds the job, which is where a patch of them has to land.
from kubementat.infrastructure.tekton.runs import (CLEANUP_JOB_NAME,
                                                   CLEANUP_JOB_SERVICE_ACCOUNT_NAME,
                                                   cleanup_job_image)
from cluster_fixture import configuration_only_context

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    from kubementat.infrastructure.kubernetes import KubernetesUtils
    from kubementat.infrastructure.tekton import runs

    configured_fixture(fixture_repo, namespace)
    # The same `Context` and the same seven cluster operations, handed over as the arguments the
    # function now takes: what it used to read off the instance it was given is read off the
    # `Context`. The team is asked for there too -- `setup_cleanup_job` refuses a run with no team
    # before it creates the namespace, not five operations later on the way to the pull Secret.
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    runs.setup_cleanup_job(context, KubernetesUtils(context), context.config, context.environment)
    return None


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
