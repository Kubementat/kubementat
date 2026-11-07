"""A run that named no team is refused before it writes, wherever the team is read.

`TektonUtils.__init__` used to be where kubementat said "this command was not given a team". It
said it once, at construction, before the object had touched a cluster or a file — and when P4-6
dissolved the class the refusal had to go somewhere, because it cannot simply go away: four of the
manifests a generator writes carry a team name, every Tekton object is applied into a namespace the
team's `static.json` names, and the cleaner's pod pulls its image with a credential that lives in
the team's encrypted file. A guard is not a policy knob, and a constructor that is deleted without
the guard is a library that stops refusing and starts writing `None`.

So the refusal moved to where the team is *read*, which is what "before it applies anything" means
when there is no construction step any more. Every path below is run against a `Context` built for
no team, with the cluster recorded rather than replaced, and the assertion is the same one in both
directions: the run raised the named refusal, and the record is empty. An empty record is the half
that matters — a test that only asserts the exception would still pass on a flow that created the
namespace, wrote the RBAC and *then* refused, which is exactly what two of these paths did before
this ticket moved the guard.

The second direction these tests hold up is the one a guard that travels too far breaks: a team is
needed by a `team_static` read, a `team_static_encrypted` read, an argv namespace or a name built
out of the team, and by nothing else. `kmt install --no-configure-tekton-pipelines` reads none of
them, so the install's guard is asked inside the branch that does and a run that asks for no
pipeline configuration is not refused for it. The same rule is what `test_the_commands_that_refused_a_team_they_do_not_read.py`
enforces over the whole of `src/`; what is asserted here is the position of the guards that survived,
and that a command which already required a team is refused no further down than it was —
`test_team_option.py` is the file that holds that claim.
"""

from types import SimpleNamespace

import pytest

from cluster_fixture import configuration_only_context
from kubementat import errors
from kubementat.automations.install import kubementat as install_slice
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import generation, runs, triggers
from asset_paths import tekton_ci_path

# The trigger manifests are written by `generate_trigger_configs` and read by `setup_triggers`, so
# the generator test needs an app name and a git host the product has templates for.
APP = "the-app"
PIPELINE = "build-the-app"
TRIGGER_TYPE = "github"


def refuses_by_name(call, sentence):
    """The refusal, and the sentence that says which read it was asked for.

    The sentence is asserted and not just the type, because a team-less `Context` can be refused two
    ways and both raise `TeamNotSuppliedError`: the `Context`'s own guard, and `Config`'s refusal of
    a team-scoped read the guard did not stand in front of. The type cannot tell the two apart, and
    only one of them is the refusal this ticket moved. What separates them is the first clause, which
    names what the code was about to do -- so asserting it says where the run stopped, and a guard
    that quietly disappeared is caught by a sentence that is no longer there.

    `sentence=""` asks for the refusal without naming its first clause, for a path refused by
    `Config`'s own read rather than by a `require_team`; that test asserts the directory instead.
    """
    with pytest.raises(errors.TeamNotSuppliedError) as raised:
        call()
    assert str(raised.value).startswith(sentence), str(raised.value)
    return str(raised.value)


def the_writes(recorded_api):
    """Everything this run changed on the cluster it was pointed at.

    Reads are not what is being counted: a guard that refuses after a `read_namespaced_service` has
    already happened has not done anything wrong, and a claim about writes has to say which half of
    the log it is reading or it is a claim about noise.
    """
    from conftest import is_a_write

    return [method for method, _kwargs in recorded_api.calls if is_a_write(method)]


# ---------------------------------------------------------------------------------------
# the paths that read a team-scoped namespace
# ---------------------------------------------------------------------------------------


def test_starting_a_run_refuses_a_team_it_was_not_given_before_it_applies_one(
        fixture_repo, dry_run_env, recorded_api):
    """`runs.run_pipeline` reads `team_static.PIPELINE_NAMESPACE` on its way to an `apply`.

    The run file is written first — that is the fixture's team directory, laid down for the team
    this fixture normally runs as — so the refusal is asked with a file to run and a cluster to run
    it against. What the run does not get to is the read of that file, the placeholder substitution
    over it, or the `PipelineRun` applied from it.
    """
    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    context = configuration_only_context(fixture_repo.environment, None)

    refuses_by_name(
        lambda: runs.run_pipeline(context, KubernetesUtils(context), context.config,
                                  context.environment, run_file.name),
        "the namespace the Tekton objects live in")

    assert the_writes(recorded_api) == [], recorded_api.as_str()


def test_the_cleanup_job_refuses_before_it_creates_the_namespace_it_prunes_in(
        fixture_repo, dry_run_env, recorded_api):
    """The one path where the refusal used to arrive late rather than never.

    `setup_cleanup_job` reads no `team_static` at all: the team it wants is the
    `team_static_encrypted` block the cleaner's pod pulls its image with, and until the guard moved
    to the function's first line that read was the path's only refusal -- reached one operation in,
    right after the `create_namespace` that makes the namespace the cleaner prunes in. So a
    team-less run did not merely fail late, it failed holding a namespace.

    Hence the record. The mutation that fails this test is deleting the guard, and what it leaves
    behind is one `create_namespace` and a refusal that reads like a different bug -- `Config`
    refusing a team-scoped read rather than the `Context` refusing the run. The sentence and the
    empty record are two halves of one claim, and the record is the half that says *before*.
    """
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

    The RBAC, the webhook Secrets and the team's trigger directory all land in the namespace the
    team's `static.json` names, so a team-less flow has nothing to address. `setup_triggers` is also
    the entry point that takes a raw `Context` for the directory walk, which is why it can ask the
    `Context` for the team itself rather than being handed one.
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
    """The generator commands used to buy their refusal by constructing a class.

    They read `ctx.config` now, and what refuses is the same read they cannot avoid: a generated
    manifest is one team's configuration, so it is written under a team-scoped directory
    (`Config.tekton_team_pipeline_dir`, `..._trigger_dir`, `..._pipeline_run_dir`), and a `Config`
    built for no team refuses that path rather than interpolating `None` into it. That is the
    refusal P4-1 recorded as a debt — `None` in the team fields of four manifests — closed without
    an object standing in front of the call.

    All three are run, because the three write through three different paths and the claim is about
    all of them.
    """
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
    """The install's own collaborators, replaced so that *only* the ordering is observable.

    The same treatment `test_tekton_install_asserts_the_served_api_versions.py` gives the install
    order, for the same reason: the step is a module function now, so the module it is called
    through is the seam, and everything else in `install_kubementat` has to be told not to answer a
    cluster for this to be a test about position rather than about a machine.
    """
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
    monkeypatch.setattr(install_slice, "validate_cluster_prompt", lambda ctx, assume_yes=False: None)
    monkeypatch.setattr(install_slice, "check_cluster_permissions", lambda ctx: None)
    monkeypatch.setattr(install_slice, "_print_install_finish_message", lambda ctx: None)


def test_the_install_refuses_a_team_it_did_not_name_before_the_first_release_is_applied(
        fixture_repo, monkeypatch, dry_run_env):
    """The seam P4-4 reported, and the reason this is a ticket and not a deletion.

    `kmt install` names its team as a required argument, so the only caller that could reach a
    team-less `Context` here was a library one — and what it got was three pinned release manifests
    applied into cluster-wide namespaces, a helmfile apply, and *then* the refusal. Nothing in the
    install's first half reads a team, which is why dropping the constructor on that path was
    reportable drift rather than a fix, and why the guard is asked here: two lines ahead of the
    first apply, inside the branch that goes on to write one team's namespaces, directories and
    Secrets.

    The record is the assertion. With the journal empty there is no apply to point at, and with a
    `TeamNotSuppliedError` raised there is a sentence the operator can act on.
    """
    journal = []
    _install_with_the_writes_recorded(monkeypatch, journal)
    context = configuration_only_context(fixture_repo.environment, None)

    with pytest.raises(errors.TeamNotSuppliedError):
        install_slice.install_kubementat(context)

    assert journal == [], f"the install wrote before it refused: {journal}"


def test_an_install_that_configures_no_pipelines_is_not_refused_for_a_team_it_never_read(
        fixture_repo, monkeypatch, dry_run_env):
    """The other half, because a guard placed too high is a guard that refuses work it cannot do.

    `--no-configure-tekton-pipelines` installs the releases and the helmfile group and touches no
    team-scoped read of any kind. Refusing that run for a team would be the bug this tree's census
    test is named for, so the guard is asked inside the branch that needs the answer, and the
    install of a team-less `Context` that asks for none of that half runs to the end.
    """
    journal = []
    _install_with_the_writes_recorded(monkeypatch, journal)
    context = configuration_only_context(fixture_repo.environment, None)

    install_slice.install_kubementat(context, configure_tekton_pipelines=False)

    assert journal == ["apply", "discovery", "helmfile_apply"], journal
