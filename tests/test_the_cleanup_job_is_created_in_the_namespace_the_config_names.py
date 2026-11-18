"""The cleanup cronjob goes where the environment says, and it prunes what it is told to prune.

The job's namespace is read from the same key the install deploys into and the uninstall sweeps, so
renaming it moves the job, the lane's `cleanup-cronjob-exists` assert and the teardown together. A
namespace carried as a constant in the job's own definition is the bug this file exists for: the job
was created in one place and looked for in another, and the lane reported a cluster with no cronjobs
on it.
"""

import re
from pathlib import Path

import pytest
from kubernetes.client.exceptions import ApiException

from kubementat.errors import CleanupJobImageNotConfiguredError
# The cleanup job's names and the reader that answers its image moved out of `tekton_utils.py`
# (P4-3) into the module that builds the job, which is where a patch of them has to land.
from kubementat.infrastructure.tekton.runs import (cleanup_job_image)
from cluster_fixture import configuration_only_context

REPO_ROOT = Path(__file__).resolve().parent.parent


NAMESPACE = "fixture-cronjobs"
TEAM = "fixture"
CI_IMAGE = "registry.example.test/ci-runner:1.2.3"


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
