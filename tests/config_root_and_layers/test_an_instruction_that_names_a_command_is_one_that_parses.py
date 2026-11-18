"""A refusal that tells an operator what to run has to print a command that runs."""

import json
import re
from pathlib import Path

import pytest
from asset_paths import REPO_ROOT
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.automations.routing import routing
from kubementat.core.config.config import Config

ENVIRONMENT = "instructionenv"
TEAM = "instructionteam"

SRC = REPO_ROOT / "src"

# The instruction sentence as it arrives in a refusal: `kmt config set …  is the verb`.
INSTRUCTION = re.compile(r"kmt config set\b(?P<arguments>.*?)\s+is the verb", re.S)
PLACEHOLDER = re.compile(r"^<[^>]+>$")


SUBSTITUTIONS = {
    "<dir>": lambda root: str(root),
    "<configuration-root>": lambda root: str(root),
    "<environment>": lambda root: ENVIRONMENT,
    "<env>": lambda root: ENVIRONMENT,
    "<address>": lambda root: "someone@example.test",
    "<registry-url>": lambda root: "registry.example.test",
    "<team>": lambda root: TEAM,
}


def seed(root, environment_level=None) -> Config:
    """Both pairs of both layers present, so the refusal is about the key and not about a missing file.
    """
    environment_dir = root / "platform_config" / ENVIRONMENT
    team_dir = environment_dir / TEAM
    team_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in ((environment_dir / "static.json", environment_level or {}),
                          (environment_dir / "static.encrypted.json", {}),
                          (team_dir / "static.json", {}),
                          (team_dir / "static.encrypted.json", {})):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def _argv_from(message, root) -> list:
    """The argv the refusal is telling a human to run, placeholders resolved, group name taken off.
    """
    match = INSTRUCTION.search(message)
    assert match, f"no instruction sentence in {message!r}. This file's rule is that the sentence is a " \
                  "command; one that only describes the verb has no argv to check and needs saying so."
    argv = ["set"]
    for token in match.group("arguments").split():
        if PLACEHOLDER.match(token):
            maker = SUBSTITUTIONS.get(token.lower())
            assert maker, f"the refusal prints an unrecognised placeholder {token!r}"
            argv.append(maker(root))
        elif token == "KEY=VALUE":
            argv.append("CLUSTER_MANAGER_EMAIL=someone@example.test")
        elif token == "KEY":
            argv.append("CLUSTER_MANAGER_EMAIL")
        else:
            argv.append(token)
    return argv


def _assert_parses(argv, root, where):
    """Click's verdict on argv as a human would type it, and nothing else."""
    result = CliRunner(mix_stderr=False).invoke(config_module.config, argv)
    assert result.exit_code != 2, \
        f"{where}: the instruction {argv[1:]!r} is a usage error and not a command. A human following " \
        f"that sentence gets {result.output.strip()[-180:]!r} and no answer written."


def test_the_refusal_that_names_config_set_prints_the_answer_as_one_argument(tmp_path):
    """`routing`'s ACME refusal, end to end: the key it reads is the key it tells you to set."""
    config = seed(tmp_path)
    with pytest.raises(Exception) as error:
        routing._cluster_manager_email(config)
    root = Path(config.platform_config_dir)
    _assert_parses(_argv_from(str(error.value), root), root, "routing._cluster_manager_email")
