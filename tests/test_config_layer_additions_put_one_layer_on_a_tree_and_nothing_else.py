"""`kmt config add-environment` / `add-team` put one layer on a tree, and nothing else.

Four claims, and the first two are what separates these verbs from `config init`.

**A layer is added and not started.** `kubementat.yaml` is what says which product a tree is for, and
one verb writes it, so `add-environment` refuses a root with no manifest and `add-team` refuses an
environment that was never laid down. Nothing is written on the way out of either refusal: an
additive verb that bootstraps is a second `init` with a different name, and the rule — no second
function creates a layer — is what both of them here are held to.

**One layer, and no answers.** The write set is the environment's two static files plus its two
`tekton/` directories, or the team's two empty files plus its four layer directories. No template is
copied, no namespace is written, no cluster key goes into a team's file (`EnvironmentRegistry.resolve`
and `_environment_declaration`, both in `core/identity/registry.py`, read the environment's file and no
other, and one directory is exactly one cluster). Where a
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
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import init as config_init
from kubementat.core.config.config import Config
from kubementat.config.init import (SKIPPED, add_team,
                                    init_configuration)

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


def run_verb(verb, root, *arguments):
    """`kmt config <verb> --config-dir <root> …`, through the group so the verb's name is exercised.

    A bug inside the command is re-raised rather than folded into an exit code, and a refusal is not:
    the library raises a `ClickException` to say "I will not write", and standing up as
    `SystemExit` is how that answer reaches a terminal. The distinction is the whole test for the
    refusals, so a raised `ValueError` cannot hide behind `exit_code == 1`.

    The root is the second argument and it travels as `--config-dir`, because that is the only way
    the surface names a root: a test that handed one over any other way would be asserting about a
    command nobody can type.
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


# --- (8) neither verb writes product shape ---------------------------------------------------------
