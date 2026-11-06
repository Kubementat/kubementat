"""`kmt config add-environment` / `add-team` put one layer on a tree, and nothing else.

Four claims, and the first two are what separates these verbs from `config init`.

**A layer is added and not started.** `kubementat.yaml` is what says which product a tree is for, and
one verb writes it, so `add-environment` refuses a root with no manifest and `add-team` refuses an
environment that was never laid down. Nothing is written on the way out of either refusal: an
additive verb that bootstraps is a second `init` with a different name, and the rule — no second
function creates a layer — is what both of them here are held to.

**One layer, and no answers.** The write set is the environment's two static files plus its two
`tekton/` directories, or the team's two empty files plus its four layer directories. No template is
copied, no namespace is written, no cluster key goes into a team's file (`cluster.py:737` and
`:898` read the environment's file and no other, and one directory is exactly one cluster). Where a
key belongs it is `null`, the same way `init` writes it, because `config set` is the verb that answers
and these two verbs write no answer at all.

**The directories are the properties.** Both verbs go through `Config` and through the same plan and
directory primitives `init_configuration` uses, so the assertion is `step.path == the property's path`
and not a string typed into a test: a test that spells the path cannot tell a moved property from a
moved directory. The step *names* are init's too, which is the point of the rule rather than a
coincidence — two verbs that add a layer print the same sentence about a layer.

**Idempotence is the same claim.** Adding what is already there is all `skipped`, no bytes, no error.
A file that is there and differs is refused by init's rule, naming the path.
"""

import json
import logging
from pathlib import Path

import pytest
from click.testing import CliRunner

from asset_paths import asset_path
from cli.commands import config as config_module
from kubementat import config_init
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.config_init import (CREATED, SKIPPED, TEAM_STATIC_STEP, add_environment, add_team,
                                    init_configuration)

ENVIRONMENT = "dev"
NEW_ENVIRONMENT = "staging"
TEAM = "ops"

# The tree that `init` lays down for one environment, and what a second environment or a team layer
# adds to it. Spelled as paths because this whole file is a claim about exactly these paths.
MANIFEST = Path("kubementat.yaml")
ENV_STATIC = Path(ENVIRONMENT) / "static.json"

NEW_ENVIRONMENT_FILES = {Path(NEW_ENVIRONMENT) / "static.json",
                         Path(NEW_ENVIRONMENT) / "static.encrypted.json"}
# The whole of what one environment layer adds, directories included. The two that hold the files are
# part of the layer and not evidence of anything else, so a set difference that leaves them out reads
# as a verb that wrote an extra path.
NEW_ENVIRONMENT_LAYER = (NEW_ENVIRONMENT_FILES | {Path(NEW_ENVIRONMENT),
                                                  Path(NEW_ENVIRONMENT) / "tekton",
                                                  Path(NEW_ENVIRONMENT) / "tekton" / "tasks",
                                                  Path(NEW_ENVIRONMENT) / "tekton" / "pipelines"})
TEAM_FILES = {Path(NEW_ENVIRONMENT) / TEAM / "static.json",
              Path(NEW_ENVIRONMENT) / TEAM / "static.encrypted.json"}
TEAM_LAYER = (TEAM_FILES | {Path(NEW_ENVIRONMENT) / TEAM,
                            Path(NEW_ENVIRONMENT) / TEAM / "tekton",
                            Path(NEW_ENVIRONMENT) / TEAM / "tekton" / "tasks",
                            Path(NEW_ENVIRONMENT) / TEAM / "tekton" / "pipelines",
                            Path(NEW_ENVIRONMENT) / TEAM / "tekton" / "pipeline-runs",
                            Path(NEW_ENVIRONMENT) / TEAM / "tekton" / "triggers"})

# The names of everything the package ships under `templates/`, as the copy pass would name it: a
# destination is its template's name minus the `.template` suffix (`automation.py`'s own walk). What
# these verbs must not write is anything on this list *other than* the two static files, which they
# do write, empty and by rule rather than by copying a shipped document into the tree.
SHIPPED_TEMPLATE_DESTINATIONS = {path.name[: -len(".template")]
                                 for path in asset_path("templates").rglob("*.template")}
LAYER_FILES = ("static.json", "static.encrypted.json")


def config_root(tmp_path) -> Path:
    """A configuration directory that exists and holds nothing."""
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    return root


def initialized_root(tmp_path) -> Path:
    """A tree `config init` made for one environment — the precondition both verbs insist on."""
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT)
    return root


def layers(root, environment, team=None, preload=False) -> Config:
    """The `Config` the readers would use for that tree — the same construction the verbs make.

    `preload` is False for the path assertions, which want the properties and not the files, and True
    for the readback in test 8, whose whole claim is what a loaded configuration answers.
    """
    return Config(environment, team, platform_config_dir=root, preload_configs=preload)


def tree(root: Path) -> set[Path]:
    """Everything under `root`, relative to it. Directories included: they are part of the tree."""
    assert root.is_dir(), f"{root} is not a directory, so there is nothing to enumerate"
    return {path.relative_to(root) for path in root.rglob("*")}


def snapshot(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def documents(root: Path) -> dict[Path, object]:
    return {path.relative_to(root): json.loads(path.read_text(encoding="utf-8"))
            for path in root.rglob("*.json") if path.is_file()}


def run_verb(verb, *arguments):
    """`kmt config <verb> …`, invoked through the group so the verb's own name is exercised.

    A bug inside the command is re-raised rather than folded into an exit code, and a refusal is not:
    the library raises a `ClickException` to say "I will not write", and standing up as
    `SystemExit` is how that answer reaches a terminal. The distinction is the whole test for the
    refusals, so a raised `ValueError` cannot hide behind `exit_code == 1`.
    """
    result = CliRunner(mix_stderr=False).invoke(config_module.config, [verb, *[str(a) for a in arguments]])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def printed(result) -> list[str]:
    """The stdout lines, which are the steps and nothing else. Stderr is guidance, not output."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def reported(result) -> str:
    return f"{result.stdout}{result.stderr}"


def step_paths(steps, step_name) -> list[Path]:
    return [step.path for step in steps if step.name == step_name]


def refuse(verb, *arguments) -> str:
    """The one refusal sentence, checked to be a refusal and not a crash."""
    result = run_verb(verb, *arguments)
    assert result.exit_code != 0, reported(result)
    assert not printed(result), f"the refusal printed steps on stdout: {reported(result)}"
    return reported(result)


def environment_layer_paths(root, environment) -> list[Path]:
    """The directory paths an environment layer owes, in the order they are applied, from `Config`."""
    config = layers(root, environment)
    return [config.tekton_env_task_dir, config.tekton_env_pipeline_dir]


def team_layer_paths(root, environment, team) -> list[Path]:
    """The directory paths a team layer owes, in the order they are applied, from `Config`."""
    config = layers(root, environment, team)
    return [config.tekton_team_task_dir, config.tekton_team_pipeline_dir,
            config.tekton_team_pipeline_run_dir, config.tekton_team_trigger_dir]


# --- (1) one environment layer lands on a tree that already exists --------------------------------


def test_add_environment_lands_the_layer_on_a_tree_init_made(tmp_path, caplog):
    """Test 1: the environment's two files and two `tekton/` directories appear, and nothing else.

    `tree(after) - tree(before)` is the set, so a fifth path that rode along is a failure here and not
    an extra line of output. The manifest is compared as bytes because its two keys are the answer to
    a question only `init` may ask.
    """
    root = initialized_root(tmp_path)
    before = tree(root)
    manifest_before = (root / MANIFEST).read_bytes()

    # The cluster sentence is a warning the verb logs and not a line it echoes, so the capture here is
    # `caplog` and not the runner's streams. Cleared first: `initialized_root` logged the same sentence
    # about `dev`, and this assertion is about the verb run below.
    with caplog.at_level(logging.WARNING):
        caplog.clear()
        result = run_verb("add-environment", root, NEW_ENVIRONMENT)

    assert result.exit_code == 0, reported(result)
    assert tree(root) - before == NEW_ENVIRONMENT_LAYER
    assert (root / MANIFEST).read_bytes() == manifest_before, "add-environment touched the manifest"
    assert len(printed(result)) == 4, printed(result)
    assert len({line for line in printed(result)}) == 4, "one step printed twice is two write claims"
    # The four lines, in order, as `created <path>` — the property paths, so the print is checked
    # against where the readers look and not against strings this file typed.
    config = layers(root, NEW_ENVIRONMENT)
    assert printed(result) == [
        f"{CREATED} {path}"
        for path in (config.env_static_config_path, config.env_static_encrypted_config_path,
                     config.tekton_env_task_dir, config.tekton_env_pipeline_dir)], printed(result)
    assert "does not address a cluster yet" in caplog.text, \
        "the new environment reaches no cluster, and the verb that created it has to say so"
    assert NEW_ENVIRONMENT in caplog.text


# --- (2) the second run ---------------------------------------------------------------------------


def test_the_second_add_environment_reports_every_step_skipped_and_writes_no_bytes(tmp_path):
    """The idempotence claim, held to a second verb: adding what is there changes nothing.

    Four `skipped` lines on stdout and a byte-for-byte identical tree. `created` on a second run would
    claim a write that did not happen, which is the same lie the file steps must not tell, and a
    rewrite that happened to land on identical bytes would still fail the snapshot even though the
    step list stayed honest.
    """
    root = initialized_root(tmp_path)
    run_verb("add-environment", root, NEW_ENVIRONMENT)
    before = snapshot(root)

    result = run_verb("add-environment", root, NEW_ENVIRONMENT)

    assert result.exit_code == 0, reported(result)
    assert printed(result) == [
        f"{SKIPPED} {path}"
        for path in (layers(root, NEW_ENVIRONMENT).env_static_config_path,
                     layers(root, NEW_ENVIRONMENT).env_static_encrypted_config_path,
                     layers(root, NEW_ENVIRONMENT).tekton_env_task_dir,
                     layers(root, NEW_ENVIRONMENT).tekton_env_pipeline_dir)], printed(result)
    assert snapshot(root) == before, "the second add-environment wrote bytes over an untouched layer"


# --- (3) refuse to bootstrap ----------------------------------------------------------------------


def test_add_environment_refuses_a_root_with_no_manifest_and_names_config_init(tmp_path):
    """No manifest is no tree, and no layer is a tree. One sentence, one refusal, nothing written.

    `kubementat.yaml` is written by exactly one verb, so the refusal does more than say "not found":
    it names `kmt config init` as the one way to make the thing that is missing. The empty tree is the
    other half — a verb that got to this conclusion by writing the manifest on the way would leave a
    directory behind.
    """
    root = config_root(tmp_path)

    message = refuse("add-environment", root, NEW_ENVIRONMENT)

    assert "kmt config init" in message, message
    assert "kubementat.yaml" in message, message
    assert tree(root) == set(), "the refusal wrote part of a tree on its way out"


def test_add_team_refuses_a_root_that_is_not_a_directory_at_all_and_says_which_root(tmp_path):
    """The ordinary refusal, for the root that is not there. `tmp_path` exists; the root does not.

    "Not a configuration directory" is the same sentence in both verbs, and the root is the fact in it:
    an operator who mistyped the directory should hear which one this verb looked at.
    """
    missing = tmp_path / "nowhere"

    message = refuse("add-team", missing, NEW_ENVIRONMENT, TEAM)

    assert str(missing) in message, message
    assert "not a configuration directory" in message, message
    assert "kmt config init" in message, message


# --- (4) the team's layer --------------------------------------------------------------------------


def test_add_team_writes_two_empty_documents_and_the_four_directories_the_properties_name(tmp_path):
    """The team's layer is `{}` twice and four directories, and the paths *are* the properties.

    The documents are asserted as empty and not as present: an empty file that exists and a file with
    a cluster key in it both satisfy `is_file()`, and only one of them is a team layer — the other is
    a claim that a team addresses a cluster of its own, which is the measured wrong answer
    (`cluster.py:737`, `:898`: one directory is exactly one cluster, and the reader opens the
    environment's file). The four directories are compared against `Config`, so a moved property is a
    failed assertion here and not a path that quietly keeps matching itself.
    """
    root = initialized_root(tmp_path)
    run_verb("add-environment", root, NEW_ENVIRONMENT)
    before = tree(root)

    steps = add_team(root, NEW_ENVIRONMENT, TEAM)

    documents_ = documents(root)
    assert documents_[Path(NEW_ENVIRONMENT) / TEAM / "static.json"] == {}, \
        "the team's static file carries an answer; that is config set --team's job"
    assert documents_[Path(NEW_ENVIRONMENT) / TEAM / "static.encrypted.json"] == {}, \
        "the team's encrypted file carries an answer"
    team_config = layers(root, NEW_ENVIRONMENT, TEAM)
    assert step_paths(steps, TEAM_STATIC_STEP) == [team_config.team_static_config_path]
    assert step_paths(steps, config_init.TEAM_ENCRYPTED_STEP) == [
        team_config.team_static_encrypted_config_path]
    assert step_paths(steps, config_init.TEAM_TEKTON_STEP) == \
        team_layer_paths(root, NEW_ENVIRONMENT, TEAM)
    assert all(step.action == CREATED for step in steps), [str(step) for step in steps]

    added = tree(root) - before
    assert added == TEAM_LAYER, added


# --- (5) a team needs the environment under it ------------------------------------------------------


def test_add_team_refuses_an_environment_that_was_never_added_and_names_add_environment(tmp_path):
    """No environment is no layer to sit under, so the refusal names the verb that makes one.

    This is `init_configuration`'s refusal of `--team` without `--environment`, said to an operator
    who is already standing at a tree. The whole tree is snapshotted around the run: `static.json` for
    the team under a directory the environment never had is the exact half-write this rule exists to
    prevent, and a step list would say `created` about it.
    """
    root = initialized_root(tmp_path)
    before = snapshot(root)

    message = refuse("add-team", root, "never-added", TEAM)

    assert "kmt config add-environment" in message, message
    assert "static.json" in message, message
    assert snapshot(root) == before, "the refusal wrote the team layer beside no environment"
    assert not (root / "never-added" / TEAM).exists()


# --- (6) the second team ---------------------------------------------------------------------------


def test_the_second_add_team_reports_every_step_skipped(tmp_path):
    """The same claim as the second `add-environment`, about four directories and two files.

    Six lines, all `skipped`, and a tree that cannot tell the run happened. A `created` here would
    train an operator to expect writes from a verb that does none — and to reach for the one flag that
    must mean something else, `force`, which these verbs do not even take.
    """
    root = initialized_root(tmp_path)
    run_verb("add-environment", root, NEW_ENVIRONMENT)
    add_team(root, NEW_ENVIRONMENT, TEAM)
    before = snapshot(root)

    result = run_verb("add-team", root, NEW_ENVIRONMENT, TEAM)

    assert result.exit_code == 0, reported(result)
    team_config = layers(root, NEW_ENVIRONMENT, TEAM)
    assert printed(result) == [
        f"{SKIPPED} {path}"
        for path in (team_config.team_static_config_path, team_config.team_static_encrypted_config_path,
                     team_config.tekton_team_task_dir, team_config.tekton_team_pipeline_dir,
                     team_config.tekton_team_pipeline_run_dir,
                     team_config.tekton_team_trigger_dir)], printed(result)
    assert snapshot(root) == before


# --- (7) the names init refuses, these verbs refuse too --------------------------------------------

RESERVED = list(config_init.RESERVED_ROOT_NAMES)
LAYER = list(config_init.LAYER_DIRECTORY_NAMES)


@pytest.mark.parametrize("name", RESERVED)
def test_add_environment_refuses_a_reserved_root_name_the_way_init_does(tmp_path, name):
    """The reserved-name rule, said by the verb that adds a layer: `secrets` is not an environment's name.

    One refusal, the value quoted back, and the whole tree untouched. The name check runs before the
    plan exists, which is why nothing can have been written on the way to the sentence — the shape of
    the assertion is `init`'s, and these two verbs are held to it with the same table.
    """
    root = initialized_root(tmp_path)
    before = snapshot(root)

    message = refuse("add-environment", root, name)

    assert repr(name) in message, message
    assert "is already taken" in message, message
    assert snapshot(root) == before


@pytest.mark.parametrize("name", LAYER)
def test_add_team_refuses_a_team_wearing_a_layer_directory_name(tmp_path, name):
    """A team that shares a name with `tekton` or `values` is one directory two rules both claim.

    `kubementat_components` is refused for the reason it is filtered out of the team listing already.
    Same table as `init`, same sentence, and a tree that stays as it was — a layer verb that let a
    reserved name through would be a way to make the tree the epic deleted.
    """
    root = initialized_root(tmp_path)
    run_verb("add-environment", root, NEW_ENVIRONMENT)
    before = snapshot(root)

    message = refuse("add-team", root, NEW_ENVIRONMENT, name)

    assert repr(name) in message, message
    assert "is already taken" in message, message
    assert snapshot(root) == before


# --- (8) neither verb writes product shape ---------------------------------------------------------


def test_neither_layer_verb_writes_a_product_shaped_file_and_a_default_still_answers(tmp_path,
                                                                                      caplog):
    """No product shape in a grown tree, asked of the two verbs that grow it instead of making one.

    Three things have to be true at once, and only the third proves the first two:

    * no `REPLACEME_` string anywhere in the tree — that marker is what a copied template leaves
      behind, and a layer that has one in it was copied and not written;
    * no file named after a shipped template's destination, other than the two static files these
      verbs write empty and by rule. `mirrored_docker_images.json` and `values.encrypted.yaml` are the
      names that would appear if a template walker had been reached for;
    * a `Config` reading the new layer still answers a namespace from `constants.py` **and warns that
      it did** — the one assertion that says no file in the tree answered a key a code default owns.

    The whole file set is checked too, because a shape claim that inspects only names would miss a
    template copied under a name that is not shipped, and because "one layer and nothing else" is
    exactly the kind of sentence a set difference settles.
    """
    root = initialized_root(tmp_path)
    run_verb("add-environment", root, NEW_ENVIRONMENT)
    run_verb("add-team", root, NEW_ENVIRONMENT, TEAM)

    files = {path: (root / path).read_bytes() for path in tree(root) if (root / path).is_file()}
    assert not [path for path, payload in files.items() if b"REPLACEME_" in payload], \
        "a layer verb copied a shipped template into the tree"
    written_names = {path.name for path in files}
    assert not written_names & (SHIPPED_TEMPLATE_DESTINATIONS - set(LAYER_FILES)), \
        f"{written_names & SHIPPED_TEMPLATE_DESTINATIONS} is a template's destination and not an answer"

    expected_files = ({MANIFEST, ENV_STATIC, Path(ENVIRONMENT) / "static.encrypted.json"}
                      | NEW_ENVIRONMENT_FILES | TEAM_FILES)
    assert set(files) == expected_files, set(files) ^ expected_files

    config = layers(root, NEW_ENVIRONMENT, TEAM, preload=True)
    with caplog.at_level(logging.WARNING):
        namespace = config.component_namespace("grafana")

    assert namespace == constants.DEFAULT_NAMESPACES["grafana"]
    assert "GRAFANA_DEPLOYMENT_NAMESPACE" in caplog.text, \
        "the default did not answer, so some file the layer verbs wrote did"
