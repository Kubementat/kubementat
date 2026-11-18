"""Installing Tekton: what the configuration says, and what a refusal to read it looks like.

`tekton_install.install()` is the whole of `kmt install`'s Tekton step: four release manifests from
one base URL, and the ClusterRole that lets a helm deploy deploy. The tests here are the three
branches a wrong answer needs — everything present, the object already there, and something the
configuration was supposed to say and did not.

The shape this file insists on is *fail before the cluster does*. An install that gets halfway and
then discovers its own missing configuration is the one failure mode a cluster install should not
have, because every manifest already sent stays sent. So the missing-configuration case is asserted
as "nothing left this process", not merely as an exception.

The 409 case matters for the same reason in the other direction: kubementat is run against clusters
that already have it, and there the only sane behaviour is to write the same object again rather
than to report that the operator is doing something twice.
"""

import pytest
from cluster_fixture import configuration_only_context
from conftest import is_apply

from kubementat.errors import TektonReleaseVersionNotConfiguredError

# Both names the install is built from moved out with it (P4-4), and the module is imported as a
# module as well as by name: the role write is patched *on* `install` below, and a name imported past
# its home is a second binding that patch would miss.
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
    """The one install every test here runs, and where its arguments now come from.

    The `Context` carries what the class carried before `P4-6` deleted it, and the client is built
    from that `Context` exactly as the constructor built it. Nothing here asks for a team: the
    install reads none, and the refusal a team-less `Context` is given on this path belongs to
    `install_kubementat`, ahead of the first apply rather than behind it.
    """
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    return tekton_install.install(KubernetesUtils(context), context.config, context.environment)


def applied_urls(fake_binaries):
    return [call[-1] for call in fake_binaries.commands()
            if is_apply(call) and call[-1].startswith("https://")]


def test_a_replace_refused_for_a_real_reason_is_not_reported_as_an_upgrade(
        fixture_repo, dry_run_env, recorded_api):
    """409 is the only difference between "it exists" and "you cannot have it".

    A 403 on the replace is a permission problem the operator can fix, and swallowing it would
    leave an install claiming a ClusterRole it was refused. So the exception arrives with its own
    status on it, which is what the uninstall path already promises for the same object.
    """
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
    """The other half, and the one that keeps the fail-before-the-cluster guard whole.

    Absent is not the same fact as *declared null*: a key somebody wrote down and left unanswered
    (`config init` writes that shape for a key no human has answered) is an answer that says nothing,
    and no default may overrule it — that is the override rule and not a gap in it. This is the
    case the guard belongs to: the whole list of manifests is decided from the configuration *before*
    the first command runs, so a configuration that cannot be read is an install that touched nothing.
    """
    fixture_repo.environment_static(**{**PINS, "TEKTON_VERSION_TRIGGERS": None})

    with pytest.raises(TektonReleaseVersionNotConfiguredError) as error:
        install_the_pinned_releases(fixture_repo)

    assert "TEKTON_VERSION_TRIGGERS" in str(error.value)
    assert applied_urls(fake_binaries) == [], "the install sent manifests before checking its pins"
    assert recorded_api.calls == [], "the install wrote a ClusterRole it had no business writing"
