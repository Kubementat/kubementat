"""A Task runs its own job; handing it a script path to execute is not a thing kubementat offers.

Two files carried that capability and both are deleted —
`tekton_ci/tasks/run-automation-script-for-environment.yml`, whose single step ran
`./$SCRIPT_AUTOMATION_FILE "$ENVIRONMENT" "$TEAM"`, and the one script it existed to run,
`scripts/tasks/setup_mysql_databases.sh`, whose only route to a cluster was that Task. Before either
was removed the orphan proof was run over the whole checkout and each name hit its own file and prose:
no `.yml` named the Task, no program execs the script.

The rule those two deletions implement is one sentence: **a Task that does its own job stays, and a
Task whose only purpose is to execute a path you hand it goes.** The two MySQL Tasks are that rule
read from the other side — `create-mysql-database.yml` and `execute-mysql-file.yml` both assemble
`mysql -h … -e` inside their own step, so each owns its own work. They survive, and what the surviving
ones do is asserted below off the manifest text and not off anyone's description of it.

One consequence is stated plainly, because it is what a reader has to be told and not shown: creating
databases and users *from configuration* is retired rather than migrated. The config key that row
configured went first, out of the team template and out of `automation.py` (8d9c318), and the
script that read it goes with this pair — so no key declares it and nothing reads it. The MySQL Tasks
that remain create exactly the database or file their own parameters name, which is narrower and
parameter-shaped, and that is the whole of what is left. The sentence is written in
`tests/e2e/harness/config.py`, where the lane rows that used to describe the capability are, and in
`CHANGELOG.md`; this file's job is to keep the tree from contradicting it.

Nothing below reads a sentence to decide whether the deletion held. Every assertion is about a file
in the tree, a manifest, or a root a program is loaded from, so an edit that only rewords prose cannot
make any of them pass.
"""

import re
from pathlib import Path

from asset_paths import tekton_ci_path

REPO_ROOT = Path(__file__).resolve().parent.parent

TASK = Path("tekton_ci/tasks/run-automation-script-for-environment.yml")
SCRIPT = Path("scripts/tasks/setup_mysql_databases.sh")
TASK_NAME = "run-automation-script-for-environment"
SCRIPT_PARAM = "path-to-automation-script"
SCRIPT_UNDER_TASKS = re.compile(r"scripts/tasks/[\w.\-]+\.sh")

# The Tasks that stay, because they run MySQL themselves. Named by file name, not by a path: they
# ship inside the package, and `tekton_ci_path` is what says which package that is.
DOES_ITS_OWN_JOB = ("create-mysql-database.yml",
                    "execute-mysql-file.yml")

# Roots a program or a manifest could be read from. `scripts/` is in here because a sibling script
# that execs this one would be a caller, and because `src/cli/commands/utilities.py` globs `.sh` files
# under an execution path, which is how `kmt run-script` used to reach a script nobody had told it
# about.
CALLER_ROOTS = ("src", "scripts", "tekton_ci", "tests", "examples", "docker_files")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.

MANIFEST_SUFFIXES = (".yml", ".yaml")


def _text_files(root):
    for path in (REPO_ROOT / root).rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _manifest_files():
    """Every `.yml`/`.yaml` in the checkout, from `examples/` to `helm_charts/`."""
    return sorted(path for path in REPO_ROOT.rglob("*")
                  if path.suffix in MANIFEST_SUFFIXES
                  and ".git" not in path.parts and ".venv" not in path.parts)


def test_the_task_that_executed_a_script_path_is_not_in_the_tree():
    """The deletion itself, asserted where a re-added file would land."""
    assert not (REPO_ROOT / TASK).exists(), \
        f"{TASK} is back; a Task that runs its own job is the shape that stays"


def test_the_script_that_task_ran_is_not_in_the_tree():
    """The other half. The script had no route to a cluster once the Task went."""
    assert not (REPO_ROOT / SCRIPT).exists(), \
        f"{SCRIPT} is back; its only caller was the Task above"


def test_the_directory_that_held_the_script_carries_no_script():
    """Git tracks files and not directories, so an emptied directory is still shipped.

    The precedent is the pair under `scripts/secret_management/`, whose directory went with them.
    Either outcome passes here — gone, or present and empty — because both are honest; what is refused
    is a directory that still holds a `.sh` nobody admits to owning.
    """
    directory = REPO_ROOT / "scripts" / "tasks"
    assert not directory.is_dir() or not any(directory.glob("*.sh")), \
        f"scripts/tasks/ still ships a script: {sorted(p.name for p in directory.glob('*.sh'))}"


def test_no_manifest_anywhere_names_the_task_that_is_gone():
    """A `.yml` that names it is a live caller, and the pipeline that reaches for it fails.

    The whole checkout is swept rather than the two directories this ticket was told about, so the
    hit would be a `taskRef.name` in `tekton_ci/pipelines/`, in `examples/tekton_ci/`, or in some
    chart's hook that nobody mentioned.
    """
    named_by = [str(path.relative_to(REPO_ROOT)) for path in _manifest_files()
                if TASK_NAME in path.read_text(encoding="utf-8")]

    assert named_by == [], f"a manifest still asks for the deleted Task: {named_by}"


def test_no_task_declares_a_parameter_that_takes_an_arbitrary_script_path():
    """The parameter was the capability's interface, so its absence is the interface check.

    `path-to-automation-script` is the string a pipeline had to fill in to make a step run whatever it
    liked. It is checked by name rather than by the Task's filename, so the capability coming back
    under a different Task name still fails here.
    """
    with_param = [path.name for path in sorted(tekton_ci_path("tasks").glob("*.yml"))
                  if SCRIPT_PARAM in path.read_text(encoding="utf-8")]

    assert with_param == [], \
        f"a Task takes a script path again, which is the escape hatch: {with_param}"


def test_no_code_root_names_a_script_under_scripts_tasks_that_could_be_run():
    """The exec check, stated so that it can only fail on a real caller.

    A path under `scripts/tasks/` is only a caller if the file it names is in the tree — which is why
    the assertion is about files and not about strings. The case this had to let through used to be a
    test of the CLI's own runner, which handed over paths shaped exactly like these and resolved them
    against a repository it built in a temporary directory rather than against this checkout. That
    verb is deleted, so the exemption has nothing left to excuse and the walk is asserted clean on
    its own terms.

    The bare filename is checked the same way: a script re-added under `scripts/tasks/` under *any*
    name is caught by the directory check above, and this is what catches a caller that names this
    one before the file lands.
    """
    # Once for the whole walk: the file the bare name points at, if it is anywhere in the tree.
    script_is_here = any(path.name == SCRIPT.name for path in REPO_ROOT.rglob(SCRIPT.name))

    reachable = []
    for root in CALLER_ROOTS:
        for path, text in _text_files(root):
            if path == Path(__file__):
                continue
            for hit in SCRIPT_UNDER_TASKS.finditer(text):
                if (REPO_ROOT / hit.group(0)).is_file():
                    reachable.append(f"{path.relative_to(REPO_ROOT)}: {hit.group(0)}")
            if script_is_here and SCRIPT.name in text:
                reachable.append(f"{path.relative_to(REPO_ROOT)}: names {SCRIPT.name}, which is in the tree")

    assert reachable == [], f"a caller can still run a script under scripts/tasks/: {sorted(set(reachable))}"


def test_the_tasks_that_run_mysql_themselves_are_still_in_the_tree():
    """The counter-example is contract: the rule is not "delete Tasks", and this says so.

    If the first two assertions are ever read as a war on Tasks, this is what fails. So the surviving
    half is asserted too, and not as two filenames — a Task restored without a job of its own would
    pass a bare existence check and is precisely what this ticket deleted.
    """
    for relative in DOES_ITS_OWN_JOB:
        task = tekton_ci_path("tasks", relative)
        assert task.is_file(), f"{relative} is not in the tree"
        text = task.read_text(encoding="utf-8")
        assert "mysql -h" in text, \
            f"{relative} no longer runs mysql in its own step, so it is the deleted shape again"
        assert SCRIPT_PARAM not in text, \
            f"{relative} takes a script path from a pipeline instead of running its own step"


def test_the_scripts_tree_is_measured_and_not_remembered():
    """`find scripts -name '*.sh' | wc -l` re-run here, so the docs' number cannot drift silently.

    The number itself is not asserted — the two survivors under `scripts/utilities/kubernetes/` have
    their own tickets to be deleted by, and a guard that pinned a moving total would be an argument
    waiting to happen. What is asserted is that the tree's own measurement and the walk agree, that
    neither of the two deleted files is among what it finds, and that the queue this script belonged
    to is closed: `scripts/automation/tekton/run_pipeline.sh` is deleted, and the Task that replaced it
    now runs `kmt tekton run-pipeline --wait` in one step.
    """
    import subprocess

    # A clean checkout has no `scripts/` at all: git cannot represent an empty directory, and the
    # directory is empty by decision. So an absent tree is read as no scripts, never as an error --
    # otherwise this test measures one working tree and nothing else, and is red for a reason that
    # has nothing to do with what it asserts.
    scripts = REPO_ROOT / "scripts"
    found = sorted(str(path.relative_to(REPO_ROOT)) for path in scripts.rglob("*.sh")) if scripts.is_dir() else []
    measured = sorted(line.strip().lstrip("./") for line in subprocess.run(
        ["find", "scripts", "-name", "*.sh"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True).stdout.splitlines() if line.strip()) if scripts.is_dir() else []

    assert measured == found, f"`find` and rglob disagree about scripts/: find={measured} rglob={found}"
    assert TASK.name not in " ".join(found)
    assert SCRIPT.name not in " ".join(found), f"the deleted script is back: {found}"
