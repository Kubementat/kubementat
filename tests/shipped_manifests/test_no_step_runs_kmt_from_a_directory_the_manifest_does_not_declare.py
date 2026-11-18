"""Where a `uv run … kmt` step starts is part of the manifest, and a step that does not declare it is
broken.
"""

import re

import yaml
from asset_paths import tekton_ci_path

# The Tasks ship inside the package. `REPO_ROOT` here is the guard's own file read, not a
# claim about where the manifests live: this file's subject is what a step declares, and that is the
# same YAML whether it arrived in a checkout or in a wheel.
TASKS = tekton_ci_path("tasks")

PARAM_REF = re.compile(r"\$\(params\.([\w.\-]+)\)")
WORKSPACE_REF = re.compile(r"\$\(workspaces\.([\w\-]+)\.path\)")
# `uv run [--flags] kmt …`, on one line. The line form is what both Tasks in the tree use.
UV_KMT = re.compile(r"uv run[^\n]*\bkmt\b")
CD_LINE = re.compile(r"(?m)^\s*cd\s+(?P<q>[\"']?)(?P<target>[^\"'\n]+)(?P=q)\s*$")

# The Tasks whose steps run `uv run … kmt`, read from the shipped tree so a renamed Task is still checked.
UV_KMT_TASKS = {"create-secret-from-json.yml", "run-tekton-pipeline.yml"}


def _steps(manifest):
    """Every step of a Task, whatever the shape (`steps` for a Task, `tasks` for a pipeline's step
    list).
    """
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
    """A Tekton variable reference down to the concrete path the step would get."""
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
    """(how the step says it, the path it resolves to) or None when the step does not say."""
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
    """Every `uv run … kmt` step in `tekton_ci/tasks/`, with its manifest."""
    found = []
    for path in sorted(TASKS.glob("*.yml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            continue
        for step in _steps(manifest):
            if UV_KMT.search(_step_text(step)):
                found.append((path.name, manifest, step))
    return found


def test_no_uv_kmt_step_runs_from_a_directory_the_manifest_does_not_declare():
    """The general rule: a `uv run … kmt` step names its directory, or it is broken."""
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
