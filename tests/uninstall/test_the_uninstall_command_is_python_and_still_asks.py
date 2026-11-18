"""`kmt uninstall`'s contract, held through the swap from bash to Python."""

import json
from types import SimpleNamespace

from click.testing import CliRunner

from cli.commands import kubementat
from kubementat.core import constants

CLUSTER_ADDONS = {"default", "kube-system", "kube-public", "kube-node-lease"}


def namespace_list(names):
    """A `list_namespace` answer over a set that shrinks when something is deleted."""
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


def cluster_is_installed(recorded_api, fixture_repo):
    """A cluster that still has every namespace kubementat could have created."""
    present = sorted(set(constants.DEFAULT_NAMESPACES.values())
                     | {fixture_repo.pipeline_namespace, fixture_repo.app_namespace}
                     | CLUSTER_ADDONS)
    listing = namespace_list(present)
    recorded_api.answer("list_namespace", listing)
    recorded_api.answer("delete_namespace", listing.delete)
    recorded_api.answer("list_node", namespace_list(["k3s-server-1"]))
    return present


def responds(fake_binaries):
    fake_binaries.respond("helm", "[]", subcommand="list")
    fake_binaries.respond("kubectl", json.dumps({"items": []}), subcommand="get.crd")
    fake_binaries.respond("kubectl", json.dumps({"items": []}), subcommand="get.persistentvolumeclaims")


def test_without_yes_and_without_a_terminal_it_refuses_and_names_the_flag(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The fail-closed guard is the CLI's and it still answers before anything is read."""
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)
    recorded_api.calls.clear()
    fake_binaries.take_calls()

    result = CliRunner().invoke(kubementat.uninstall, ["dev"])

    assert result.exit_code != 0
    assert "--yes" in result.output
    assert recorded_api.calls == [], "it deleted something it was never told to delete"
    assert fake_binaries.calls() == [], "it ran commands before asking"


def test_answering_no_says_the_uninstall_was_cancelled_and_exits_non_zero(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """`confirmation-rejected-changes-nothing` is a lane assert, and it greps for words."""
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)
    recorded_api.calls.clear()
    fake_binaries.take_calls()
    # A `CliRunner` replaces `sys.stdin` while it invokes, so the terminal check is answered by a
    # stand-in on `cli.consent`, the one module that reads it.
    monkeypatch.setattr("cli.consent.sys",
                        SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    result = CliRunner().invoke(kubementat.uninstall, ["dev"])

    assert result.exit_code != 0, "declining the uninstall exited green"
    assert "Cancelled uninstall script" in result.output, result.output
    assert "nothing was deleted" in result.output.lower(), result.output
    assert [method for method, _ in recorded_api.calls if method.startswith("delete_")] == []
    assert [call.argv for call in fake_binaries.calls()
            if "delete" in call.argv or "uninstall" in call.argv] == []
