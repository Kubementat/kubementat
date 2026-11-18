"""The tree a human gets from `kmt config init` has to be installable."""

import json
from pathlib import Path

from bootstrap_tools import EMAIL
from click.testing import CliRunner

from cli.commands import config as config_command

ENVIRONMENT = "dev"
TEAM = "dev1"
ADMIN_USER_KEY = "GRAFANA_ADMIN_USER"


def _init(root: Path, tools, *extra: str) -> None:
    """Run `kmt config init` exactly as the CLI does, and assert it was happy."""
    root.mkdir(parents=True, exist_ok=True)
    result = CliRunner().invoke(
        config_command.init_command,
        ["--environment", ENVIRONMENT, "--team", TEAM, "--gpg-email", EMAIL, *extra,
         "--config-dir", str(root)])
    assert result.exit_code == 0, result.output


def test_an_operator_who_names_the_key_wins_over_the_seed(tmp_path, tools):
    """`--encrypted-set` answers a question, and a default must not answer it a second time."""
    root = tmp_path / "platform_config"
    _init(root, tools, "--encrypted-set", f"{ADMIN_USER_KEY}=alice")

    written = json.loads((root / ENVIRONMENT / "static.encrypted.json").read_text())
    assert written[ADMIN_USER_KEY] == "alice", \
        "the seed overrode what the operator typed; init answered a question it was asked"
