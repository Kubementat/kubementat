"""The five team-shaped answers are rules, and the operator's file outranks them."""

import json

import pytest

from kubementat.config import answers as config_answers
from kubementat.core.config import derived as config_derived
from kubementat.core.config.config import Config
from kubementat.errors import TeamNotSuppliedError

ENVIRONMENT = "derivedenv"
TEAM = "astronauts"

# The one place `src/` still substitutes the placeholder is `initialize`, which is a *writer*: it
# copies a template and fills the hole in it. This file says nothing about that writer — deleting it
# belongs with `initialize` — and everything it does say is about readers.
PLACEHOLDER = "REPLACEME_TEAM"


def seed(root, team=TEAM, environment_static=None, team_static=None,
         environment_encrypted=None, team_encrypted=None) -> Config:
    """A `Config` over a tree that answered nothing but the team's name."""
    environment_dir = root / "platform_config" / ENVIRONMENT
    environment_dir.mkdir(parents=True, exist_ok=True)
    (environment_dir / "static.json").write_text(json.dumps(environment_static or {}))
    (environment_dir / "static.encrypted.json").write_text(json.dumps(environment_encrypted or {}))
    if team is None:
        # A run with no team at all: there is no team directory to read, and no answer to derive.
        return Config(ENVIRONMENT, None, kubementat_main_dir=str(root),
                      platform_config_dir=str(root / "platform_config"))
    team_dir = environment_dir / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "static.json").write_text(json.dumps(team_static or {}))
    (team_dir / "static.encrypted.json").write_text(json.dumps(team_encrypted or {}))
    return Config(ENVIRONMENT, team, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


# --- the five answers, one test per key ------------------------------------------------------


def test_the_deploy_key_namespace_is_the_team_where_the_entry_said_nothing(tmp_path):
    """The fifth answer is a field inside a structure, and that is the one a reader gets wrong."""
    entry = {"NAME": "deploy-key", "PUBLIC_KEY": "ssh-rsa AAAA", "TARGET_SECRET_NAME": "deploy-key"}
    config = seed(tmp_path, team_encrypted={"SSH_DEPLOY_KEYS": [entry]})

    keys = config.get("team_static_encrypted")["SSH_DEPLOY_KEYS"]

    assert keys[0]["TARGET_NAMESPACE"] == TEAM
    # The operator wrote the rest, so the rest is what answers.
    assert keys[0]["NAME"] == "deploy-key"
    assert keys[0]["PUBLIC_KEY"] == "ssh-rsa AAAA"
    assert keys[0]["TARGET_SECRET_NAME"] == "deploy-key"
    # The dictionary the file declared is not what the rule wrote into.
    assert entry.get("TARGET_NAMESPACE") is None


def test_the_deploy_keys_stay_a_list_and_are_never_flattened_to_a_string(tmp_path):
    """The one of the five that is a structure keeps its shape, in both directions."""
    config = seed(tmp_path, team_encrypted={
        "SSH_DEPLOY_KEYS": [
            {"NAME": "one", "PUBLIC_KEY": "ssh-rsa AAAA"},
            {"NAME": "two", "PUBLIC_KEY": "ssh-ed 2222", "TARGET_NAMESPACE": "shared-deployers"},
        ]})

    keys = config.get("team_static_encrypted")["SSH_DEPLOY_KEYS"]

    assert isinstance(keys, list) and all(isinstance(entry, dict) for entry in keys)
    assert [entry["TARGET_NAMESPACE"] for entry in keys] == [TEAM, "shared-deployers"], keys
    assert [entry["NAME"] for entry in keys] == ["one", "two"], keys

    # A value nobody may flatten: not a list, answered exactly as it was written.
    assert config_derived.derived_declared("SSH_DEPLOY_KEYS", "ssh-rsa AAAA", TEAM) == "ssh-rsa AAAA"


# --- the override half, which is what decides whether the precedence survived ---------------


def test_a_key_any_layer_declares_outranks_the_rule(tmp_path):
    """Team over environment over default; a derived answer never beats a declared one."""
    environment_wins = seed(tmp_path, environment_static={
        "PIPELINE_NAMESPACE": "env-shared", "APP_DEPLOYMENT_NAMESPACE": "env-apps",
        "MYSQL_HOST": "mysql.environment", "REDIS_MASTER_HOST": "redis.environment"})
    for key, value in {"PIPELINE_NAMESPACE": "env-shared", "APP_DEPLOYMENT_NAMESPACE": "env-apps",
                       "MYSQL_HOST": "mysql.environment",
                       "REDIS_MASTER_HOST": "redis.environment"}.items():
        assert environment_wins.get("team_static")[key] == value, key

    declared_null = seed(tmp_path, team_static={"MYSQL_HOST": None})
    assert declared_null.get("team_static")["MYSQL_HOST"] is None

    team_wins = seed(tmp_path, environment_static={"APP_DEPLOYMENT_NAMESPACE": "env-apps"},
                     team_static={"APP_DEPLOYMENT_NAMESPACE": "team-apps"})
    assert team_wins.get("team_static")["APP_DEPLOYMENT_NAMESPACE"] == "team-apps"


def test_the_report_names_the_rule_and_not_a_file_that_wrote_nothing(tmp_path):
    """The answer's *why* is part of the answer, and the derivation changed it."""
    seed(tmp_path)
    root = tmp_path / "platform_config"

    answered = {answer.key: answer for answer
                in config_answers.effective_answers(root, ENVIRONMENT, TEAM)}
    assert answered["PIPELINE_NAMESPACE"].value == f"{TEAM}-pipelines"
    assert answered["PIPELINE_NAMESPACE"].source is None, \
        "a derived answer that claims to come from a file is a derived answer that lies"
    assert config_answers.default_source("PIPELINE_NAMESPACE") == config_answers.DERIVED_DEFAULT_SOURCE
    assert config_answers.default_source("MYSQL_HOST") == config_answers.DERIVED_DEFAULT_SOURCE
    # A key `constants.py` really does own is still tagged as its own.
    assert config_answers.default_source("GRAFANA_DEPLOYMENT_NAMESPACE") == \
        config_answers.CONSTANTS_DEFAULT_SOURCE


# --- the team is a precondition ---------------------------------------------------------------


def test_a_run_that_names_no_team_refuses_and_names_the_key_and_the_missing_answer(tmp_path):
    """No team is no input, and `None` is not a stand-in for a team's name."""
    config = seed(tmp_path, team=None)
    with pytest.raises(TeamNotSuppliedError):
        config.get("team_static")

    with pytest.raises(TeamNotSuppliedError) as refused:
        config_derived.pipeline_namespace(None)
    assert "PIPELINE_NAMESPACE" in str(refused.value)
    assert "team" in str(refused.value)

    # An empty team is the same nothing wearing whitespace, and it answers the same way.
    with pytest.raises(TeamNotSuppliedError):
        config_derived.app_deployment_namespace("   ")


# --- that these are rules, and not something else ---------------------------------------------
