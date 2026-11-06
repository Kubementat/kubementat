"""`config init` makes the layer directories its readers open, and refuses names it cannot use.

Two claims, and both are census claims and not opinions.

**The directories are the properties.** Census G found `<environment>/tekton/tasks` and
`<environment>/tekton/pipelines` read at two sites in `src/` and created by nothing, which is how a
layer that is correct in code lands in a tree that is empty there. So init creates them, through
`Config`'s properties — and the assertion is `path == the property's path`, not a string typed into a
test, because a test that spells the path cannot tell a moved property from a moved directory. A run
with `--team` adds the team's four, where `Config.tekton_team_*` says they live.

**The directories not created are not an omission.** The environment's `tekton/triggers` and
`tekton/pipeline-runs` are read by nothing — the apply path that opens a trigger directory is
team-only, and a pipeline run is written when it happens and not looked for as a layer. A directory
that exists and is read by nothing is the shape of `kubementat_components/`, which the epic deleted,
so their absence is asserted and the *reason* is the failure message. `values/`, `values.encrypted/`
and `secrets/` are absent for the reasons written at the step.

**A name is decided before the first byte.** An environment named after something kubementat puts at
the root of a configuration directory would be read as that root entry and not as a layer; the named
case is an environment called `secrets`. A team named after one of kubementat's own layer directories
is a directory two rules both claim; a name that is not one path element cannot be found by a reader
at all. Each is one refusal, and nothing is written when one is raised.

Idempotence is the same rule that governs the files: a directory that is already there reports
`skipped` and is not made twice, so a second init is a no-op about directories exactly as it is about
bytes.
"""

import click
import pytest

from kubementat import config_init
from kubementat.core.config.config import Config
from kubementat.config_init import CREATED, SKIPPED, init_configuration
from kubementat.errors import TeamNotSuppliedError

ENVIRONMENT = "dev"
TEAM = "ops"

# The step names, imported from the module that decides them: this file asserts which job ran, and a
# renamed step is a changed sentence on a terminal and so belongs in the same place.
ENVIRONMENT_STEP = config_init.ENVIRONMENT_TEKTON_STEP
TEAM_STEP = config_init.TEAM_TEKTON_STEP


def config_root(tmp_path):
    """A configuration directory that exists and holds nothing."""
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    return root


def unbuilt_root(tmp_path):
    """A root that does not exist yet, for the refusals: `not root.exists()` is the whole verdict."""
    return tmp_path / "platform_config"


def layers(root, environment, team=None):
    """The `Config` the readers would use for that tree — the same construction init makes."""
    return Config(environment, team, platform_config_dir=root, preload_configs=False)


def tree(root):
    """Every path under `root`, relative to it. Directories included: they are the subject here."""
    return {path.relative_to(root) for path in root.rglob("*")}


def directory_steps(steps):
    """Only the layer-directory steps, as `(name, path, action)`. Files stay out of this claim."""
    return [(step.name, step.path, step.action) for step in steps
            if step.name in (ENVIRONMENT_STEP, TEAM_STEP)]


def the_layer_directories(root, environment, team=None):
    """Every directory init creates, keyed by the property that names it. Spelled once, used thrice."""
    config = layers(root, environment, team)
    found = {"tekton_env_task_dir": config.tekton_env_task_dir,
             "tekton_env_pipeline_dir": config.tekton_env_pipeline_dir}
    if team is not None:
        found.update({
            "tekton_team_task_dir": config.tekton_team_task_dir,
            "tekton_team_pipeline_dir": config.tekton_team_pipeline_dir,
            "tekton_team_pipeline_run_dir": config.tekton_team_pipeline_run_dir,
            "tekton_team_trigger_dir": config.tekton_team_trigger_dir,
        })
    return found


# --- (1) the directories that are created, asserted against the properties -----------------------


def test_init_makes_the_layer_directories_and_each_one_is_the_paths_the_readers_open(tmp_path):
    """Both layers, every directory, and each one checked as *that property's* path.

    `is_dir()` and not `exists()`: a file sitting where a layer belongs would satisfy the second and
    leave the reader with a directory it cannot list.
    """
    root = config_root(tmp_path)

    init_configuration(root, ENVIRONMENT, TEAM)

    expected = {
        "tekton_env_task_dir": root / ENVIRONMENT / "tekton" / "tasks",
        "tekton_env_pipeline_dir": root / ENVIRONMENT / "tekton" / "pipelines",
        "tekton_team_task_dir": root / ENVIRONMENT / TEAM / "tekton" / "tasks",
        "tekton_team_pipeline_dir": root / ENVIRONMENT / TEAM / "tekton" / "pipelines",
        "tekton_team_pipeline_run_dir": root / ENVIRONMENT / TEAM / "tekton" / "pipeline-runs",
        "tekton_team_trigger_dir": root / ENVIRONMENT / TEAM / "tekton" / "triggers",
    }
    found = the_layer_directories(root, ENVIRONMENT, TEAM)
    assert set(found) == set(expected), "the set of layer properties init has to reach"
    for name, path in expected.items():
        assert found[name] == path, f"{name} does not resolve where this tree puts that layer"
        assert found[name].is_dir(), f"{name} ({found[name]}) is not a directory init created"


def test_the_steps_name_the_layer_directory_and_the_layer_it_belongs_to(tmp_path):
    """The print is a claim about the tree, so the step carries the layer and not `mkdir`.

    Environment first, then team, in the order the layers apply: this asserts the order and the
    action for a first run in one shape, and `steps` is what the verb prints verbatim.
    """
    root = config_root(tmp_path)

    steps = init_configuration(root, ENVIRONMENT, TEAM)

    found = the_layer_directories(root, ENVIRONMENT, TEAM)
    assert directory_steps(steps) == [
        (ENVIRONMENT_STEP, found["tekton_env_task_dir"], CREATED),
        (ENVIRONMENT_STEP, found["tekton_env_pipeline_dir"], CREATED),
        (TEAM_STEP, found["tekton_team_task_dir"], CREATED),
        (TEAM_STEP, found["tekton_team_pipeline_dir"], CREATED),
        (TEAM_STEP, found["tekton_team_pipeline_run_dir"], CREATED),
        (TEAM_STEP, found["tekton_team_trigger_dir"], CREATED),
    ]


def test_the_environment_directories_nothing_reads_are_absent_and_the_reason_is_the_message(tmp_path):
    """Two candidates from the same census stay unmade, and *why* is what the assert says.

    Both halves are census G: the trigger reader is team-only, so an environment `triggers/` would be
    a directory that exists and is read by nothing; a pipeline run is output. Creating either would
    recreate the dead `kubementat_components/` tree, which is the other half of the same finding.
    """
    root = config_root(tmp_path)

    init_configuration(root, ENVIRONMENT, TEAM)

    assert not (root / ENVIRONMENT / "tekton" / "triggers").exists(), (
        "nothing in `src/` opens an environment trigger directory — the apply path that reads one is "
        "team-only, so creating this would be a directory that exists and is read by nothing, the "
        "shape of the tree the epic deleted (census G, `Config.tekton_env_task_dir`'s note)")
    assert not (root / ENVIRONMENT / "tekton" / "pipeline-runs").exists(), (
        "an environment `tekton/pipeline-runs` has no reader either, and a pipeline run is output: "
        "written when a run happens, never looked for as a layer (census G)")


def test_init_makes_no_values_or_secrets_directory_and_nothing_a_default_already_answers(tmp_path):
    """`values/` and `values.encrypted/` are guarded by helmfile's `isFile` *because* they may be
    absent, and `secrets/` arrives with the keys in phase 5 — so a fresh tree is the environment's
    two directories, the team's four, and nothing else."""
    root = config_root(tmp_path)

    init_configuration(root, ENVIRONMENT, TEAM)

    for absent in ("values", "values.encrypted", "secrets", "kubementat_components"):
        assert not (root / ENVIRONMENT / absent).exists(), f"{absent} is not a layer init creates"
        assert not (root / absent).exists(), f"{absent} is not created at the root either"


# --- (2) no team, no team layer -------------------------------------------------------------------


def test_a_run_without_a_team_makes_no_team_layer_directory_at_all(tmp_path):
    """The same shape as the assertions above — the environment's two, and nothing that claims a team.

    The other half is that the properties themselves cannot be reached without a team: init does not
    invent a layer to put a directory in, and neither does `Config`.
    """
    root = config_root(tmp_path)

    steps = init_configuration(root, ENVIRONMENT)

    found = the_layer_directories(root, ENVIRONMENT)
    assert directory_steps(steps) == [
        (ENVIRONMENT_STEP, found["tekton_env_task_dir"], CREATED),
        (ENVIRONMENT_STEP, found["tekton_env_pipeline_dir"], CREATED),
    ]
    assert sorted(p.name for p in (root / ENVIRONMENT).iterdir()) == ["static.encrypted.json",
                                                                      "static.json",
                                                                      "tekton"], \
        "a team directory appeared without a team being named"
    config = layers(root, ENVIRONMENT)
    for name in ("tekton_team_task_dir", "tekton_team_pipeline_dir",
                 "tekton_team_pipeline_run_dir", "tekton_team_trigger_dir"):
        with pytest.raises(TeamNotSuppliedError):
            getattr(config, name)


# --- (3) a second run touches no directory --------------------------------------------------------


def test_a_second_run_reports_every_layer_directory_as_skipped_and_makes_no_new_one(tmp_path):
    """A directory that is there was put there. `created` on the second run would claim a write that
    did not happen, which is the same lie the file steps must not tell."""
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT, TEAM)
    before = tree(root)

    second = init_configuration(root, ENVIRONMENT, TEAM)

    found = the_layer_directories(root, ENVIRONMENT, TEAM)
    assert directory_steps(second) == [
        (ENVIRONMENT_STEP, found["tekton_env_task_dir"], SKIPPED),
        (ENVIRONMENT_STEP, found["tekton_env_pipeline_dir"], SKIPPED),
        (TEAM_STEP, found["tekton_team_task_dir"], SKIPPED),
        (TEAM_STEP, found["tekton_team_pipeline_dir"], SKIPPED),
        (TEAM_STEP, found["tekton_team_pipeline_run_dir"], SKIPPED),
        (TEAM_STEP, found["tekton_team_trigger_dir"], SKIPPED),
    ]
    assert tree(root) == before, "the second init made a directory the first one had not"


def test_a_tree_that_arrives_with_its_layers_already_on_disk_still_reports_skipped(tmp_path):
    """A directory is never foreign material, so an operator's tree is accepted and the step says
    `skipped` rather than `created` — the same rule that keeps `force` from meaning `overwrite`."""
    root = config_root(tmp_path)
    config = layers(root, ENVIRONMENT, TEAM)
    config.tekton_env_task_dir.mkdir(parents=True)
    config.tekton_team_trigger_dir.mkdir(parents=True)

    steps = init_configuration(root, ENVIRONMENT, TEAM)

    actions = {path: action for _, path, action in directory_steps(steps)}
    assert actions[config.tekton_env_task_dir] == SKIPPED
    assert actions[config.tekton_team_trigger_dir] == SKIPPED


# --- (4) the old trigger directory is refused, not scaffolded beside -------------------------------


def obsolete_trigger_dir(root, environment=ENVIRONMENT, team=TEAM):
    """The trigger directory an older kubementat wrote: `<env>/<team>/triggers/`, at the root."""
    return root / environment / team / "triggers"


def test_a_tree_with_the_old_trigger_directory_is_refused_and_nothing_is_written(tmp_path):
    """The upgrade case: an operator re-runs `config init` on a tree a pre-move kubementat laid down.

    Scaffolding `tekton/triggers/` beside the old `triggers/` would leave both, and only the new
    one is ever read — silently applying nothing is the failure this refusal exists to prevent,
    so init stops, names both paths, and the tree is byte-for-byte what it was.
    """
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT, TEAM)
    obsolete = obsolete_trigger_dir(root)
    obsolete.mkdir(parents=True)
    (obsolete / "kept-manifest.yml").write_text("apiVersion: triggers.tekton.dev/v1beta1\n")
    before = tree(root)

    with pytest.raises(click.ClickException) as excinfo:
        init_configuration(root, ENVIRONMENT, TEAM)

    message = str(excinfo.value)
    assert str(obsolete) in message, message
    assert str(obsolete.parent / "tekton" / "triggers") in message, message
    assert tree(root) == before, "the refusal moved, renamed or deleted part of the tree"


def test_removing_the_old_directory_by_hand_is_the_way_through(tmp_path):
    """The refusal names what closes the gap: the operator moves what they keep, by hand.

    kubementat records no rename table and rewrites no operator tree, so a moved path is refused by
    name and this is the whole migration path — asserted end to end: refuse, move, succeed.
    """
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT, TEAM)
    obsolete = obsolete_trigger_dir(root)
    obsolete.mkdir(parents=True)
    (obsolete / "kept-manifest.yml").write_text("apiVersion: triggers.tekton.dev/v1beta1\n")
    with pytest.raises(click.ClickException):
        init_configuration(root, ENVIRONMENT, TEAM)

    config = layers(root, ENVIRONMENT, TEAM)
    kept = config.tekton_team_trigger_dir / "kept-manifest.yml"
    kept.write_bytes((obsolete / "kept-manifest.yml").read_bytes())
    for entry in obsolete.iterdir():
        entry.unlink()
    obsolete.rmdir()

    steps = init_configuration(root, ENVIRONMENT, TEAM)
    assert kept.is_file()
    assert {action for _, _, action in directory_steps(steps)} == {SKIPPED}, \
        "everything was already where the operator's move left it"


def test_add_team_refuses_the_old_trigger_directory_the_same_way(tmp_path):
    """One helper, one sentence: the additive verb gets the same refusal as init."""
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT)
    obsolete = obsolete_trigger_dir(root, ENVIRONMENT, "latecomer")
    obsolete.mkdir(parents=True)
    before = tree(root)

    with pytest.raises(click.ClickException) as excinfo:
        config_init.add_team(root, ENVIRONMENT, "latecomer")

    assert str(obsolete) in str(excinfo.value), str(excinfo.value)
    assert tree(root) == before, "the refusal wrote part of a team layer before refusing"


# --- (5) names refused before the first byte -------------------------------------------------------

# The bad-character set, one row each. A layer is one directory, so every one of these is an attempt
# to name two directories, or no directory, or a directory no reader can spell.
BAD_NAMES = ["", "with/slash", "with\x00nul", "leading ", "trailing ", ".", ".."]

# The reserved root names and kubementat's own layer directory names, read from the module
# so a new one is refused here the day it is added there.
RESERVED = list(config_init.RESERVED_ROOT_NAMES)
LAYER = list(config_init.LAYER_DIRECTORY_NAMES)


@pytest.mark.parametrize("name", RESERVED)
def test_an_environment_named_after_a_reserved_root_name_is_refused(tmp_path, name):
    """One refusal, naming the value and saying it is *taken*, and a tree that was never written.

    `secrets` is the named case; the rest of the list is refused for the same reason, so the row
    that matters is the one that reads `already taken` and not a filesystem error.
    """
    root = unbuilt_root(tmp_path)

    with pytest.raises(click.ClickException) as excinfo:
        init_configuration(root, name, TEAM)

    message = str(excinfo.value)
    assert repr(name) in message, message
    assert "is already taken" in message, message
    assert not root.exists(), "a refused name wrote part of a tree before refusing"


@pytest.mark.parametrize("name", LAYER)
def test_a_team_named_after_a_layer_directory_is_refused(tmp_path, name):
    """A team that shares a name with a layer is a directory two rules both claim.

    `kubementat_components` is in the list because the team listing already filters it as stale, so
    the row that matters is the one that says *taken* and not *not a name*.
    """
    root = unbuilt_root(tmp_path)

    with pytest.raises(click.ClickException) as excinfo:
        init_configuration(root, ENVIRONMENT, name)

    message = str(excinfo.value)
    assert repr(name) in message, message
    assert "is already taken" in message, message
    assert not root.exists(), "a refused team name wrote part of a tree before refusing"


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_team_name_that_is_not_a_name_is_refused(tmp_path, name):
    """The other kind of refusal: not taken, just unusable. The value is quoted back and the reason
    says what a layer directory's name is allowed to be."""
    root = unbuilt_root(tmp_path)

    with pytest.raises(click.ClickException) as excinfo:
        init_configuration(root, ENVIRONMENT, name)

    message = str(excinfo.value)
    assert repr(name) in message, message
    assert "is not a name kubementat can use" in message, message
    assert not root.exists(), "a refused team name wrote part of a tree before refusing"


@pytest.mark.parametrize("name", [name for name in BAD_NAMES if name != ""])
def test_an_environment_name_that_is_not_a_name_is_refused(tmp_path, name):
    """Same rule, one layer up. The empty case is skipped here because it is refused by the sentence
    that says a tree needs an environment at all, which is the truer one."""
    root = unbuilt_root(tmp_path)

    with pytest.raises(click.ClickException) as excinfo:
        init_configuration(root, name)

    message = str(excinfo.value)
    assert repr(name) in message, message
    assert "is not a name kubementat can use" in message, message
    assert not root.exists(), "a refused environment name wrote part of a tree before refusing"


def test_a_refused_name_leaves_the_tree_it_refused_exactly_as_it_found_it(tmp_path):
    """The refusal is not a half-run: an existing tree is untouched by a name it would have rejected.

    This is the assertion a name check that lives inside the write path would pass anyway, so it is
    made against bytes and not against a step list.
    """
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT, TEAM)
    before = {path: (root / path).read_bytes() for path in tree(root) if (root / path).is_file()}
    directories = {path for path in tree(root) if (root / path).is_dir()}

    for bad in ("kubementat.yaml", "secrets", "with/slash", "."):
        with pytest.raises(click.ClickException):
            init_configuration(root, bad, TEAM)

    assert {path: (root / path).read_bytes() for path in tree(root)
            if (root / path).is_file()} == before
    assert {path for path in tree(root) if (root / path).is_dir()} == directories
