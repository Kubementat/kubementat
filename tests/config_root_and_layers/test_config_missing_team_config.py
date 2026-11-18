"""Tests for loading a team config that is missing: an error message, not a crash."""

import json

import pytest

from kubementat.core.config.config import Config
from kubementat.errors import TeamConfigurationFileMissingError

ENVIRONMENT = "testenv"
TEAM = "testteam"
OTHER_TEAM = "otherteam"


def write_json(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))


def make_repo_root(tmp_path, team_files=("static.json", "static.encrypted.json")):
    """A tree shaped like platform_config/{environment}/{team}/static[.encrypted].json."""
    root = tmp_path
    platform_config_dir = root / "platform_config"
    environment_dir = platform_config_dir / ENVIRONMENT
    write_json(environment_dir / "static.json", {"ENV_ONLY": "env-value"})
    write_json(environment_dir / "static.encrypted.json", {"GRAFANA_ADMIN_USER": "admin"})
    if team_files:
        team_dir = environment_dir / TEAM
        for file_name in team_files:
            write_json(team_dir / file_name, {"TEAM_ONLY": "team-value", "PIPELINE_NAMESPACE": TEAM})
    return root, platform_config_dir


def config_for(root, platform_config_dir, **kwargs):
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root), platform_config_dir=str(platform_config_dir), **kwargs)


def test_missing_team_config_fails_with_a_message_not_an_unbound_local_error(tmp_path):
    """The eager load used to crash the moment it hit the return statement."""
    root, platform_config_dir = make_repo_root(tmp_path, team_files=())

    try:
        config = config_for(root, platform_config_dir)
    except UnboundLocalError as error:  # pragma: no cover - the old behaviour
        pytest.fail(f"Config still crashes on a missing team config: {error}")

    assert config.get("env_static")["ENV_ONLY"] == "env-value"


def test_lazy_loading_reads_environment_config_before_any_team_read(tmp_path):
    """This is the `Context`'s shape: preload_configs=False, then get(...)."""
    root, platform_config_dir = make_repo_root(tmp_path, team_files=())
    config = config_for(root, platform_config_dir, preload_configs=False)

    assert config.get("env_static_encrypted")["GRAFANA_ADMIN_USER"] == "admin"

    with pytest.raises(TeamConfigurationFileMissingError):
        config.get("team_static")
