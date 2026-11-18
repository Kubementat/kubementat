"""`kmt config add-environment` / `add-team` put one layer on a tree, and nothing else."""

import json
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import init as config_init
from kubementat.config.init import SKIPPED, add_team, init_configuration
from kubementat.core.config.config import Config

ENVIRONMENT = "dev"
NEW_ENVIRONMENT = "staging"
TEAM = "ops"

# The tree that `init` lays down for one environment, and what a second environment or a team layer
# adds to it. Spelled as paths because this whole file is a claim about exactly these paths.
MANIFEST = Path("kubementat.yaml")
ENV_STATIC = Path(ENVIRONMENT) / "static.json"


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
    """The `Config` the readers would use for that tree — the same construction the verbs make."""
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


def run_verb(verb, root, *arguments):
    """`kmt config <verb> --config-dir <root> …`, through the group so the verb's name is exercised.
    """
    result = CliRunner(mix_stderr=False).invoke(
        config_module.config,
        [verb, *[str(a) for a in arguments], "--config-dir", str(root)])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def printed(result) -> list[str]:
    """The stdout lines, which are the steps and nothing else. Stderr is guidance, not output."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def reported(result) -> str:
    return f"{result.stdout}{result.stderr}"


def refuse(verb, root, *arguments) -> str:
    """The one refusal sentence, checked to be a refusal and not a crash."""
    result = run_verb(verb, root, *arguments)
    assert result.exit_code != 0, reported(result)
    assert not printed(result), f"the refusal printed steps on stdout: {reported(result)}"
    return reported(result)


# --- (1) one environment layer lands on a tree that already exists --------------------------------


# --- (2) the second run ---------------------------------------------------------------------------


def test_the_second_add_environment_reports_every_step_skipped_and_writes_no_bytes(tmp_path):
    """The idempotence claim, held to a second verb: adding what is there changes nothing."""
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
    """
    root = config_root(tmp_path)

    message = refuse("add-environment", root, NEW_ENVIRONMENT)

    assert "kmt config init" in message, message
    assert "kubementat.yaml" in message, message
    assert tree(root) == set(), "the refusal wrote part of a tree on its way out"


def test_add_team_refuses_a_root_that_is_not_a_directory_at_all_and_says_which_root(tmp_path):
    """The ordinary refusal, for the root that is not there. `tmp_path` exists; the root does not.
    """
    missing = tmp_path / "nowhere"

    message = refuse("add-team", missing, NEW_ENVIRONMENT, TEAM)

    assert str(missing) in message, message
    assert "not a configuration directory" in message, message
    assert "kmt config init" in message, message


# --- (4) the team's layer --------------------------------------------------------------------------


# --- (5) a team needs the environment under it ------------------------------------------------------


def test_add_team_refuses_an_environment_that_was_never_added_and_names_add_environment(tmp_path):
    """No environment is no layer to sit under, so the refusal names the verb that makes one."""
    root = initialized_root(tmp_path)
    before = snapshot(root)

    message = refuse("add-team", root, "never-added", TEAM)

    assert "kmt config add-environment" in message, message
    assert "static.json" in message, message
    assert snapshot(root) == before, "the refusal wrote the team layer beside no environment"
    assert not (root / "never-added" / TEAM).exists()


# --- (6) the second team ---------------------------------------------------------------------------


def test_the_second_add_team_reports_every_step_skipped(tmp_path):
    """The same claim as the second `add-environment`, about four directories and two files."""
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


# --- (8) neither verb writes product shape ---------------------------------------------------------
