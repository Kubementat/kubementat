"""What `kmt install` did is a value now, and this file pins both halves of that sentence.

`install_kubementat` used to return `None` and describe itself in 113 `logging.` calls. That is not
a report, it is a transcript: whoever ran the command could scroll it, and whoever called the
function had nothing to branch on, print, or hand to a test. The shape being copied is
`automations/routing/routing.py`'s `RoutingInstallResult` — a frozen dataclass with a `summary()`
the command layer prints and a `command()` that spells the fix — and what this file holds is the
contract that copy created:

* **the def answers with a value** (`InstallResult`, and the annotation says so, so a reverter is a
  `TypeError` at the call and not a quieter thing);
* **the CLI prints it** — and specifically prints *the rendered output*, which is asserted from the
  real `kmt install` command line and not from a stub that returns a prepared object, because a
  command that echoes its own argument instead of the library's answer would pass a test built the
  other way;
* **the operator's exit code and the log did not move.** The finish message, the two option lines
  `kmt install` has always printed before it calls anything, and the refusal sentences are all
  still here, because plan §9 says logs may be demoted and never removed. A test that only checks
  the new lines is a test that would let the old ones be deleted.

The stubs are the ones every install-order test in this tree uses: the steps are module functions
now, so the module the code calls them through is the seam, and replacing them is what makes the
install runnable without a cluster.
"""

import inspect
import logging
from types import SimpleNamespace

from click.testing import CliRunner

from cli.commands import kubementat as kubementat_cli
from kubementat.automations.install import kubementat as install_slice
from kubementat.consent import Consent
from cluster_fixture import configuration_only_context


def _an_install_that_writes_everything(monkeypatch, journal, *, keep_the_finish_message=False):
    """Every step of the install replaced by one journal line, so only its answer is observable.

    Nothing here stands in for a *decision* — the gates, the writes and the skip branch are the
    code's own, and each appends its name as it runs. The finish message is silenced by default
    because it is twelve `logging.info` lines of next steps and no test here reads them; pass
    `keep_the_finish_message=True` when the claim under test is about the log stream, which is the
    one claim a silenced message would pass vacuously.
    """
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

    The annotation is asserted as well as the instance, because a `None`-returning body wearing a
    dataclass annotation is a defect a caller cannot see until it is a `AttributeError` two frames
    up in someone else's code.
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


def test_the_return_annotation_is_a_dataclass_and_not_none():
    """`-> None` on this def is the item's own failure mode, so it is grepped as a contract.

    Checked through `inspect` and not a string search, because the annotation is what a caller and
    a type checker read, and because the rest of the module still legitimately contains functions
    that return nothing (`_print_install_finish_message` prints, it does not answer).
    """
    returned = inspect.signature(install_slice.install_kubementat).return_annotation
    assert returned is install_slice.InstallResult, returned
    assert install_slice.InstallResult.__dataclass_fields__, \
        "the copy of `RoutingInstallResult` is not a dataclass, so it is not the shape the plan named"


def test_the_two_gates_answer_with_the_fact_and_not_with_their_exit():
    """`validate_cluster` and `check_cluster_permissions` return True because everything else leaves.

    Each has exactly one way out that is not a value — `exit(1)` for an unreachable cluster, a named
    refusal for a denied verb — and a caller that reaches the return value wants the fact that the
    read happened. Pinned here rather than only at the call site so a future gate that "returns
    None as before" fails where it is defined.
    """
    returned = inspect.signature(install_slice.validate_cluster).return_annotation
    assert returned is bool, returned
    assert inspect.signature(install_slice.check_cluster_permissions).return_annotation is bool


def test_a_run_asked_to_skip_the_pipeline_half_says_so_and_names_the_fix(
        fixture_repo, monkeypatch, dry_run_env):
    """`--no-configure-tekton-pipelines` is a decision, and it belongs in the answer.

    Without this the two installs print the same sentence and one of them wrote a quarter more.
    The `command()` half is routing's own idiom: the summary points at the command that changes
    the outcome instead of describing the flag it was told.
    """
    journal = []
    _an_install_that_writes_everything(monkeypatch, journal)
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)

    result = install_slice.install_kubementat(
        context, configure_tekton_pipelines=False, consent=Consent.assumed())

    assert journal == ["cluster", "permissions", "tekton", "discovery", "helmfile_apply"], journal
    assert result.pipelines_configured is False
    assert result.triggers_configured is False
    assert result.skipped, "the half that did not run is not in the answer"
    summary = result.summary()
    assert "skipped: the Tekton pipeline configuration" in summary, summary
    assert f"kmt install {fixture_repo.environment} {fixture_repo.team}" in summary, summary


# ---------------------------------------------------------------------------------------------
# the rendered output, as the terminal reads it
# ---------------------------------------------------------------------------------------------


def test_kmt_install_prints_the_answer_to_the_terminal(monkeypatch, resolvable_environment):
    """The rendered output is asserted from the command line, which is the only place it is real.

    The whole install runs here — argument parsing, the `@kmt_context` door, the library's own
    branch choices — with the steps stubbed the way the ordering tests stub them. A test that
    replaced `install_kubementat` itself could only prove that the CLI knows how to call `echo`.
    """
    _an_install_that_writes_everything(monkeypatch, [])

    result = CliRunner().invoke(kubementat_cli.install, ["dev", "dev1", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Helmfile installation group: standard" in result.output, \
        "the two lines this command printed before it called anything are still printed"
    assert "Configure tekton pipelines: True" in result.output
    assert "kubementat installed for dev (dev1)" in result.output, result.output
    assert "group=standard" in result.output
    assert "the cluster answered, and this service account may create namespaces" in \
           result.output, result.output
    assert "--yes answered the confirmation" in result.output, result.output
    assert "skipped:" not in result.output, \
        "a run that configured the whole install is reporting a skip it never asked for"


def test_the_skipped_half_is_printed_as_a_skip_by_the_command_too(monkeypatch,
                                                                  resolvable_environment):
    """The other branch, through the same door: the flag the operator typed is what they read back.

    Both branches are asserted because both branches get read — a summary that said "installed" for
    a run that wrote a quarter of it would be the worse of the two ways to be wrong.
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
    """The preserve half of §9's change-vs-preserve, stated as an assertion.

    Nothing was deleted to make room for the value: the install's finish message — which is where
    the next-steps sentences have lived since this was a bash script — is still written, still at
    INFO, and still says the words. The value prints what was written; the log keeps saying what to
    do next, and both claims are only honest if neither one moves.
    """
    _an_install_that_writes_everything(monkeypatch, [], keep_the_finish_message=True)
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)

    with caplog.at_level(logging.INFO):
        install_slice.install_kubementat(context, consent=Consent.assumed())

    assert "Installed kubementat to cluster successfully :D" in caplog.text, caplog.text
    assert "Now you are ready to start making it your own." in caplog.text
    assert "CONFIGURING: Kubementat for environment" in caplog.text
    assert "uv run kmt tekton run-pipeline" in caplog.text, \
        "the next-steps sentences are the part of the log an operator has always read"
