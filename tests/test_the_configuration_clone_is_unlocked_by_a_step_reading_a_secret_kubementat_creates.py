"""A step after the clone unlocks the configuration, from a Secret kubementat creates.

Two halves, and each one is only true if the two ends of it agree:

*1. kubementat creates the Secret.* `setup_pipelines` writes `git-crypt-configuration-key` out of
   `<configuration root>/git_crypt_symmetric.key`. That file is gitignored and is never read back
   into the repository, which is the whole reason a clone arrives locked and the reason this Secret has to
   exist at all — the key cannot travel inside the repository it opens.

*2. a step reads it.* `git-crypt-unlock.yml` takes the key by `secretKeyRef` and pipes it into
   `git-crypt unlock -`, inside the clone. kubementat reads and never unlocks, and census K
   measured that nothing under `src/` runs `git-crypt unlock`. That rule is about the product; this is a
   Tekton Task, which is why the ruling put the unlock here and not in `kmt`.

The failure this guards is the quiet one. If the step is missing, or is not ordered ahead of the steps that
read configuration, the clone succeeds, the unlock step succeeds or never exists, the reader gets
ciphertext, and what it reads is the *shape* of YAML without the values in it. Nothing errors. That is why
the ordering assertions below are about graph position and not about names, and why the Secret's name is
checked on both sides of a file boundary where a rename on one half is invisible to the other.

Measured against git-crypt 0.7.0 on this machine, and used rather than assumed:
* `git-crypt unlock -` reads a key from stdin and exits 0, after which the file is plaintext;
* unlocking an already-open repository exits 0 and prints nothing, so a green step proves only that the
  tree is readable from there onward;
* a key from a different repository exits 1 and says `encrypted file has been tampered with!`, then fails
  the smudge filter and leaves the repository configured but undecrypted.

Nothing here enforces a tree shape in `Config` or the read paths: most trees in this suite are fixtures
and not prepared directories, and that kind of rule once broke 227 unrelated tests.
"""
import base64
import os
import re
import tempfile
from pathlib import Path

import yaml

from asset_paths import tekton_ci_path

TASKS = tekton_ci_path("tasks")
PIPELINES = tekton_ci_path("pipelines")
EXAMPLE_PIPELINES = tekton_ci_path("../examples/tekton_ci/pipelines")

UNLOCK_TASK = "git-crypt-unlock"
UNLOCK_STEP_NAME = "git-crypt-unlock-configuration"

# The Secret's name, spelled once. Both halves assert against this: kubementat's `create_or_replace_secret`
# call and the Task's `secretKeyRef`. A rename that reaches one of them and not the other is the whole
# failure mode of a two-file contract, and it is invisible to either file read alone.
CONFIGURATION_KEY_SECRET = "git-crypt-configuration-key"
CONFIGURATION_KEY_DATA = "git-crypt-key"

# Pipelines that clone the configuration, so that unlocking is a step they need.
CLONING_PIPELINES = [
    PIPELINES / "templates" / "template-deploy-pipeline.yml",
    EXAMPLE_PIPELINES / "01-full-deployment-pipeline.yml",
    EXAMPLE_PIPELINES / "deploy-pipeline-nginx-example.yml",
]


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(manifest: dict) -> list:
    return manifest.get("spec", {}).get("tasks") or manifest.get("spec", {}).get("steps") or []


def _task_names(pipeline: dict) -> dict:
    return {step["name"]: step for step in _steps(pipeline)}


def _script(manifest: dict) -> str:
    out = []
    for step in _steps(manifest):
        text = "\n".join([step.get("script", "")] + list(step.get("command") or []))
        out.extend(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for env in step.get("env") or []:
            out.append(f"{env.get('name')}={env.get('value')}{_env_ref(env)}")
    for env in manifest.get("spec", {}).get("stepTemplate", {}).get("env") or []:
        out.append(f"{env.get('name')}={env.get('value')}{_env_ref(env)}")
    return "\n".join(str(line) for line in out)


def _env_ref(env: dict) -> str:
    ref = env.get("valueFrom") or {}
    key = ref.get("secretKeyRef") or {}
    return f" <- secret {key.get('name')}/{key.get('key')}" if key else ""


# ---------------------------------------------------------------------------------------------
# The step, and where it sits.
# ---------------------------------------------------------------------------------------------

def test_a_clone_of_the_configuration_is_unlocked_by_a_step_and_not_by_the_clone():
    """The first half of the unlock rule: *a step after* `git-clone` unlocks it.

    The mutation this catches is folding the unlock into the clone task, which the rule explicitly rules out
    because it makes "did we fetch it" and "can we read it" one answer again.
    """
    for path in CLONING_PIPELINES:
        if not path.exists():
            continue
        steps = _task_names(_load(path))
        clone = None
        unlock = None
        for name, step in steps.items():
            if "configuration" in name and "clone" in name:
                clone = name
            if (step.get("taskRef") or {}).get("name") == UNLOCK_TASK:
                assert "unlock" in name, \
                    f"{path.name}: the task named {name} runs the unlock Task but is called something " \
                    "without 'unlock' in it, so a reader looking for the unlock step will not find it"
                unlock = step
        assert clone, f"{path.name}: no clone of the configuration, so this file has nothing to unlock"
        assert unlock, \
            f"{path.name}: clones the configuration but never unlocks it. The clone lands a locked tree " \
            "because git_crypt_symmetric.key is gitignored and so never travels inside the repository, " \
            "and every step after it would read ciphertext and call it configuration"

        # It is a step of its own and not part of the clone: same workspace, different taskRef.
        assert (unlock.get("taskRef") or {}).get("name") == UNLOCK_TASK, \
            f"{path.name}: the unlock is not its own Task"


def test_the_unlock_runs_after_the_clone_and_before_what_reads_configuration():
    """Ordering, asserted on the graph rather than on names.

    The step must run *after* the clone (nothing to open before there is a tree) and *before* anything that
    reads configuration out of that tree (otherwise it reads ciphertext, which is the silent half of this
    bug: the read succeeds and returns the shape of YAML without the values).

    This is the assertion that fails if someone re-adds `- git-clone-configuration` to a step that waits
    for the unlock, because "waits for the clone" is the shape that was shipped and it is wrong.
    """
    for path in CLONING_PIPELINES:
        if not path.exists():
            continue
        pipeline = _load(path)
        steps = _task_names(pipeline)
        unlock_name = next((name for name, step in steps.items()
                            if (step.get("taskRef") or {}).get("name") == UNLOCK_TASK), None)
        clone_name = next((name for name, step in steps.items()
                           if "configuration" in name and "clone" in name), None)
        assert unlock_name and clone_name, f"{path.name}: missing clone or unlock step"

        run_after = steps[unlock_name].get("runAfter") or []
        assert clone_name in run_after, \
            f"{path.name}: {unlock_name} does not wait for {clone_name}, so it is unlocking a directory " \
            "that may not be there yet"

        for name, step in steps.items():
            if name in (clone_name, unlock_name):
                continue
            if not _reads_configuration(step, pipeline):
                continue
            after = step.get("runAfter") or []
            assert _reaches(step, unlock_name, steps), \
                f"{path.name}: {name} reads configuration and its runAfter is {after}, which does not " \
                "end at the unlock step -- directly or transitively. A clone alone is not enough: the " \
                "tree has to be open before anything reads it"


def _reaches(step: dict, wanted: str, steps: dict, seen=None) -> bool:
    """True when `wanted` is in `step`'s transitive dependencies — a graph fact, not a spelling."""
    seen = seen or set()
    for dep in step.get("runAfter") or []:
        if dep == wanted:
            return True
        if dep in steps and dep not in seen:
            seen.add(dep)
            if _reaches(steps[dep], wanted, steps, seen):
                return True
    return False


def _reads_configuration(step: dict, pipeline: dict) -> bool:
    """Does this task touch configuration? Answered from what it passes and what its Task reads.

    Deliberately conservative: a step that names a config param, or that calls a Task whose params name a
    configuration root, counts. Missing a reader is the worse error, since the whole point of the file is
    that a missed reader is silent.
    """
    text = yaml.safe_dump(step)
    if "configuration-root" in text or "path-to-configuration" in text:
        return True
    ref = (step.get("taskRef") or {}).get("name")
    if ref:
        task_path = TASKS / f"{ref}.yml"
        if task_path.exists():
            task_params = {p["name"] for p in _load(task_path).get("spec", {}).get("params") or []}
            if task_params & {"configuration-root", "path-to-configuration"}:
                return True
    return False


# ---------------------------------------------------------------------------------------------
# The Secret, at both ends.
# ---------------------------------------------------------------------------------------------

def test_kubementat_creates_the_secret_the_unlock_step_reads():
    """The name of the Secret is a contract across two files, and both ends are asserted.

    `setup_pipelines` in `src/kubementat/automations/install/pipelines.py` creates it; `git-crypt-unlock.yml` reads it by
    `secretKeyRef`. A rename on one side is a step that unlocks nothing, and neither file read alone shows
    it, so this reads the constant out of the source and compares it against the YAML.
    """
    automation = Path("src/kubementat/automations/install/pipelines.py").read_text(encoding="utf-8")
    assert CONFIGURATION_KEY_SECRET in automation, \
        f"nothing in pipelines.py creates {CONFIGURATION_KEY_SECRET}, so the unlock step's Secret is " \
        "created by nobody and the step's own error message blames the operator for kmt install"

    task = _load(TASKS / f"{UNLOCK_TASK}.yml")
    declared = {p["name"]: p.get("default", "") for p in task["spec"].get("params") or []}

    # Resolved, not grepped. The Secret's name lives in a param's *default* and is only turned into a
    # secretKeyRef by stepTemplate, so a check that reads the script text never sees it and would pass
    # vacuously. Find which param secretKeyRef hands over, then read that param's declared default: a
    # rename on kubementat's side fails here, and so does a param that quietly disappeared.
    # Order within the block is the serialiser's business, so the block is read rather than the two lines
    # being matched in a fixed sequence -- the first draft of this assumed name-then-key and found nothing
    # in a file that is correct.
    blocks = re.findall(r"secretKeyRef:([\s\S]{0,300}?)\n(?:[^ ]|\s{0,4}\S)", yaml.safe_dump(task))
    assert blocks, "no secretKeyRef appears in the unlock Task, so it reads no Secret and unlocks nothing"

    def _param_in(block, field):
        found = re.search(field + r":\s*\"?\$\(params\.([a-z-]+)\)?\"?", block)
        return found.group(1) if found else None

    name_param = _param_in(blocks[0], "name")
    key_param = _param_in(blocks[0], "key")
    assert name_param and key_param, \
        "a secretKeyRef block is here without both a name and a key, so the pod resolves one of them " \
        "against nothing and the step fails on a reference instead of on the key"
    for param in (name_param, key_param):
        assert param in declared, f"{param} is handed to secretKeyRef and never declared, so Tekton " \
            "resolves it to nothing at run time"
    assert declared[name_param] == CONFIGURATION_KEY_SECRET, \
        f"the Secret's name defaults to {declared[name_param]!r} but kubementat creates " \
        f"{CONFIGURATION_KEY_SECRET!r} in `automations/install/pipelines.py`. The two ends of this " \
        "contract have drifted, and " \
        "neither file looks wrong when read alone"
    assert declared[key_param] == CONFIGURATION_KEY_DATA, \
        f"the key within the Secret defaults to {declared[key_param]!r}, not {CONFIGURATION_KEY_DATA!r}, " \
        "so the step looks up a field nothing writes"


def test_the_secret_is_built_from_the_file_that_a_clone_cannot_carry():
    """What goes into the Secret is `git_crypt_symmetric.key`, read from the configuration directory.

    The file is gitignored, so a clone cannot contain it. That is the premise of the whole ticket, and an
    implementation that reads the key from somewhere inside a checkout would work on one machine and fail
    in every pod.
    """
    bootstrap = Path("src/kubementat/config_bootstrap.py").read_text(encoding="utf-8")
    assert "def require_symmetric_key" in bootstrap, \
        "nothing reads git_crypt_symmetric.key back, so the Secret is built from a file nobody resolved"

    automation = Path("src/kubementat/automations/install/pipelines.py").read_text(encoding="utf-8")
    assert "require_symmetric_key" in automation, \
        "the Secret is created without the reader that names the file it is built from"

    # The key travels as the file's bytes base64-encoded exactly once, because the step decodes it once.
    # A second layer presents as a wrong key, and that is indistinguishable from a bad Secret.
    call = re.search(r"create_or_replace_secret\(\s*namespace=pipeline_ns,\s*secret_name=\"git-crypt-configuration-key\""
                     r"[\s\S]{0,600}?\)", automation)
    assert call, "the Secret's create call is not where the other deployer secrets are"
    body = call.group(0)
    assert "string_data" in body and "raw_data" not in body, \
        f"{CONFIGURATION_KEY_SECRET} is not built with string_data. Measured against git-crypt 0.7.0 the " \
        "step pipes the value through exactly one `base64 -d`; a layer this side of that is a key that " \
        "fails as `not a valid git-crypt key file` and reads like someone's mistake rather than ours"
    assert "_base64_encode_file" in body, "the file's bytes are not put in the Secret at all"


def test_kubementat_still_never_unlocks():
    """The no-unlock rule is about the product, and this change must not smuggle `git-crypt unlock` into it.

    The step runs git-crypt in a pod. `src/` must not. That distinction is why the unlock lives in a
    step, so the first thing this guards is that the product stayed clean while the lane gained the ability.

    Checked against the *invocation* and not the words, because "git-crypt unlock" already appears in
    `src/` several times inside prose that says kubementat does not run it -- `core/config/reads.py` and
    `errors.py` carry it
    in an error message whose whole job is to send someone to the pipeline. A guard that flagged those
    would fail on documentation and pass on a real regression, which is worse than no guard: the next
    person relaxes it and the rule stops being watched.
    """
    offenders = []
    for path in Path("src/kubementat").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            # Only argv built for a subprocess counts: that is the one shape that execs anything.
            if "GIT_CRYPT_BINARY" in line and "'unlock'" in line.replace('"unlock"', "'unlock'") or \
               "git-crypt" in line and "'unlock'" in line.replace('"unlock"', "'unlock'") and "shell.run" in line:
                offenders.append(f"{path}: {line.strip()}")
    assert not offenders, \
        "git-crypt unlock is invoked in src/: " + "; ".join(offenders) + \
        ". kubementat reads and never unlocks, and census K measured that nothing under src/ " \
        "ran it. The unlock lives in a Tekton step precisely so this stays true."

    # The positive half, so this is not a guard that only ever says no.
    step = _load(TASKS / f"{UNLOCK_TASK}.yml")
    assert "git-crypt unlock" in _script(step), \
        f"{UNLOCK_TASK} never runs git-crypt unlock, so the clone stays locked and the product kept its " \
        "rule by doing nothing at all"


def test_the_unlock_task_needs_no_key_on_a_host():
    """A pod has no filesystem to find a key on, so the key may arrive only through `secretKeyRef`.

    The distinction is between a path that gets opened and a filename named in a sentence. This step's
    error message says `git_crypt_symmetric.key` on purpose -- it is telling the operator what kmt install
    reads -- so banning the word would ban the fix. Look for the things that actually touch a filesystem.
    """
    task = _load(TASKS / f"{UNLOCK_TASK}.yml")
    text = _script(task)
    for pattern in (r"cat\s+[^\n]*git_crypt", r"<\s*/[^\n]*git_crypt", r"--key-file", r"/etc/git-crypt",
                    r"~/\.git-crypt"):
        hit = re.search(pattern, text)
        assert not hit, f"the unlock Task opens a key off a host path ({hit.group(0)!r}), which a pod " \
            "does not have. The key arrives through secretKeyRef, which is the only transport that " \
            "survives the move from a workstation into a pipeline pod"

    assert "empty" in text, \
        "a Secret that exists but is empty presents as git-crypt's 'not a valid git-crypt key file', " \
        "which reads like a wrong key. Those need different fixes and the step has to say which one"


def test_the_task_passes_only_params_it_declares():
    """A pipeline may not hand a Task a param the Task does not declare.

    Same rule `test_a_step_only_passes_params_the_task_it_calls_declares.py` established for the older
    tasks; the new step is new surface for the same mistake.
    """
    declared = {p["name"] for p in _load(TASKS / f"{UNLOCK_TASK}.yml")["spec"].get("params") or []}
    for path in CLONING_PIPELINES:
        if not path.exists():
            continue
        for step in _steps(_load(path)):
            if (step.get("taskRef") or {}).get("name") != UNLOCK_TASK:
                continue
            for param in step.get("params") or []:
                assert param["name"] in declared, \
                    f"{path.name}: {step['name']} passes {param['name']!r} to {UNLOCK_TASK}, which does " \
                    "not declare it"
