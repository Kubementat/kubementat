"""Every configuration root a shipped CI Task names must resolve against a real tree.

This file exists because a guard can check a param's *shape* and still miss the bug.

The earlier guard (`test_the_configuration_root_in_ci_is_a_clone_and_not_the_product_checkout.py`) asked
whether the clone step and the reader step agree about one directory. They did agree, and both were wrong:
the reader was handed `<clone>/platform_config`, which a configuration root does not have. A root puts
`<environment>/` one
directory below the configuration root, so a clone of a configuration repository *is* the root, and
appending `platform_config` to it reads one level too deep. The disagreement that the shape guard watches
for never happens; the failure is a `no environment in ... declares a cluster` printed about a clone that
does declare one, attributed to the operator.

So the check here is not what the path says but what the path **opens**. A configuration-root-shaped
directory is built once for real, a workspace path is invented, each shipped Task's param default is
substituted into it, and the resulting path is required to name something that exists in the tree. The
mutations that pass a spelling check and fail this one:

* append `platform_config` to a root -> the environment directory is no longer under it;
* point the root at the code checkout -> nothing in the configuration tree is under it;
* drop the environment segment out of a values path -> the app directory is no longer under it.

Two shapes are asserted and neither is a fiction:

*1. A configuration repository*: the root holds `kubementat.yaml`, `<environment>/static.json`, and
`<environment>/<team>/`. The first directory that carries a `static.json` at depth one is an
environment, which is what makes depth
the thing to get right.

*2. A combined tree*, which is allowed to keep `platform_config/` inside a checkout. There the
operator points the root *at* that directory, and the same assertions hold one level down — which is the
proof that the rule is "name the root", not "know the layout". Nothing in a shipped script may add the
segment, because a script that adds it can only work for one of the two shapes, and both shapes have to
work.

Nothing in `src/` is touched. The rule is enforced on shipped YAML, against a directory built in a temp
tree, and `Config`/`config_init.py`/the read paths stay out of it — most trees in this suite are fixtures
and not prepared directories, and a tree-shape rule phrased against `Config` is how 227 unrelated tests
break.
"""
import re
from pathlib import Path

import yaml
from click.testing import CliRunner

from asset_paths import tekton_ci_path
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
    """`<root>/kubementat.yaml` + `<root>/<environment>/...`, the shape a configuration root has.

    `static.json` carries `CLUSTER_KUBE_CONTEXT` because `environments list` prints an environment that
    declares a cluster and refuses the rest, and the refusal is indistinguishable from a root that names
    nothing. A test that cannot tell those apart is a test that passes on the bug.
    """
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
    """A combined tree, where `platform_config/` survives inside a checkout.

    Same contents, one level down, under the directory an operator is told to point `--config-dir`
    at. The rule under test has to hold for this tree too, and it holds only because the operator names
    the root rather than the Task guessing the segment.
    """
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
    """The steps' scripts and commands joined, with comments dropped.

    A commented line about `platform_config` is prose about the rule, not the rule, and this file is
    about the argv a pod runs.
    """
    lines = []
    for step in manifest.get("spec", {}).get("steps") or []:
        text = "\n".join([step.get("script", "")] + list(step.get("command") or []))
        lines.extend(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return "\n".join(lines)


def _resolve_shell_variables(text: str) -> dict:
    """`NAME="$(params.x)"` lines the way bash's `set -ue` block would, for one pass.

    The Task's script assigns every parameter to an upper-case name before it builds a path, so a path
    built out of those names is a path built out of the params -- and the assignment lines are the only
    honest way to learn the names, rather than this file inventing a mapping.
    """
    variables = {}
    for name, value in re.findall(r'^\s*([A-Z_]+)="([^"]*)"\s*$', text, re.M):
        variables[name] = value
    return variables


def _substitute(value: str, params: dict, workspace: str) -> str:
    """Resolve `$(params.X)` and `$(workspaces.workspace.path)` down to a filesystem path.

    Params with no shipped default (an environment, a team, an app name are never defaulted -- naming one
    for the operator is what no shipped default does) are answered by `RUN_PARAM_VALUES`, which is what a
    TaskRun would pass. Leaving them empty is the bug class this file exists to close: a path with empty
    segments still "resolves", and it resolves to the wrong directory.
    """
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
    assigns them into. Both are the same path; the second is how three of these Tasks write it."""
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
    """Every shipped Task that declares a root it reads configuration from.

    A Task with no root param is not this file's business: it reads no configuration, which is a different
    question and is answered by whichever guard covers that Task.
    """
    found = []
    for path in sorted(tekton_ci_path("tasks").glob("*.yml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        params = _params(manifest)
        if any(name in params for name in ROOT_PARAMS):
            found.append(path.stem)
    return found


def _is_configuration_clone(root: Path, workspace: str) -> bool:
    """True when this root is the configuration clone, not the code checkout.

    Both are directories under the same workspace, and which one a Task's root lands in is exactly the
    question the source A/source B split is about -- so it is decided by resolving, not by trusting the
    param's name.
    """
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
    """{(task, root param): {root, paths, appends_platform_config}} for every shipped configuration root.

    Classified per **param**, not per Task, because `run-tekton-pipeline` declares two roots and means two
    different things by them: `path-to-configuration` is the directory `uv` walks up from to find the
    project, and `configuration-root` is the directory the command reads. Sorting that out per Task would
    call the clearest documented case "mixed" and be wrong about it.
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
    """A root that leads to no read is the directory `uv` runs from, not a configuration root.

    `run-tekton-pipeline`'s `path-to-configuration` is that: the manifest says so out loud, in the param's
    own description. Calling it a configuration root would be the file guessing, so the census says what the
    manifest says.
    """
    return not entry["paths"]


# ---------------------------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------------------------

def test_the_configuration_root_a_ci_task_hands_the_command_resolves_against_a_real_v2_tree(tmp_path):
    """`--config-dir` as shipped must find an environment, not refuse one that is there.

    This is the whole point of the file, and the check is deliberately against the **argument the script
    passes**, not the param's default: the bug was in how the argument was built, and a test that reads
    the default would watch a param that is correct while the command line underneath it points somewhere
    else. So each shipped `--config-dir` is pulled out of the script, resolved against a configuration-root
    tree, and
    handed to the command. The tree must be readable and the environment must appear.

    The mutation this catches is the one that broke a real deploy path: the same command line shipped
    with `/platform_config` appended, which resolves to a directory containing no environment directory
    at all, and the command answers that *no environment declares a cluster* about a clone that does.
    """
    workspace = tmp_path / "workspace"
    root = _build_v2_root(workspace / CONFIG_DIR)
    checked = 0

    for task in ("run-tekton-pipeline", "create-secret-from-json"):
        manifest = _task(task)
        params = _params(manifest)
        script = _script(manifest)
        for argument in re.findall(r'--config-dir\s+"([^"]+)"', script):
            checked += 1
            resolved = _substitute_path(argument, params, str(workspace), script)
            result = CliRunner(mix_stderr=False).invoke(environments_list, ["--config-dir", resolved])
            assert result.exit_code == 0, f"{task}: --config-dir {resolved!r} failed: {result.output}"
            assert ENVIRONMENT in result.output, \
                f"{task}: --config-dir {resolved!r} reads {root.parent} and does not see {ENVIRONMENT!r}. " \
                "The root is one directory off, and the command's answer will be blamed on the clone"
        # And the param has to be what is actually passed: a correct param that the script never uses is
        # decoration, which is the other way this rule goes quiet.
        assert "$(params.configuration-root)" in script, \
            f"{task}: declares configuration-root but does not pass it, so the root is stated and unused"

    assert checked == 2, f"expected two shipped --config-dir arguments, found {checked}"


def test_a_source_b_root_is_never_written_with_a_platform_config_segment_between_it_and_the_read(tmp_path):
    """Naming a root is the point; a script that appends `platform_config` is locating one.

    This is resolved, not grepped, for the same reason the rest of the file is: the bug hid in
    `"${PATH_TO_CONFIGURATION}/platform_config/..."`, which is a script variable and not a param
    reference, so a check written against `$(params.` would have read past it.

    Two things are asserted per resolved path. The first is containment: a path that is not under the root
    its own param names is a path that is not reading the tree that was declared. The second is the
    segment, and only for the clone-rooted half -- because the Tasks still reading
    `platform_config/` out of the code checkout are still to do, and the other test in this file names
    that half rather than pretending it is clean.
    """
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


def test_the_configuration_roots_still_reading_through_the_code_checkout_are_named(tmp_path):
    """These are still source A, so this file names them instead of letting them pass quietly.

    Every root that leads to a read is one of two answers: the configuration clone, or the code checkout.
    The first must not append `platform_config`; the second still does, and that half is still to do. Both halves are asserted here, and the point of naming them is that a reader who
    finds `platform_config` in a shipped Task script can tell, from this file, whether they are looking at
    the pending half or at a regression -- and a Task that moves its root onto the clone while keeping the
    segment trips the assertion above.
    """
    workspace = tmp_path / "workspace"
    survey = _census(str(workspace))

    reads = {key: entry for key, entry in survey.items() if not _locates_only_the_project(entry)}
    assert reads, "no shipped root leads to a read, so the census is guarding nothing"

    on_clone, on_checkout = [], []
    for key, entry in sorted(reads.items()):
        clone = _is_configuration_clone(entry["root"], str(workspace))
        (on_clone if clone else on_checkout).append(key)

    # The clone-rooted half: no segment, because a configuration repository has nowhere to put one.
    for key in on_clone:
        entry = reads[key]
        for path in entry["paths"]:
            assert "platform_config" not in Path(path).parts, \
                f"{key[0]}.{key[1]}: {path} puts platform_config between the configuration clone " \
                f"({entry['root']}) and what it reads, which is one directory too deep on a " \
                "configuration repository"

    # The checkout-rooted half: still source A, still reading through the old level, stated by name.
    for key in on_checkout:
        assert reads[key]["appends_platform_config"], \
            f"{key[0]}.{key[1]}: root is the code checkout and nothing it reads passes through " \
            "platform_config, so this root is no longer source A's shape and the census has to change"

    assert on_checkout, "every configuration root is on the clone, so the checkout half is done and " \
        "this file should say that rather than assert an empty list"
    assert on_clone, "no configuration root is on the clone, so source B did not land and the census " \
        "above is asserting against a shape that is not shipped"


def _paths_from_root(script: str, params: dict, root_param: str, workspace: str) -> list:
    """Every absolute path in the script that descends from one root param.

    Collected from assignment lines and from quoted paths, because the Tasks do both: `deploy-local-helm-chart`
    builds `HELM_VALUES_FILE` from a variable that was assigned the param, and the kmt Tasks put the
    param straight onto a command line. Missing either one is how a path guard goes quiet while the bug
    stays in the file.
    """
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
    """True when a `$VARIABLE` inside `text` was assigned the param that names this root.

    Both bash spellings count, because these Tasks use both: `${NAME}` and the bare `$NAME`.
    """
    for name, assigned in _resolve_shell_variables(script).items():
        if f"$(params.{root_param})" not in str(assigned):
            continue
        if f"${{{name}}}" in text:
            return True
        if re.search(r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])", text):
            return True
    return False


def test_the_values_file_a_deploy_step_reads_lands_on_the_app_directory(tmp_path):
    """A configuration root's team layer is `<root>/<environment>/<team>/<app>/values.encrypted.yaml`, so
    that is where
    the deploy step's `HELM_VALUES_FILE` has to point.

    A path built with the extra segment names a file that no prepared tree has, and the step fails with
    helm's own `values file does not exist` rather than a sentence about the tree.
    """
    workspace = tmp_path / "workspace"
    _build_v2_root(workspace / CONFIG_DIR)

    manifest = _task("deploy-local-helm-chart")
    params = _params(manifest)
    script = _script(manifest)

    assignment = re.search(r'HELM_VALUES_FILE="([^"]+)"', script)
    assert assignment, "the deploy step no longer derives its values file, so nothing pins where it is"
    resolved = _substitute_path(assignment.group(1), params, str(workspace), script)
    expected = workspace / CONFIG_DIR / ENVIRONMENT / TEAM / APP / "values.encrypted.yaml"
    assert Path(resolved) == expected, \
        f"HELM_VALUES_FILE resolves to {resolved!r}, not {expected}. The app directory sits under " \
        "the team, under the environment, under the root -- and a deploy that reads a path nobody " \
        "writes to is a deploy that succeeds with the product's defaults and calls it configured"
    assert Path(resolved).is_file(), f"{resolved!r} is not a file in the configuration tree"


def test_a_combined_tree_works_by_naming_platform_config_as_the_root(tmp_path):
    """An operator whose tree keeps `platform_config/` inside the checkout, held to the same assertions.

    The tree keeps `platform_config/` inside a checkout. Pointing the root at that directory finds the
    environment; pointing it at the checkout's top level does not, and that is the operator's choice to
    make by naming a root, not something a Task may fix by appending.
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
