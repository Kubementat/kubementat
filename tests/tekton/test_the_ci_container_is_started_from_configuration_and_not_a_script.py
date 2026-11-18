"""`kmt run-ci-container`, once it is the library calling `kubectl` and not a script."""

import logging

import pytest
from click.testing import CliRunner

from cli.commands.utilities import run_ci_container
from kubementat.automations.ci import CI_CONTAINER_NAME, CiUtils
from kubementat.core.identity.context import Context
from kubementat.errors import CiImageNotConfiguredError

# What the fixture's `static.json` says the CI image is, assembled the way `CiUtils` assembles it.
CI_IMAGE = "registry.example.test/ci-runner:1.2.3"


def container_runs(fake_binaries):
    """The `kubectl run` calls, as argv. Reads are not part of this claim."""
    return [call for call in fake_binaries.commands()
            if call[0] == "kubectl" and "run" in call]


def kubectl_reads(fake_binaries):
    """Every kubectl call that is not the pod. One of them used to be a context read."""
    return [call for call in fake_binaries.commands() if call[0] == "kubectl" and "run" not in call]


def test_the_cluster_the_container_lands_in_is_named_and_never_asked_for(
        fixture_repo, dry_run_env, fake_binaries, caplog):
    """The script printed the context with `set -e` in front of it, so it had to answer."""
    with caplog.at_level(logging.INFO):
        CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).run_ci_container()

    assert [call for call in kubectl_reads(fake_binaries) if "config" in call] == [], \
        "the context read came back, and it is the read that answered for the wrong cluster"
    assert "context: fixture" in caplog.text, caplog.text
    assert "https://" in caplog.text, "the address travels with the name, so the claim is checkable"


def test_an_image_key_the_configuration_omits_names_itself_and_touches_nothing(
        fixture_repo, dry_run_env, fake_binaries):
    """The missing-configuration branch, asserted as "the cluster was never asked"."""
    fixture_repo.environment_static(TEKTON_CI_IMAGE_TAG="")

    with pytest.raises(CiImageNotConfiguredError) as error:
        CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).run_ci_container()

    assert "TEKTON_CI_IMAGE_TAG" in str(error.value)
    assert str(fixture_repo.platform_config / fixture_repo.environment / "static.json") \
        in str(error.value), "the message has to name the file the key belongs in"
    assert fake_binaries.commands() == [], "the command created a pod it could not name the image of"


def test_a_registry_url_is_not_glued_to_the_image_name_twice(fixture_repo, dry_run_env,
                                                             fake_binaries):
    """`https://registry/` + `/ci-runner` is `https://registry//ci-runner`, and that is a bad image.
    """
    fixture_repo.environment_static(DOCKER_REGISTRY_BASE_URL="registry.example.test/")

    assert CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).ci_image() \
        == "registry.example.test/ci-runner:1.2.3"


def test_the_command_is_the_reachable_path_and_takes_the_environment_and_the_team(
        fixture_repo, dry_run_env, fake_binaries):
    """`kmt run-ci-container dev fixture` is still how an operator runs this."""
    result = CliRunner().invoke(run_ci_container,
                                [fixture_repo.environment, fixture_repo.team],
)

    assert result.exit_code == 0, result.output
    run = container_runs(fake_binaries)[0]
    assert run[run.index("run") + 1:run.index("run") + 4] == \
        [CI_CONTAINER_NAME, "-n", fixture_repo.team]
