"""The five team-shaped answers are rules, and the operator's file outranks them.

`APP_DEPLOYMENT_NAMESPACE`, `PIPELINE_NAMESPACE`, `MYSQL_HOST`,
`REDIS_MASTER_HOST` and the deploy key's `TARGET_NAMESPACE` are consequences of a team's name and not
answers an operator has to repeat. `initialize` used to produce them by substituting the name into a
`REPLACEME_TEAM` template, which is the reason they were never written down anywhere and the reason a
tree that skipped the substitution answered with the placeholder. So they are computed at read time,
as L0, in `kubementat/core/config/derived.py`.

Two behaviors, and this file judges both, because only one of them can be seen from a passing run:

* **Derivation.** A tree that answered nothing but the team's name still answers all five. One test per
  key, because a single test for the set is how a rule nobody wired survives — the assertion that
  never runs for the key that is missing is worth nothing, and this file was written against exactly
  that bug.
* **Override.** The same five yield to anything a layer declares. This is the half that decides whether
  the precedence rule survived the rules being introduced: a derivation that answers ahead of
  `static.json` is a
  rule that became a lie, and it would pass every test in the bullet above.

The rest is the shape of it: the team is a precondition rather than a nullable argument
(`TeamNotSuppliedError`, and never a silent `None`), the deploy keys stay a list of structures, the
answers carry no entropy, and no reader depends on the placeholder that survives in the templates.

What this file judges: one rule per key, the precedence between a rule and a declared layer, the line
between what is derived and what is generated, and that no file moves on the way.
"""

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
    """A `Config` over a tree that answered nothing but the team's name.

    The team's two files exist and declare nothing relevant, which is the state these rules are
    written against: a `config init` tree where a human typed a team name and nothing else. Every layer is
    optional and defaults to `{}`, and the environment's pair is always present, because a missing
    environment file is a different failure than an unset key and this file is not about that.

    The root is named and never located, so the fixture names it.
    """
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
    """The fifth answer is a field inside a structure, and that is the one a reader gets wrong.

    An entry that names its namespace is the operator's; an entry that does not is answered by the
    team's name, and the derivation reaches inside the list to say so. This is `SSH_DEPLOY_KEYS[0]
    .TARGET_NAMESPACE`, which is not a scalar and is not answered by `RULES`.
    """
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
    """The one of the five that is a structure keeps its shape, in both directions.

    A list of entries in answers as a list of entries out, and a value that is not a list is returned
    as declared rather than unwrapped or "corrected" — flattening is the failure a rule over a
    structure is most likely to make, and it is the one a passing derivation test cannot see.
    """
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
    """L2 > L1 > L0, with the rules sitting in L0 and never above a declared value.

    The environment's answer is a *declared* answer, so a team that declares nothing still falls
    through to the environment before the rule is consulted — which is the half that makes the
    derivation an L0 and not a policy. A layer that declares `null` is believed too: a half-written
    line is a decision to say nothing and not a request to derive something.
    """
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
    """The answer's *why* is part of the answer, and the derivation changed it.

    `kmt config show` resolves through the same read path as a command, so
    they answer the five keys too — and where they used to say `constants.py` for a key nothing
    declared, they now have to say that a rule over the team's name did. A report that named
    `constants.py` here would be naming an owner that says nothing about the key, which is the same
    lie as a template that says `REPLACEME_TEAM`.
    """
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
    """No team is no input, and `None` is not a stand-in for a team's name.

    Two refusals, one rule: a category read says this configuration has no team, and the rule itself
    says which key it was asked for and what it is missing. The second is the one `errors.py`'s
    `PipelineNamespaceNotConfiguredError` docstring exists to explain — a namespace that answered
    `None` put eight objects into a namespace called `null`, and every one of the nine errors that
    followed named the cluster and none of them named the key.
    """
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
