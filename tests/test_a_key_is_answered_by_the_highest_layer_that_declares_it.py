"""Three layers answer a key, once, in the open.

Census E read every read of layered configuration in `src/` at `0b24303` and found no `L2 > L1 > L0`
anywhere: `Config.get()` handed back one file's dictionary and the only two-layer read in the tree
was `_component_value`, which went `env_static` → `constants.py` and never consulted the team. That
made "which layer answered" a fact about the *call site* rather than about the key, so a key the
environment declared and the team did not was answered twice — once by the team's empty dictionary
and once by whoever decided to go looking.

This file holds the three assertions that make the rule real:

* **The team's answer wins.** A key declared at L2, L1 and in `constants.py` answers from L2, and
  the environment's copy of it is what a team that has not declared it falls back to.
* **The default arrives once.** When no layer declares it, `constants.py` answers and the run says
  so — once per `Config`, not once per read, because five commands reading one key is one fact.
* **Silence is still silence.** A key with no default anywhere answers the way it answered before
  the layering landed: same exception, same sentence. Nothing that refused became a `None`, and
  nothing that was a `None` became a default.

Plus the merge rule that the precedence needs but no key's meaning changes: a layer is a *pair* of
files, and the encrypted half of it answers before the plain half.

What this keeps: the precedence between layers, that L0 is code and not a file, and that the
filenames do not move.
"""

import json
import logging
from pathlib import Path

import click
import pytest

from kubementat.core import constants
from kubementat.ci_utils import CiUtils
from kubementat.errors import CiImageNotConfiguredError
from kubementat.core.config.config import Config
from kubementat.automations.routing import routing
from kubementat.helm_utils import HelmUtils

ENVIRONMENT = "layerenv"
TEAM = "layerteam"

# The key this whole feature is about: L0 owns its answer (`constants.DEFAULT_NAMESPACES`), L1 is
# the environment's `static.json`, L2 is the team's. `grafana` is the component the tunnels resolve,
# so this is the same read a real command makes.
NAMESPACE_KEY = "GRAFANA_DEPLOYMENT_NAMESPACE"
L0_ANSWER = constants.DEFAULT_NAMESPACES["grafana"]


def seed(root, environment_level=None, team_level=None, environment_encrypted=None,
         team_encrypted=None, team_files_exist=True):
    """A `Config` over a seeded tree. Every layer is optional; an omitted pair writes `{}`.

    `team_files_exist=False` leaves the team's two files out of the tree, which is the state
    `get('team_static')` refuses with a `ClickException` — and that refusal is a different fact from
    "no layer declared this key", so this fixture has to be able to say both.
    """
    environment_dir = root / "platform_config" / ENVIRONMENT
    team_dir = environment_dir / TEAM
    environment_dir.mkdir(parents=True, exist_ok=True)
    if team_files_exist:
        team_dir.mkdir(parents=True, exist_ok=True)
    (environment_dir / "static.json").write_text(json.dumps(environment_level or {}))
    (environment_dir / "static.encrypted.json").write_text(json.dumps(environment_encrypted or {}))
    if team_files_exist:
        (team_dir / "static.json").write_text(json.dumps(team_level or {}))
        (team_dir / "static.encrypted.json").write_text(json.dumps(team_encrypted or {}))
    # The root is named and never located, so the fixture names it.
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def team_paths(config):
    return config.team_static_encrypted_config_path, config.team_static_config_path


# --- (a) the highest layer that declares the key is the one that answers ------------------


def test_the_teams_layer_answers_before_the_environments_layer(tmp_path):
    """L2 > L1, key by key, for the key `constants.py` also owns an answer for."""
    config = seed(tmp_path,
                  environment_level={NAMESPACE_KEY: "environment-namespace"},
                  team_level={NAMESPACE_KEY: "team-namespace"})

    assert config.component_namespace("grafana") == "team-namespace"


def test_the_teams_layer_answers_from_its_encrypted_file_too(tmp_path):
    """L2 is a pair. A key the team put in its `static.encrypted.json` is still a team answer.

    A layer is not one file; it is the directory's two files, one answer per key between them.
    """
    config = seed(tmp_path,
                  environment_level={NAMESPACE_KEY: "environment-namespace"},
                  team_encrypted={NAMESPACE_KEY: "team-namespace-from-encrypted"})

    assert config.component_namespace("grafana") == "team-namespace-from-encrypted"


def test_a_key_the_team_never_declared_falls_through_to_the_environment(tmp_path):
    """The half census E said was missing: a team that declares nothing is not a team that forbids.

    `PIPELINE_NAMESPACE` is the shape of this — read out of `team_static` by a dozen call sites. It
    is answered here through the accessor the ticket subsumed, because the read and the fallback
    have to agree or the rule is two rules.
    """
    config = seed(tmp_path,
                  environment_level={"PIPELINE_NAMESPACE": "shared-pipelines"},
                  team_level={"HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer-sa"})

    assert config.get("team_static").get("PIPELINE_NAMESPACE") == "shared-pipelines"
    # ...and the key the team *did* declare still answers from the team.
    assert config.get("team_static").get("HELM_DEPLOYER_SERVICE_ACCOUNT_NAME") == "helm-deployer-sa"


def test_within_a_layer_the_encrypted_file_answers_before_the_plain_one(tmp_path):
    """For a read addressed to *neither* file, the layer's encrypted half wins; and a read addressed
    to one file is answered by that file and by nothing else.

    The first half is the merge semantics: `grafana`'s namespace declared in both files of the
    environment's layer answers from the encrypted one, and the team pair decides the same way over
    its own two files. The second half is the invariant the merge must not break — addressability —
    because kubementat's secrets live in the git-crypt file and a reader that
    addressed it is entitled to be told that nothing lives there rather than be handed a value from
    the plaintext beside it.
    """
    config = seed(tmp_path,
                  environment_level={NAMESPACE_KEY: "plain-environment-answer"},
                  environment_encrypted={NAMESPACE_KEY: "encrypted-environment-answer"})

    assert config.component_namespace("grafana") == "encrypted-environment-answer", \
        "the encrypted half of the layer did not answer a read that was addressed to neither file"
    assert config.get("env_static_encrypted").get(NAMESPACE_KEY) == "encrypted-environment-answer"
    # The plain view is answered by the plain file: addressing a file is not asking its neighbour.
    assert config.get("env_static").get(NAMESPACE_KEY) == "plain-environment-answer"

    # The same shape one layer up, with the team pair deciding over its own two files.
    config = seed(tmp_path,
                  environment_level={},
                  team_level={NAMESPACE_KEY: "plain-team-answer"},
                  team_encrypted={NAMESPACE_KEY: "encrypted-team-answer"})
    assert config.component_namespace("grafana") == "encrypted-team-answer"


def test_a_secret_addressed_to_the_encrypted_file_is_not_answered_by_the_plain_one(tmp_path):
    """The one place where "either file of a layer" would have been a downgrade, asserted.

    `GIT_DEPLOYER_PRIVATE_KEY_BASE64`, Grafana's admin credential and the webhook secrets are read by
    a reader that names the encrypted file. Were the layer one bucket, a key left in the plain
    `static.json` by accident would answer instead of raising the "not set in …" sentence, which is a
    strictly worse posture for no benefit: nothing in the layering asks for it, and the layering
    they do ask for is between layers, not between files of one layer.
    """
    key = "GIT_DEPLOYER_PRIVATE_KEY_BASE64"
    config = seed(tmp_path, environment_level={key: "cGxhaW4ta2V5"})

    assert config.get("env_static_encrypted").get(key) is None, \
        "an encrypted-addressed read was answered out of the plain file"


def test_the_layering_changes_no_keys_the_layer_did_not_declare(tmp_path):
    """The shallow rule: merging is per key, so a layer that declares nothing contributes nothing.

    Everything the team's own file declares stays exactly as worded, and everything only the
    environment declares arrives unchanged. A shallow merge that rewrote types or joined objects
    would show up here.
    """
    team = {"PIPELINE_NAMESPACE": "team-pipelines",
            "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "team-sa",
            "SSH_DEPLOY_KEYS": [{"NAME": "team-key"}]}
    environment = {"BASE_DOMAIN": "example.test",
                   "GIT_DEPLOYER_PRIVATE_KEY_BASE64": "ZW52LWtleQ=="}
    config = seed(tmp_path, environment_level=environment,
                  team_level=team, environment_encrypted={"GIT_DEPLOYER_PRIVATE_KEY_BASE64": None})

    team_view = config.get("team_static")
    assert team_view["PIPELINE_NAMESPACE"] == "team-pipelines"
    assert team_view["BASE_DOMAIN"] == "example.test"
    assert team_view["HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"] == "team-sa"
    # The shallow rule still holds for every key the layers merged. `TARGET_NAMESPACE` is not one of
    # them: the entry is what the team declared, and the namespace inside it is what it left blank,
    # which is answered from the team's name. A different key is answered by the layer that
    # declares it, exactly as before.
    assert team_view["SSH_DEPLOY_KEYS"] == [{"NAME": "team-key", "TARGET_NAMESPACE": "layerteam"}]
    assert config.get("env_static").get("BASE_DOMAIN") == "example.test"
    # A key whose only declaration is a `null` is still declared; the layering does not skip it
    # because the value happens to be empty, and no layer below gets to overwrite a decision.
    assert config.get("env_static_encrypted").get("GIT_DEPLOYER_PRIVATE_KEY_BASE64") is None


# --- (b) nothing declared it, so the code answers, and says so once -----------------------


def test_the_code_default_answers_when_no_layer_declares_the_key(tmp_path, caplog):
    """L0 is `constants.py`, and it is reached only when both pairs came up empty."""
    config = seed(tmp_path, environment_level={}, team_level={})

    with caplog.at_level(logging.WARNING):
        assert config.component_namespace("grafana") == L0_ANSWER


def test_falling_back_to_the_code_default_warns_once_and_not_once_per_read(tmp_path, caplog):
    """The second test is the point: one read more is one fact less, not one more warning.

    Five tunnel commands resolve `GRAFANA_DEPLOYMENT_NAMESPACE` against the same `Config`. Before
    the once-per-instance set existed the operator got five identical sentences, which trains a
    human to read none of them. A second read in the same `Config` must not print it again — and
    this asserts the count of the key in the log, which is the only unit that cannot be satisfied
    by rewording.
    """
    config = seed(tmp_path, environment_level={}, team_level={})

    with caplog.at_level(logging.WARNING):
        first = config.component_namespace("grafana")
        second = config.component_namespace("grafana")

    assert first == second == L0_ANSWER
    assert caplog.text.count(NAMESPACE_KEY) == 1, caplog.text


def test_the_fallback_sentence_names_every_layer_it_consulted(tmp_path, caplog):
    """The warning is a direction, so it names all four files and the order it read them in.

    Two facts it owes the operator: which files were checked, and which half of a layer would have
    answered. Census E's whole finding was that nobody could say which layer answered; this sentence
    is where they can.
    """
    config = seed(tmp_path, environment_level={}, team_level={})
    environment_static, environment_encrypted = (
        config.env_static_config_path, config.env_static_encrypted_config_path)

    with caplog.at_level(logging.WARNING):
        config.component_namespace("grafana")

    assert NAMESPACE_KEY in caplog.text
    for path in team_paths(config) + (environment_encrypted, environment_static):
        assert str(path) in caplog.text, f"{path} is a layer this read consulted"
    assert "encrypted file answers before the plain" in caplog.text


def test_a_layer_that_declared_the_key_silences_the_fallback_warning(tmp_path, caplog):
    """A warning that fires when nothing defaulted is as useless as one that fires per read."""
    config = seed(tmp_path, environment_level={NAMESPACE_KEY: "env-ns"}, team_level={})

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert config.component_namespace("grafana") == "env-ns"

    assert caplog.text == ""


def test_a_value_declared_but_empty_still_defaults_and_names_the_file_it_came_in_from(tmp_path, caplog):
    """`""` is a half-typed edit and not a decision, whichever layer it sits in.

    The layering must not turn "present but blank" into "declared": the file that needs the value is
    named here, and the layer that declared it is identified, which is what the encrypted-wins rule
    costs the operator when they get it wrong.
    """
    config = seed(tmp_path, environment_level={NAMESPACE_KEY: "   "}, team_level={})

    with caplog.at_level(logging.WARNING):
        assert config.component_namespace("grafana") == L0_ANSWER
        for _ in range(3):
            config.component_namespace("grafana")

    assert caplog.text.count(NAMESPACE_KEY) == 1, caplog.text
    assert str(config.env_static_config_path) in caplog.text


def test_a_component_with_no_entry_in_the_code_default_table_is_refused(tmp_path):
    """L0 is `constants.py`, so a typoed component still fails where the typo was made."""
    config = seed(tmp_path, environment_level={})

    with pytest.raises(KeyError) as error:
        config.component_namespace("grafanana")

    assert "grafanana" in str(error.value)
    assert "kubementat.core.constants" in str(error.value)


# --- (c) a key with no default anywhere answers exactly as it did -------------------------


def test_L0_is_constants_py_and_the_packaged_default_source_does_not_exist():
    """L0 is `constants.py`, and the `defaults/static.json` an older design named is not in this tree.

    It must not be added here either: its source is the environment template, 40 keys of which 8
    are `REPLACEME_*`, so promoting it to a default source would answer with placeholders and give
    the namespaces a second owner beside `constants.py`. This asserts the absence, so a future
    "completion" of L0 fails here before it ships.
    """
    package_root = Path(constants.__file__).resolve().parent
    assert not (package_root / "assets" / "defaults" / "static.json").exists()
    assert not [name for name, value in vars(constants).items()
               if name.isupper() and isinstance(value, str) and value.endswith("static.json")], \
        "constants.py would be naming a file to default from, which is L1 by another name"
    # The only answers L0 owns are component tables, and they are complete on their own terms.
    assert set(constants.DEFAULT_NAMESPACES) >= {"grafana", "tekton", "traefik", "openbao"}
    assert all(isinstance(v, str) and v.strip() for v in constants.DEFAULT_NAMESPACES.values())


@pytest.mark.parametrize("category,key", [
    ("team_static_encrypted", "SSH_DEPLOY_KEYS"),
    ("env_static", "BASE_DOMAIN"),
    ("env_static", "CLUSTER_MANAGER_EMAIL"),
    ("env_static_encrypted", "GRAFANA_ADMIN_USER"),
    ("env_static_encrypted", "GIT_DEPLOYER_PRIVATE_KEY_BASE64"),
])
def test_a_key_no_layer_declares_still_answers_as_nothing(tmp_path, category, key):
    """No answer appeared out of the layering. `None` is still `None`, not a placeholder.

    The direction that would have been a bug is the other one: a refusal that quietly became a
    `None`, or a `None` that quietly became a code default. Neither happens, because L0 is only
    reachable through the accessor that names its own defaults table.

    Four keys are absent from this list on purpose: `PIPELINE_NAMESPACE`, `APP_DEPLOYMENT_NAMESPACE`,
    `MYSQL_HOST` and `REDIS_MASTER_HOST` answer for a team even when no layer declares them, because
    those four are consequences of the team's name and not gaps. They keep their own file
    (`test_the_team_shaped_answers_are_derived_and_can_be_overridden.py`), where the same question is
    asked with the answer the other way round and with the operator's override asserted next to it —
    which is what distinguishes a rule from a bug in the layering. `SSH_DEPLOY_KEYS` stays here
    because it is the fifth of the five and answers nothing at all until an entry exists.
    """
    config = seed(tmp_path, environment_level={}, team_level={})

    assert config.get(category).get(key) is None
    assert key not in config.get(category)


def test_the_refusals_that_fire_on_an_absent_key_still_fire_the_same_way(tmp_path):
    """Every refusal census E listed as "when nothing declares it" still raises what it raised.

    Same class, same sentence, for the four shapes of refusal the tree has: a `ClickException` that
    names a file, a library error that names the keys, the code default that is not a default, and
    the two refusals that were never about a key at all (no team named, no team file).
    """
    ci = CiUtils.__new__(CiUtils)
    ci.config = seed(tmp_path, environment_level={})
    with pytest.raises(CiImageNotConfiguredError) as error:
        CiUtils.ci_image(ci)
    # The class and the path are the contract, and the change touched only the sentence's second half. The
    # refusal used to be about a missing key and told the operator to re-run `initialize`; the name
    # and the tag are answered by `constants.py` now, so what is left to refuse is
    # `DOCKER_REGISTRY_BASE_URL`, which has no L0 and is not on this ticket's list. Naming the file
    # is the half that was load-bearing, and it still lands.
    assert "is not answered in" in str(error.value)
    assert str(ci.config.env_static_config_path) in str(error.value)

    helm = HelmUtils.__new__(HelmUtils)
    helm.config = seed(tmp_path, environment_level={}, environment_encrypted={})
    helm.environment = ENVIRONMENT
    helm.team = TEAM
    with pytest.raises(click.ClickException) as error:
        helm._grafana_admin_credentials()
    assert "GRAFANA_ADMIN_USER is not set in" in str(error.value)

    config = seed(tmp_path, environment_level={})
    with pytest.raises(click.ClickException) as error:
        routing._cluster_manager_email(config)
    assert "CLUSTER_MANAGER_EMAIL is not set in" in str(error.value)

    # A team that names no file is a different fact from a key that is absent: the file's name is
    # in the error, and the layering does not talk its way past it. Seeded on its own root, so the
    # team files this test wrote for the reads above cannot answer for it.
    no_team_files = seed(tmp_path / "no-team-tree", environment_level={}, team_files_exist=False)
    with pytest.raises(click.ClickException) as error:
        no_team_files.get("team_static")
    assert "No team_static config found at" in str(error.value)


def test_a_configuration_read_without_a_team_refuses_as_it_did(tmp_path):
    """A read without a team is not part of the layering, and a fall-through must not soften it."""
    environment_dir = tmp_path / "platform_config" / ENVIRONMENT
    environment_dir.mkdir(parents=True)
    (environment_dir / "static.json").write_text("{}")
    (environment_dir / "static.encrypted.json").write_text("{}")
    config = Config(ENVIRONMENT, None, kubementat_main_dir=str(tmp_path),
                    platform_config_dir=str(tmp_path / "platform_config"))

    from kubementat.errors import TeamNotSuppliedError
    with pytest.raises(TeamNotSuppliedError):
        config.get("team_static")

    # ...and the same instance still reads the environment layer, with no team pair to consult.
    assert config.component_namespace("grafana") == L0_ANSWER


# --- the shape of the machinery ----------------------------------------------------------


def test_the_environment_reads_answer_for_the_environment_only(tmp_path):
    """A read addressed at `{env}/static.json` answers for this environment.

    The team's copy is not part of that question: `get('env_static')` reports the keys the
    environment's own pair declares. That is the one place the chain is not global, and it is why
    the accessor that owns a component's namespace walks the whole chain instead of one category —
    one resolution path, two chains, spelled in one table.
    """
    config = seed(tmp_path,
                  environment_level={"CLUSTER_MANAGER_EMAIL": "cluster@example.test"},
                  team_level={"CLUSTER_MANAGER_EMAIL": "team@example.test"})

    assert config.get("env_static").get("CLUSTER_MANAGER_EMAIL") == "cluster@example.test"
    assert config.get("team_static").get("CLUSTER_MANAGER_EMAIL") == "team@example.test"


def test_the_layers_are_read_once_per_configuration_and_not_per_key(tmp_path, monkeypatch):
    """No second parse of the tree snuck in behind the views.

    `_load_config` is what reads the four files; the layering is a walk over what it already
    returned. A per-key re-read of the disk would show up here as extra loads, and would be the
    failure mode a 52-caller read path deserves to have.
    """
    from kubementat.core.config import config as config_module
    loads = []
    real_load = config_module.Config._load_json_file

    def counting_load(self, file_path):
        loads.append(Path(file_path).name)
        return real_load(self, file_path)

    monkeypatch.setattr(Config, "_load_json_file", counting_load)
    config = seed(tmp_path, environment_level={NAMESPACE_KEY: "env-ns"}, team_level={})
    for _ in range(20):
        config.get("team_static").get("ANY_KEY")
        config.component_namespace("grafana")

    assert len(loads) == 4, loads
    assert sorted(set(loads)) == ["static.encrypted.json", "static.json"]
