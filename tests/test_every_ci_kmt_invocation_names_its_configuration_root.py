"""A `kmt` command that names no configuration root dies at the first step, in CI like anywhere else.

The defect is measured and not inferred. A root is *named, never located*:
`default_platform_config_dir()` (`config.py:260-300`) reads `KUBEMENTAT_PLATFORM_CONFIG_DIR` and
nothing else, and raises when it is unset. `KUBEMENTAT_HOME` is not the other spelling — it names the
tree a checkout hangs its *output* off, which is why both shipped Tasks can set it and still hand their
command no root. Measured on this checkout:

    env -u KUBEMENTAT_PLATFORM_CONFIG_DIR uv run kmt create-secret-from-json dev ns sec /tmp/nope.json
    → Error: no configuration root is named. … it has no default root to fall back to.

So the CI lane's own `kmt` step could not run: `KUBEMENTAT_PLATFORM_CONFIG_DIR` appears in zero places
under `src/kubementat/core/assets/`, and neither `run-tekton-pipeline` nor `create-secret-from-json` passed
`--config-dir`. This file says that *every* `kmt` invocation in a TaskRun names its root, enforced where
the rule belongs: in the shipped YAML, read off the tree by a test.

The layer it is enforced in matters. Most of this suite's trees are fixtures and not prepared
directories, so a rule phrased as "a tree must have a `platform_config`" and implemented in `Config`
breaks 227 unrelated tests — that is how this epic lost hours before, and it is why nothing here or in
the Task files touches `config.py`, `config_init.py`, `ClusterTarget`, or any Python read path. A
refusal that gets *nicer* is a silent default wearing a better sentence, which is what the rule that
named roots never locate deletes. The same is true of a `KUBEMENTAT_PLATFORM_CONFIG_DIR` step env entry
added to rescue the step: the noun is `--config-dir`, and an env default is the behaviour that rule
removed.

Two conventions for the value coexist, deliberately. Inside the **code** checkout the root is
`<checkout>/platform_config`, which is the shape the Tasks that read configuration by path still spell
(`deploy-local-helm-chart.yml:128`, `execute-mysql-file.yml:70`, `create-mysql-database.yml:59`).
The Tasks that run `kmt` spell it a second way: a `configuration-root` param that defaults to
`/workspace/configuration`, the directory the configuration clone is fetched into, plus
`/platform_config` on the command line. The workspace-clone shape mounts both clones into the *same*
workspace at *different* paths, which is why the param is a path and not a second
workspace mount: fewer moving parts in a pipeline that is already made of Tasks. One reason the derivation
stays on the step's own line instead of becoming a default is that a flip is one line only if the value is
one line now, and `configuration-root` is that line."""

import re
from pathlib import Path

import yaml

from asset_paths import tekton_ci_path

# The Tasks ship inside the package, so this comes from the same resolver the product uses
# rather than from a repo-root literal a wheel cannot answer.
TASKS = tekton_ci_path("tasks")

PARAM_REF = re.compile(r"\$\(params\.([\w.\-]+)\)")
WORKSPACE_REF = re.compile(r"\$\(workspaces\.([\w\-]+)\.path\)")
# `uv run [--flags] kmt …`, on one line — the form both Tasks in the tree use.
UV_KMT = re.compile(r"uv run[^\n]*\bkmt\b")
# `--config-dir <value>` or `--config-dir=<value>`, quoted or not, at the end of a command or on a
# continuation line. The value is read as a run of `$(…)` references and plain path characters, which
# is what lets a `$(params.x)` inside it keep its closing paren instead of ending the token there.
# The quote is a backreference so a `"…"` value cannot be matched by a bare token, and the `)` a
# bash-array close would otherwise hand back is not part of the answer.
CONFIG_DIR = re.compile(r"--config-dir(?:=|\s+)([\"']?)((?:\$\([^)]*\)|[^\s\"')])+)\1")

# Measured and not remembered: the Tasks whose steps run `uv run … kmt`, read off the tree below.
# Without this pin a rule stated over a filtered list goes vacuous the day the filter stops
# matching — a guard that checks nothing reports green.
UV_KMT_TASKS = {"create-secret-from-json.yml", "run-tekton-pipeline.yml"}


def _steps(manifest):
    return manifest.get("spec", {}).get("steps") or []


def _step_text(step):
    return "\n".join([step.get("script", "")]
                     + list(step.get("command") or [])
                     + [str(arg) for arg in (step.get("args") or [])])


def _param_defaults(manifest):
    return {param["name"]: param.get("default", "") for param in manifest.get("spec", {}).get("params") or []}


def _mounts(manifest):
    return [workspace["mountPath"] for workspace in manifest.get("spec", {}).get("workspaces") or []
            if workspace.get("mountPath")]


def _resolve(value, manifest):
    """A Tekton variable reference down to the concrete path the step would get.

    `$(workspaces.<name>.path)` becomes that workspace's `mountPath` and `$(params.<name>)` becomes
    the param's default. A param with no default resolves to the empty string, which is honest: the
    Task says "this part comes from the caller". What is checked is the *shape* — that the value is
    built out of references the Task declares and that it lands under a mount the Task declares —
    and not a fabricated absolute path.
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


def _uv_kmt_steps():
    """Every `uv run … kmt` step in `tekton_ci/tasks/`, with its manifest.

    Walks the whole directory and not the two files anyone named, so a third Task that picks this up
    is covered by the same rule instead of having to be remembered.
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


def _command_text(step):
    """The step's executable text, with the comments a shell would never run taken out.

    Both extractions below read this and not `_step_text`, so the counts are measured on one text and
    a prose "--config-dir" in an explanatory comment cannot be mistaken for a flag. A flag that only
    exists in a comment is exactly the bug this file is here for, so it is worth saying which text a
    guard reads.
    """
    return "\n".join(line for line in _step_text(step).splitlines()
                     if not line.lstrip().startswith("#"))


def _config_dir_values(step):
    """Every value `--config-dir` is handed on this step's `kmt` line(s)."""
    return [match.group(2) for match in CONFIG_DIR.finditer(_command_text(step))]


def test_every_kmt_step_in_a_shipped_task_names_its_configuration_root():
    """The rule: where a step runs `uv run … kmt`, that same line says which root it reads.

    Both halves get checked. One `--config-dir` per `kmt` invocation is what stops the flag landing
    on a line the command never reaches, and the resolved value sitting under a workspace the Task
    mounts is what stops a root that is a literal path to one cluster's filesystem — the failure the
    sibling guard checks for `workingDir`, applied to the directory the command *reads* instead of
    the one `uv` walks up from.
    """
    for name, manifest, step in _uv_kmt_steps():
        text = _command_text(step)
        invocations = len(UV_KMT.findall(text))
        values = _config_dir_values(step)
        assert len(values) >= invocations, \
            f"{name}: step {step.get('name')!r} runs `uv run … kmt` {invocations} time(s) and names " \
            f"a root {len(values)} time(s). Without --config-dir the command resolves no root, and " \
            "dies on the first read with 'no configuration root is named' — see this file's docstring"
        for value in values:
            assert PARAM_REF.search(value) or WORKSPACE_REF.search(value), \
                f"{name}: --config-dir is handed {value!r}, a literal path. It is not bindable to a " \
                "workspace the pipeline hands the Task, so the Task cannot move between clusters"
            resolved = _resolve(value, manifest)
            assert resolved.rstrip("/"), \
                f"{name}: --config-dir {value!r} resolves to nothing"
            assert _inside_a_mounted_workspace(resolved, manifest), \
                f"{name}: --config-dir {value!r} resolves to {resolved!r}, which is not under a " \
                f"workspace this Task mounts ({_mounts(manifest)}), so no pod could put it on disk"


def test_the_tasks_that_run_kmt_are_measured_and_not_remembered():
    """The rule is not vacuous: the walk finds exactly the two Tasks that carry it.

    A guard over a filtered list says nothing the moment the filter stops matching, and the set here
    was read off the tree rather than asserted from the ticket — every other `kmt` string under
    `assets/` is prose in a comment or a values file. Adding a third `uv run … kmt` Task is a
    legitimate change; this assertion is the note that says the rule above has to be read against it.
    """
    assert {name for name, _, _ in _uv_kmt_steps()} == UV_KMT_TASKS, \
        "the set of Tasks that run `uv run … kmt` has moved; re-read the second clause this file pins " \
        "against the new list and extend this pin deliberately"


def test_the_run_pipeline_task_names_a_root_that_is_not_where_the_step_starts():
    """The root the command reads and the directory the step starts in are two different answers.

    This is the second spelling, and it landed. `workingDir` and `KUBEMENTAT_HOME` say
    *where the step starts*, so `uv` can resolve the `kmt` entry point out of the **code** checkout; the
    `--config-dir` below says *what the command reads*, and that is now the **configuration** clone, a
    second repository fetched into the same workspace at a path of its own. Those two lines were kept
    apart on one param so that the day the configuration moved out of the checkout the flip would be a
    param swap and not a rewrite of the step; this is that day, and both params now exist.

    What is checked is that they still answer different questions. The failure the original docstring
    warned about -- someone folding the two back onto one value so the code checkout quietly becomes the
    configuration root again -- is caught here rather than in a pipeline that runs for an hour before
    `platform_config` turns out to be read from the wrong tree.
    """
    manifest = yaml.safe_load(tekton_ci_path("tasks", "run-tekton-pipeline.yml").read_text(encoding="utf-8"))
    steps = _steps(manifest)
    assert len(steps) == 1, "one step is the shape of this Task; more than one changes where this reads"
    step = steps[0]

    values = _config_dir_values(step)
    assert len(values) == 1, f"the step's kmt line names {len(values)} roots, and 1 is the shape here"
    value = values[0]

    assert "configuration-root" in value, \
        f"--config-dir is handed {value!r}, which is not the configuration-root param that moved the " \
        "root off the code checkout. Deriving it from the code checkout would say that code and " \
        "configuration are the same clone, which is the state this rule exists to leave behind"
    assert "path-to-configuration" not in value, \
        f"--config-dir {value!r} is still spelled off the code checkout's param. That was the old " \
        "answer, and the first clause of the rule is written to retire it"

    working_dir = step.get("workingDir")
    env_values = {entry["name"]: entry["value"] for entry in step.get("env") or []}
    assert working_dir == env_values.get("KUBEMENTAT_HOME"), \
        "the step's directory and its home are two values here, so they can drift — that is the " \
        "that is the lesson of two values that used to be one, and it still applies to the half that " \
        "stays put"
    assert "path-to-configuration" in (working_dir or ""), \
        f"workingDir is {working_dir!r}, which is no longer the code-checkout param. The code checkout " \
        "is what `uv` resolves the project from; moving it off this param needs its own argument"

    assert _resolve(value, manifest) != _resolve(working_dir, manifest), \
        "the root the command reads collapsed onto the directory the step starts in. Two params that " \
        "resolve to one path are one param wearing two names, and the configuration clone would be " \
        "read from the code tree -- the accident this rule was written to prevent"
    assert _inside_a_mounted_workspace(_resolve(value, manifest), manifest), \
        f"{_resolve(value, manifest)!r} is not under a workspace this Task mounts ({_mounts(manifest)})"


def test_the_configuration_root_in_the_task_is_a_path_and_not_a_borrowed_checkout():
    """`configuration-root` defaults to a directory the pipeline must create, and says so.

    The default is `/workspace/configuration`, which no Task can produce on its own: it is where a clone
    step puts the configuration repository. A default that names a directory nothing creates is only
    honest if the file says so and the failure is legible, which is what this pins -- and it keeps the
    value a workspace reference rather than a literal, so the Task still moves between clusters.
    """
    manifest = yaml.safe_load(tekton_ci_path("tasks", "run-tekton-pipeline.yml").read_text(encoding="utf-8"))
    params = {param["name"]: param.get("default", "")
              for param in manifest.get("spec", {}).get("params") or []}

    assert "configuration-root" in params, "the Task that started this rule should now carry the param"
    default = params["configuration-root"]
    assert WORKSPACE_REF.search(default) and not PARAM_REF.search(default), \
        f"configuration-root defaults to {default!r}. A default built out of other params is a guess " \
        "about the pipeline's clone layout; a workspace reference is a statement about this Task"
    assert _inside_a_mounted_workspace(_resolve(default, manifest), manifest), \
        f"{default!r} is not under a workspace the Task mounts, so no pod could put it on disk"

    text = _command_text(_steps(manifest)[0])
    assert "configuration-root" in text, \
        "the param exists but the step never reads it, so the root the command gets is still source A's"


def test_a_configuration_root_is_never_rescued_by_a_default_or_a_variable_the_step_sets():
    """Neither Task may fix the missing root with something that answers when nothing was named.

    `KUBEMENTAT_HOME` is a step env entry in both Tasks, and it is a *different* noun — it puts the
    step inside the project. The other spelling, `KUBEMENTAT_PLATFORM_CONFIG_DIR`, is a container-level
    default of exactly the kind the no-default rule deleted, and this repo declares it nowhere on purpose
    (`grep -rn KUBEMENTAT_PLATFORM_CONFIG_DIR src/kubementat/core/assets/` is empty). If it appeared on a
    step's env it would mean a Task that "passes" while its `--config-dir` is doing nothing, and the
    flag's whole meaning is that the operator/TaskRun named this run's root.
    """
    for name, manifest, step in _uv_kmt_steps():
        env_names = {entry["name"] for entry in step.get("env") or []}
        assert "KUBEMENTAT_PLATFORM_CONFIG_DIR" not in env_names, \
            f"{name}: step {step.get('name')!r} sets KUBEMENTAT_PLATFORM_CONFIG_DIR as an env entry, " \
            "which is the silent default that rule deleted. The flag is the operator speaking; an env " \
            "in a shipped Task is the Task answering for them"
        for value in _config_dir_values(step):
            assert "KUBEMENTAT_PLATFORM_CONFIG_DIR" not in value, \
                f"{name}: --config-dir is handed {value!r}, which routes back through the variable " \
                "the rule refuses to default"
