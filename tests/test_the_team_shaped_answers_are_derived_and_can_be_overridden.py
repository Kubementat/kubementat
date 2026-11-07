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

import ast
import json
from pathlib import Path

import pytest

import kubementat
from kubementat import config_answers
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


def test_the_application_namespace_is_the_team_nobody_named_a_namespace_for(tmp_path):
    """`APP_DEPLOYMENT_NAMESPACE` is the team's own name, read out of a file that never says so.

    A team and the applications it deploys share one location; the second name for it existed only
    because a template had to have *something* in the field.
    """
    config = seed(tmp_path)

    assert config.get("team_static")["APP_DEPLOYMENT_NAMESPACE"] == TEAM


def test_the_pipeline_namespace_is_the_team_plus_the_pipelines_suffix(tmp_path):
    """`PIPELINE_NAMESPACE` is `<team>-pipelines` — the one derived answer with a suffix.

    The suffix is the part a team could not restate by naming a team: pipelines are kept out of the
    namespace its applications run in, and that is a product decision rather than a preference.
    """
    config = seed(tmp_path)

    assert config.get("team_static")["PIPELINE_NAMESPACE"] == f"{TEAM}-pipelines"


def test_the_mysql_host_is_the_teams_own_service_when_no_layer_declares_it(tmp_path):
    """`MYSQL_HOST` is `mysql-<team>`: one team, one database, and a name anybody could predict.

    Read from `team_static_encrypted`, because that is the category the deploy flow actually opens —
    a rule that only answered in one category would be a rule with a reader that never gets an answer.
    """
    config = seed(tmp_path)

    assert config.get("team_static_encrypted")["MYSQL_HOST"] == f"mysql-{TEAM}"


def test_the_redis_master_host_is_the_teams_own_service_when_no_layer_declares_it(tmp_path):
    """`REDIS_MASTER_HOST` is `redis-<team>`, for the same reason MySQL is `mysql-<team>`."""
    config = seed(tmp_path)

    assert config.get("team_static_encrypted")["REDIS_MASTER_HOST"] == f"redis-{TEAM}"


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


def test_both_files_of_a_team_layer_answer_a_derived_key(tmp_path):
    """The rule is a fact about the team and not about which of its two files a key sits in.

    `MYSQL_HOST` read from `team_static` answers as surely as the same key read from
    `team_static_encrypted`, because the answer is computed from the team and not stored in a file.
    """
    config = seed(tmp_path)

    assert config.get("team_static")["MYSQL_HOST"] == config.get("team_static_encrypted")["MYSQL_HOST"]
    assert config.get("team_static")["PIPELINE_NAMESPACE"] == f"{TEAM}-pipelines"


# --- the override half, which is what decides whether the precedence survived ---------------


def test_an_operators_pipeline_namespace_is_believed_and_the_rule_stays_quiet(tmp_path):
    """The test this ticket exists for: `static.json` says `shared-pipelines`, and `static.json` wins.

    Nothing else in the tree answers for this. A rule that fires here is a rule that became a lie, and
    it would pass every derivation assertion above while overwriting a namespace the operator chose.
    """
    config = seed(tmp_path, team_static={"PIPELINE_NAMESPACE": "shared-pipelines"})

    assert config.get("team_static")["PIPELINE_NAMESPACE"] == "shared-pipelines"
    # ...and the four answers the operator did not write are still answered, so this is one key
    # yielding and not the whole feature switching off.
    assert config.get("team_static")["APP_DEPLOYMENT_NAMESPACE"] == TEAM


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


def test_an_entry_with_a_namespace_is_the_operators_and_the_rule_does_not_reach_in(tmp_path):
    """The structural rule reaches inside an entry only where the entry said nothing.

    A deploy key filed in a shared namespace is a team that means it, and a rule that "corrected" the
    field back to the team's own name would move a Secret that already works.
    """
    config = seed(tmp_path, team_encrypted={"SSH_DEPLOY_KEYS": [
        {"NAME": "shared", "TARGET_NAMESPACE": "shared-deployers"}]})

    assert config.get("team_static_encrypted")["SSH_DEPLOY_KEYS"][0]["TARGET_NAMESPACE"] \
        == "shared-deployers"


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


def test_the_rules_answer_in_a_library_read_and_not_only_through_a_command(tmp_path):
    """Nothing routes through the CLI: the rule is in the read path, so `Config` answers alone.

    Three call sites in `src/` read one of these keys out of a category dictionary, and none of them
    know a rule exists. That is the point of putting it in the layer resolution rather than in a
    command — a fix that lives in the CLI answers for one verb and lies for the other three.
    """
    config = seed(tmp_path)

    assert config.get("team_static").get("PIPELINE_NAMESPACE") == f"{TEAM}-pipelines"
    assert config.get("team_static_encrypted").get("SSH_DEPLOY_KEYS") is None


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


def test_a_team_without_a_deploy_key_entry_is_no_answer_at_all(tmp_path):
    """The fifth rule has a precondition the other four do not: an entry has to exist.

    A deploy key is six fields of operator's material and one field the team's name determines, so
    deriving it from a name alone would be generation with a nicer name — and a stub entry would land
    in `automation.py`'s Secret creation as a `KeyError` after the run has started to write. So
    nothing answers, which is a fact the reader can act on and not a hole in the rules.
    """
    config = seed(tmp_path)

    assert "SSH_DEPLOY_KEYS" not in config.get("team_static_encrypted")
    assert config.get("team_static_encrypted").get("SSH_DEPLOY_KEYS") is None


# --- that these are rules, and not something else ---------------------------------------------


def test_the_derived_answers_are_stable_and_carry_no_entropy():
    """Two calls answer the same thing, and the module that answers has no way to answer otherwise.

    The boundary in a line: a derived answer is one answer, so a rule that returned something
    different next time would be a generator wearing a table of rules, and a generated password that
    lands in `static.json` under a namespace's name is the failure mode this whole distinction is
    there to keep somebody from shipping.
    """
    for key, rule in config_derived.RULES.items():
        assert rule(TEAM) == rule(TEAM), key

    source = Path(kubementat.core.config.derived.__file__).read_text()
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    assert imports.isdisjoint({"random", "secrets", "uuid"}), imports


def test_the_rules_are_five_answers_and_not_six_keys(tmp_path):
    """What the module exposes is the ruling, so an extra key has to be visible here.

    Four rules answer a key from a name; the fifth answers a field inside an entry. A sixth key that
    turns out to be a rule and not a default belongs in the rules table before it belongs in this file,
    and this assertion is the seam that says which of the two happened.
    """
    assert set(config_derived.RULES) == {
        "APP_DEPLOYMENT_NAMESPACE", "PIPELINE_NAMESPACE", "MYSQL_HOST", "REDIS_MASTER_HOST"}
    assert config_derived.TARGET_NAMESPACE == "TARGET_NAMESPACE"


def test_the_templates_still_carry_the_placeholder_and_no_reader_depends_on_it():
    """The substitution survives in `initialize`, and nothing that *reads* configuration needs it.

    `initialize` still copies the templates, which is why the placeholder is still in them; deleting
    the copy is not this file's business. What has to be true in the meantime is that no reader
    resolves an answer through it, so the assertion looks for the placeholder in the place a reader
    would put it — inside a call, in source, in `src/kubementat` — and refuses everything the writer
    does not have to do.

    The templates and the fixture trees stay out of the scan on purpose: they are inputs, and a
    template that mentions its own placeholder is what `initialize` was built to substitute.
    """
    sources = sorted(Path(kubementat.__file__).resolve().parent.rglob("*.py"))
    assert sources, "the package this test scans was not found"

    readers = []
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                arguments = list(node.args) + [keyword.value for keyword in node.keywords]
                for argument in arguments:
                    if any(PLACEHOLDER in part.value for part in ast.walk(argument)
                           if isinstance(part, ast.Constant) and isinstance(part.value, str)):
                        readers.append(f"{path.relative_to(Path(kubementat.__file__).parents[2])}:{node.lineno}")
                        break

    # `initialize`'s writers, and only those: the substitution lives where a template is copied.
    writers = {f"{path}" for path in readers}
    assert all("automation.py" in writer for writer in writers), readers


def test_the_placeholder_still_reaches_a_tree_from_the_templates():
    """It is still there, so the assertion above is about a reader and not about a name that vanished.

    If `REPLACEME_TEAM` ever leaves the templates, this test is the one that says so — and the reader
    assertion would then pass for no reason anyone should trust.
    """
    templates = sorted(Path(kubementat.__file__).resolve().parent.glob(f"core/assets/templates/**/*"))
    with_placeholder = [t for t in templates if t.is_file() and PLACEHOLDER in t.read_text()]
    assert with_placeholder, "the templates this rule replaces carry no placeholder any more"
    assert any(t.name.endswith("static.encrypted.json.template") for t in with_placeholder)
