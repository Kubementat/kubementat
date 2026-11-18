"""The ten defaults `initialize`'s templates carried have one owner, and it is `constants.py`."""

import json

import pytest
from asset_paths import REPO_ROOT

from kubementat.config import answers as config_answers
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key

ENVIRONMENT = "l0env"
TEAM = "cartographers"

# The ten keys, exactly as `constants.DEFAULT_STATIC_ANSWERS` spells them, with the shipped template's
# line for each. The template is cited here rather than trusted: these files live apart and a value
# that stopped matching the template is a different product, not a rounding error.
# The two witnesses, read from `tests/fixtures/configuration_shape/` — see `tests/asset_paths.py`.
# They are not package data: nothing in a run opens them, and what they witness is this table.
TEMPLATE = (REPO_ROOT
            / "tests/fixtures/configuration_shape/environment/static.json.template")

TEN_KEYS = ("TEKTON_VERSION_PIPELINE", "TEKTON_VERSION_DASHBOARD", "TEKTON_VERSION_TRIGGERS",
            "TEKTON_CI_IMAGE_NAME", "TEKTON_CI_IMAGE_TAG", "TEKTON_KUBERNETES_STORAGE_CLASS",
            "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME", "AUTOMATION_GIT_PROJECT_NAME",
            "AUTOMATION_GIT_REVISION", "TRAEFIK_INGRESS_CLASS")


# Which category a reader asks. HELM_DEPLOYER_SERVICE_ACCOUNT_NAME lives in the team's file (the
# team template carries it), and the other nine in the environment's, which is what the substitution
# table in `generation.CONFIG_PLACEHOLDER_SOURCES` also says.
READ_CATEGORY = {key: "team_static" for key in ("HELM_DEPLOYER_SERVICE_ACCOUNT_NAME",)}
READ_CATEGORY.update({key: "env_static" for key in TEN_KEYS if key not in READ_CATEGORY})

SRC = REPO_ROOT / "src"


def seed(root, environment_static=None, team_static=None, write_team=True) -> Config:
    """A `Config` over a tree that declares nothing, or declares only what the test handed."""
    environment_dir = root / "platform_config" / ENVIRONMENT
    environment_dir.mkdir(parents=True, exist_ok=True)
    (environment_dir / "static.json").write_text(json.dumps(environment_static or {}))
    (environment_dir / "static.encrypted.json").write_text(json.dumps({}))
    if write_team:
        team_dir = environment_dir / TEAM
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "static.json").write_text(json.dumps(team_static or {}))
        (team_dir / "static.encrypted.json").write_text(json.dumps({}))
    return Config(ENVIRONMENT, TEAM if write_team else None,
                  kubementat_main_dir=str(root), platform_config_dir=str(root / "platform_config"))


def answered(root, environment: str, team=None) -> dict:
    """The report's own answers keyed by key name."""
    return {answer.key: answer for answer in config_answers.effective_answers(root, environment, team)}


def answered_by(config, key: str) -> str:
    """Which owner answered `key`: a layer's tag, or `default`."""
    return answered(config.platform_config_dir, ENVIRONMENT,
                    config.team if config.team else None)[key].origin


# --- the ten answers, one test per key ---------------------------------------------------------


def test_a_tree_that_declared_nothing_still_answers_the_pipelines_version_and_l0_said_so(tmp_path):
    """`TEKTON_VERSION_PIPELINE` comes from `constants.py`, and what reads it is `install`'s list.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_VERSION_PIPELINE"] == "1.17.1"
    assert answered_by(config, "TEKTON_VERSION_PIPELINE") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_dashboard_version_and_l0_said_so(tmp_path):
    """`TEKTON_VERSION_DASHBOARD` is answered for a tree that never wrote it — applied second, because
    it reads the Pipelines API the release above brings.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_VERSION_DASHBOARD"] == "0.73.0"
    assert answered_by(config, "TEKTON_VERSION_DASHBOARD") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_triggers_version_and_l0_said_so(tmp_path):
    """`TEKTON_VERSION_TRIGGERS`, whose release also answers the interceptor ConfigMap the trigger
    controller cannot start without.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_VERSION_TRIGGERS"] == "0.37.2"
    assert answered_by(config, "TEKTON_VERSION_TRIGGERS") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_ci_image_name_and_l0_said_so(tmp_path):
    """`TEKTON_CI_IMAGE_NAME`, read by `CiUtils.ci_image()` as part of one reference."""
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_CI_IMAGE_NAME"] == "ubuntu-ci-minimal"
    assert answered_by(config, "TEKTON_CI_IMAGE_NAME") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_ci_image_tag_and_l0_said_so(tmp_path):
    """`TEKTON_CI_IMAGE_TAG` is `latest`, which is what the tree always ran; the value did not move.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_CI_IMAGE_TAG"] == "latest"
    assert answered_by(config, "TEKTON_CI_IMAGE_TAG") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_storage_class_and_l0_said_so(tmp_path):
    """`TEKTON_KUBERNETES_STORAGE_CLASS`, substituted into a generated PipelineRun's workspace."""
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_KUBERNETES_STORAGE_CLASS"] == "local-path"
    assert answered_by(config, "TEKTON_KUBERNETES_STORAGE_CLASS") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_deployer_service_account(tmp_path):
    """`HELM_DEPLOYER_SERVICE_ACCOUNT_NAME` is asked of the **team's** file, and answered anyway."""
    config = seed(tmp_path)

    assert config.get("team_static")["HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"] == "helm-deployer-sa"
    assert answered_by(config, "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_git_project_name_and_l0_said_so(tmp_path):
    """`AUTOMATION_GIT_PROJECT_NAME` is `kubementat`, the project half of the git coordinate."""
    config = seed(tmp_path)

    assert config.get("env_static")["AUTOMATION_GIT_PROJECT_NAME"] == "kubementat"
    assert answered_by(config, "AUTOMATION_GIT_PROJECT_NAME") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_git_revision_and_l0_said_so(tmp_path):
    """`AUTOMATION_GIT_REVISION` is `main`, the half of the coordinate that changes without an edit.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["AUTOMATION_GIT_REVISION"] == "main"
    assert answered_by(config, "AUTOMATION_GIT_REVISION") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_ingress_class_and_l0_said_so(tmp_path):
    """`TRAEFIK_INGRESS_CLASS`, which is read as a component key and not as a bare name."""
    config = seed(tmp_path)

    answer = config._component_value(constants.COMPONENT, "INGRESS_CLASS",
                                     constants.DEFAULT_INGRESS_CLASSES)
    assert answer == "traefik"
    assert answered_by(config, component_config_key(constants.COMPONENT,
                                                    "INGRESS_CLASS")) == "default"
    assert constants.DEFAULT_INGRESS_CLASSES[constants.COMPONENT] == \
        constants.DEFAULT_STATIC_ANSWERS["TRAEFIK_INGRESS_CLASS"]
    # The key `constants.DEFAULT_INGRESS_CLASSES` indexes its own table with is spelled, not built.
    # It must still be the key `component_config_key` builds, because a key spelled in two places is
    # two owners of a naming rule: the day the two spellings disagree, this fallback answers a key no
    # layer carries and the operator's own `static.json` stops being read. That is a statement about
    # the naming behaviour and not about where the source lives, so it belongs here.
    assert "TRAEFIK_INGRESS_CLASS" == component_config_key(constants.COMPONENT, "INGRESS_CLASS")


# --- the set, and what it must not do ----------------------------------------------------------


def test_every_key_the_templates_carried_answers_from_l0_on_a_tree_that_declared_nothing(tmp_path):
    """The whole set, in one test, because the per-key tests may not all be run by every runner."""
    config = seed(tmp_path)

    for key in TEN_KEYS:
        answer = answered(config.platform_config_dir, ENVIRONMENT, TEAM)[key]
        assert answer.origin == "default", f"{key} was answered by {answer.origin}, not by kubementat's default"
        assert answer.value == constants.DEFAULT_STATIC_ANSWERS[key], key
        assert answer.source is None, f"{key} claims a file answered it: {answer.source}"
        assert config_answers.default_source(key) == config_answers.CONSTANTS_DEFAULT_SOURCE


def test_what_an_operator_declared_still_answers_the_ten_and_beats_the_default_for_all_of_them(
        tmp_path):
    """One test for the set and not one key, because precedence that holds for one key is precedence
    that holds until it does not.
    """
    config = seed(tmp_path,
                  environment_static={key: f"environment-said-{key.lower()}" for key in TEN_KEYS},
                  team_static={key: f"team-said-{key.lower()}" for key in TEN_KEYS})

    for key in TEN_KEYS:
        assert answered(config.platform_config_dir, ENVIRONMENT, TEAM)[key].value == f"team-said-{key.lower()}", key
        assert answered_by(config, key) == "team", key

    # ...and with no team named, the environment's own answer answers.
    environment_only = seed(tmp_path, environment_static={
        key: f"environment-said-{key.lower()}" for key in TEN_KEYS}, write_team=False)
    for key in TEN_KEYS:
        answer = answered(environment_only.platform_config_dir, ENVIRONMENT)[key]
        assert answer.value == f"environment-said-{key.lower()}", key
        assert answer.origin == "environment", key
        assert answer.source == environment_only.env_static_config_path, key


def test_an_absent_key_falls_to_kubementats_value_and_a_declared_null_still_refuses(tmp_path):
    """An absent key falls to kubementat's default; a key declared as null still refuses."""
    from kubementat import errors
    from kubementat.infrastructure.tekton import install as tekton_install

    omitted = seed(tmp_path / "omitted")
    assert tekton_install.release_manifests_to_apply(omitted.get("env_static")), \
        "an omitted pin no longer resolves to kubementat's own version, which is what the default is for"

    declared_null = seed(tmp_path / "declared",
                         environment_static={"TEKTON_VERSION_PIPELINE": None})
    with pytest.raises(errors.TektonReleaseVersionNotConfiguredError):
        tekton_install.release_manifests_to_apply(declared_null.get("env_static"))
