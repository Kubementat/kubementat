"""A partially-succeeded uninstall is a result now, and this file pins what that costs and saves."""

import json
import logging

from click.testing import CliRunner

from cli.commands import kubementat as kubementat_cli
from kubementat.automations.uninstall import kubementat as uninstall_slice
from kubementat.core import constants

# The cluster addons every real guest carries, and therefore what a sweep is never allowed to
# delete. The same set the sibling file's `cluster_is_installed` builds from.
CLUSTER_ADDONS = {"default", "kube-system", "kube-public", "kube-node-lease"}


def _a_sweep(residue=(), deleted=("cert-manager", "grafana"), absent=("openbao",)):
    """The result object one run produces, built without a cluster."""
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


def test_the_summary_says_the_verdict_and_the_two_branches_are_different():
    """The rendered output, before it is printed: clean and unclean cannot read the same."""
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
    """A cluster carrying every namespace kubementat could have created."""
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


def test_a_surviving_release_is_still_exit_one_with_the_same_sentence(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, caplog):
    """A teardown that leaves a release behind says so and exits non-zero."""
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)
    # One release the cluster still reports, and a `helm uninstall` that refuses it: a partial teardown.
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
    """The preserve half of the change-vs-preserve rule, for the teardown path."""
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)

    with caplog.at_level(logging.INFO):
        CliRunner().invoke(kubementat_cli.uninstall, ["dev", "--yes"])

    assert "DONE" in caplog.text, caplog.text
    assert "still present: none" in caplog.text.lower(), caplog.text
