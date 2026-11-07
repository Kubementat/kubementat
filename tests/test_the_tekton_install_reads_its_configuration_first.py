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

from kubementat import errors
from kubementat.infrastructure import shell
from kubementat.errors import TektonReleaseVersionNotConfiguredError
# Both names the install is built from moved out with it (P4-4), and the module is imported as a
# module as well as by name: the role write is patched *on* `install` below, and a name imported past
# its home is a second binding that patch would miss.
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import install as tekton_install
from kubementat.infrastructure.tekton.install import (HELM_DEPLOYER_CLUSTER_ROLE_NAME,
                                                      TEKTON_RELEASE_BASE_URL)
from conftest import is_apply
from cluster_fixture import configuration_only_context

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


def test_a_full_configuration_installs_all_three_releases_and_the_role(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The happy path, in one place: four fetches, one ClusterRole write, nothing else.

    `create_cluster_role` rather than `replace_…` because this is a cluster that did not have it,
    which is the branch the create takes and the only branch a fresh install can be on.
    """
    install(fixture_repo)

    assert applied_urls(fake_binaries) == [
        f"{TEKTON_RELEASE_BASE_URL}/pipeline/previous/v{PINS['TEKTON_VERSION_PIPELINE']}/release.yaml",
        f"{TEKTON_RELEASE_BASE_URL}/dashboard/previous/v{PINS['TEKTON_VERSION_DASHBOARD']}/release-full.yaml",
        f"{TEKTON_RELEASE_BASE_URL}/triggers/previous/v{PINS['TEKTON_VERSION_TRIGGERS']}/release.yaml",
        f"{TEKTON_RELEASE_BASE_URL}/triggers/previous/v{PINS['TEKTON_VERSION_TRIGGERS']}/interceptors.yaml",
    ]
    # Two calls, and only two: the ClusterRole write, and the one read that answers "is the
    # webhook reachable yet" once the cluster says yes. The wait is not allowed to poll a healthy
    # cluster, so a second endpoint read here would be a regression in the primitive, not in the
    # test.
    assert [method for method, _kwargs in recorded_api.calls] == [
        "create_cluster_role", "read_namespaced_endpoints"]
    _method, write = recorded_api.calls[0]
    body = write["body"].to_dict()
    assert write["body"].metadata.name == HELM_DEPLOYER_CLUSTER_ROLE_NAME
    assert body["metadata"]["labels"] == {"managed-by": "kubementat"}


def test_an_upgrade_is_the_same_write_as_the_install(
        fixture_repo, dry_run_env, recorded_api, fake_binaries):
    """The branch every rerun takes: the role is already there, so replace it.

    `create_or_replace_cluster_role` swallows only 409, so the upgrade path is one write of the
    same object — the equivalent of what `kubectl apply` did for the whole script, and the reason
    there is no `install`-versus-`upgrade` argument anywhere in this code.
    """
    from kubernetes import client

    recorded_api.answer("create_cluster_role",
                        client.exceptions.ApiException(status=409, reason="already exists"))

    install(fixture_repo)

    methods = [method for method, _kwargs in recorded_api.calls]
    assert methods == ["create_cluster_role", "replace_cluster_role", "read_namespaced_endpoints"], \
        "an upgrade should write the role once, not recreate it"
    # The replace is the second call, not the last: the endpoint read that follows it is the
    # webhook the install waits on, and pointing at the tail would read the wrong object.
    assert dict(recorded_api.calls[1][1])["name"] == HELM_DEPLOYER_CLUSTER_ROLE_NAME
    assert len(applied_urls(fake_binaries)) == 4


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


def test_the_role_is_written_after_the_control_plane_it_authorises(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """Order, not decoration: the role is useless before the controller, and the script knew that.

    bash put the `kubectl apply -f -` of the role between the Pipelines release and the Dashboard.
    This keeps that position explicitly, because a refactor that moved it to the end would still
    pass every other test here and would leave a cluster running a controller whose bindings point
    at a role that was never written.
    """
    journal = []
    fixture_repo.environment_static(**PINS)
    run_the_command = shell.run
    write_the_role = tekton_install._install_helm_deployer_cluster_role

    def recording_run(argv, **kwargs):
        journal.append("apply:" + argv[-1])
        return run_the_command(argv, **kwargs)

    def recording_role(kubernetes_utils):
        journal.append("clusterrole")
        write_the_role(kubernetes_utils)

    # Both patches are on `install`, which is what runs this now. The first is a dotted string that
    # reaches `shell.run` only because `install.py` binds `shell` as a module; the second is the role
    # write, which stopped being a bound method in P4-4 and so has to be patched as the module
    # function it is.
    monkeypatch.setattr("kubementat.infrastructure.tekton.install.shell.run", recording_run)
    monkeypatch.setattr(tekton_install, "_install_helm_deployer_cluster_role", recording_role)

    install_the_pinned_releases(fixture_repo)

    # Reached, and reached once. The position assertion below cannot tell "written once here" from
    # "written twice here", so this is the half that says the substitute is load-bearing.
    assert journal.count("clusterrole") == 1, journal

    role_at = journal.index("clusterrole")
    fetched = [index for index, entry in enumerate(journal) if entry.startswith("apply:")]
    assert len(fetched) == 4, journal
    assert fetched[1] > role_at > fetched[0], \
        f"the role was written at position {role_at} of {journal}"


def test_a_release_version_the_configuration_omits_installs_the_version_constants_carries(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The key is *gone* from the file, and that is not an unreadable configuration.

    It used to be: bash read these keys with `jq -r`, so an absent key became the literal string
    `null` in a URL and the install failed as a 404 nobody could name; the first fix refused to
    install anything at all. What changed is where the answer comes from when no layer has it
    — `constants.DEFAULT_STATIC_ANSWERS`, the same `0.37.2` the shipped template carried — so the
    install runs, and the URL it builds is the one the template's tree built. That is the whole
    claim of an L0, and it is why deleting `initialize` is now possible: a tree that typed nothing
    installs the releases kubementat was built for.
    """
    pins = dict(PINS)
    del pins["TEKTON_VERSION_TRIGGERS"]
    fixture_repo.environment_static(**pins)

    install_the_pinned_releases(fixture_repo)

    assert f"{TEKTON_RELEASE_BASE_URL}/triggers/previous/v{PINS['TEKTON_VERSION_TRIGGERS']}/release.yaml" \
        in applied_urls(fake_binaries), \
        "an omitted pin is answered by constants.py, not by nothing"


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


def test_a_release_version_present_but_empty_is_treated_as_missing(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """`""` is a value a YAML editor will happily produce, and it still builds no URL.

    Treating empty as present is how `…/previous/v//release.yaml` happens, which is a 404 with a
    nicer shape and no explanation.
    """
    fixture_repo.environment_static(**{**PINS, "TEKTON_VERSION_DASHBOARD": "   "})

    with pytest.raises(TektonReleaseVersionNotConfiguredError) as error:
        install_the_pinned_releases(fixture_repo)

    assert "TEKTON_VERSION_DASHBOARD" in str(error.value)
    assert applied_urls(fake_binaries) == []


def test_kubectl_refusing_a_manifest_is_the_operator_saying_no(fixture_repo, dry_run_env,
                                                               fake_binaries, recorded_api):
    """A non-zero exit ends the install; it does not become a line in a log.

    `shell.run` raises, and the point of asserting this here is that the failure is *positional*:
    the release that was refused was the one that came third, so the two before it are on the
    cluster and the role is not. That is what the deleted script's `set -e` did too, and what a
    Python path that logged-and-continued would not.
    """
    fake_binaries.responses.joinpath("kubectl.exit").write_text("1")

    with pytest.raises(errors.CommandExecutionError) as error:
        install(fixture_repo)

    assert error.value.result.returncode == 1
    assert len(applied_urls(fake_binaries)) == 1, \
        "the install kept fetching after the cluster said no"
    assert recorded_api.calls == [], "the role was written after the release it authorises failed"
