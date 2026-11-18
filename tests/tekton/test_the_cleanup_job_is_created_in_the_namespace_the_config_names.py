"""The cleanup cronjob goes where the environment says, and it prunes what it is told to prune."""


import pytest
from cluster_fixture import configuration_only_context
from kubernetes.client.exceptions import ApiException

from kubementat.errors import CleanupJobImageNotConfiguredError

from kubementat.infrastructure.tekton.runs import cleanup_job_image

NAMESPACE = "fixture-cronjobs"
TEAM = "fixture"
CI_IMAGE = "registry.example.test/ci-runner:1.2.3"


def configured_fixture(fixture_repo, namespace=NAMESPACE):
    """The fixture's configuration, with the two keys this job reads spelled the way a team's are.
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
    """The state a re-run finds: everything is already there. That is not an error, and it is not a
    different run.
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
    """No image, no job — and one sentence saying which of the three keys to go add."""
    with pytest.raises(CleanupJobImageNotConfiguredError) as error:
        cleanup_job_image({"DOCKER_REGISTRY_BASE_URL": "registry.example.test",
                           "TEKTON_CI_IMAGE_TAG": "1.2.3"})

    assert "TEKTON_CI_IMAGE_NAME" in str(error.value)
