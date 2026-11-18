"""`config init`'s write set: answers, the manifest, and nothing that looks like a product.

Two claims are tested here, and the second is the one that decides what may be written.

**The write set is keys and not shape.** `init_configuration` may write the manifest and the static
layers and nothing else — no namespaces, no domains, no registry, no templates, no
`mirrored_docker_images.json`, no app `values.encrypted.yaml`, no `.gitattributes`, no `.gitkeep`, no
marker file. It is not enough to assert that the file count is five: a copied template would keep the
count and change the tree. So the set of paths is asserted, and the readback through `Config` is
asserted to *still* fall back to `constants.py` and say so — the one test that proves no file init
wrote answered a key a default owns.

**The second run writes nothing.** It is a claim about bytes, so it is asserted against a
snapshot of them and not against a log line. Three cases decide a path — missing, byte-identical,
different — and the third one's refusal does not become permissive under `force`, which is the
difference between a flag that means "this tree is not mine" and a flag that means "overwrite
whatever". `force` is only ever about *foreign* material, so the tests that pass it say which of the
two kinds they are about.

Both layers go under the **environment**, always: the team's two files are written empty, because a
team layer with answers is `config set --team`'s job and the flag that would say so does not exist
yet. Neither cluster key goes into the team's file either — one directory is exactly one cluster, and
what resolves them reads the environment's `static.json` and no other path.

The tree is rooted in `tmp_path` per `tests/test_no_test_loads_config_from_the_checkout_root.py`, and
`root` here is the configuration directory itself (`tmp_path/platform_config`), the way a
`--config-dir` names it.
"""

import json
from pathlib import Path

import pytest

from kubementat.config.init import (
    CREATED,
    EXISTED,
    SKIPPED,
    InitStep,
    init_configuration,
)
from kubementat.errors import DuplicateAnswerKeyError, EnvironmentNotSuppliedError

ENVIRONMENT = "dev"
TEAM = "ops"

# What a first init writes, spelled out because this whole file is a claim about exactly this set.
MANIFEST = Path("kubementat.yaml")
ENV_STATIC = Path(ENVIRONMENT) / "static.json"
ENV_ENCRYPTED = Path(ENVIRONMENT) / "static.encrypted.json"
TEAM_STATIC = Path(ENVIRONMENT) / TEAM / "static.json"
TEAM_ENCRYPTED = Path(ENVIRONMENT) / TEAM / "static.encrypted.json"

EXPECTED_FILES = {MANIFEST, ENV_STATIC, ENV_ENCRYPTED, TEAM_STATIC, TEAM_ENCRYPTED}
# The directories init creates, spelled the way the write path reaches them: the two that hold the
# files, the environment's two `tekton/` layers, and the team's four. They are part of the tree and
# they are not foreign material, so any set that names the files has to say them too. The four
# `tekton` ones exist because their readers exist and their maker did not (census G) — the two
# environment `tekton/` subdirectories nobody reads are asserted absent in
# `tests/test_config_init_creates_the_directories_a_reader_looks_in.py`.
EXPECTED_TEKTON_DIRECTORIES = {Path(ENVIRONMENT) / "tekton" / "tasks",
                               Path(ENVIRONMENT) / "tekton" / "pipelines",
                               TEAM_STATIC.parent / "tekton" / "tasks",
                               TEAM_STATIC.parent / "tekton" / "pipelines",
                               TEAM_STATIC.parent / "tekton" / "pipeline-runs",
                               TEAM_STATIC.parent / "tekton" / "triggers"}
EXPECTED_DIRECTORIES = ({Path(ENVIRONMENT), TEAM_STATIC.parent,
                         Path(ENVIRONMENT) / "tekton", TEAM_STATIC.parent / "tekton"}
                        | EXPECTED_TEKTON_DIRECTORIES)
EXPECTED_TREE = EXPECTED_FILES | EXPECTED_DIRECTORIES

# The names that would have to appear if init were shipping product shape instead of answers. Each
# is a file some other command writes, and none of them is an answer.
#
# `.gitattributes` needs the verb split said out loud rather than left implied. R-11 moved git-crypt
# into the configuration directory, so the filter declaration has to live there too -- but not in
# `init_configuration`'s write set, which stays five files for a plain `config init`. The tree that
# carries the file is a *prepared* one: `config_bootstrap.prepare_configuration_directory` writes it
# three steps before this function lays the encrypted layer down, and hands the path back as
# `own_paths` so the guard below accounts for it instead of calling it foreign. Both halves of that
# are asserted in `test_the_two_verbs_that_touch_the_root_agree_about_which_one_writes_the_filters`.
PRODUCT_SHAPE = ("mirrored_docker_images.json", "values.encrypted.yaml", "values.yaml",
                 "helmfile.yaml.gotmpl", ".gitattributes", ".gitkeep", "kubementat_components")


def config_root(tmp_path) -> Path:
    """A configuration directory root that exists and contains nothing.

    `exist_ok` because the root is named and never located: a test that wants two roots from one
    `tmp_path` gets the same empty directory and not a fixture error.
    """
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    return root


def tree(root: Path) -> set[Path]:
    """Every path under `root`, relative to it. Directories included, because they are on disk too."""
    assert root.is_dir(), f"{root} was never created, so there is nothing to enumerate"
    return {path.relative_to(root) for path in root.rglob("*")}


def files(root: Path) -> set[Path]:
    """Only the files. Shape is a claim about files; a directory says nothing about what answered."""
    return {path for path in tree(root) if (root / path).is_file()}


def byte_snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def documents(root: Path) -> dict[Path, object]:
    return {p.relative_to(root): json.loads(p.read_text())
            for p in root.rglob("*.json") if p.is_file()}


# --- (1) the write set, enumerated ---------------------------------------------------------------


def test_the_files_init_writes_are_the_manifest_and_the_layers_and_nothing_else(tmp_path):
    """The set and not the count: five files is also true of a tree with a copied template in it.

    Both halves of the rule are here in two assertions. The first names every path; the second is the
    shape claim, that nothing on disk is anything other than the manifest or a static file.
    """
    root = config_root(tmp_path)

    init_configuration(root, ENVIRONMENT, TEAM)

    assert tree(root) == EXPECTED_TREE, (tree(root) ^ EXPECTED_TREE)
    assert {path for path in files(root)
            if path.name not in ("static.json", "static.encrypted.json")} == {MANIFEST}, \
        "init wrote a file that is not the manifest and not an answer"
    for name in PRODUCT_SHAPE:
        assert name not in {path.name for path in files(root)}, (
            f"{name} is product shape, not an answer. If the name is `.gitattributes`, the verb under "
            f"test is `config init` and not `config init`'s prepared half: R-11 moved git-crypt into "
            f"the configuration directory, but `init_configuration` still writes no filter declaration "
            f"of its own. A *prepared* tree is the one that has the file, and `config_bootstrap."\
            f"prepare_configuration_directory` writes it three steps before this function lays the "
            f"encrypted layer, which is asserted in "
            f"tests/test_config_init_prepares_the_directory_it_is_handed.py.")


def test_the_steps_report_what_happened_to_each_path_in_the_order_it_was_applied(tmp_path):
    """First run: all `created`, one step per path, and every path is one of the five."""
    root = config_root(tmp_path)

    steps = init_configuration(root, ENVIRONMENT, TEAM)

    assert all(isinstance(step, InitStep) for step in steps)
    assert [step.action for step in steps] == [
        CREATED] * (len(EXPECTED_FILES) + len(EXPECTED_TEKTON_DIRECTORIES))
    assert ({step.path for step in steps}
            == {root / path for path in EXPECTED_FILES}
            | {(root / directory).resolve() for directory in EXPECTED_TEKTON_DIRECTORIES})
    assert not (root / "nothing").exists()


# --- (2) the second run writes nothing -----------------------------------------------------------


def test_a_second_init_on_an_untouched_tree_writes_no_bytes_and_reports_every_step_skipped(tmp_path):
    """The no-retry rule, in the only unit that cannot be satisfied by a log line: the bytes.

    The snapshot is taken before the second run and compared after it, which catches a rewrite that
    is byte-identical (still a write, still a lost edit timestamp) as well as one that changes a
    key. `skipped` and not `created` is the other half of the same fact — a step list that says
    "created" while touching nothing is a step list that lies about the tree.
    """
    root = config_root(tmp_path)
    first = init_configuration(root, ENVIRONMENT, TEAM)
    before = byte_snapshot(root)
    assert all(step.action == CREATED for step in first)

    second = init_configuration(root, ENVIRONMENT, TEAM)

    assert [step.action for step in second] == [
        SKIPPED] * (len(EXPECTED_FILES) + len(EXPECTED_TEKTON_DIRECTORIES))
    assert byte_snapshot(root) == before, "the second run wrote bytes over an untouched tree"
    assert tree(root) == EXPECTED_TREE, "the second run added or removed a path"


# --- (3) foreign material ------------------------------------------------------------------------


def test_force_accepts_a_tree_with_foreign_material_and_leaves_that_material_alone(tmp_path):
    """`force` means "this tree has things in it I accept". Here it is the only claim it is about.

    The foreign file is checked byte for byte afterwards, and reported as `existed` — it was not
    created and it was not skipped, and a step that claimed either would be describing a file init
    never touched.
    """
    root = config_root(tmp_path)
    (root / ENVIRONMENT).mkdir(parents=True)
    stray = root / ENVIRONMENT / "operator-notes.yaml"
    stray.write_text("# a human put this here\n")

    steps = init_configuration(root, ENVIRONMENT, TEAM, force=True)

    assert stray.read_text() == "# a human put this here\n"
    assert any(step.action == EXISTED and step.path == stray for step in steps)
    assert tree(root) == EXPECTED_TREE | {Path(ENVIRONMENT) / "operator-notes.yaml"}


# --- (4) a file init would write, but that already differs ---------------------------------------


# --- (5) which layer answers, and what type it keeps ---------------------------------------------


def test_a_key_answered_twice_is_refused_because_json_cannot_hold_it_twice(tmp_path):
    """The answer parameter is a tuple so a duplicate is *visible* rather than last-write-wins.

    Picking one silently is a wrong answer with a straight face, so this says which key.
    """
    root = config_root(tmp_path)

    with pytest.raises(DuplicateAnswerKeyError) as excinfo:
        init_configuration(root, ENVIRONMENT, answers=(("A_KEY", "one"), ("A_KEY", "two")))

    assert "A_KEY" in str(excinfo.value)


def test_an_environment_is_not_defaultable(tmp_path):
    """No `--environment` is a refusal and not `""` or `all`, and a team without one is the same."""
    root = config_root(tmp_path)

    with pytest.raises(EnvironmentNotSuppliedError) as excinfo:
        init_configuration(root, None)

    assert "environment" in str(excinfo.value).lower()
    assert tree(root) == set()


# --- (6) the readback that proves no file init wrote answered it ----------------------------------


# --- (7) one sentence, one owner ---------------------------------------------------------------
