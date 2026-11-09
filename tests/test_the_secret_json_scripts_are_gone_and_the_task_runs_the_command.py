"""The two scripts under `scripts/secret_management/` that a json file needed stay deleted.

`kmt kubernetes create-secret-from-json` replaced the behaviour of
`create_secret_from_json.sh`, whose only caller in the whole checkout was the Tekton Task
`tekton_ci/tasks/create-secret-from-json.yml`. That is the reason the pair is deletable now and was
not before: the script was the only thing the library `base64_library.sh` had left — the other
script that sourced it, `generate_ssh_key.sh`, went earlier — and the library's one function,
`base64_encode`, was never called by either of them, so it is verified rather than assumed that
deleting the script orphaned the library and not the reverse:

    git show HEAD^:scripts/secret_management/create_secret_from_json.sh

sources the library and calls no function from it, which is the whole of what "the library stays
because two scripts source it" was resting on. The Task is repointed at the command in the same
commit, so nothing in the tree can still run either file.

The claim this started on was that the library "leaves
when they do". It did, so it did. What is asserted below is the two halves of
that claim that could rot: the files are not in the tree, no program carries a path that could run
them, and the Task — which is customer contract, so its params are checked as a set and not
reformatted on trust — now runs a command instead of a filename.
"""

from pathlib import Path
import re

from asset_paths import tekton_ci_path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCRIPT = "scripts/secret_management/create_secret_from_json.sh"
LIBRARY = "scripts/secret_management/base64_library.sh"
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

# Anything that is allowed to still name what was deleted, because it is prose about the deletion
# rather than a way to run it: the plans that scheduled it, the ledger that measured it, the ticket
# that ordered it, and this file's own assertions.
PROSE_ROOTS = {"docs", ".agent-work"}
PROSE_FILES = {"CHANGELOG.md", "TODOs.md"}


def _files_under(root):
    for path in (REPO_ROOT / root).rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _lines_naming(text, needle, relative):
    return [f"{relative}:{number}" for number, line in enumerate(text.splitlines(), start=1)
            if needle in line]


def test_neither_script_is_in_the_tree():
    """The deletion itself, asserted where a re-added file would land."""
    for path in (SCRIPT, LIBRARY):
        assert not (REPO_ROOT / path).exists(), \
            f"{path} is back; kmt kubernetes create-secret-from-json is the implementation, not a second one"


def test_the_directory_that_held_them_holds_nothing():
    """A directory emptied by a deletion and left behind is a directory that is still shipped.

    Git tracks files and not directories, so `scripts/secret_management/` would survive this
    ticket's deletion as an empty thing on someone's disk and a `scripts/` section in the overview
    that no longer matches. Both files are gone, so the directory is gone with them.
    """
    left = sorted(str(path.relative_to(REPO_ROOT / "scripts"))
                  for path in (REPO_ROOT / "scripts").rglob("*")
                  if "secret_management" in str(path))
    assert left == [], f"the empty directory came back: {left}"


def test_the_library_is_named_by_nothing_in_the_code_roots():
    """The claim this deletion was told to verify, checked rather than trusted.

    `base64_library.sh` defined one function and was `source`d, never imported, so a consumer would
    have to name it. Nothing in a root a program is read from does.
    """
    offenders = []
    for root in CALLER_ROOTS:
        for path, text in _files_under(root):
            hits = _lines_naming(text, "base64_library", str(path.relative_to(REPO_ROOT)))
            offenders.extend(hits)

    assert offenders == [], \
        f"something still names the library, so it is not free to go: {offenders}"


def test_no_program_carries_a_path_to_the_deleted_script():
    """The full path, not the filename: this is the string a caller needs in order to run it.

    A bare `create_secret_from_json.sh` inside a comment that says what a command replaced is
    prose, and the rule the pvc-usage guard states applies here unchanged.
    """
    for needle in (SCRIPT, LIBRARY):
        named_by = []
        for root in CALLER_ROOTS:
            for path, text in _files_under(root):
                if needle in text:
                    named_by.append(str(path.relative_to(REPO_ROOT)))

        assert named_by == [], \
            f"something carries the path of {needle} and could still run it: {named_by}"


def test_neither_name_appears_where_a_caller_would_find_it():
    """`scripts/` and `tekton_ci/` are the two roots a caller could be standing in.

    Measured rather than trusted: what the ticket's own gate checks is that neither name appears in
    either root, because those are the two places a string could be turned into a running script. A
    comment elsewhere that says either name is prose about the deletion, and prose is what the
    guard above already places.
    """
    for root in ("scripts", "tekton_ci"):
        named_by = []
        for path, text in _files_under(root):
            hits = (_lines_naming(text, "create_secret_from_json", str(path.relative_to(REPO_ROOT)))
                    + _lines_naming(text, "base64_library", str(path.relative_to(REPO_ROOT))))
            named_by.extend(hits)

        assert named_by == [], f"{root}/ still names a script it could run: {named_by}"


def test_the_task_names_no_script_at_all():
    """The Task's whole job is to run a command now, so there is no script path left to find.

    This is the strictest form of the check: not "the deleted path is gone" but "nothing under
    `scripts/` is mentioned here", which is what a reader of the Task should be able to conclude
    without knowing which filename to look for.
    """
    text = TASK.read_text(encoding="utf-8")

    assert "scripts/" not in text, "the Task still points into scripts/; it runs kmt"
    assert "PATH_TO_SCRIPT" not in text, "the env var that carried the script's path is back"
    assert "| jq" not in text, "the command reads the json itself; a step that pipes the file to a " \
                               "jq is a second implementation of the thing the command refuses"


def test_the_task_still_declares_the_params_the_customer_pipeline_passes():
    """The params block is customer contract, so it is checked as a set and not as a feeling.

    One live pipeline pair calls this Task by name and passes these. A param renamed or folded into a
    workspace would fail in someone else's pipeline run and not in this repository, which is the only
    reason the set is asserted here instead of trusted from the diff. The additions this file's tickets
    made are additions and not renames: nothing the pair passed before changed its name.
    """
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
    """The step's own line, read out of the manifest rather than remembered.

    Four substitutions for the four arguments the command takes, in the order the command takes
    them, and no `cat` of the json file in the step: the file's contents used to be passed as one
    argument, which put the whole document in the step's argv and made `jq` decide what a nested
    value looked like.

    The first substitution is the environment and that ordering is the point — a Task that passes
    the namespace first is a Task that cannot say which cluster the Secret is written into. The
    check below is positional and not a membership test for exactly that reason.
    """
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
        "the Secret — see tests/test_every_ci_kmt_invocation_names_its_configuration_root.py")
    for param in ("environment", "target-namespace", "secret-name", "json-file-location"):
        assert f'$(params.{param})' in text, f"the step no longer passes $(params.{param})"
    assert "cat $JSON_FILE_LOCATION" not in text


def test_the_command_the_task_calls_is_the_one_that_takes_those_four_arguments():
    """The other side of the repoint: the name in the Task resolves to the command it means.

    The Task is a string, and a command is Python, so the only check that means anything is that the
    command the registration answers to exists and is addressed by exactly four positionals —
    environment first, then the three the script took.

    The pair is checked together rather than in two tickets: a Task that gains a param and a command
    that gains a positional are one change, because the other order is the bug.
    """
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


def test_nothing_outside_the_prose_still_names_the_deleted_pair():
    """Where the two names are allowed to survive, and no further.

    The plans and the ledger record the deletion, the ticket ordered it, and this file asserts it;
    that is prose about the deletion, which is what a guard is allowed to be. Anywhere else —
    `templates/`, `examples/`, `docker_files/`, a Task or a pipeline in `examples/tekton_ci/` — a
    hit is a caller that was not found, and this is the check that says so without waiting for a
    pipeline pod to find it out. The full path is the needle and not the filename, for the reason
    the pvc-usage guard gives: `create_secret_from_json` is also the Python callback's name, and an
    identifier is not a caller.
    """
    offenders = []
    for path, text in _files_under("."):
        relative = path.relative_to(REPO_ROOT)
        first = relative.parts[0]
        # `.git`, `.venv`, `.codegraph`, `.pi`, `.pytest_cache` and the rest: copies of the
        # checkout or the machine's own droppings, never shipped programs.
        if first.startswith(".") or first in PROSE_ROOTS:
            continue
        if str(relative) in PROSE_FILES or str(relative).startswith("tests/"):
            continue
        if SCRIPT in text or LIBRARY in text:
            offenders.append(str(relative))

    assert offenders == [], f"something outside the prose still names the deleted pair: {offenders}"
