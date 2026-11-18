"""`kmt uninstall`'s contract, held through the swap from bash to Python.

The command line had exactly two promises, and a migration that changes the implementation is
not allowed to change either of them:

* `--yes`/`-y` is the scripting form, and asking for a deletion without it on a stdin that
  cannot answer makes the command fail closed, naming the flag. That is `click.UsageError` in
  `cli/commands/kubementat.py`, and it used to be a `[[ ! -t 0 ]]` in the script — the same
  rule one layer down, which is why the rule could look like a script's opinion.
* what runs afterwards is kubementat's own code. No script is executed for `uninstall` any
  more, so the argument list the script took (`environment`, and no team) is gone with it.

The evidence that the *deletions* are right is in
`test_uninstall_sweeps_everything_kubementat_owns.py`; this file only pins the door in.
"""

import json
from types import SimpleNamespace

from click.testing import CliRunner

from cli.commands import kubementat
from kubementat.core import constants

CLUSTER_ADDONS = {"default", "kube-system", "kube-public", "kube-node-lease"}


def namespace_list(names):
    """A `list_namespace` answer over a set that shrinks when something is deleted.

    A cluster that goes on reporting a namespace it was told to delete contradicts its own API, and
    an uninstall reading that fake is right to call the leftovers unfinished rather than gone.
    """
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
    """The fail-closed guard is the CLI's and it still answers before anything is read.

    `CliRunner` hands the command a stream that is not a terminal, which is exactly the case
    this guard is for.
    """
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
    """`confirmation-rejected-changes-nothing` is a lane assert, and it greps for words.

    A decline has to be legible three ways: to the person at the terminal, to the test that reads
    the log, and in the exit code. The bash printed a line and exited 1; the Python CLI raises a
    `ClickException`, which prints that line as its `Error:` and still exits 1 -- so long as the
    message itself says what happened.
    """
    cluster_is_installed(recorded_api, fixture_repo)
    responds(fake_binaries)
    recorded_api.calls.clear()
    fake_binaries.take_calls()
    # `script -qec` on the lane is what makes the command's stdin a terminal; a `CliRunner`
    # replaces `sys.stdin` while it invokes, so patching the real one is not enough -- the one
    # module that reads it gets a stand-in module attribute instead. It is `cli.consent` since item
    # 7: the guard at the command's door and the question after the plan are the same reader now,
    # and a decline that reads EOF in either place would be a refusal that never says so.
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
