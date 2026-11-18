"""`kmt config set` / `config unset` put one answer on one layer, and nothing else."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config.init import (
    CREATED,
    ENVIRONMENT_ENCRYPTED_STEP,
    ENVIRONMENT_STATIC_STEP,
    SKIPPED,
    TEAM_ENCRYPTED_STEP,
    TEAM_STATIC_STEP,
    init_configuration,
    set_answer,
    unset_answer,
)
from kubementat.core.config.config import Config

ENVIRONMENT = "prod"
TEAM = "alpha"

# The key every precedence test uses. `PIPELINE_NAMESPACE` is read by the pipeline layer, is not a
# cluster key, and is the shape of answer `config set --team` exists to make, so it is an honest
# example rather than a name invented for this file.
KEY = "PIPELINE_NAMESPACE"
ENVIRONMENT_ANSWER = "team-ci"
TEAM_ANSWER = "team-alpha"

# What `kmt config init` lays down, spelled relative to the root so a set difference reads as a write
# set and not as a guess about the layout.
INITIALIZED_LAYER = {Path("kubementat.yaml"), Path(ENVIRONMENT),
                     Path(ENVIRONMENT) / "static.json", Path(ENVIRONMENT) / "static.encrypted.json",
                     Path(ENVIRONMENT) / "tekton", Path(ENVIRONMENT) / "tekton" / "tasks",
                     Path(ENVIRONMENT) / "tekton" / "pipelines"}

ENV_STATIC = Path(ENVIRONMENT) / "static.json"
ENV_ENCRYPTED = Path(ENVIRONMENT) / "static.encrypted.json"
TEAM_STATIC = Path(ENVIRONMENT) / TEAM / "static.json"
TEAM_ENCRYPTED = Path(ENVIRONMENT) / TEAM / "static.encrypted.json"


def config_root(tmp_path) -> Path:
    """A configuration directory that exists and holds nothing."""
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    return root


def initialized_root(tmp_path) -> Path:
    """A tree `kmt config init` made for one environment — the precondition both verbs insist on."""
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT)
    return root


def layered(root: Path, team=None) -> Config:
    """The `Config` a reader would use for that tree — the same construction the library makes."""
    return Config(ENVIRONMENT, team, platform_config_dir=root, preload_configs=True)


def properties(root: Path, team=None) -> Config:
    """The same `Config`, unloaded — for its paths, which is what a step's line must be checked
    against.
    """
    return Config(ENVIRONMENT, team, platform_config_dir=root, preload_configs=False)


def tree(root: Path) -> set[Path]:
    """Everything under `root`, relative to it. Directories included: they are part of the tree."""
    assert root.is_dir(), f"{root} is not a directory, so there is nothing to enumerate"
    return {path.relative_to(root) for path in root.rglob("*")}


def files(root: Path) -> dict[Path, bytes]:
    """Every file's bytes, keyed by its path under the root."""
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def stamp(root: Path) -> dict[Path, int]:
    """Every file's mtime, for the claim that a verb wrote one file and left the rest alone."""
    return {path.relative_to(root): path.stat().st_mtime_ns
            for path in root.rglob("*") if path.is_file()}


def document(root: Path, relative: Path) -> dict:
    """A layer file read as JSON. For the two claims that are about the file itself: order, and keys
    that belong to someone else. Every other assertion goes through `Config`.
    """
    return json.loads((root / relative).read_text(encoding="utf-8"))


def run_verb(*arguments):
    """`kmt config <verb> …`, invoked through the group so the verb's own name is exercised."""
    # The root is the caller's second argument and it travels as `--config-dir`, which is the
    # only spelling the surface gives a root, so what is checked here is the command somebody can
    # actually type and not a private argv this file invented.
    verb, root, *rest = arguments
    result = CliRunner(mix_stderr=False).invoke(
        config_module.config,
        [verb] + [str(argument) for argument in rest] + ["--config-dir", str(root)])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def printed(result) -> list[str]:
    """The stdout lines, which are the steps and nothing else. Stderr is guidance, not output."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def reported(result) -> str:
    return f"{result.stdout}{result.stderr}"


def refused(*arguments) -> str:
    """Run a verb expecting a refusal, and return the sentence it said."""
    result = run_verb(*arguments)
    assert result.exit_code != 0, reported(result)
    assert not printed(result), f"the refusal printed steps on stdout: {reported(result)}"
    return reported(result)


def added_team(root: Path) -> None:
    """The team layer, made the way the surface makes it: `kmt config add-team`."""
    result = run_verb("add-team", root, ENVIRONMENT, TEAM)
    assert result.exit_code == 0, reported(result)


# --- (1) the environment's file answers, read back through `Config` --------------------------------


# --- (2) precedence, asserted by who answers -------------------------------------------------------


# --- (3) the contract: unset on the team returns the environment's answer ------------------------


# --- (4) the encrypted pair: one answer, two spellings, both cleared -------------------------------


def test_an_encrypted_answer_lands_once_and_is_cleared_from_both_files_of_the_layer(tmp_path):
    """Test 4: `set --encrypted` writes one file, `unset` clears the pair, and the layer above answers.
    """
    root = initialized_root(tmp_path)
    run_verb("set", root, ENVIRONMENT, "GRAFANA_ADMIN_PASSWORD=env-secret")
    added_team(root)

    result = run_verb("set", root, ENVIRONMENT, "GRAFANA_ADMIN_PASSWORD=team-secret",
                      "--team", TEAM, "--encrypted")

    assert printed(result) == [f"{CREATED} {properties(root, TEAM).team_static_encrypted_config_path}"], \
        printed(result)
    assert document(root, TEAM_ENCRYPTED)["GRAFANA_ADMIN_PASSWORD"] == "team-secret"
    assert document(root, TEAM_STATIC) == {}, "an encrypted answer does not belong in the plain file"
    assert layered(root, TEAM).get('team_static_encrypted')['GRAFANA_ADMIN_PASSWORD'] == "team-secret"

    result = run_verb("unset", root, ENVIRONMENT, "GRAFANA_ADMIN_PASSWORD", "--team", TEAM)

    config = properties(root, TEAM)
    assert printed(result) == [f"{CREATED} {config.team_static_encrypted_config_path}",
                               f"{SKIPPED} {config.team_static_config_path}"], printed(result)
    assert "GRAFANA_ADMIN_PASSWORD" not in document(root, TEAM_ENCRYPTED)
    assert "GRAFANA_ADMIN_PASSWORD" not in document(root, TEAM_STATIC)
    # What the read side must *not* show after this unset is the team's answer. It may well show an
    # answer, because that is what a layer means: `config init` generated the environment's copy of
    # this key precisely so helmfile's requiredEnv would be satisfied, so a cleared team layer reveals
    # the environment's value rather than a void. Asserting a void here would have been asserting
    # something the layering rule forbids -- and before generation it happened to pass only because the
    # environment's encrypted file was empty, which is a fact about init's output and not about unset.
    read_encrypted = layered(root, TEAM).get('team_static_encrypted')
    assert read_encrypted.get("GRAFANA_ADMIN_PASSWORD") != "team-secret", \
        "unset cleared the file and not the answer the read side gives"
    assert read_encrypted.get("GRAFANA_ADMIN_PASSWORD") == document(root, ENV_ENCRYPTED)[
        "GRAFANA_ADMIN_PASSWORD"], \
        "a cleared team layer should answer with the environment's value and nothing else"
    assert layered(root, TEAM).get('team_static')['GRAFANA_ADMIN_PASSWORD'] != "team-secret"


# --- (5) the read-modify-write duty: keys nobody told us about ------------------------------------


def test_an_answer_that_changed_is_overwritten_and_one_that_did_not_change_is_skipped(tmp_path):
    """Test 5b: `set`'s rule over an *answer*, next to `init`'s rule over a *file*."""
    root = initialized_root(tmp_path)
    run_verb("set", root, ENVIRONMENT, f"{KEY}={ENVIRONMENT_ANSWER}")

    result = run_verb("set", root, ENVIRONMENT, f"{KEY}=team-elsewhere")

    assert printed(result) == [f"{CREATED} {properties(root).env_static_config_path}"], printed(result)
    assert document(root, ENV_STATIC)[KEY] == "team-elsewhere"

    untouched = files(root)
    again = run_verb("set", root, ENVIRONMENT, f"{KEY}=team-elsewhere")

    assert printed(again) == [f"{SKIPPED} {properties(root).env_static_config_path}"], printed(again)
    assert files(root) == untouched, "skipped is a claim about bytes and not a claim about wording"
    assert tree(root) == INITIALIZED_LAYER

    with pytest.raises(Exception) as refused:
        init_configuration(root, ENVIRONMENT)
    assert "already exists and is not what kubementat would write" in str(refused.value), \
        "init's rule over a file that differs is what would be lost by conflating the two rules"


# --- (6) the three refusals ------------------------------------------------------------------------


def test_a_missing_layer_is_refused_and_names_the_verb_that_makes_it(tmp_path):
    """Test 6a: a layer is made by `init` / `add-environment` / `add-team`, and not by an answer."""
    root = initialized_root(tmp_path)
    before = files(root)

    sentence = refused("set", root, "staging", "X=1")
    assert "add-environment" in sentence and "config init" in sentence, sentence
    assert "staging" not in {path.parts[0] for path in tree(root)}, "the refusal made a directory"
    assert files(root) == before

    sentence = refused("unset", root, "staging", "X")
    assert "add-environment" in sentence, sentence

    sentence = refused("set", root, ENVIRONMENT, "X=1", "--team", TEAM)
    assert "add-team" in sentence, sentence
    assert "not a layer" in sentence, sentence
    assert TEAM_STATIC not in tree(root), "a refusal that writes is a tree that lied"

    sentence = refused("unset", root, ENVIRONMENT, "X", "--team", TEAM)
    assert "add-team" in sentence, sentence
    assert files(root) == before, "an unset into a layer that does not exist wrote something"

    # The half-pair, made by hand because that is how it would happen.
    (root / ENVIRONMENT / TEAM).mkdir(parents=True)
    (root / ENVIRONMENT / TEAM / "static.json").write_text("{}\n", encoding="utf-8")
    sentence = refused("set", root, ENVIRONMENT, "X=1", "--team", TEAM)
    assert "add-team" in sentence, sentence
    assert document(root, TEAM_STATIC) == {}, "the plain file was written by a refusal"


def test_a_null_value_is_refused_and_names_config_unset(tmp_path):
    """Test 6b: `null` is the absence of an answer, and only one verb should create it."""
    root = initialized_root(tmp_path)
    before = files(root)

    sentence = refused("set", root, ENVIRONMENT, f"{KEY}=null")

    assert "config unset" in sentence, sentence
    assert "null" in sentence, sentence
    assert files(root) == before
    assert KEY not in document(root, ENV_STATIC)


def test_an_unset_of_a_key_no_layer_declares_is_refused_and_lists_what_it_searched(tmp_path):
    """Test 6c: a no-op is not a success, so the refusal says which files it opened."""
    root = initialized_root(tmp_path)
    before = files(root)
    config = properties(root)

    sentence = refused("unset", root, ENVIRONMENT, "NO_KEY_ANSWERS_THIS")

    assert str(config.env_static_config_path) in sentence, sentence
    assert str(config.env_static_encrypted_config_path) in sentence, sentence
    assert files(root) == before

    # A key the *environment* declares is declared somewhere in the scope a team answer sits in, so
    # it is not a typo: the team's own files say `skipped`, nothing is written, and the layer above is
    # still what answers. Clearing the environment's answer is the same verb with no `--team`, which is
    # the sentence the refusal above points at instead.
    run_verb("set", root, ENVIRONMENT, f"{KEY}={ENVIRONMENT_ANSWER}")
    added_team(root)
    untouched = files(root)

    result = run_verb("unset", root, ENVIRONMENT, KEY, "--team", TEAM)

    config = properties(root, TEAM)
    assert printed(result) == [f"{SKIPPED} {config.team_static_encrypted_config_path}",
                              f"{SKIPPED} {config.team_static_config_path}"], printed(result)
    assert files(root) == untouched, "a scope that declares nothing anywhere still wrote bytes"
    assert layered(root, TEAM).get('team_static')[KEY] == ENVIRONMENT_ANSWER


# --- (7) the one key refusal, for the one-directory-one-cluster reason ---------------------------


def test_the_cluster_keys_on_the_team_layer_are_refused_and_the_environment_layer_answers_for_them(tmp_path):
    """Test 7: one directory is exactly one cluster, so a team cannot answer for it."""
    root = initialized_root(tmp_path)
    added_team(root)
    before = files(root)

    for key in ("CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH"):
        sentence = refused("set", root, ENVIRONMENT, f"{key}=somewhere", "--team", TEAM)
        assert "one directory is exactly one cluster" in sentence.lower(), sentence
        assert key in sentence, sentence
    assert files(root) == before, "a refused key wrote into the tree"
    assert document(root, TEAM_ENCRYPTED) == {}
    assert document(root, TEAM_STATIC) == {}

    accepted = run_verb("set", root, ENVIRONMENT, "CLUSTER_KUBE_CONTEXT=prod-cluster")
    assert accepted.exit_code == 0, reported(accepted)
    assert document(root, ENV_STATIC)["CLUSTER_KUBE_CONTEXT"] == "prod-cluster"
    assert "does not address a cluster" not in accepted.stderr, \
        "the sentence was printed about a cluster the command was just handed"


# --- (8) the write set is the key and nothing else -------------------------------------------------


# --- (9) `--set`'s parser is still this verb's parser ---------------------------------------------


# --- the library functions, as the CLI's shape -----------------------------------------------------


def test_the_library_returns_the_pair_in_the_order_a_reader_resolves_it(tmp_path):
    """The step names and their order, straight from `set_answer` / `unset_answer`."""
    root = initialized_root(tmp_path)

    steps = set_answer(root, ENVIRONMENT, KEY, ENVIRONMENT_ANSWER)
    assert [(step.name, step.action) for step in steps] == [(ENVIRONMENT_STATIC_STEP, CREATED)]
    assert steps[0].path == properties(root).env_static_config_path

    steps = unset_answer(root, ENVIRONMENT, KEY)
    assert [(step.name, step.action) for step in steps] == [(ENVIRONMENT_ENCRYPTED_STEP, SKIPPED),
                                                            (ENVIRONMENT_STATIC_STEP, CREATED)]

    added_team(root)
    steps = set_answer(root, ENVIRONMENT, KEY, TEAM_ANSWER, team=TEAM, encrypted=True)
    assert [(step.name, step.action) for step in steps] == [(TEAM_ENCRYPTED_STEP, CREATED)]
    steps = unset_answer(root, ENVIRONMENT, KEY, team=TEAM)
    assert [(step.name, step.action) for step in steps] == [(TEAM_ENCRYPTED_STEP, CREATED),
                                                            (TEAM_STATIC_STEP, SKIPPED)]
