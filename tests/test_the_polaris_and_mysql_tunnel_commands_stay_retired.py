"""Row 25: the polaris and mysql tunnel commands are retired, and a guard is what keeps them out.

Neither had a lane capability (`tests/e2e/harness/config.py` has no row that names them), so nothing
in the suite could tell the operator that either was gone. A deletion nobody measures is a deletion
that comes back -- the surface test notices the name is missing only as long as somebody remembers to
run it, and a re-added registration is a one-line diff. So this file holds the two halves of what
row 25 took away:

* **the name** — `kmt tunnel-polaris` and `kmt tunnel-mysql` are refused with Click's own exit code
  for an unknown command (2) and its own message, the same shape every flat tunnel name gets.
* **the function behind it** — `open_tunnel_polaris` and `open_tunnel_mysql` are absent from
  `automations/tunnels/tunnels.py`, and no file under `src/` mentions them. A command that is not
  registered but
  whose library call is still there is a command waiting to be re-registered; the function being gone
  is the part that makes the retirement real.

What is *not* asserted here is the surface of the four surviving tunnels. `kmt tunnel`'s own verbs
and their ports belong to `test_parity_tunnels.py` and `test_cli_surface.py`, which keep running
them unchanged.
"""

import contextlib
import io
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli import kmt as kmt_module
from cli.kmt import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"

# The names that used to answer, and the two tunnel functions they alone called.
RETIRED_COMMANDS = ("tunnel-polaris", "tunnel-mysql")
RETIRED_METHODS = ("open_tunnel_polaris", "open_tunnel_mysql")


def _register_the_surface_and_invoke(argv):
    """`main()`'s registrations, then one invocation, in a single in-process run.

    Going through `main()` is the only way to check the surface that ships: the commands are attached
    inside it, so a module that defines a command without wiring it in would otherwise answer too.
    `--help` registers the whole group before the invocation below resolves anything, and a refused
    name never reaches a command body, so no cluster is touched.
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("logging.basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["kmt", "--help"])
    listing = io.StringIO()
    try:
        with contextlib.redirect_stdout(listing):
            try:
                kmt_module.main()
            except SystemExit as exit_request:
                assert exit_request.code == 0, "registering the surface must not fail"
            result = CliRunner().invoke(cli, list(argv))
    finally:
        monkeypatch.undo()
    return result, listing.getvalue()


@pytest.mark.parametrize("name", RETIRED_COMMANDS)
def test_the_retired_tunnel_name_is_refused(name):
    """`kmt tunnel-polaris` is not a command that errs, it is a name nothing answers to."""
    result, _listing = _register_the_surface_and_invoke([name])

    assert result.exit_code == 2, (
        f"kmt {name} exited {result.exit_code} instead of refusing the name:\n{result.output}")
    assert "No such command" in result.output, (
        f"kmt {name} was not refused as an unknown command:\n{result.output}")


@pytest.mark.parametrize("name", RETIRED_COMMANDS)
def test_the_retired_tunnel_name_is_not_printed(name):
    """A name in `--help` is an invitation to type it, whatever the code behind it says.

    The same rule `check_group` applies to every flat name: a command that still shows
    up in the listing is a command someone will run.
    """
    _result, listing = _register_the_surface_and_invoke(["--help"])

    assert name not in listing, f"kmt --help still prints {name}"


@pytest.mark.parametrize("method", RETIRED_METHODS)
def test_the_retired_tunnel_method_is_not_on_the_automation(method):
    """The library half, asked of the module rather than of a grep.

    The command was only ever a caller, so the function being reachable is the way the tunnel comes
    back: someone registers a verb, the function is right there, and nothing in the suite says no.
    """
    from kubementat.automations.tunnels import tunnels

    assert not hasattr(tunnels, method), (
        f"tunnels.{method} is back on the module; row 25 retired the tunnel it opened")


@pytest.mark.parametrize("method", RETIRED_METHODS)
def test_no_shipped_source_names_the_retired_tunnel_method(method):
    """Nothing in the tree can reach it, read out of the shipped code rather than remembered.

    `src/` is what `kmt` runs, so a reference anywhere under it -- a command, a re-export, a helper
    that kept the call alive for a script that no longer exists -- is a live path to a tunnel that
    was decided against.
    """
    offenders = []
    for source_file in sorted(SRC_ROOT.rglob("*.py")):
        if method in source_file.read_text():
            offenders.append(str(source_file.relative_to(REPO_ROOT)))

    assert not offenders, f"{method} is still named in {offenders}"


def test_no_tunnel_command_reaches_the_retired_tunnels_under_another_name():
    """The retirement is of a target, not only of two strings on the CLI.

    A `kmt tunnel polaris` verb would be the second door: same library call, different spelling, and
    invisible to a test that only checks the two flat names. The verbs here are the four the lane
    measures -- the group's own shape is `test_cli_surface.py`'s to hold.
    """
    _result, _listing = _register_the_surface_and_invoke(["--help"])
    verbs = set(cli.commands["tunnel"].commands)

    assert not {"polaris", "mysql"} & verbs, f"a retired tunnel is on the `tunnel` group: {verbs}"
