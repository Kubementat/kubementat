"""The variable row: the eight keys arrive through the layers, or they arrive nowhere."""

import ast
import json

import pytest
from asset_paths import REPO_ROOT

import kubementat.automations.ci as ci_utils
from kubementat.automations.ci import CiUtils
from kubementat.config import answers as config_answers
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.errors import CiImageNotConfiguredError

REPO = REPO_ROOT
SRC = REPO / "src"

ENVIRONMENT = "eightenv"
TEAM = "eightteam"

# The eight keys, at the spellings `src/` answers today. The value of each entry is what
# the key is *for*, because a row that is only names is how a list rots: the sentence is the part a
# reader checks a change against.
FORMER_ENVIRONMENT_VARIABLES = {
    "BASE_DOMAIN": "the domain every generated host is built under",
    "AUTOMATION_GIT_URL": "the automation remote kubementat clones and pushes",
    "AUTOMATION_GIT_SERVER_HOST": "the host half of that remote's coordinate",
    "AUTOMATION_GIT_SERVER_PORT": "the port half of that remote's coordinate",
    "AUTOMATION_GIT_SERVER_SSH_USER": "the user half of that remote's coordinate",
    # `KUBERNETES_DEFAULT_STORAGE_CLASS`, renamed when `initialize` went (`7888bdf`).
    "TEKTON_KUBERNETES_STORAGE_CLASS": "the volume class a generated PipelineRun asks for",
    "DOCKER_REGISTRY_BASE_URL": "the registry half of the CI image reference",
    "CLUSTER_MANAGER_EMAIL": "the ACME contact every ClusterIssuer registers",
}

# The keys kubementat has a default for (`constants.DEFAULT_STATIC_ANSWERS`): a tree that declares
# nothing still gets an answer for these, so only the others can be asserted missing.
KEYS_WITH_A_CODE_DEFAULT = tuple(key for key in FORMER_ENVIRONMENT_VARIABLES
                                 if key in constants.DEFAULT_STATIC_ANSWERS)


# A key with no kubementat default and a live reader, so its absence must refuse.
CI_REGISTRY_KEY = "DOCKER_REGISTRY_BASE_URL"


# --------------------------------------------------------------------------------------- 1. the door


def seed(root, environment_level=None, team_level=None) -> Config:
    """A tree that declares what the test handed and nothing else."""
    environment_dir = root / "platform_config" / ENVIRONMENT
    team_dir = environment_dir / TEAM
    team_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in ((environment_dir / "static.json", environment_level or {}),
                          (environment_dir / "static.encrypted.json", {}),
                          (team_dir / "static.json", team_level or {}),
                          (team_dir / "static.encrypted.json", {})):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def _answers(config, key: str):
    """One key's answer as the report gives it, from the same walk every verb uses."""
    return {answer.key: answer
            for answer in config_answers.effective_answers(config.platform_config_dir,
                                                           ENVIRONMENT, TEAM)}[key]


@pytest.mark.parametrize("key", sorted(FORMER_ENVIRONMENT_VARIABLES))
def test_a_key_the_row_leaves_to_l0_never_arrives_from_the_process_environment(tmp_path, key):
    """Nothing declares it, so the answer is the code default or nothing. Never the shell."""
    config = seed(tmp_path, environment_level={}, team_level={})
    expected = constants.static_answer(key)  # None for the keys outside the table.

    assert config.get("env_static").get(key) == expected
    if key in KEYS_WITH_A_CODE_DEFAULT:
        assert key == "TEKTON_KUBERNETES_STORAGE_CLASS"
        answer = _answers(config, key)
        assert answer.origin == config_answers.DEFAULT_ORIGIN
        assert answer.default == expected
    else:
        assert expected is None
# --------------------------------------------------------------------- 2. no key arrives from the shell


def _is_environ(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os") \
        or (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Attribute) and _is_environ(node.value))


# -------------------------------------------------------------------------------------------- 3. the refusal


def test_the_registry_url_the_configuration_never_answers_refuses_with_the_verb_that_sets_it(
        tmp_path):
    """A key with no default and no answer refuses, naming the verb that sets it."""
    config = seed(tmp_path, environment_level={}, team_level={})
    assert CI_REGISTRY_KEY not in config.get("env_static"), "the fixture declared the key itself"

    ci = CiUtils.__new__(CiUtils)
    ci.config = config
    with pytest.raises(CiImageNotConfiguredError) as error:
        ci_utils.CiUtils.ci_image(ci)

    message = str(error.value)
    assert CI_REGISTRY_KEY in message, message
    assert str(config.env_static_config_path) in message, \
        "the refusal has to name the file the answer belongs in"
    assert "kmt config set" in message, message
    assert error.value.config_path == config.env_static_config_path
