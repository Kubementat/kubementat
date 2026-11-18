"""Every configuration root a shipped CI Task names must resolve against a real tree."""
import re
from pathlib import Path

import yaml
from asset_paths import tekton_ci_path
from click.testing import CliRunner

from cli.commands.environments import environments_list

# The workspace the shipped Tasks mount by default, and the directory names their clone steps use.
# A test workspace stands in for `$(workspaces.workspace.path)`; the two clone directories hang off it.
CODE_DIR = "kubementat"
CONFIG_DIR = "configuration"

# What sits under a configuration root: the environment is the interesting half.
ENVIRONMENT = "dev"
TEAM = "dev1"
APP = "nginx"


# ---------------------------------------------------------------------------------------------
# The tree. Built once, for real, because a path guard is worth exactly what it can open.
# ---------------------------------------------------------------------------------------------

def _build_v2_root(root: Path) -> Path:
    """`<root>/kubementat.yaml` + `<root>/<environment>/...`, the shape a configuration root has."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "kubementat.yaml").write_text("schema_version: 1\nkubementat: \">=0.4,<2\"\n",
                                          encoding="utf-8")
    env = root / ENVIRONMENT
    env.mkdir(parents=True, exist_ok=True)
    (env / "static.json").write_text(
        '{"CLUSTER_KUBE_CONTEXT": "the-one-claiming-this-environment", "CLUSTER_NAME": "probe"}',
        encoding="utf-8")

    team = env / TEAM
    team.mkdir(parents=True, exist_ok=True)
    (team / "static.json").write_text('{"TEAM_NAME": "dev1"}', encoding="utf-8")
    app = team / APP
    app.mkdir(parents=True, exist_ok=True)
    # The team layer: the app's own encrypted values, inside the app directory.
    (app / "values.encrypted.yaml").write_text("replicaCount: 1\n", encoding="utf-8")
    return root


def _build_combined_root(root: Path) -> Path:
    """A combined tree, where `platform_config/` survives inside a checkout."""
    return _build_v2_root(root / "platform_config")


# The param values a TaskRun supplies for params the shipped Tasks deliberately leave empty. They are
# empty on purpose — a name is the operator's answer — and a path resolver that treats them as the
# empty string produces a path that resolves but points elsewhere -- which is the failure this file is
# written to catch, so the resolver must not make that mistake itself.
RUN_PARAM_VALUES = {"environment": ENVIRONMENT, "team": TEAM, "app-name": APP}


# ---------------------------------------------------------------------------------------------
# Reading a shipped Task's params and script the way Tekton would.
# ---------------------------------------------------------------------------------------------

def _task(name: str) -> dict:
    return yaml.safe_load(tekton_ci_path("tasks", f"{name}.yml").read_text(encoding="utf-8"))


def _params(manifest: dict) -> dict:
    return {p["name"]: p.get("default", "") for p in manifest.get("spec", {}).get("params") or []}


def _script(manifest: dict) -> str:
    """The steps' scripts and commands joined, with comments dropped."""
    lines = []
    for step in manifest.get("spec", {}).get("steps") or []:
        text = "\n".join([step.get("script", "")] + list(step.get("command") or []))
        lines.extend(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return "\n".join(lines)


def _resolve_shell_variables(text: str) -> dict:
    """`NAME="$(params.x)"` lines the way bash's `set -ue` block would, for one pass."""
    variables = {}
    for name, value in re.findall(r'^\s*([A-Z_]+)="([^"]*)"\s*$', text, re.M):
        variables[name] = value
    return variables


def _substitute(value: str, params: dict, workspace: str) -> str:
    """Resolve `$(params.X)` and `$(workspaces.workspace.path)` down to a filesystem path."""
    text = value
    for _ in range(8):
        previous = text
        for name, default in params.items():
            value_to_use = default if default else RUN_PARAM_VALUES.get(name, default)
            text = text.replace(f"$(params.{name})", str(value_to_use))
        text = text.replace("$(params.automation-git-project-name)", CODE_DIR)
        text = text.replace("$(workspaces.workspace.path)", str(workspace))
        if text == previous:
            break
    assert "$(" not in text, f"unresolved reference left in {value!r} -> {text!r}"
    return text


def _substitute_path(value: str, params: dict, workspace: str, script: str) -> str:
    """A path in a Task's script, whether it is spelled with params or with the variables the script
    assigns them into. Both are the same path; the second is how three of these Tasks write it.
    """
    variables = _resolve_shell_variables(script)
    text = value
    for _ in range(12):
        previous = text
        for name, assigned in variables.items():
            if f"${{{name}}}" in text or f"${name}" in text:
                text = text.replace(f"${{{name}}}", str(assigned))
                text = re.sub(r"\$" + name + r"(?![A-Za-z0-9_])", str(assigned), text)
        text = _substitute(text, params, workspace) if "$" in text else text
        if text == previous:
            break
    assert "$" not in text, f"unresolved variable left in {value!r} -> {text!r}"
    return text


# ---------------------------------------------------------------------------------------------
# The census, taken from the shipped Tasks rather than from a list someone remembers.
#
# The split this file cares about is one question: is the root this Task names the configuration clone
# or the code checkout? Source B landed for the clone steps and the `kmt` invocations and said plainly
# that the Tasks still reading `platform_config/` out of the code checkout are still to do, so the answer
# has to come from the manifests, not from a hardcoded pair of names that will be wrong the day someone
# finishes that work.
# ---------------------------------------------------------------------------------------------

ROOT_PARAMS = ("configuration-root", "path-to-configuration")


def _config_reading_tasks() -> list:
    """Every shipped Task that declares a root it reads configuration from."""
    found = []
    for path in sorted(tekton_ci_path("tasks").glob("*.yml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        params = _params(manifest)
        if any(name in params for name in ROOT_PARAMS):
            found.append(path.stem)
    return found


def _is_configuration_clone(root: Path, workspace: str) -> bool:
    """True when this root is the configuration clone, not the code checkout."""
    config = Path(_substitute(f"$(workspaces.workspace.path)/{CONFIG_DIR}", {}, workspace))
    code = Path(_substitute(f"$(workspaces.workspace.path)/{CODE_DIR}", {}, workspace))
    if root == code:
        return False
    assert root == config, \
        f"{root} is neither the configuration clone {config} nor the code checkout {code}, so this " \
        "file's split between source A and source B does not cover it and the census should say so " \
        "rather than guess"
    return True


def _census(workspace: str) -> dict:
    """{(task, root param): {root, paths, appends_platform_config}} for every shipped configuration
    root.
    """
    survey = {}
    for name in _config_reading_tasks():
        manifest = _task(name)
        params = _params(manifest)
        script = _script(manifest)
        for root_param in (n for n in ROOT_PARAMS if n in params):
            root = Path(_substitute(params[root_param], params, workspace))
            paths = [p for p in _paths_from_root(script, params, root_param, workspace) if p != str(root)]
            survey[(name, root_param)] = {
                "root": root,
                "paths": paths,
                "appends_platform_config": any("platform_config" in Path(p).parts for p in paths),
            }
    return survey


def _locates_only_the_project(entry: dict) -> bool:
    """A root that leads to no read is the directory `uv` runs from, not a configuration root."""
    return not entry["paths"]


# ---------------------------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------------------------


def test_a_source_b_root_is_never_written_with_a_platform_config_segment_between_it_and_the_read(tmp_path):
    """Naming a root is the point; a script that appends `platform_config` is locating one."""
    workspace = tmp_path / "workspace"
    _build_v2_root(workspace / CONFIG_DIR)
    survey = _census(str(workspace))
    checked = 0

    for (name, root_param), entry in sorted(survey.items()):
        root = entry["root"]
        if _locates_only_the_project(entry):
            continue
        clone = _is_configuration_clone(root, str(workspace))
        for path_text in entry["paths"]:
            checked += 1
            path = Path(path_text)
            assert path == root or str(path).startswith(str(root) + "/"), \
                f"{name}.{root_param}: {path} is not under the root {root} its param names"
            if not clone:
                continue
            assert "platform_config" not in path.parts, \
                f"{name}.{root_param}: {path} puts platform_config between the configuration clone " \
                f"({root}) and what it reads. On a configuration repository that is one directory too " \
                "deep, and the command there says the tree declares nothing. The combined tree's operator " \
                "passes that level *as* the root, so a script that adds it is right for one tree and wrong " \
                "for the other"

    assert checked >= 3, \
        f"only {checked} config paths resolved; the Tasks stopped building paths out of their roots, so " \
        "this file is guarding a shape that is no longer there"


def _paths_from_root(script: str, params: dict, root_param: str, workspace: str) -> list:
    """Every absolute path in the script that descends from one root param."""
    paths = []
    # Assignments, and the arguments that go straight onto a command line. Quoting is read line by line
    # rather than by pairing quotes across the whole script, because a bash script has apostrophes and
    # half-quoted echo lines, and a guard that pairs quotes wrong silently reads the wrong strings.
    candidates = [m.group(1) for m in re.finditer(r'^\s*[A-Z_]+="([^"]*)"\s*$', script, re.M)]
    candidates += re.findall(r'--config-dir\s+"([^"]+)"', script)
    # A variable passed as an argument is the same path its assignment built, so `-f
    # "$HELM_VALUES_FILE"` needs no candidate of its own; the line above already carries it.

    for text in candidates:
        if f"$(params.{root_param})" in text or _derived_from_root(script, text, root_param):
            resolved = _substitute_path(text, params, workspace, script)
            if resolved.startswith("/") and " " not in resolved and "*" not in resolved:
                paths.append(resolved)
    return sorted(set(paths))


def _derived_from_root(script: str, text: str, root_param: str) -> bool:
    """True when a `$VARIABLE` inside `text` was assigned the param that names this root."""
    for name, assigned in _resolve_shell_variables(script).items():
        if f"$(params.{root_param})" not in str(assigned):
            continue
        if f"${{{name}}}" in text:
            return True
        if re.search(r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])", text):
            return True
    return False


def test_a_combined_tree_works_by_naming_platform_config_as_the_root(tmp_path):
    """An operator whose tree keeps `platform_config/` inside the checkout, held to the same
    assertions.
    """
    workspace = tmp_path / "workspace"
    checkout = workspace / CODE_DIR
    _build_combined_root(checkout)

    named = checkout / "platform_config"
    listed = CliRunner(mix_stderr=False).invoke(environments_list, ["--config-dir", str(named)])
    assert listed.exit_code == 0, \
        "the combined tree is broken: pointing --config-dir at the checkout's own platform_config " \
        "does not read it"
    assert ENVIRONMENT in listed.output

    # And the wrong answer is still the wrong answer: the checkout itself carries no environment.
    wrong = CliRunner(mix_stderr=False).invoke(environments_list, ["--config-dir", str(checkout)])
    assert ENVIRONMENT not in wrong.output, \
        "the checkout's own top level read an environment, so the two roots were never different " \
        "things and this test is checking nothing"
