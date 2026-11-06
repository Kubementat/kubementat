"""Tests for loading a team config that is missing: an error message, not a crash.

The defect: `Config._load_config()` only bound `team_static` / `team_static_encrypted`
inside `if os.path.exists(...)` branches, so a team with no team-level config returned a
value and `return { 'team_static': team_static, ... }` blew up with
`UnboundLocalError: local variable 'team_static' referenced before assignment`. That is what
a full lane saw for `cap:helmfile-apply:routing`, and it is the reason a missing config file
has to name the path it expected and produce no traceback: the reader has one fact to act
on, and a Python error that names nothing is not that fact.

This is also where the claim that a lane row used to make lives now. No verb the lane can run
makes a single layer file go missing -- `config unset` clears a pair, not one -- so a lane
cannot reach this state, and the row that tried went red against a tree that was correct.
The behaviour is asserted here, against the door that implements it.
"""

import json
import logging

import pytest
from click.exceptions import ClickException

from kubementat.core.config.config import Config

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


def test_missing_team_config_error_names_the_file_to_create(tmp_path, caplog):
    """Both the warning and the error must name the path that was expected."""
    root, platform_config_dir = make_repo_root(tmp_path, team_files=("static.encrypted.json",))
    expected_path = str((platform_config_dir / ENVIRONMENT / TEAM / "static.json").resolve())
    config = config_for(root, platform_config_dir)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ClickException) as excinfo:
            config.get("team_static")
        # the state is not consumed: a second read of the same category fails the same way
        with pytest.raises(ClickException):
            config.get("team_static")

    assert expected_path in str(excinfo.value)
    assert "static.json" in str(excinfo.value)
    assert any("static.json" in record.message for record in caplog.records), "no warning named the file"
    assert "team_static" in str(excinfo.value)


def test_missing_encrypted_team_config_is_reported_the_same_way(tmp_path):
    root, platform_config_dir = make_repo_root(tmp_path, team_files=("static.json",))
    expected_path = str((platform_config_dir / ENVIRONMENT / TEAM / "static.encrypted.json").resolve())
    config = config_for(root, platform_config_dir)

    with pytest.raises(ClickException) as excinfo:
        config.get("team_static_encrypted")

    assert expected_path in str(excinfo.value)


def test_lazy_loading_reads_environment_config_before_any_team_read(tmp_path):
    """This is the HelmUtils/TektonUtils shape: preload_configs=False, then get(...)."""
    root, platform_config_dir = make_repo_root(tmp_path, team_files=())
    config = config_for(root, platform_config_dir, preload_configs=False)

    assert config.get("env_static_encrypted")["GRAFANA_ADMIN_USER"] == "admin"

    with pytest.raises(ClickException):
        config.get("team_static")


def test_present_team_config_files_are_unaffected(tmp_path):
    root, platform_config_dir = make_repo_root(tmp_path)
    config = config_for(root, platform_config_dir)

    assert config.get("team_static")["PIPELINE_NAMESPACE"] == TEAM
    assert config.get("team_static_encrypted")["TEAM_ONLY"] == "team-value"
