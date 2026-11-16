"""A partially-succeeded uninstall is a result now, and this file pins what that costs and saves.

`uninstall_kubementat` used to end like this:

    if residue:
        raise errors.UninstallIncompleteError(
            "uninstall left kubementat residue behind, so the cluster is not clean:\\n  "
            + "\\n  ".join(residue))

which is a run reporting a *partial* result as a joined string. The operator read a sentence and
exited 1 — both correct — and everybody else got an exception with the information folded into its
message and nowhere else. There was no way to ask "how many namespaces went", "which directories
were read as teams", or "was this run clean before I decided to catch something", because the def
returned `None` and the residue existed only as prose.

It answers `SweepResult` now, the shape `RoutingInstallResult` and `InstallResult` return, and the
one line that matters about the change is this: **the message did not move.** The failure a real
run raises with is assembled by `SweepResult.failure_message()` from the same list, in the same
order, with the same indent, so exit 1 and the operator's sentence are exactly where they were.
What is gone is the joining inside the automation def, and what is new is that the residue is a
field a caller and a test can read before anything is raised.

The lane's stake in this file is the reinstall cycle in `--scope full`: a sweep that reports clean
over releases the cluster still carries is the failure the `no-extra-*` asserts exist to catch, and
it is precisely what the unit suite here cannot see for itself.
"""

import inspect
import json
import logging

import pytest
from click.testing import CliRunner

from cli.commands import kubementat as kubementat_cli
from kubementat.automations.uninstall import kubementat as uninstall_slice
from cluster_fixture import configuration_only_context
from kubementat.consent import Consent
from kubementat.core import constants
from kubementat.errors import UninstallIncompleteError

# The cluster addons every real guest carries, and therefore what a sweep is never allowed to
# delete. The same set the sibling file's `cluster_is_installed` builds from.
CLUSTER_ADDONS = {"default", "kube-system", "kube-public", "kube-node-lease"}

# The sentence, spelled out from the string the joined exception used to build. This is the
# operator-visible half of the item's whole claim, so it is a literal and not a function's output.
FAILURE_MESSAGE = ("uninstall left kubementat residue behind, so the cluster is not clean:\n  "
                   "helm release 'cert-manager' is still installed in namespace 'cert-manager'"
                   "\n  namespace 'grafana' is still reported by the cluster (phase Terminating)")


def _a_sweep(residue=(), deleted=("cert-manager", "grafana"), absent=("openbao",)):
    """The result object one run produces, built without a cluster.

    Constructed directly for the claims that are about the *shape* of the answer — the message, the
    clean verdict, the rendered lines — because none of them needs a cluster to be true. Everything
    that is about what a run actually put in the list is asserted through a real sweep below.
    """
    return uninstall_slice.SweepResult(
        environment="dev",
        context="k3s-local",
        namespaces_deleted=tuple(deleted),
        namespaces_absent=tuple(absent),
        teams_swept=("dev1",),
        teams_not_swept=(),
        residue=tuple(residue),
    )


# ---------------------------------------------------------------------------------------------
# the value
# ---------------------------------------------------------------------------------------------


def test_the_sweep_returns_an_answer_and_not_none(fixture_repo, dry_run_env, fake_binaries,
                                                  recorded_api):
    """The Done-when's first line: the def answers with a value, and the value agrees with the writes.

    Run against the recorded fake cluster rather than a stubbed one, because the claim is about
    *what this run deleted* — and a value that agrees with a stub agrees with nothing. The
    namespace list is cross-checked against the deletes the recorder saw, so the answer cannot be
    built out of configuration and still pass this test.
    """
    present = cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)
    context = configuration_only_context(fixture_repo.environment, None)
    recorded_api.calls.clear()

    result = uninstall_slice.uninstall_kubementat(context, consent=Consent.assumed())

    assert isinstance(result, uninstall_slice.SweepResult), type(result)
    assert result.environment == fixture_repo.environment
    assert set(result.namespaces_deleted) <= set(present)
    assert result.namespaces_deleted, "the value says it swept nothing that the run deleted"
    assert result.residue == (), "a clean sweep must report itself as clean: " + str(result.residue)
    assert result.clean is True

    deleted = [kwargs["name"] for method, kwargs in recorded_api.calls
               if method == "delete_namespace"]
    assert set(result.namespaces_deleted) >= set(deleted), \
        "the value's namespace list disagrees with the deletes the cluster was actually told"


def test_the_return_annotation_is_a_dataclass_and_not_none():
    """`-> None` on this def is the item's own failure mode, so it is grepped as a contract.

    Also asserts the joined-string raise is gone from the module rather than merely bypassed,
    because a body that still contains it is a body that can still use it.
    """
    returned = inspect.signature(uninstall_slice.uninstall_kubementat).return_annotation
    assert returned is uninstall_slice.SweepResult, returned
    assert uninstall_slice.SweepResult.__dataclass_fields__, \
        "the copy of `RoutingInstallResult` is not a dataclass, so it is not the shape the plan named"

    source = inspect.getsource(uninstall_slice)
    body = inspect.getsource(uninstall_slice.uninstall_kubementat)
    assert "UninstallIncompleteError" not in body, \
        "the def still raises for residue, so a partial sweep is still reported by exception"
    assert "uninstall left kubementat residue behind" not in body, \
        "the message is still assembled inside the def and not by the value that carries the residue"
    assert "uninstall left kubementat residue behind" in \
        inspect.getsource(uninstall_slice.SweepResult.failure_message), \
        "the sentence did not move onto the value, so nothing about the reporting changed"
    # What stays is the log: `print_uninstall_result` still writes `Uninstall did not finish: <list>`.
    assert 'logging.error("Uninstall did not finish: " + ", ".join(residue))' in source, \
        "the log's own verdict line was removed, which is the half this item may not touch"


def test_the_failure_message_is_the_sentence_the_operator_already_read():
    """Byte for byte the message that `kmt uninstall` has always printed.

    This literal is the item. Everything else here is infrastructure; a reword of this sentence is
    the one change that would be a behavioural change wearing a refactoring, and the only cheap
    defence is a test that says what the string was before anything else was true of it.
    """
    result = _a_sweep(residue=FAILURE_MESSAGE.split(":\n  ")[1].split("\n  "))

    assert result.failure_message() == FAILURE_MESSAGE
    assert "cert-manager" in result.failure_message()
    assert "Terminating" in result.failure_message(), \
        "a namespace the cluster is still draining lost its phase, which is the sentence that tells " \
        "the operator whether to wait or to go looking"


def test_an_incomplete_sweep_raises_the_same_refusal_from_the_value_and_not_from_the_def():
    """`raise_if_incomplete` is the caller's half, and it raises the class the door renders.

    The pair is asserted together: the def must not raise for residue any more, and the value must
    still be able to produce the refusal `cli/context.py` prints as `Error: …` with exit 1.
    """
    with pytest.raises(UninstallIncompleteError) as raised:
        _a_sweep(residue=("helm release 'cert-manager' is still installed",)).raise_if_incomplete()
    assert str(raised.value) == FAILURE_MESSAGE.split("helm release")[0] + \
        "helm release 'cert-manager' is still installed"

    assert _a_sweep().raise_if_incomplete() is None, \
        "a clean sweep raising would turn a second pass on an empty cluster into a failure"


def test_the_summary_says_the_verdict_and_the_two_branches_are_different():
    """The rendered output, before it is printed: clean and unclean cannot read the same.

    A summary that said "uninstalled" over a surviving release is the worse way to be wrong here,
    because the lane's residue asserts would still be reading the cluster and agreeing with the
    command. Both branches therefore get asserted, and the residue is a **tuple of sentences** and
    not one long string -- counting characters as items is how a summary would report 46 survivors
    where one release was left running.
    """
    one_release = ("helm release 'cert-manager' is still installed in namespace 'cert-manager'",)
    clean = _a_sweep().summary()
    assert "kubementat uninstalled from dev (k3s-local): 2 namespaces deleted, 1 were already absent" in clean
    assert "team directories swept: dev1" in clean
    assert "nothing kubementat owned is left on the cluster." in clean
    assert "still on the cluster" not in clean

    unclean = _a_sweep(residue=one_release).summary()
    assert "1 things kubementat owned are still on the cluster." in unclean
    assert "nothing kubementat owned is left on the cluster." not in unclean


# ---------------------------------------------------------------------------------------------
# what the command prints, through the door an operator actually uses
# ---------------------------------------------------------------------------------------------


def cluster_is_installed(recorded_api, fixture_repo):
    """A cluster carrying every namespace kubementat could have created.

    The same shape `test_the_uninstall_command_is_python_and_still_asks.py` builds — that file pins
    the door (`--yes`, the no-terminal guard, the python-not-bash claim) and this one reads what
    comes out of it, so the two helpers stay in step by construction.
    """
    present = sorted(set(constants.DEFAULT_NAMESPACES.values())
                     | {fixture_repo.pipeline_namespace, fixture_repo.app_namespace}
                     | CLUSTER_ADDONS)
    listing = namespace_list(present)
    recorded_api.answer("list_namespace", listing)
    recorded_api.answer("delete_namespace", listing.delete)
    recorded_api.answer("list_node", namespace_list(["k3s-server-1"]))
    return present


def namespace_list(names):
    present = set(names)

    def listing(**kwargs):
        class List:
            def __init__(self, items):
                self.items = items

        return List([type("Namespace", (), {"metadata": type("Meta", (), {"name": n})()})
                     for n in sorted(present)])

    def delete(name=None, **kwargs):
        present.discard(name)

    listing.delete = delete
    return listing


def responds(fake_binaries):
    fake_binaries.respond("helm", "[]", subcommand="list")
    fake_binaries.respond("kubectl", '{"items": []}', subcommand="get.crd")
    fake_binaries.respond("kubectl", '{"items": []}', subcommand="get.persistentvolumeclaims")


def test_kmt_uninstall_prints_the_answer_when_the_cluster_came_clean(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The clean path, end to end: a summary on stdout and an exit code of 0.

    Before this item `kmt uninstall --yes` printed nothing on stdout at all — every word it said
    went to the log — so the answer here is genuinely new text and not a moved one. The deletes
    still happen through the same code, which is what the recorded API is checking while the output
    is being read.
    """
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)
    recorded_api.calls.clear()

    result = CliRunner().invoke(kubementat_cli.uninstall, ["dev", "--yes"])

    assert result.exit_code == 0, result.output
    assert "kubementat uninstalled from dev (" in result.output, result.output
    assert "namespaces deleted" in result.output
    assert "nothing kubementat owned is left on the cluster." in result.output, result.output
    deleted = [kwargs["name"] for method, kwargs in recorded_api.calls
               if method == "delete_namespace"]
    assert deleted, "the command printed a sweep it did not perform"


def test_a_surviving_release_is_still_exit_one_with_the_same_sentence(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, caplog):
    """The other path, and the only case the plan calls the highest-stakes sentence in Phase B.

    Nothing about the operator's experience may move: exit 1, the `Error:` rendering, the
    `uninstall left kubementat residue behind` lead. What moves is that the run also *knows* it is
    partial, which is asserted by reading the value the same sweep returned through a direct call
    in the sibling test rather than by trusting the message.
    """
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)
    # One release the cluster still reports, and a `helm uninstall` that refuses it: that is the
    # shape of a partial teardown, and it is the case the plan calls the highest-stakes sentence in
    # Phase B.
    fake_binaries.respond("helm", json.dumps([{"name": "cert-manager", "namespace": "cert-manager",
                                               "status": "deployed",
                                               "chart": "cert-manager-v1.10.1"}]),
                          subcommand="list")
    fake_binaries.respond("helm", "Error: release not uninstalled",
                          subcommand="uninstall", exit_code=1)
    recorded_api.calls.clear()

    result = CliRunner().invoke(kubementat_cli.uninstall, ["dev", "--yes"])

    assert result.exit_code == 1, result.output
    assert "uninstall left kubementat residue behind, so the cluster is not clean:" in result.output, \
        result.output
    assert "not kubementat owned is left" not in result.output, \
        "the clean-path summary was printed over a run that left residue behind"


def test_the_log_still_says_the_verdict_the_value_carries(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, caplog):
    """The preserve half of §9's change-vs-preserve, for the teardown path.

    The value did not replace the log: `print_uninstall_result` still writes the namespace diff, the
    team-directory caveat, and `DONE` only when nothing survived. That line is what the sweep tests
    read to prove a clean run, so it belongs in a file that claims the reporting moved.
    """
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)

    with caplog.at_level(logging.INFO):
        CliRunner().invoke(kubementat_cli.uninstall, ["dev", "--yes"])

    assert "DONE" in caplog.text, caplog.text
    assert "still present: none" in caplog.text.lower(), caplog.text
