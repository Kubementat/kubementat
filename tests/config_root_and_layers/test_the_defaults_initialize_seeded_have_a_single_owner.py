"""The ten defaults `initialize`'s templates carried have one owner, and it is `constants.py`.

Census L measured that these ten keys were answered *only* by the template copies `kmt initialize`
wrote into `platform_config/`. That is not a hole `initialize`'s deletion would open — it is a hole
that is already open: a tree prepared by `config init` has no template copy in it, so it answers
almost nothing of the set, and every reader that asks a file gets `None` and carries on. So the
values went into `constants.DEFAULT_STATIC_ANSWERS` and the read path consults that table as `L0`
under the layers: the team's pair, then the environment's, then the product's own default.

The point of this file is not the ten answers. Anyone can write `assert answer == "1.17.1"`. The
point is **who answers them**, and which of the three possible owners did:

* a **layer that declared it** — the operator's file, which outranks everything;
* **`constants.py`** — the product's own default, which is what this ticket moved;
* **the process environment** — the owner that is there for helmfile's templates and is *not* an
  answer for a Python read, because an answer that only works after somebody exported it is the
  answer that goes missing in the next command nobody thought of.

So every test below names the layer it came from, and one test per key rather than one for the set:
a single test over ten keys is how a key nobody wired survives, because the assertion that never
runs for the key that is missing is worth nothing (the same lesson `config_derived.py`'s tests had
to learn).

Falsifications this file is written to catch, in order of how much they cost:

* a key added to the table with **no reader** — one test per key, and each one reads through the
  reader that key actually has, not through the table (`test_a_key_added_to_the_table_with_no_reader_...`
  says what that costs);
* a **second owner** re-seeded somewhere in `src/` — the literal scan;
* a reader that **answers out of `os.environ`** because something exported first;
* the two namespace keys **"completed" into this table** later, which would put a second owner on a
  value that is computed.
"""

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
    """A `Config` over a tree that declares nothing, or declares only what the test handed.

    A `config init` tree is the input every test wants: the two pairs exist and the plain files carry
    nothing (or `{}`), because that is the state census L found and the state this table exists for.
    The root is named and never located, so the fixture names it.
    """
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
    """The report's own answers keyed by key name.

    `config_answers.effective_answers` is what `kmt config show` prints, and it is the same
    `_resolved` call every verb makes, so a test that believed a private precedence table here could
    not disagree with the listing the way two walkers can.
    """
    return {answer.key: answer for answer in config_answers.effective_answers(root, environment, team)}


def answered_by(config, key: str) -> str:
    """Which owner answered `key`: a layer's tag, or `default`."""
    return answered(config.platform_config_dir, ENVIRONMENT,
                    config.team if config.team else None)[key].origin


# --- the ten answers, one test per key ---------------------------------------------------------


def test_a_tree_that_declared_nothing_still_answers_the_pipelines_version_and_l0_said_so(tmp_path):
    """`TEKTON_VERSION_PIPELINE` comes from `constants.py`, and what reads it is `install`'s list.

    `release_manifests_to_apply` builds the URLs it fetches out of the environment view, so the read
    that matters is the view and not the table.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_VERSION_PIPELINE"] == "1.17.1"
    assert answered_by(config, "TEKTON_VERSION_PIPELINE") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_dashboard_version_and_l0_said_so(tmp_path):
    """`TEKTON_VERSION_DASHBOARD` is answered for a tree that never wrote it — applied second,
    because it reads the Pipelines API the release above brings."""
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_VERSION_DASHBOARD"] == "0.73.0"
    assert answered_by(config, "TEKTON_VERSION_DASHBOARD") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_triggers_version_and_l0_said_so(tmp_path):
    """`TEKTON_VERSION_TRIGGERS`, whose release also answers the interceptor ConfigMap the trigger
    controller cannot start without."""
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_VERSION_TRIGGERS"] == "0.37.2"
    assert answered_by(config, "TEKTON_VERSION_TRIGGERS") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_ci_image_name_and_l0_said_so(tmp_path):
    """`TEKTON_CI_IMAGE_NAME`, read by `CiUtils.ci_image()` as part of one reference.

    The image is three keys and no half of one, so this is the key whose reader used to raise when
    nothing was configured. It does not raise for a *missing* key any more — an omitted key is
    answered — and it still raises for a key that was declared and said nothing.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_CI_IMAGE_NAME"] == "ubuntu-ci-minimal"
    assert answered_by(config, "TEKTON_CI_IMAGE_NAME") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_ci_image_tag_and_l0_said_so(tmp_path):
    """`TEKTON_CI_IMAGE_TAG` is `latest`, which is what the tree always ran; the value did not move."""
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_CI_IMAGE_TAG"] == "latest"
    assert answered_by(config, "TEKTON_CI_IMAGE_TAG") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_storage_class_and_l0_said_so(tmp_path):
    """`TEKTON_KUBERNETES_STORAGE_CLASS`, substituted into a generated PipelineRun's workspace.

    The reader here is `STORAGE_CLASS_PLACEHOLDER`, which is the clearest case of "works in the
    report, fails in the guest": a `None` substituted into a manifest is a manifest that applies.
    """
    config = seed(tmp_path)

    assert config.get("env_static")["TEKTON_KUBERNETES_STORAGE_CLASS"] == "local-path"
    assert answered_by(config, "TEKTON_KUBERNETES_STORAGE_CLASS") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_deployer_service_account(tmp_path):
    """`HELM_DEPLOYER_SERVICE_ACCOUNT_NAME` is asked of the **team's** file, and answered anyway.

    This is the one key of the ten whose template sits under `team/`, so a reader that only consulted
    the environment would have answered it from the wrong layer. `kubementat/config/init.py` reads it through
    `get('team_static')` twice over, which is what makes the category part of the claim.
    """
    config = seed(tmp_path)

    assert config.get("team_static")["HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"] == "helm-deployer-sa"
    assert answered_by(config, "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_git_project_name_and_l0_said_so(tmp_path):
    """`AUTOMATION_GIT_PROJECT_NAME` is `kubementat`, the project half of the git coordinate."""
    config = seed(tmp_path)

    assert config.get("env_static")["AUTOMATION_GIT_PROJECT_NAME"] == "kubementat"
    assert answered_by(config, "AUTOMATION_GIT_PROJECT_NAME") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_git_revision_and_l0_said_so(tmp_path):
    """`AUTOMATION_GIT_REVISION` is `main`, the half of the coordinate that changes without an edit."""
    config = seed(tmp_path)

    assert config.get("env_static")["AUTOMATION_GIT_REVISION"] == "main"
    assert answered_by(config, "AUTOMATION_GIT_REVISION") == "default"


def test_a_tree_that_declared_nothing_still_answers_the_ingress_class_and_l0_said_so(tmp_path):
    """`TRAEFIK_INGRESS_CLASS`, which is read as a component key and not as a bare name.

    The read is `_component_value(COMPONENT, "INGRESS_CLASS", DEFAULT_INGRESS_CLASSES)`, the same one
    `install_routing` and `create-ingress` make, so the key here is
    `component_config_key("traefik", "INGRESS_CLASS")` — which is this table's spelling too, and is
    how a test can say "the same key" without quoting it.

    The answer is still `L0` from `constants.py`, and that is the half of this row that matters: the
    fallback this read *used* to make was a literal written in `routing.py`, which was a second owner
    of the same word. It is now read out of this table by the table's own module, so the two paths
    agree by construction and not by whoever remembered to edit both.
    """
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
    """The whole set, in one test, because the per-key tests may not all be run by every runner.

    The assertion is the pair `(value, layer)` for each key: an answer with no layer named is the
    claim that got checked, not the answer itself.
    """
    config = seed(tmp_path)

    for key in TEN_KEYS:
        answer = answered(config.platform_config_dir, ENVIRONMENT, TEAM)[key]
        assert answer.origin == "default", f"{key} was answered by {answer.origin}, not L0"
        assert answer.value == constants.DEFAULT_STATIC_ANSWERS[key], key
        assert answer.source is None, f"{key} claims a file answered it: {answer.source}"
        assert config_answers.default_source(key) == config_answers.CONSTANTS_DEFAULT_SOURCE


def test_what_an_operator_declared_still_answers_the_ten_and_beats_the_default_for_all_of_them(
        tmp_path):
    """One test for the set and not one key, because precedence that holds for one key is precedence
    that holds until it does not.

    Every key is written into the **environment's** file with a value that cannot be mistaken for the
    default, and every one of them must come back as the declared value at the `environment` layer.
    The team's file then wins over it, key by key, which is `L2 > L1` and not a second claim.
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
    """The one distinction L0 must never collapse: "not declared" is not "declared as nothing".

    A default that answers an *omitted* key is what this table is for. The same default answering a key
    the operator wrote down as `null` is a refusal turned into a version being installed, and the two
    states differ by one character in a file. This is asserted as a pair for exactly that reason —
    either half alone would pass while the guard next door had been quietly rewritten to fire on
    nothing at all.

    `config set` will not produce the `null` half (`config set K=null` is refused and pointed at
    `unset`), so the state is reachable only by editing a file, which is the case the guard exists for
    and not a reason to skip it.
    """
    from kubementat import errors
    from kubementat.infrastructure.tekton import install as tekton_install

    omitted = seed(tmp_path / "omitted")
    assert tekton_install.release_manifests_to_apply(omitted.get("env_static")), \
        "an omitted pin no longer resolves to kubementat's own version, which is the point of L0"

    declared_null = seed(tmp_path / "declared",
                         environment_static={"TEKTON_VERSION_PIPELINE": None})
    with pytest.raises(errors.TektonReleaseVersionNotConfiguredError):
        tekton_install.release_manifests_to_apply(declared_null.get("env_static"))
