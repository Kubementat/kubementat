"""What `kmt install` did is a value now, and this file pins both halves of that sentence."""

import inspect
import logging
from types import SimpleNamespace

from click.testing import CliRunner
from cluster_fixture import configuration_only_context

from cli.commands import kubementat as kubementat_cli
from kubementat.automations.install import kubementat as install_slice
from kubementat.consent import Consent


def _an_install_that_writes_everything(monkeypatch, journal, *, keep_the_finish_message=False):
    """Every step of the install replaced by one journal line, so only its answer is observable."""
    finish = install_slice._print_install_finish_message
    monkeypatch.setattr(install_slice, "check_dependencies_are_installed",
                        lambda dependencies: None)
    monkeypatch.setattr(install_slice, "validate_cluster",
                        lambda ctx, consent=None: journal.append("cluster") or True)
    monkeypatch.setattr(install_slice, "check_cluster_permissions",
                        lambda ctx: journal.append("permissions") or True)
    monkeypatch.setattr(install_slice, "KubernetesUtils", lambda ctx: SimpleNamespace())
    monkeypatch.setattr(install_slice.tekton_install, "install",
                        lambda kubernetes_utils, config, environment: journal.append("tekton"))
    monkeypatch.setattr(install_slice.preflight, "preflight_installed_tekton_api_versions",
                        lambda kubernetes_utils, config: journal.append("discovery"))
    monkeypatch.setattr(install_slice, "HelmUtils", lambda ctx: SimpleNamespace(
        helmfile_apply=lambda **kwargs: journal.append("helmfile_apply")))
    monkeypatch.setattr(install_slice, "setup_pipelines",
                        lambda ctx: journal.append("pipelines"))
    monkeypatch.setattr(install_slice.triggers, "setup_triggers",
                        lambda ctx, kubernetes_utils, config, environment:
                        journal.append("triggers"))
    monkeypatch.setattr(install_slice, "_print_install_finish_message",
                        (lambda ctx: finish(ctx)) if keep_the_finish_message else (lambda ctx: None))


# ---------------------------------------------------------------------------------------------
# the value itself
# ---------------------------------------------------------------------------------------------


def test_the_install_returns_an_answer_and_not_none(fixture_repo, monkeypatch, dry_run_env):
    """The Done-when's first line: the def's signature is a dataclass, and the value is populated.
    """
    journal = []
    _an_install_that_writes_everything(monkeypatch, journal)
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)

    result = install_slice.install_kubementat(context, consent=Consent.assumed())

    assert result is not None, "the install still reports through its log and returns nothing"
    assert isinstance(result, install_slice.InstallResult), type(result)
    assert result.environment == fixture_repo.environment
    assert result.team == fixture_repo.team
    assert result.helmfile_installation_group == "standard"
    assert result.helmfile_label_filter == "group=standard"
    # The two reads that gate the first write are in the value, because they are the two facts an
    # operator cannot re-derive from a cluster that has since been installed onto.
    assert result.cluster_answered is True
    assert result.permissions_granted is True
    # And every write this run was told to make: the journal is the cross-check, so the value is
    # not agreeing with itself.
    assert journal == ["cluster", "permissions", "tekton", "discovery", "helmfile_apply",
                       "pipelines", "triggers"], journal
    assert result.what_was_written() == (
        "the three pinned Tekton releases and the ClusterRole that lets helm deploy",
        "the platform's Helm releases through group=standard",
        "the team's pipeline, task and pipeline-run manifests",
        "the webhook triggers' RBAC, Secrets and generated resources")


def test_the_two_gates_answer_with_the_fact_and_not_with_their_exit():
    """`validate_cluster` and `check_cluster_permissions` return True because everything else leaves.
    """
    returned = inspect.signature(install_slice.validate_cluster).return_annotation
    assert returned is bool, returned
    assert inspect.signature(install_slice.check_cluster_permissions).return_annotation is bool


# ---------------------------------------------------------------------------------------------
# the rendered output, as the terminal reads it
# ---------------------------------------------------------------------------------------------


def test_the_skipped_half_is_printed_as_a_skip_by_the_command_too(monkeypatch,
                                                                  resolvable_environment):
    """The other branch, through the same door: the flag the operator typed is what they read back.
    """
    _an_install_that_writes_everything(monkeypatch, [])

    result = CliRunner().invoke(
        kubementat_cli.install,
        ["dev", "dev1", "--yes", "--no-configure-tekton-pipelines"])

    assert result.exit_code == 0, result.output
    assert "skipped: the Tekton pipeline configuration" in result.output, result.output
    assert "is the command that runs it" in result.output, result.output


def test_the_log_still_carries_the_finish_message_and_the_next_steps(
        fixture_repo, monkeypatch, dry_run_env, caplog):
    """The preserve half of the change-vs-preserve rule, stated as an assertion."""
    _an_install_that_writes_everything(monkeypatch, [], keep_the_finish_message=True)
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)

    with caplog.at_level(logging.INFO):
        install_slice.install_kubementat(context, consent=Consent.assumed())

    assert "Installed kubementat to cluster successfully :D" in caplog.text, caplog.text
    assert "Now you are ready to start making it your own." in caplog.text
    assert "CONFIGURING: Kubementat for environment" in caplog.text
    assert "uv run kmt tekton run-pipeline" in caplog.text, \
        "the next-steps sentences are the part of the log an operator has always read"
