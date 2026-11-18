"""The configuration root is a clone, and it is not the code checkout."""


import yaml
from asset_paths import REPO_ROOT, tekton_ci_path

PIPELINES = tekton_ci_path("pipelines")
TASKS = tekton_ci_path("tasks")
TEMPLATES = PIPELINES / "templates"
EXAMPLES = REPO_ROOT / "examples" / "tekton_ci" / "pipelines"

# Measured off the tree and not remembered: the shipped deploy pipeline templates that clone at all.
# A guard that walks a list somebody typed goes vacuous the day the list stops matching, which is the
# failure class every sibling guard file in this suite has written down.
CLONING_PIPELINE_TEMPLATES = {"template-deploy-pipeline.yml"}

# The shipped examples that clone, measured the same way: the hello-world example clones nothing, so it
# is not in this set, and neither is any example that clones an app without reading a configuration.
# Those absences are the assertion below's other half -- the walk finds what clones and not what merely
# exists.
CLONING_EXAMPLE_PIPELINES = {"01-full-deployment-pipeline.yml", "deploy-pipeline-nginx-example.yml"}

# The code clone's local directory name, as the shipped default says.
CODE_PROJECT_DEFAULT = "kubementat"
CONFIG_PROJECT_DEFAULT = "configuration"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(manifest):
    return manifest.get("spec", {}).get("steps") or []


def _task_steps(path):
    manifest = _load(path)
    return manifest, _steps(manifest)


def _pipeline_tasks(manifest):
    return manifest.get("spec", {}).get("tasks") or []


def _params(manifest):
    return {param["name"]: param.get("default", "")
            for param in manifest.get("spec", {}).get("params") or []}


def _step_params(step):
    return {param["name"]: param.get("value", "") for param in step.get("params") or []}


def _substitute(value, params, workspace="/workspace"):
    """A Tekton reference down to what it would resolve to in the shipped defaults."""
    text = str(value)
    for _ in range(8):
        replaced = text
        for name, default in params.items():
            replaced = replaced.replace(f"$(params.{name})", str(default))
        replaced = replaced.replace("$(workspaces.workspace.path)", workspace)
        if replaced == text:
            break
        text = replaced
    return text


def _clone_steps(manifest):
    """The pipeline's clone steps, each resolved to the directory it puts on disk."""
    found = []
    for task in _pipeline_tasks(manifest):
        if (task.get("taskRef") or {}).get("name") != "git-clone-with-ssh-auth":
            continue
        values = _step_params(task)
        found.append({
            "name": task.get("name"),
            "url": values.get("git-url", ""),
            "project_name": values.get("git-project-name", ""),
            "revision": values.get("git-revision", ""),
        })
    return found


def _configuration_reads(manifest):
    """Every value in the pipeline that points at a configuration tree."""
    reads = []
    for task in _pipeline_tasks(manifest):
        for name, value in _step_params(task).items():
            text = str(value)
            if "platform_config" in text or "configuration" in name:
                reads.append({"task": task.get("name"), "param": name, "value": text})
    return reads


def test_the_shipped_deploy_template_clones_the_configuration_repository():
    """The first half of the sentence: there is a step that clones the configuration."""
    templates = sorted((TEMPLATES / n for n in CLONING_PIPELINE_TEMPLATES), key=lambda p: p.name)
    assert templates, f"the shipped deploy templates are gone: {CLONING_PIPELINE_TEMPLATES}"

    for path in templates:
        if not path.exists():
            continue
        manifest = _load(path)
        clones = _clone_steps(manifest)
        assert clones, f"{path.name}: no clone step at all, so nothing puts configuration on disk"

        # `git-clone-with-ssh-auth` puts the clone at <workspace>/<git-project-name>, so the param that
        # names the directory is the only thing a reader can be told.
        project_names = [clone["project_name"] for clone in clones]
        assert any("configuration" in name for name in project_names), (
            f"{path.name}: the clone steps land in {project_names}, and none of them is declared as the "
            "configuration clone. A reader that appends platform_config to the code checkout's directory "
            "is reading code, not configuration")


def test_the_configuration_clone_lands_where_the_reader_reads_from():
    """The cost of the cheaper shape, asserted: the clone's directory and the read path have to agree.
    """
    for name in sorted(CLONING_PIPELINE_TEMPLATES):
        path = TEMPLATES / name
        if not path.exists():
            continue
        manifest = _load(path)
        params = _params(manifest)
        clones = _clone_steps(manifest)
        config_clones = [c for c in clones if "configuration" in str(c["project_name"])]
        assert config_clones, f"{name}: no clone is declared as the configuration clone"
        config_landing = _substitute(f"$(workspaces.workspace.path)/{config_clones[0]['project_name']}",
                                     params)

        for read in _configuration_reads(manifest):
            resolved = _substitute(read["value"], params)
            # Strip the layer if the value spells it, and use the rest as the root either way. Leaving
            # out the values that do not spell `platform_config` would leave the exact hole this file
            # is for: a param that *means* the configuration root but hands over a bare directory would
            # be skipped, and the mutation that points it back at the code checkout would report green.
            root = resolved.split("/platform_config")[0]
            assert root == config_landing, (
                f"{name}: {read['task']}.{read['param']} reads configuration out of {root!r}, but the "
                f"configuration clone lands in {config_landing!r}. That is the accident the rule was "
                "written to end, and here it would be silent: the clone succeeds, the step succeeds, and the "
                "failure is a missing file")
            assert root.rstrip("/") != _substitute(
                f"$(workspaces.workspace.path)/{CODE_PROJECT_DEFAULT}", params).rstrip("/"), (
                f"{name}: {read['task']}.{read['param']} still reads from the code checkout {root!r}")


def test_the_task_default_is_a_configuration_path_and_not_a_borrowed_checkout():
    """The half of the rule that says the root is stated and not inferred: the *default*."""
    path = TASKS / "deploy-local-helm-chart.yml"
    assert path.exists(), f"{path} is where this rule ships"
    manifest = _load(path)
    params = _params(manifest)

    assert "path-to-configuration" in params, "the Task that started this rule lost its param"
    default = params["path-to-configuration"]
    assert "$(workspaces.workspace.path)" in default, \
        f"path-to-configuration defaults to {default!r}, which is not anchored on a workspace the Task " \
        "mounts. A literal path is a path to one cluster's filesystem"
    assert "automation-git-project-name" not in default, \
        f"path-to-configuration defaults to {default!r}, which is built out of the code clone's param. " \
        "That was the answer that made code and configuration one tree, and it is the sentence this " \
        "rule is written to retire"

    resolved = _substitute(default, params)
    mounts = [w.get("mountPath") for w in manifest.get("spec", {}).get("workspaces") or []]
    assert any(resolved == (m or "").rstrip("/") or resolved.startswith((m or "").rstrip("/") + "/")
               for m in mounts), \
        f"{resolved!r} is not under a workspace this Task mounts ({mounts})"
    assert resolved.rstrip("/").endswith(CONFIG_PROJECT_DEFAULT), \
        f"the default resolves to {resolved!r}, which does not name the configuration clone's directory"


def test_the_task_that_runs_kmt_names_the_configuration_root_from_a_param_of_its_own():
    """The clause about the Task that runs `kmt`, in the shape it has now."""
    path = TASKS / "create-secret-from-json.yml"
    assert path.exists(), f"{path} is where this rule ships"
    manifest, steps = _task_steps(path)
    params = _params(manifest)

    assert "configuration-root" in params, "the root this command reads is still someone else's param"
    root = _substitute(f"{params['configuration-root']}/platform_config", params)
    assert "platform_config" in root

    for step in steps:
        text = "\n".join([step.get("script", "")] + list(step.get("command") or []))
        executable = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        if "kmt" not in executable:
            continue
        assert "configuration-root" in executable, \
            "the step names a root that is not the param, so the param is decoration"
        assert "$(params.automation-git-project-name)/platform_config" not in executable, \
            "the step still reads configuration out of the code checkout"

    # And the two answers have to stay two answers: `cd` on code, `--config-dir` on configuration.
    code = _substitute("/workspace/$(params.automation-git-project-name)", params)
    assert code.rstrip("/") != root.rstrip("/").removesuffix("/platform_config"), \
        "the code checkout and the configuration root resolve to one directory, which is the single-" \
        "clone shape this change was written to leave behind"


def test_the_two_clone_steps_land_in_different_directories():
    """One workspace, so the two clones must not collide."""
    for name in sorted(CLONING_PIPELINE_TEMPLATES):
        path = TEMPLATES / name
        if not path.exists():
            continue
        manifest = _load(path)
        params = _params(manifest)
        clones = _clone_steps(manifest)
        landings = {_substitute(f"$(workspaces.workspace.path)/{c['project_name']}", params)
                    for c in clones}
        assert len(landings) == len(clones), (
            f"{name}: the clone steps land in {sorted(landings)} and there are {len(clones)} of them. "
            "Two clones of two sources may not unpack into one directory")

        # The rule the whole option rests on: code and configuration must be distinguishable by path.
        code = [c for c in clones if not _is_configuration_clone(c, params)]
        config = [c for c in clones if _is_configuration_clone(c, params)]
        assert code and config, \
            f"{name}: expected one code clone and one configuration clone, found {len(code)} and " \
            f"{len(config)}. With only one clone the source-B half of this file is asserting nothing"


def test_the_shipped_examples_that_clone_carry_the_same_shape():
    """An example is what an operator copies, so it carries the rule or the rule is documentation only.
    """
    measured = set()
    for path in sorted(EXAMPLES.glob("*.yml")):
        try:
            manifests = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError:
            continue
        for manifest in manifests:
            if not isinstance(manifest, dict):
                continue
            if _clone_steps(manifest):
                measured.add(path.name)

    assert measured == CLONING_EXAMPLE_PIPELINES, (
        f"the shipped examples that clone have moved: {sorted(measured)}. Re-read this rule against the "
        "new list and extend the pin deliberately rather than letting the walk go quiet")

    for name in sorted(CLONING_EXAMPLE_PIPELINES):
        path = EXAMPLES / name
        for manifest in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(manifest, dict) or not _clone_steps(manifest):
                continue
            params = _params(manifest)
            clones = _clone_steps(manifest)
            code = [c for c in clones if not _is_configuration_clone(c, params)]
            config = [c for c in clones if _is_configuration_clone(c, params)]
            assert config, (
                f"examples/{name}: it clones {len(clones)} repo(s) and declares none of them as the "
                "configuration clone, so the Task's configuration root names a directory nothing creates")
            assert code, f"examples/{name}: the code clone is gone, so the configuration clone is alone"
            assert len({_substitute(f"$(workspaces.workspace.path)/{c['project_name']}", params)
                        for c in clones}) == len(clones), \
                f"examples/{name}: two clones land in one directory"


def _is_configuration_clone(clone, params):
    return "configuration" in _substitute(clone["project_name"], params)


def test_the_shipped_tasks_that_read_configuration_by_path_still_name_a_workspace():
    """The Tasks that read configuration by path before this change and were not touched by it."""
    untouched = {
        "execute-mysql-file.yml": "path-to-configuration",
        "create-mysql-database.yml": "path-to-configuration",
    }
    for name, param_name in sorted(untouched.items()):
        path = TASKS / name
        if not path.exists():
            continue
        params = _params(_load(path))
        assert param_name in params, f"{name} lost the param this rule reads"
        default = str(params[param_name])
        assert "$(workspaces.workspace.path)" in default, \
            f"{name}.{param_name} defaults to {default!r}, a literal path to one cluster's filesystem"
