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

import pytest

from kubementat.core import constants
from kubementat.automations.ci import CiUtils
from kubementat.errors import (CiImageNotConfiguredError, ClusterManagerEmailNotConfiguredError, GrafanaAdminCredentialsNotConfiguredError, TeamConfigurationFileMissingError)
from kubementat.core.config.config import Config
from kubementat.automations.routing import routing
from kubementat.infrastructure.helm import HelmUtils

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


# --- (a) the highest layer that declares the key is the one that answers ------------------


def test_the_teams_layer_answers_before_the_environments_layer(tmp_path):
    """L2 > L1, key by key, for the key `constants.py` also owns an answer for."""
    config = seed(tmp_path,
                  environment_level={NAMESPACE_KEY: "environment-namespace"},
                  team_level={NAMESPACE_KEY: "team-namespace"})

    assert config.component_namespace("grafana") == "team-namespace"


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


# --- (c) a key with no default anywhere answers exactly as it did -------------------------


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

    Same class, same sentence, for the four shapes of refusal the tree has: a named error that
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
    with pytest.raises(GrafanaAdminCredentialsNotConfiguredError) as error:
        helm._grafana_admin_credentials()
    assert "GRAFANA_ADMIN_USER is not set in" in str(error.value)

    config = seed(tmp_path, environment_level={})
    with pytest.raises(ClusterManagerEmailNotConfiguredError) as error:
        routing._cluster_manager_email(config)
    assert "CLUSTER_MANAGER_EMAIL is not set in" in str(error.value)

    # A team that names no file is a different fact from a key that is absent: the file's name is
    # in the error, and the layering does not talk its way past it. Seeded on its own root, so the
    # team files this test wrote for the reads above cannot answer for it.
    no_team_files = seed(tmp_path / "no-team-tree", environment_level={}, team_files_exist=False)
    with pytest.raises(TeamConfigurationFileMissingError) as error:
        no_team_files.get("team_static")
    assert "No team_static config found at" in str(error.value)


# --- the shape of the machinery ----------------------------------------------------------


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
