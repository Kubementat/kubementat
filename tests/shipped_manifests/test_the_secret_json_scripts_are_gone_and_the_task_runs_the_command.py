"""The two scripts under `scripts/secret_management/` that a json file needed stay deleted."""

import re

from asset_paths import tekton_ci_path

SCRIPT = "scripts/secret_management/create_secret_from_json.sh"
# The Task itself, read from where the installed `kubementat` carries it and not out of this
# checkout: it ships inside the package now, so `REPO_ROOT` is the one place this file may not
# look.
TASK = tekton_ci_path("tasks", "create-secret-from-json.yml")

# The params one live customer pipeline pair passes to this Task by name, so the set is contract
# and not decoration. `json-file-location` keeps its meaning: a path to a flat json file.
# `environment` is in the set because the command it drives takes the environment as its first
# positional and a Task that cannot pass it cannot say which cluster it writes to.
# `configuration-root` is here because the configuration became its own clone: the root a
# command reads may no longer be inferred from `automation-git-project-name`, which names the *code*
# checkout and has been asked to stop meaning configuration. It is an addition and not a rename, and the
# test below asserts it as one. `cluster-kubeconfig` is the second addition: the
# environment's `CLUSTER_KUBE_CONTEXT` names *which* cluster this is, and the TaskRun has to state
# *where* the work happens, because inside a pod the clone's own `CLUSTER_KUBECONFIG_PATH` is a
# reference to a file that is not there. It is a param and not a step env entry because a statement is
# the operator speaking, and a default is the product answering for them.
TASK_PARAMS = {"image", "environment", "target-namespace", "secret-name", "json-file-location",
               "automation-git-project-name", "configuration-root", "cluster-kubeconfig"}

# Roots a caller could live in. `tests/` is deliberately not one of them for the path check: this
# file and the command's own tests are where the record of the deletion lives, exactly as the
# pvc-usage and create-ingress guards keep `tests/` out of the same rule.
CALLER_ROOTS = ("src", "scripts", "tekton_ci", "examples", "tools")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.


def test_the_task_still_declares_the_params_the_customer_pipeline_passes():
    """The params block is customer contract, so it is checked as a set and not as a feeling."""
    import yaml

    document = yaml.safe_load(TASK.read_text(encoding="utf-8"))

    assert document["apiVersion"] == "tekton.dev/v1", "the Task must stay applyable by the cluster"
    declared = {param["name"] for param in document["spec"]["params"]}
    assert declared == TASK_PARAMS, (
        f"the Task's params are customer contract and changed: unexpected={sorted(declared - TASK_PARAMS)}, "
        f"missing={sorted(TASK_PARAMS - declared)}")
    assert document["spec"]["params"][0]["default"] == "julianweberdev/ubuntu-ci-minimal:latest", \
        "the image param keeps its default, which is what keeps the step runnable at all"


def test_the_task_runs_the_command_on_four_positionals():
    """The step's own line, read out of the manifest rather than remembered."""
    text = TASK.read_text(encoding="utf-8")

    assert "kmt kubernetes create-secret-from-json" in text
    # rsplit, not split: the step's own comment names the command in prose before the step runs it.
    argv = text.rsplit("kmt kubernetes create-secret-from-json", 1)[1]
    # The list below is the command's arguments and nothing else, so the flag that follows them is
    # cut before the scan. It is a flag and not a fifth positional: `--config-dir` names the root the
    # command reads, which is a different sentence from "where inside the cluster", and
    # a positional test that reads it as an argument would report a Task that gained a flag as one
    # that gained an argument. The assert under this one is what keeps the cut from hiding its absence.
    after = argv.split("--config-dir")[0]
    sent = re.findall(r'\$\(params\.([a-z-]+)\)', after)
    assert sent == ["environment", "target-namespace", "secret-name", "json-file-location"], (
        "the step's arguments are the command's positionals, in that order, and the first of them "
        f"is the environment: {sent}")
    assert "--config-dir" in argv, (
        "the step names no configuration root, so `kmt` resolves none and refuses before it writes "
        "the Secret — see tests/shipped_manifests/test_every_ci_kmt_invocation_names_its_configuration_root.py")
    for param in ("environment", "target-namespace", "secret-name", "json-file-location"):
        assert f'$(params.{param})' in text, f"the step no longer passes $(params.{param})"
    assert "cat $JSON_FILE_LOCATION" not in text


def test_the_command_the_task_calls_is_the_one_that_takes_those_four_arguments():
    """The other side of the repoint: the name in the Task resolves to the command it means."""
    from click.core import Argument, Option

    from cli.commands import kubernetes

    command = kubernetes.create_secret_from_json
    assert command.name == "create-secret-from-json"
    assert [param.name for param in command.params] == [
        "environment", "namespace", "secret_name", "json_file_path",
        "context", "kubeconfig", "config_dir"]
    assert all(isinstance(param, Argument) for param in command.params[:4]), \
        "the four positionals the Task sends must stay positionals"
    assert all(isinstance(param, Option) for param in command.params[4:]), (
        "the two flags that came with the decorator arrive as flags, so they cannot collide with "
        "the positional params the Task sends")
