"""Three library reads that used to answer "which root?" for themselves, and now do not."""

import json

from cluster_fixture import configuration_only_context, redirect_checkout

import kubementat.infrastructure.grafana.accounts as grafana_accounts
from kubementat.core.config.config import Config

ENVIRONMENT = "dev"
TEAM = "dev1"


def a_root(tmp_path, name, *, environment_values, team_values=None, encrypted_values=None):
    """One tree, named *name*, carrying the environment and team files this subject reads."""
    root = tmp_path / name
    environment_dir = root / "platform_config" / ENVIRONMENT
    environment_dir.mkdir(parents=True)
    (environment_dir / "static.json").write_text(json.dumps(environment_values), encoding="utf-8")
    (environment_dir / "static.encrypted.json").write_text(
        json.dumps(encrypted_values or {}), encoding="utf-8")
    if team_values is not None:
        team_dir = environment_dir / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "static.json").write_text(json.dumps(team_values), encoding="utf-8")
    return root


def handed_down(root):
    """The `Config` an object would be handed: a tree, and the pair that lives in it."""
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=root,
                  platform_config_dir=root / "platform_config")


def test_the_grafana_credential_a_command_opens_with_is_the_one_its_context_handed_down(
        tmp_path, monkeypatch):
    """A `Context` that was told which tree to read reads that tree, and no other."""
    handed = a_root(tmp_path, "root-the-command-was-handed",
                    environment_values={"ENVIRONMENT": ENVIRONMENT},
                    encrypted_values={"GRAFANA_ADMIN_USER": "admin@handed.test",
                                      "GRAFANA_ADMIN_PASSWORD": "handed-password"})
    redirect_checkout(monkeypatch, a_root(tmp_path, "root-the-process-would-find",
                                          environment_values={"ENVIRONMENT": ENVIRONMENT},
                                          encrypted_values={}))

    ctx = configuration_only_context(ENVIRONMENT, TEAM, config=handed_down(handed))

    client = grafana_accounts._client_for(ctx, "https://grafana.test", None)

    assert client.credential_source == "GRAFANA_ADMIN_USER", \
        "the pair was read out of a tree this command never pointed at"
