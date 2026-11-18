"""A run that named no team is refused before it writes, wherever the team is read."""

from types import SimpleNamespace

import pytest
from asset_paths import tekton_ci_path
from cluster_fixture import configuration_only_context

from kubementat import errors
from kubementat.automations.install import kubementat as install_slice
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import generation, runs, triggers

# The trigger manifests are written by `generate_trigger_configs` and read by `setup_triggers`, so
# the generator test needs an app name and a git host the product has templates for.
APP = "the-app"
PIPELINE = "build-the-app"
TRIGGER_TYPE = "github"


def refuses_by_name(call, sentence):
    """The refusal, and the sentence that says which read it was asked for."""
    with pytest.raises(errors.TeamNotSuppliedError) as raised:
        call()
    assert str(raised.value).startswith(sentence), str(raised.value)
    return str(raised.value)


def the_writes(recorded_api):
    """Everything this run changed on the cluster it was pointed at."""
    from conftest import is_a_write

    return [method for method, _kwargs in recorded_api.calls if is_a_write(method)]


# ---------------------------------------------------------------------------------------
# the paths that read a team-scoped namespace
# ---------------------------------------------------------------------------------------


def test_starting_a_run_refuses_a_team_it_was_not_given_before_it_applies_one(
        fixture_repo, dry_run_env, recorded_api):
    """`runs.run_pipeline` reads `team_static.PIPELINE_NAMESPACE` on its way to an `apply`."""
    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    context = configuration_only_context(fixture_repo.environment, None)

    refuses_by_name(
        lambda: runs.run_pipeline(context, KubernetesUtils(context), context.config,
                                  context.environment, run_file.name),
        "the namespace the Tekton objects live in")

    assert the_writes(recorded_api) == [], recorded_api.as_str()


def test_the_cleanup_job_refuses_before_it_creates_the_namespace_it_prunes_in(
        fixture_repo, dry_run_env, recorded_api):
    """The one path where the refusal used to arrive late rather than never."""
    fixture_repo.environment_static()
    context = configuration_only_context(fixture_repo.environment, None)

    refuses_by_name(
        lambda: runs.setup_cleanup_job(context, KubernetesUtils(context), context.config,
                                       context.environment),
        "the credential the cleanup job's pods pull their image with")

    assert the_writes(recorded_api) == [], recorded_api.as_str()


def test_the_trigger_setup_refuses_before_it_writes_any_rbac(fixture_repo, dry_run_env,
                                                             recorded_api):
    """`setup_triggers` is the path `kmt install` reaches last, and the one that needs both halves.
    """
    context = configuration_only_context(fixture_repo.environment, None)

    refuses_by_name(
        lambda: triggers.setup_triggers(context, KubernetesUtils(context), context.config,
                                        context.environment),
        "the namespace the Tekton objects live in")

    assert recorded_api.operations() == [], recorded_api.as_str()


# ---------------------------------------------------------------------------------------
# the generators, which refuse through the team-scoped path they write through
# ---------------------------------------------------------------------------------------


def test_no_generator_writes_the_team_of_a_run_that_named_none(fixture_repo, dry_run_env):
    """The generator commands used to buy their refusal by constructing a class."""
    context = configuration_only_context(fixture_repo.environment, None)
    calls = [
        # All five git coordinates, because the deploy generator refuses a missing one by name
        # before it reads a path, and that refusal would be the test's subject instead of this one.
        lambda: generation.generate_deploy_pipeline(
            context.config, APP, git_url="ssh://git.example.test/the-app.git",
            git_project_name=APP, git_server_host="git.example.test",
            git_server_port="22", git_server_ssh_user="git"),
        lambda: generation.generate_trigger_configs(context.config, APP, pipeline_name=PIPELINE,
                                                    trigger_type=TRIGGER_TYPE),
        lambda: generation.generate_pipeline_run(
            context.config, str(tekton_ci_path("pipelines", "hello-world-pipeline.yml"))),
    ]

    for call in calls:
        refused = refuses_by_name(call, "")
        assert "<team>" in refused, refused

    # Nothing on disk, either: a directory is what the generators create before they write, and the
    # refusal lands ahead of both.
    team_dirs = list((fixture_repo.platform_config / fixture_repo.environment).glob("*/tekton"))
    assert team_dirs == [], f"a generator wrote into {team_dirs}"


# ---------------------------------------------------------------------------------------
# the install path, which is the one that applied three releases on the way to a refusal
# ---------------------------------------------------------------------------------------


def _install_with_the_writes_recorded(monkeypatch, journal):
    """The install's own collaborators, replaced so that *only* the ordering is observable."""
    monkeypatch.setattr(install_slice.tekton_install, "install",
                        lambda kubernetes_utils, config, environment: journal.append("apply"))
    monkeypatch.setattr(install_slice.preflight, "preflight_installed_tekton_api_versions",
                        lambda kubernetes_utils, config: journal.append("discovery"))
    monkeypatch.setattr(install_slice, "KubernetesUtils", lambda ctx: SimpleNamespace())
    monkeypatch.setattr(install_slice, "HelmUtils",
                        lambda ctx: SimpleNamespace(
                            helmfile_apply=lambda **kwargs: journal.append("helmfile_apply")))
    monkeypatch.setattr(install_slice, "check_dependencies_are_installed",
                        lambda dependencies: None)
    monkeypatch.setattr(install_slice, "validate_cluster", lambda ctx, consent=None: True)
    monkeypatch.setattr(install_slice, "check_cluster_permissions", lambda ctx: True)
    monkeypatch.setattr(install_slice, "_print_install_finish_message", lambda ctx: None)


def test_the_install_refuses_a_team_it_did_not_name_before_the_first_release_is_applied(
        fixture_repo, monkeypatch, dry_run_env):
    """No generator writes a team into a run that named none."""
    journal = []
    _install_with_the_writes_recorded(monkeypatch, journal)
    context = configuration_only_context(fixture_repo.environment, None)

    with pytest.raises(errors.TeamNotSuppliedError):
        install_slice.install_kubementat(context)

    assert journal == [], f"the install wrote before it refused: {journal}"


def test_an_install_that_configures_no_pipelines_is_not_refused_for_a_team_it_never_read(
        fixture_repo, monkeypatch, dry_run_env):
    """The other half, because a guard placed too high is a guard that refuses work it cannot do."""
    journal = []
    _install_with_the_writes_recorded(monkeypatch, journal)
    context = configuration_only_context(fixture_repo.environment, None)

    install_slice.install_kubementat(context, configure_tekton_pipelines=False)

    assert journal == ["apply", "discovery", "helmfile_apply"], journal
