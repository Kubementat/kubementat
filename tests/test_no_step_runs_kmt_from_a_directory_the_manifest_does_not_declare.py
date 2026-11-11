"""Where a `uv run … kmt` step starts is part of the manifest, and a step that does not declare it is broken.

The defect that prompted this file was measured and not inferred. `uv` resolves the `kmt` entry point
from the *project*, and it finds the project by walking **up** from its cwd looking for
`pyproject.toml`/`uv.lock` — never down. The CI image ships `uv` and `uvx` and installs nothing else
(`docker_files/ci_images/ubuntu_ci_minimal/Dockerfile:131`), so kubementat is not `pip install`ed and
`kmt` exists there only as the project's entry point. Run `uv run --locked --no-dev kmt --version` from
outside a checkout and it does not say "project not found": it tries to spawn a bare `kmt` binary and
answers `error: Failed to spawn: kmt / cause: No such file or directory`.

That command landed in a step with no `cd` and no `workingDir`. The step's own workspace mounts
at `/workspace` (`spec.workspaces[].mountPath`) while the checkout is a *child* of it, so the step ran
from outside the project and would have failed exactly as above — a step whose argv its unit tests
 happily passed, because a fake `uv` does not care which directory it is called from. The old step had
survived this by accident: its `cd` landed inside the checkout. Deleting the `cd` deleted the accident
without replacing it, and `workingDir:` is the replacement — the Tekton-native one that
`helmfile-cli.yml:36` and `terraform-cli.yml:59,82` already use.

Two conventions exist in this tree for naming that directory, and this guard names both rather than
picking a fight. `run-tekton-pipeline` declares `workingDir: $(params.path-to-configuration)`, the same
value it exports as `KUBEMENTAT_HOME`; `create-secret-from-json.yml:56` `cd`s into
`"/workspace/$(params.automation-git-project-name)"`, which it also exports as `KUBEMENTAT_HOME`. Both
are legal here because both *declare* the directory the command runs in — a `cd` whose target is a
value the manifest already states is an entry, not an escape — and neither is silently moved onto the
other mid-ticket. What is refused is the third state: `uv run kmt` on line, and the directory nowhere.

So, as the general statement that outlives the ticket: every step under `tekton_ci/tasks/` that runs
`uv run … kmt` declares its directory, through `workingDir:` or through a `cd` to a path the step also
states as a value, and that directory resolves inside a workspace the Task actually mounts.
"""

import re
from pathlib import Path

import yaml

from asset_paths import tekton_ci_path

REPO_ROOT = Path(__file__).resolve().parent.parent
# The Tasks ship inside the package. `REPO_ROOT` here is the guard's own file read, not a
# claim about where the manifests live: this file's subject is what a step declares, and that is the
# same YAML whether it arrived in a checkout or in a wheel.
TASKS = tekton_ci_path("tasks")

PARAM_REF = re.compile(r"\$\(params\.([\w.\-]+)\)")
WORKSPACE_REF = re.compile(r"\$\(workspaces\.([\w\-]+)\.path\)")
# `uv run [--flags] kmt …`, on one line. The line form is what both Tasks in the tree use.
UV_KMT = re.compile(r"uv run[^\n]*\bkmt\b")
CD_LINE = re.compile(r"(?m)^\s*cd\s+(?P<q>[\"']?)(?P<target>[^\"'\n]+)(?P=q)\s*$")

# Measured and not remembered: these are the Tasks whose steps run `uv run … kmt`, read off the tree
# below rather than asserted from the ticket. Without this the guard goes vacuous the moment somebody
# renames a command out of a manifest, and a guard that checks nothing reports green.
UV_KMT_TASKS = {"create-secret-from-json.yml", "run-tekton-pipeline.yml"}


def _steps(manifest):
    """Every step of a Task, whatever the shape (`steps` for a Task, `tasks` for a pipeline's step list)."""
    return manifest.get("spec", {}).get("steps") or []


def _step_text(step):
    return "\n".join([step.get("script", "")]
                     + list(step.get("command") or [])
                     + [str(arg) for arg in (step.get("args") or [])])


def _param_defaults(manifest):
    return {param["name"]: param.get("default", "") for param in manifest.get("spec", {}).get("params") or []}


def _mounts(manifest):
    mounts = []
    for workspace in manifest.get("spec", {}).get("workspaces") or []:
        mount = workspace.get("mountPath")
        if mount:
            mounts.append(mount)
    return mounts


def _resolve(value, manifest):
    """A Tekton variable reference down to the concrete path the step would get.

    `$(workspaces.<name>.path)` becomes that workspace's `mountPath` and `$(params.<name>)` becomes the
    param's default. A param with no default resolves to the empty string, which is honest: the Task
    says "this part comes from the caller", and what is checked below is only that no *declared* part
    leaves the mount.
    """
    text = str(value)
    for _ in range(8):  # nesting is one level deep in this tree; this is the guard against a loop
        if not PARAM_REF.search(text) and not WORKSPACE_REF.search(text):
            break
        text = PARAM_REF.sub(lambda m: str(_param_defaults(manifest).get(m.group(1), "")), text)
        text = WORKSPACE_REF.sub(
            lambda m: next((mount for name, mount in
                            ((w.get("name"), w.get("mountPath"))
                             for w in manifest.get("spec", {}).get("workspaces") or [])
                            if name == m.group(1)), ""), text)
    return text


def _inside_a_mounted_workspace(path, manifest):
    for mount in _mounts(manifest):
        root = mount.rstrip("/")
        if path == root or path.startswith(root + "/"):
            return True
    return False


def _declared_dir(step, manifest):
    """(how the step says it, the path it resolves to) or None when the step does not say.

    `workingDir` is the Tekton declaration. A `cd` is allowed to be the declaration too, but only when
    the string it enters is a value the step already states (an `env` entry), so that the directory is
    a fact of the manifest and not of the command line's text.
    """
    working_dir = step.get("workingDir")
    if working_dir:
        return "workingDir", working_dir, _resolve(working_dir, manifest)

    env_values = {str(entry.get("value")) for entry in step.get("env") or []}
    for match in CD_LINE.finditer(step.get("script", "")):
        target = match.group("target")
        if target in env_values:
            return "cd", target, _resolve(target, manifest)
    return None


def _uv_kmt_steps():
    """Every `uv run … kmt` step in `tekton_ci/tasks/`, with its manifest.

    Walks the whole directory and not the two files anyone named, so a third Task that picks this up
    is covered by the same rule and does not have to be remembered.
    """
    found = []
    for path in sorted(TASKS.glob("*.yml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            continue
        for step in _steps(manifest):
            if UV_KMT.search(_step_text(step)):
                found.append((path.name, manifest, step))
    return found


def test_the_run_pipeline_task_declares_its_working_directory():
    """The three asserts the defect needs: declared, param-resolved, mounted.

    A step with `workingDir: /workspace/kubementat` written out is a Task that cannot be moved to a
    cluster whose workspace is elsewhere, and a step that declares nothing runs from wherever the pod
    started, which is the bug. The param reference is what makes the declared directory bindable, and
    the same value is what `KUBEMENTAT_HOME` says, so the command and the step cannot drift apart.
    """
    manifest = yaml.safe_load(
        tekton_ci_path("tasks", "run-tekton-pipeline.yml").read_text(encoding="utf-8"))
    steps = _steps(manifest)
    assert len(steps) == 1, "one step is the ticket; more than one changes where this assert reads"
    step = steps[0]

    assert "workingDir" in step, \
        "the step runs `uv run kmt` from a directory no manifest states, so uv walks up from a cwd " \
        "that is not the checkout and fails with 'Failed to spawn: kmt' — see this file's docstring"

    working_dir = step["workingDir"]
    assert working_dir == "$(params.path-to-configuration)", \
        f"the step declares {working_dir!r}, and a literal directory is not something a Task can " \
        "bind to the workspace its pipeline hands it"

    env = {entry["name"]: entry["value"] for entry in step.get("env") or []}
    assert env.get("KUBEMENTAT_HOME") == working_dir, \
        "the command's home and the step's working directory are two values, so they can drift"

    assert _inside_a_mounted_workspace(_resolve(working_dir, manifest), manifest), \
        f"{_resolve(working_dir, manifest)!r} is not under a workspace the Task mounts " \
        f"{_mounts(manifest)}, so nothing could put the step in the project"


def test_no_uv_kmt_step_runs_from_a_directory_the_manifest_does_not_declare():
    """The general rule: a `uv run … kmt` step names its directory, or it is broken.

    This is the half that outlives the change it came from, and it is deliberately about every Task
    in the directory.
    A Task that adds a `kmt` command and forgets the directory — which is what happens when the
    `workingDir` belongs to a param the author did not notice — fails here even if its own unit tests
    all pass, because those tests run the argv and not the cwd.
    """
    for name, manifest, step in _uv_kmt_steps():
        declared = _declared_dir(step, manifest)
        assert declared is not None, \
            f"{name}: step {step.get('name')!r} runs `uv run … kmt` and declares no directory, " \
            "neither `workingDir:` nor a `cd` to a stated value, so the command runs from outside " \
            "the project it resolves"
        how, raw, resolved = declared
        assert _inside_a_mounted_workspace(resolved, manifest), \
            f"{name}: step {step.get('name')!r} enters {resolved!r} via {how}, which is not under a " \
            f"mounted workspace {_mounts(manifest)}"


def test_the_tasks_that_run_kmt_are_measured_and_not_remembered():
    """The guard is not vacuous: the walk finds the two Tasks that carry the rule.

    A rule stated over a filtered list stops meaning anything when the filter stops matching, so the
    set is pinned to what the tree holds today. Adding a third `uv run … kmt` Task is a legitimate
    change, and this assertion is the note that says the directory rule has to be applied to it — the
    check above is what tells you whether the new Task got it right.
    """
    assert {name for name, _, _ in _uv_kmt_steps()} == UV_KMT_TASKS, \
        "the set of Tasks that run `uv run … kmt` has moved; re-read the rule against the new list"


