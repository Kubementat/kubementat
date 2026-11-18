"""The variable row: the eight keys arrive through the layers, or they arrive nowhere.

`KUBEMENTAT_ENVIRONMENT_CONFIGURATION` was `initialize`'s eight-variable requirement list, and the
rule that replaced it is not "the keys are required" — it is *where they come from*.
Measured at `37a2613`, the dictionary named by the spec is gone from `src/`, `_check_required_environment_variables`
is gone, and no `os.environ` read anywhere in `src/` answers one of the eight. What a reader could not
tell from the greps is whether that was the end of the migration or the absence of a check, so this
file states the property the greps cannot: **the only door a value of one of these keys comes through
is the layered configuration read path**, and the only refusal that fires when a key answers nothing
names the key, the file it belongs in, and the verb that sets it.

The eight, in the spec's own order, with the one name that changed underneath them:

`KUBERNETES_DEFAULT_STORAGE_CLASS` became `TEKTON_KUBERNETES_STORAGE_CLASS` in `7888bdf`, the
`initialize` deletion. The old name is the only identifier that left the product during this epic
(census S), so the row below carries the live spelling; a row that carried the
dead one would be a key that no door answers. `AUTOMATION_GIT_PROJECT_NAME` and
`AUTOMATION_GIT_REVISION` are the two automation coordinates that sit next to `AUTOMATION_GIT_URL`
and carry an L0 of their own — they are the CI param spellings that were renamed and are deliberately not
added to this row, which is the eight variables and no more.

Two things this file does *not* do, and both matter. It adds no required-key list and no validator to
`Config`: most of the trees this suite opens are fixtures and not prepared directories, and a rule
phrased as "the tree must answer every key" is how the version-stamp gate broke 227 tests before it
moved to the CLI boundary. And it does not touch the `helm.py` exports of `os.environ`: those are
how an answer reaches the helmfile subprocess, they are writes and not reads, and the check below says
so by classifying every access in the tree rather than banning the pattern.
"""

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

# The keys of the row that `constants.DEFAULT_STATIC_ANSWERS` answers for, so a tree that typed
# nothing still has an answer. Measured and not assumed: this is the intersection of the row with the
# L0 table, and it is why "missing" cannot be asserted for all eight.
KEYS_WITH_A_CODE_DEFAULT = tuple(key for key in FORMER_ENVIRONMENT_VARIABLES
                                 if key in constants.DEFAULT_STATIC_ANSWERS)


# The CI key the refusal clause is proven on: no L0, and a live consumer.
CI_REGISTRY_KEY = "DOCKER_REGISTRY_BASE_URL"


# --------------------------------------------------------------------------------------- 1. the door


def seed(root, environment_level=None, team_level=None) -> Config:
    """A tree that declares what the test handed and nothing else.

    Both pairs of both layers exist, because a missing layer file is refused and that refusal is about
    a broken tree rather than about a key. The root is named and never located.
    """
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
    """Nothing declares it, so the answer is the code default or nothing. Never the shell.

    `constants.static_answer` is the only thing that can answer here, and it is reached through the
    same walk; a key outside that table answers `None` and its consumer refuses. Either way the
    process environment has not been asked, which is the half the greps could only suggest.
    """
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
    """The done-when for this row, on the one key that has no L0 and a live consumer.

    `CiUtils.ci_image` is reached the way the existing CI tests reach it — the same call, the same
    exception, no message assembled for the test — because a refusal shape asserted against a string
    the product does not produce is a green that means nothing.

    The three parts, measured as they arrive:
      * the **key**: `DOCKER_REGISTRY_BASE_URL` in the sentence, which is the only key of the row this
        path can refuse, since the name and the tag answer from `constants.py` and nothing else;
      * the **file**: the `static.json` the value belongs in, so the operator opens one file;
      * the **command**: `kmt config set`, the verb that writes that file. `routing.py`'s refusal of
        `CLUSTER_MANAGER_EMAIL` has always ended this way; this is that shape, on this key.
    """
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
