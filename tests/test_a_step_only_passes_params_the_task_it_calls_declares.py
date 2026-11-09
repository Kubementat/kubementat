"""A param a step hands to a `taskRef` has to exist in the Task it refers to — this file measures the set.

The trigger was the pipeline work: while adding a configuration clone to the shipped pipelines I found
the examples still passing `git-crypt-unlock` to `git-clone-with-ssh-auth`, five months after the step
that read it was deleted (`a46a7ad`). Nothing broke, which is the point worth writing down: a param whose
consumer is gone does not fail loudly, it just stops meaning anything, and the file that passes it reads
as though the unlock were still configured.

What is asserted is the *set*, measured out of the tree, and nothing more. Whether Tekton rejects a param
the referenced Task does not declare was **not** established here — no cluster was reachable while this
file was written, and the tree already hands undeclared params to Tasks in places that presumably work, so
"the apply fails" is a guess and is not asserted anywhere below. What is known is smaller and harder: the
Task's own params block is the list of what it accepts, a param outside that list is read by nobody, and
after `a46a7ad` `git-crypt-unlock` was in exactly that position in two shipped example pipelines.

So this guard does one thing: a new undeclared param anywhere in a shipped or example pipeline turns this
green test red. And it makes the pre-existing hits visible instead of quietly tolerating them, naming each
one as *measured and unresolved* rather than as correct.
"""

import pathlib

import yaml

from asset_paths import tekton_ci_path

TASKS = tekton_ci_path("tasks")
PIPELINES = tekton_ci_path("pipelines")
EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples" / "tekton_ci" / "pipelines"
# Same resolver, twice: the shipped Tasks and the shipped pipelines must be read from where the installed
# package carries them, because a guard that reads the old checkout location passes only while that
# directory happens to exist.

# Measured, not curated: every place a pipeline step passes a param its `taskRef` Task does not declare.
# The key is (file, step); the value is what is handed over that the Task never asks for.
#
# What is left here predates this file and was not touched by any ticket that landed since. It is left
# exactly as it is and reported here, because "we know about this one and have not checked it against a
# cluster" is the true statement, and pinning it is the only way to keep the set from silently growing
# past what somebody actually read off the tree. The counts here are a measurement and not a judgement.
PREEXISTING_UNDECLARED_PARAMS = {
    "pipelines/copy-docker-image-pipeline.yml::skopeo-copy":
        {"environment", "team"},
}

# The two params whose consumer the migration deleted. If either reappears, the deletion was undone and the
# sentence that says otherwise is in the file.
DELETED_CONSUMER_PARAMS = {"git-crypt-unlock"}


def _declared_params():
    declared = {}
    for path in sorted(TASKS.glob("*.yml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            continue
        declared[path.stem] = {param["name"] for param in (manifest.get("spec") or {}).get("params") or []}
    return declared


def _steps_passing_params(roots):
    """Every (file, step, taskRef, params) tuple for a step that refers to a Task this repository ships."""
    found = []
    for root in roots:
        for path in sorted(root.rglob("*.yml")):
            for manifest in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if not isinstance(manifest, dict):
                    continue
                for step in (manifest.get("spec") or {}).get("tasks") or []:
                    ref = (step.get("taskRef") or {}).get("name")
                    if not ref:
                        continue
                    found.append((path, step.get("name"), ref, step.get("params") or []))
    return found


def _rel(path):
    """A label for a finding that is stable across an installed package and a checkout.

    The two roots differ by however many directories a wheel inserts, so the key is built out of the
    last three parts -- `<area>/<file>.yml` for a pipeline, and for an example the same shape -- rather
    than out of a prefix only one of the two has.
    """
    parts = path.parts[-2:]
    return "/".join(parts)


def test_the_measured_set_of_undeclared_params_is_the_one_recorded_here():
    """Nothing new may hand a Task a param it never declared, and the known ones stay known.

    A set assertion rather than a "no new ones" filter, because a filter that drops what the author did
    not like is how a guard ends up agreeing with whatever the next change does.
    """
    declared = _declared_params()
    assert declared, f"no Tasks under {TASKS}; the rule below would be asserting nothing"

    roots = [PIPELINES, EXAMPLES]
    found = {}
    for path, step_name, ref, params in _steps_passing_params(roots):
        if ref not in declared:
            # A `taskRef` pointing at a Task this repository does not ship is somebody else's contract,
            # and the check would be a guess about it. Recorded rather than compared.
            continue
        extra = {param["name"] for param in params} - declared[ref]
        if extra:
            found[f"{_rel(path)}::{step_name}"] = extra

    unexpected = {k: v for k, v in found.items() if k not in PREEXISTING_UNDECLARED_PARAMS}
    assert not unexpected, (
        f"steps pass params their Task does not declare: {unexpected}. A param outside a Task's params "
        "block is read by no step in that Task, so it cannot be doing what its name says; if the Task "
        "gained the param deliberately, say so by adding it to the Task and to this pin")

    for key, expected in PREEXISTING_UNDECLARED_PARAMS.items():
        assert found.get(key) == expected, (
            f"{key} no longer matches the recorded undeclared params "
            f"{sorted(expected)}; it is now {sorted(found.get(key) or [])}. This pin is a measurement, "
            "so update it from the tree and not from the diff")


def test_the_param_whose_step_is_gone_stays_gone():
    """`git-crypt-unlock` is read by nothing, in this repository or anywhere else.

    The unlocking step was deleted out of `git-clone-with-ssh-auth.yml` (kubementat does not
    unlock, and nothing in the fork is encrypted), and the Task param that carried
    the answer went with it. Two example pipelines kept passing it, which is why this half is separate: it is the case
    where the param is not a convention, not a convenience, and not awaiting a cluster check — its reader
    does not exist.
    """
    declared = _declared_params()
    for clone_task in ("git-clone-with-ssh-auth.yml", "git-clone.yml"):
        path = TASKS / clone_task
        if not path.exists():
            continue
        assert "git-crypt-unlock" not in declared[path.stem], \
            f"{clone_task} declares git-crypt-unlock again. Unlocking is not this product's job, " \
            "and the step that had the param is deleted"

    for path, step_name, ref, params in _steps_passing_params([PIPELINES, EXAMPLES]):
        if ref not in declared:
            continue
        passed = {param["name"] for param in params}
        assert not (passed & DELETED_CONSUMER_PARAMS), (
            f"{path.name}::{step_name} still passes {sorted(passed & DELETED_CONSUMER_PARAMS)} to "
            f"{ref}, whose step that read it was deleted in a46a7ad. The param does nothing, and the "
            "pipeline reads as though a git-crypt unlock still happens")
