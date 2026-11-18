"""Installing Tekton: what the configuration says, and what a refusal to read it looks like."""

import pytest
from cluster_fixture import configuration_only_context
from conftest import is_apply

from kubementat.errors import TektonReleaseVersionNotConfiguredError

# Imported as a module too: the role write is patched *on* `install`, and a name imported elsewhere
# would be a second binding the patch misses.
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import install as tekton_install

# The fixture's configuration does not ship Tekton versions (they are product pins, not fixture
# values), so every test that expects an install to run has to write them first.
VERSION_KEYS = ("TEKTON_VERSION_PIPELINE", "TEKTON_VERSION_TRIGGERS", "TEKTON_VERSION_DASHBOARD")
PINS = dict(zip(VERSION_KEYS, ("1.17.1", "0.37.2", "0.73.0")))

NAMESPACE = "tekton-pipelines"


def install(fixture_repo, **overrides):
    """`tekton_install.install()` against the fixture's configuration, with the pins in place."""
    fixture_repo.environment_static(**{**PINS, **overrides})
    install_the_pinned_releases(fixture_repo)


def install_the_pinned_releases(fixture_repo):
    """The one install every test here runs, and where its arguments now come from."""
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    return tekton_install.install(KubernetesUtils(context), context.config, context.environment)


def applied_urls(fake_binaries):
    return [call[-1] for call in fake_binaries.commands()
            if is_apply(call) and call[-1].startswith("https://")]


def test_a_replace_refused_for_a_real_reason_is_not_reported_as_an_upgrade(
        fixture_repo, dry_run_env, recorded_api):
    """409 is the only difference between "it exists" and "you cannot have it"."""
    from kubernetes import client

    recorded_api.answer("create_cluster_role",
                        client.exceptions.ApiException(status=409, reason="already exists"))
    recorded_api.answer("replace_cluster_role",
                        client.exceptions.ApiException(status=403, reason="forbidden"))

    with pytest.raises(client.exceptions.ApiException) as error:
        install(fixture_repo)

    assert error.value.status == 403


def test_a_release_version_declared_null_stops_the_install_before_anything_is_sent(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The other half, and the one that keeps the fail-before-the-cluster guard whole."""
    fixture_repo.environment_static(**{**PINS, "TEKTON_VERSION_TRIGGERS": None})

    with pytest.raises(TektonReleaseVersionNotConfiguredError) as error:
        install_the_pinned_releases(fixture_repo)

    assert "TEKTON_VERSION_TRIGGERS" in str(error.value)
    assert applied_urls(fake_binaries) == [], "the install sent manifests before checking its pins"
    assert recorded_api.calls == [], "the install wrote a ClusterRole it had no business writing"
