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

import ast
import json
from pathlib import Path

import pytest

from kubementat import config_answers
from kubementat.core.config import derived as config_derived
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key

ENVIRONMENT = "l0env"
TEAM = "cartographers"

# The ten keys, exactly as `constants.DEFAULT_STATIC_ANSWERS` spells them, with the shipped template's
# line for each. The template is cited here rather than trusted: these files live apart and a value
# that stopped matching the template is a different product, not a rounding error.
TEMPLATE = (Path(__file__).resolve().parents[1]
            / "src/kubementat/core/assets/templates/environment/static.json.template")
TEAM_TEMPLATE = (Path(__file__).resolve().parents[1]
                 / "src/kubementat/core/assets/templates/environment/team/static.json.template")

TEN_KEYS = ("TEKTON_VERSION_PIPELINE", "TEKTON_VERSION_DASHBOARD", "TEKTON_VERSION_TRIGGERS",
            "TEKTON_CI_IMAGE_NAME", "TEKTON_CI_IMAGE_TAG", "TEKTON_KUBERNETES_STORAGE_CLASS",
            "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME", "AUTOMATION_GIT_PROJECT_NAME",
            "AUTOMATION_GIT_REVISION", "TRAEFIK_INGRESS_CLASS")

# The two keys this table deliberately excludes. They are not here because they are hard to write,
# and they are not here as a to-do; the reason is in the failure message of the last test.
EXCLUDED_NAMESPACE_KEYS = ("PIPELINE_NAMESPACE", "APP_DEPLOYMENT_NAMESPACE")

# Which category a reader asks. HELM_DEPLOYER_SERVICE_ACCOUNT_NAME lives in the team's file (the
# team template carries it), and the other nine in the environment's, which is what the substitution
# table in `generation.CONFIG_PLACEHOLDER_SOURCES` also says.
READ_CATEGORY = {key: "team_static" for key in ("HELM_DEPLOYER_SERVICE_ACCOUNT_NAME",)}
READ_CATEGORY.update({key: "env_static" for key in TEN_KEYS if key not in READ_CATEGORY})

SRC = Path(__file__).resolve().parents[1] / "src"

# Values that mean something else in this product as well as the answer to one of these ten keys:
# `traefik` is the component's name and `kubementat` is the project's, so they appear all over the
# tree for reasons that have nothing to do with a default. The strict literal check below is still
# run for every other value, and the pair check (key *and* value on the same line) is run for all
# ten. Spelling the exemption out is what makes it a decision and not blindness.
GENERIC_VALUES = {constants.DEFAULT_STATIC_ANSWERS["TRAEFIK_INGRESS_CLASS"],
                  constants.DEFAULT_STATIC_ANSWERS["AUTOMATION_GIT_PROJECT_NAME"]}

# ...and the values that are not a second owner of a key but a fact about something else, cited where
# they are. `local-path` is this table's own answer for `TEKTON_KUBERNETES_STORAGE_CLASS` -- the key
# that took over `KUBERNETES_DEFAULT_STORAGE_CLASS` when `initialize` was deleted (`7888bdf`) -- so a
# file that cites it while talking about a cluster's volume classes is citing a fact and not writing
# a second default. The one module that ever held this allowance, `config_init.py`, is
# gone: the split spread its body across `src/kubementat/automations/`, and none of those files
# carries the literal (measured: the only `local-path` in `src/` is the owner's own entry in
# `constants.py`). So the table is empty and the guard is exactly as strict as it was -- a
# `local-path` in any of the slices still fails, which is what an allowance for a deleted file
# could no longer have kept true of.
UNRELATED_USES = {}


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
    the environment would have answered it from the wrong layer. `config_init.py` reads it through
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
    """`AUTOMATION_GIT_REVISION` is `master`, the half of the coordinate that changes without an edit."""
    config = seed(tmp_path)

    assert config.get("env_static")["AUTOMATION_GIT_REVISION"] == "master"
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


def test_constants_py_is_the_only_place_in_src_that_writes_these_values():
    """A second literal is how this migration gets undone by the next one.

    Two checks, and they catch different things:

    * the **value** check: the exact string equal to a default may not appear as a string literal
      anywhere in `src/**/*.py` but `constants.py`. That is the check that fails on someone typing
      `1.17.1` or `ubuntu-ci-minimal` into a reader — the re-seed this file exists to prevent;
    * the **pair** check: no line outside `constants.py` may name one of these ten keys *and* a
      string literal equal to its value, which catches the second owner that is spelled out (a
      fallback table like the one `routing.py` used to own) and is invisible to the
      value check because the word also names a component.

    Two values are exempt from the value check and are named as exempt, because `traefik` and
    `kubementat` are the component's and the project's names and appear all over the tree for
    unrelated reasons. The pair check still runs for them, which is the half that matters.

    `src/**/*.py` and not `src/**`: `helmfile.yaml.gotmpl` is the second owner of
    `TRAEFIK_INGRESS_CLASS`'s `default`, that is reported and deliberately not
    settled here, and a test that failed on it would be a test that forbids the ticket's own finding.
    """
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "constants.py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        allowed = UNRELATED_USES.get(str(path.relative_to(SRC.parent)), set())
        for key, value in constants.DEFAULT_STATIC_ANSWERS.items():
            if value in literals and value not in GENERIC_VALUES and value not in allowed:
                pytest.fail(f"{path.relative_to(SRC.parent)} contains the literal {value!r}, which is "
                            f"`constants.DEFAULT_STATIC_ANSWERS[{key!r}]`'s value. Put the read on the "
                            f"table instead of the literal: the table is the owner, and two owners "
                            f"agree only until one of them is edited.")
            for line in _code_lines(text):
                if key in line and value in line:
                    pytest.fail(f"{path.relative_to(SRC.parent)} writes {key} and {value!r} on the "
                                f"same line, which is a second answer to a question the table owns:\n"
                                f"    {line.strip()}")


def _code_lines(text: str) -> list:
    """The lines of *text* that are code and not prose.

    A docstring that explains the drift is not a second owner of a default, and a test that fails on
    prose would be edited to say the opposite rather than made true.
    """
    lines = text.splitlines()
    docstrings = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" in node.value:
            docstrings.update(range(node.lineno - 1, node.lineno + node.value.count("\n")))
    out = []
    for number, line in enumerate(lines):
        if number in docstrings or line.strip().startswith("#"):
            continue
        out.append(line)
    return out


def test_no_key_on_this_list_is_answered_from_the_process_environment(tmp_path, monkeypatch):
    """Nothing in this list may be answered by something somebody exported first.

    `helm.py` does export configuration into the environment the **manifest** reads — that is
    how helmfile's templates get their variables — and every one of those exports is of a namespace
    or a release knob, both of which the exporting function reads out of the layers itself. These ten
    keys are not exported, and they must not become answers by way of the environment either: the
    shape that goes wrong is a reader that works only after a previous command exported the value,
    which is the answer that disappears in the command nobody thought of.

    So each key is planted in `os.environ` with a value that differs from both the default and the
    declared value, and both reads are asserted: an undeclared tree still answers `constants.py`, and
    a declared tree still answers the file. A read that went to the environment would pass for the
    command that ran before this one and fail for this one.
    """
    declared = seed(tmp_path / "declared", environment_static={key: "declared-value" for key in TEN_KEYS})
    bare = seed(tmp_path / "bare")

    for key in TEN_KEYS:
        monkeypatch.setenv(key, "answered-from-the-environment")
        for config, expectation in ((bare, constants.DEFAULT_STATIC_ANSWERS[key]),
                                    (declared, "declared-value")):
            got = config.get(READ_CATEGORY[key])[key]
            assert got == expectation, (
                f"{key} answered {got!r} while {key} was in the process environment, which means the "
                f"environment answered and not the layer or L0")
        assert answered(bare.platform_config_dir, ENVIRONMENT, TEAM)[key].value == constants.DEFAULT_STATIC_ANSWERS[key]
        assert answered(declared.platform_config_dir, ENVIRONMENT, TEAM)[key].origin == "environment"


def test_these_two_keys_are_excluded_on_purpose_and_are_not_a_gap_someone_may_close(tmp_path):
    """`PIPELINE_NAMESPACE` and `APP_DEPLOYMENT_NAMESPACE` are **not** in this table. Say it loudly.

    Both templates carry them — the environment template does not, the team template says
    `dev1-pipelines` and `dev1` — and a reader who compares the ten here against the templates would
    conclude these two were forgotten. They were not: they are derivations, computed before this
    change and after it from the team's name at read time
    (`core/config/derived.py`). A `constants.py` default would be a second owner for a value that is
    *computed*, which is the one kind of second owner that reads as documentation and answers as a
    lie: it would outrank nothing, agree with nothing, and be edited by someone who assumed the table
    was where answers live.

    This test is written to fail for anybody who adds them, with the reason in the message, so that
    "completing" the table is a decision somebody has to make and not a slip.
    """
    for key in EXCLUDED_NAMESPACE_KEYS:
        assert key not in constants.DEFAULT_STATIC_ANSWERS, (
            f"{key} is in DEFAULT_STATIC_ANSWERS. It belongs to config_derived.RULES, which answers "
            f"it as a consequence of the team's name and not as a default: a team called "
            f"pandas gets `pandas-pipelines` and `pandas`, and a table entry would be a second owner "
            f"for a value that is computed. The template's `dev1`/`dev1-pipelines` were placeholders "
            f"for that substitution, not a default. If you believe the rule is wrong, change the "
            f"rule; do not add a default beside it."
        )
        assert key in config_derived.RULES, f"{key} should still be derived"

    config = seed(tmp_path)
    assert config.get("team_static")["PIPELINE_NAMESPACE"] == f"{TEAM}-pipelines"
    assert config.get("team_static")["APP_DEPLOYMENT_NAMESPACE"] == TEAM

    # `MYSQL_HOST` / `REDIS_MASTER_HOST` are derived for the same reason, and the encrypted half of
    # the set is generated by `config set --generate`: two correct answers that differ is
    # generation, and nothing here may invent one.
    for key in ("MYSQL_HOST", "REDIS_MASTER_HOST"):
        assert key not in constants.DEFAULT_STATIC_ANSWERS
        assert key in config_derived.RULES
    for key in ("MYSQL_ROOT_PASSWORD", "GRAFANA_ADMIN_PASSWORD"):
        assert key not in constants.DEFAULT_STATIC_ANSWERS


def test_the_values_still_match_the_templates_that_carried_them():
    """Nothing here is invented: every value equals what the shipped template says.

    The template is still the other copy — it dies with `initialize`, which is a later ticket — so
    this is the check that keeps the two agreeable until then, and it names the one place they are
    allowed to differ (the team template's `HELM_DEPLOYER_SERVICE_ACCOUNT_NAME`, which is the only one
    of the ten that lives under `team/`).
    """
    environment_copy = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    team_copy = json.loads(TEAM_TEMPLATE.read_text(encoding="utf-8"))

    for key in TEN_KEYS:
        value = constants.DEFAULT_STATIC_ANSWERS[key]
        source = environment_copy.get(key, team_copy.get(key))
        assert source == value, (f"{key} is {value!r} in constants.py and {source!r} in the templates")

    assert "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME" in team_copy
    assert "PIPELINE_NAMESPACE" not in environment_copy


def test_a_key_added_to_the_table_with_no_reader_is_the_thing_this_file_catches(tmp_path):
    """The falsification, made permanent.

    A table entry is not an answer. This is what an unwired key costs: the value is in `constants.py`
    and the view that `kmt install` reads does not answer it, which is a migration that reports "the
    defaults moved to L0" and fails in the guest. It is asserted for **every** key and not for one,
    because the single unwired key is the usual shape of the mistake.
    """
    config = seed(tmp_path)
    view = config.get("env_static")
    team_view = config.get("team_static")

    for key in constants.DEFAULT_STATIC_ANSWERS:
        answered = view.get(key) if READ_CATEGORY.get(key, "env_static") == "env_static" \
            else team_view.get(key)
        assert answered == constants.DEFAULT_STATIC_ANSWERS[key], (
            f"{key} is declared in constants.DEFAULT_STATIC_ANSWERS but no read path answers it — "
            f"the view said {answered!r}. Wiring the table into the walk is the other half of L0.")


def test_no_layer_of_the_read_path_looks_at_the_environment_for_any_of_the_ten():
    """The static half of the same claim: no line in `src/` opens `os.environ` for these keys.

    The dynamic test above proves the answer does not come from the environment in a running process;
    this one says why it cannot, by reading the tree. `helm.py`'s exports are assignment and not
    reads, and they address namespaces and release knobs, so the scan finds nothing — and if someone
    starts exporting one of these ten, the other half of this pair starts failing.
    """
    for path in sorted(SRC.rglob("*.py")):
        for line in _code_lines(path.read_text(encoding="utf-8")):
            if "os.environ" not in line:
                continue
            hit = [key for key in constants.DEFAULT_STATIC_ANSWERS if key in line]
            assert not hit, (f"{path.relative_to(SRC.parent)} touches os.environ on a line that names "
                             f"{', '.join(hit)}: {line.strip()}")


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
