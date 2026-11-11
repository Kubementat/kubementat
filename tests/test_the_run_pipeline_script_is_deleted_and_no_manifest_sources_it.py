"""The script that started a pipeline run stays deleted, and nothing can `source` it again.

`scripts/automation/tekton/run_pipeline.sh` was the last script that survived a migration because
the thing that called it was a Tekton Task and not a command. `tekton_ci/tasks/run-tekton-pipeline.yml`
`source`d it inside a pod — where there was no `kmt` then, which is the whole reason the parity test
that covered it ran live bash (the migration made `kmt` reachable there and repointed the step at
it) — and after that one use the file had none left. Deleting a script whose only caller is a
manifest is the kind of deletion that stays quiet forever unless a test says it, because the manifest
keeps parsing, the Task keeps being applied, and the failure lands on the next pipeline run.

So this file asserts four things:

* the script is not in the tree, and neither is the empty directory that held it;
* nothing in a root a program is read from carries a path to it;
* no manifest anywhere `source`s it or polls for the run the way the old step did — which is the
  same gate the ticket measured with `grep -rn "run_pipeline.sh\\|tkn pipelinerun describe"`;
* the Task itself is still in the tree and still called by the example pipeline, because the rule
  here was "one step", not "no step".

The one place that still names it is a comment in `tests/shims/yq`, which explains what that shim
converts. It is prose about a deleted file and not a way to run it, so it is excluded from the path
check the same way the plans and the ledger that scheduled the deletion are.
"""

import subprocess
from pathlib import Path

from asset_paths import tekton_ci_path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCRIPT = "scripts/automation/tekton/run_pipeline.sh"
# The Task ships inside the package, so "is it still in the tree" is asked of the resolver
# and not of a checkout that no longer carries it.
TASK = tekton_ci_path("tasks", "run-tekton-pipeline.yml")
CALLING_PIPELINE = "examples/tekton_ci/pipelines/01-full-deployment-pipeline.yml"

# Roots a caller could live in. `tests/` is not one of them for the path check — the parity
# comparison and this file are the record of the deletion, exactly as the pvc-usage and
# create-ingress guards keep `tests/` out of the same rule — and `docs/` and `.agent-work/` are the
# prose that scheduled it.
CALLER_ROOTS = ("src", "scripts", "tekton_ci", "examples")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.
PROSE_ONLY_FILES = {"tests/shims/yq"}

# Nothing survives here. This file's survivors used to be the two `scripts/utilities/kubernetes/`
# one-liners, which had Python reads and parity tests of their own; a later sweep deleted both,
# so the set the measurement below asserts against is empty. A `.sh` turning up under `scripts/`
# under any name is what fails it.
SURVIVORS: set = set()


def _files_under(root):
    for path in (REPO_ROOT / root).rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _named_in(text, needle):
    return [line for line in text.splitlines() if needle in line]


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_the_script_is_not_in_the_tree():
    """The deletion itself, asserted where a re-added file would land."""
    assert not (REPO_ROOT / SCRIPT).exists(), \
        f"{SCRIPT} is back; kmt tekton run-pipeline --wait is the implementation, not a second one"


def test_the_directory_that_held_it_holds_nothing():
    """A directory emptied by a deletion and left behind is a directory that is still shipped.

    Git tracks files and not directories, so `scripts/automation/tekton/` would survive as an empty
    thing on somebody's disk and a `scripts/` section in the overview that no longer matches what is
    in it.
    """
    left = sorted(str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "scripts").rglob("*")
                  if "automation" in str(path))
    assert left == [], f"the directory the script came from came back: {left}"


def test_no_program_or_manifest_carries_a_path_to_the_script():
    """The full path, not the filename: this is the string a caller needs in order to run it.

    A bare mention in a comment that says what a command replaced is prose, and the rule the
    pvc-usage guard states applies here unchanged — which is why the shim that describes what `yq`
    was doing for the script is read and not convicted, and why `docs/` and `.agent-work/` (which
    scheduled the deletion) and `tests/` (which records it) are outside the roots a caller can live
    in.
    """
    for root in CALLER_ROOTS:
        for path, text in _files_under(root):
            assert SCRIPT not in text, \
                f"{path.relative_to(REPO_ROOT)} still carries the path of a deleted script"

    # ...and the same path is not carried by the files that sit at the repository root, which are
    # documentation and configuration rather than something a manifest could run. Measured rather
    # than asserted from the outside, so the exception list stays honest: `docs/` and
    # `.agent-work/` scheduled the deletion, and `tests/` is where its record lives.
    named_by = [path.name for path in REPO_ROOT.iterdir()
                if path.is_file() and SCRIPT in _read(path)]
    assert named_by == [], f"a root-level file still names the deleted script: {named_by}"


def test_no_task_still_polls_for_the_run_the_way_the_old_step_did():
    """The ticket's own gate, run as a walk instead of a grep.

    `grep -rn "run_pipeline.sh\\|tkn pipelinerun describe" tekton_ci/ src/` is what green means here,
    and what it is really checking is that the polling half of the deleted step went into Python and
    did not get copied into another manifest. So the whole of `tekton_ci/`, `src/` and `examples/` is
    read for both strings, and the answer is empty.
    """
    for root in ("tekton_ci", "src", "examples"):
        for path, text in _files_under(root):
            for needle in ("run_pipeline.sh", "tkn pipelinerun describe", "while true"):
                assert needle not in text, \
                    f"{path.relative_to(REPO_ROOT)} still does the thing this ticket moved, with {needle}"


def test_the_task_is_still_in_the_tree_and_still_called_by_name():
    """The Task is contract: this ticket collapsed its step, not its existence.

    `examples/tekton_ci/pipelines/01-full-deployment-pipeline.yml` names it in a `taskRef`, so the
    manifest and the caller are both asserted to be where a pipeline that runs today expects them.
    """
    assert TASK.is_file(), "the Task is not a thing this ticket was allowed to delete"
    assert (REPO_ROOT / CALLING_PIPELINE).is_file()
    assert "name: run-tekton-pipeline" in (REPO_ROOT / CALLING_PIPELINE).read_text(encoding="utf-8")


def test_the_one_exception_is_a_comment_and_not_a_caller():
    """The allowed list, measured. `tests/shims/yq` still says what it converts.

    That shim exists because the deleted script used `yq` twice, and the comment on its third line is
    the only thing in the checkout that describes what it was converting. It names the script's
    *filename* and not a path to it, so it is not a caller and never was — but it is the reason this
    file's exception list exists, so if the comment goes the rule needs re-reading.
    """
    text = (REPO_ROOT / "tests/shims/yq").read_text(encoding="utf-8")
    assert "run_pipeline.sh" in text, \
        f"the shim comment that explains {sorted(PROSE_ONLY_FILES)} is gone, and with it the reason " \
        "this guard allows a mention of the deleted script's name"


def test_the_scripts_left_in_the_tree_are_the_ones_this_ticket_is_not():
    """`find scripts -name '*.sh' | wc -l`, measured rather than remembered.

    This ticket deleted one file, so the set is asserted rather than the count. The set this file
    used to pin is empty: the two one-liners that were its survivors were deleted by a later sweep,
    which guards its own deletion in `test_the_cluster_read_scripts_are_deleted_and_the_primitives_answer_for_them.py`,
    so what is measured here is that the tree holds nothing else. A guard that pinned a moving total
    would be an argument waiting to happen; this pins the set instead, and the set is empty.
    """
    # Absence is a reading, not an error: git cannot represent an empty directory, so a clean
    # checkout has no `scripts/` where a developer's tree still has the husk left by the migration.
    # `find` exits 1 on the first and empty on the second, which is how this came to be green in one
    # tree and red in every other.
    measured = sorted(line.strip().lstrip("./") for line in subprocess.run(
        ["find", "scripts", "-name", "*.sh"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True).stdout.splitlines() if line.strip()) \
        if (REPO_ROOT / "scripts").is_dir() else []

    assert measured == sorted(SURVIVORS), measured
    assert SCRIPT not in measured
