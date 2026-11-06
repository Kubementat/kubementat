"""Tests for the command-execution rule: argv everywhere, one module doing the executing.

`kubementat.shell.run` is the only way kubementat starts a program
(`test_shell_run.py` is its contract). This file proves two things about everywhere else.

Two kinds of test live here on purpose:

**A rule**, asserted against the syntax tree of everything in `src/` — no `shell=True`,
no `os.chdir`, no direct `subprocess` outside `kubementat/shell.py`. AST rather than grep
because the prose in this repo's own comments mentions `shell=True` at least as often as
the code did.

**The call sites**, run against fake `helmfile`/`kubectl`/`.sh` binaries that record the
argv they were given, the directory they were run from, and what arrived on stdin. These
are what prove the conversions did not change a command line, which is the one thing a
mechanical argv rewrite can get wrong.
"""

import ast
import json
import os
import shlex
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from kubementat import constants, errors, shell
from kubementat.automation import Automation
from kubementat.config import component_config_key
from cluster_fixture import TARGET_DEV, configuration_only_context
from asset_paths import values_path
from kubementat.helm_utils import HelmUtils
from kubementat.errors import PodNotFoundError
from kubementat.kubernetes_utils import KubernetesUtils, kubectl_target_args

REPO_ROOT = Path(__file__).resolve().parent.parent

# The marker line that makes an argv mistake visible: one `arg=[...]` per argv entry, so a
# shell that split `a;b` in two would change the count, not just the text.
RECORDER = """#!/bin/sh
{{
  echo "cwd=$PWD"
  echo "argc=$#"
{stdin_line}
  for arg in "$@"; do echo "arg=[$arg]"; done
}} >> "{log}"
{stdout}
exit {code}
"""

# What the fake does instead of answering: print the given text, or nothing at all.
SILENCE = ":"


def say(text):
    """A command that prints *text* verbatim — quoting a JSON body is how it stays inert."""
    return "printf '%s\\n' " + shlex.quote(text)


# `cat` on an inherited terminal would block the test, so stdin is only slurped when the
# test explicitly says the command is supposed to be fed something.
READS_STDIN = '  echo "stdin=$(cat)"'
SKIPS_STDIN = "  : # stdin not read"

KUBECTL_ITEMS = '{"items": [{"metadata": {"name": "pod-from-selector-1"}}]}'


def write_binary(bin_dir, name, log, stdout="", exit_code=0, reads_stdin=False):
    """An executable that records how it was invoked instead of doing its job."""
    log.touch()
    script = bin_dir / name
    script.write_text(RECORDER.format(
        log=log, stdout=say(stdout) if stdout else SILENCE, code=exit_code,
        stdin_line=READS_STDIN if reads_stdin else SKIPS_STDIN))
    script.chmod(0o755)
    return log


@pytest.fixture
def shims(tmp_path, monkeypatch):
    """Fake binaries on PATH that append their invocation to `logs[name]`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    logs = {}

    def add(name, stdout="", exit_code=0, reads_stdin=False):
        logs[name] = write_binary(bin_dir, name, tmp_path / f"{name}.log", stdout, exit_code,
                                  reads_stdin=reads_stdin)
        return logs[name]

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    # The log path is baked into each shim, so nothing depends on the child's environment.
    return add, logs


def calls(log):
    """Everything the fake binary recorded, one call at a time."""
    sections, current = [], {}
    for line in log.read_text().splitlines():
        if line.startswith("cwd="):
            current = {"cwd": line[4:], "argc": 0, "stdin": "", "args": []}
            sections.append(current)
        elif line.startswith("argc=") and current:
            current["argc"] = int(line[5:])
        elif line.startswith("stdin=") and current:
            current["stdin"] = line[6:]
        elif line.startswith("arg=[") and line.endswith("]") and current:
            current["args"].append(line[5:-1])
    return sections


# ---------------------------------------------------------------------------
# 1. the rule
# ---------------------------------------------------------------------------
def python_files():
    return sorted((REPO_ROOT / "src").rglob("*.py"))


def every_call_node(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def test_no_command_is_executed_through_a_shell():
    offenders = []
    for path in python_files():
        for node in every_call_node(ast.parse(path.read_text(), filename=str(path))):
            if any(keyword.arg == "shell" and getattr(keyword.value, "value", None) is True
                   for keyword in node.keywords):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == [], "argv is the only form of command line kubementat has left"


def test_no_module_changes_the_process_working_directory():
    offenders = []
    for path in python_files():
        for node in every_call_node(ast.parse(path.read_text(), filename=str(path))):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "chdir":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == [], "working directory is an argument (shell.run's cwd=), not chdir"


def test_only_one_module_executes_commands():
    """The rule that makes the first two unnecessary: one place, one policy.

    If a second module needs to run a program, the change belongs in `shell.py`, not in a
    fresh `subprocess.run` that decides its own quoting and error handling.
    """
    offenders = []
    for path in python_files():
        if path.name == "shell.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            imported = (isinstance(node, ast.Import) and any(alias.name == "subprocess" for alias in node.names)) \
                or (isinstance(node, ast.ImportFrom) and (node.module or "").endswith("subprocess")) \
                or (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "subprocess")
            if imported:
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break

    assert offenders == []


def test_no_command_module_carries_its_own_way_to_start_a_script():
    """Every private route from a command to a script is gone, including the shared one.

    `kubementat.py` and `tekton.py` each had their own `run_script` wrapper; both commands are
    Python now, so neither has a script to start. The shared helper they used to import,
    `run_kubementat_script`, went with its last caller — `kmt run-ci-container` assembles its pod
    spec from `platform_config` today, and the command that took the path from the operator is
    deleted. A leftover call would be an `ImportError` at startup or
    a 127 on the cluster, so it is asserted as absence rather than remembered.
    """
    for module in ("kubementat", "tekton"):
        tree = ast.parse((REPO_ROOT / "src" / "cli" / "commands" / f"{module}.py").read_text())
        defined = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert "run_script" not in defined, f"{module}.py still carries its own copy"

    # The `utilities.py` exception below this loop used to read "there `run_script` is the
    # `kmt run-script` *command*, not a wrapper", and it earned its place: that file was the only
    # one under `src/cli/commands/` that called `shell.run` itself, and it did so to start the
    # script an operator had handed it. The command is deleted, so the exception is narrowed to
    # what is still true of it — it must not reach for the shared helper, which is asserted here —
    # and the half that demanded `shell.run` in it is gone with the function that called it. No
    # command module gets to start a script by path any more, which is asserted as a rule in
    # `tests/test_no_cli_command_starts_a_script_by_path.py` and not remembered per file here.
    utilities = (REPO_ROOT / "src/cli/commands/utilities.py").read_text()
    assert "run_kubementat_script" not in utilities

    # The name has no definition to reach, so the only thing it can be in `src/` is a mistake.
    callers = []
    for path in python_files():
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run_kubementat_script":
                callers.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert callers == []


# Where the CLI takes a script, it takes it as a string — `run_kubementat_script(...,
# 'run_ci_container.sh', ...)` is the last of them. The type checker cannot see a string, so the
# only thing a deleted script can be checked against is the ledger, which is the directory tree.
# That is what the next test does.
SCRIPT_ROOTS = ("scripts", "tests", "examples", "docker_files", "tekton_ci")


def checkout_scripts():
    """Every `.sh` the checkout ships, keyed by file name."""
    found = {}
    for path in REPO_ROOT.glob("*.sh"):
        found.setdefault(path.name, []).append(path)
    for root in SCRIPT_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.sh"):
            if "__pycache__" not in path.parts:
                found.setdefault(path.name, []).append(path)
    return found


def script_names_the_source_uses():
    """`(file, name)` for every string in `src/` that ends in `.sh`.

    AST rather than grep because the same string appears twice on one line and once inside a
    path built out of two arguments. A glob pattern is skipped: `kmt run-script` lists scripts by
    scanning for `*.sh`, and a listing that finds nothing is a listing with nothing to claim.
    """
    used = set()
    for path in python_files():
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                if text.endswith(".sh") and "*" not in text:
                    used.add((path, text))
    return used


def test_every_script_the_source_names_still_exists_in_the_checkout():
    """A script deleted from the ledger without the string that names it going with it.

    This is the failure mode the deletion ledger creates: `kmt` would still start, still print
    its banner, and then fail on the operator's cluster. So every script name in `src/` is
    resolved against the checkout, and anything that no longer has a file behind it is a failure
    here rather than a bug report.
    """
    available = checkout_scripts()

    unresolved = []
    for path, name in sorted(script_names_the_source_uses()):
        if "/" in name:
            if not (REPO_ROOT / name).exists():
                unresolved.append(f"{path.relative_to(REPO_ROOT)} names {name}, which is not there")
        elif name not in available:
            unresolved.append(f"{path.relative_to(REPO_ROOT)} names {name}, which is nowhere in the tree")

    assert unresolved == []


# `kmt run-script` used to be the operator-facing half of that mistake, and it was the only
# command that could be handed a file this checkout had already deleted. It is deleted, so the
# check that survived is the one above: nothing in `src/` may name a script that is not there,
# and nothing that is left takes a path from a human and runs it.

# ---------------------------------------------------------------------------
# 2. helmfile apply (helm_utils.py)
# ---------------------------------------------------------------------------
def apply_helmfile(tmp_path, monkeypatch, shims, **kwargs):
    add, logs = shims
    add("helmfile")
    # The manifest ships now, so the apply asks the cluster which releases it holds before it
    # touches anything. A cluster with no releases is the same state this helper was in before the
    # manifest moved into the package, so the fake answers "none" rather than being a new claim.
    add("helm", stdout="[]")
    main_dir = tmp_path / "checkout"
    platform_config = main_dir / "platform_config"
    # Nothing has to be created for helmfile's `cwd`: the packaged values root is the working
    # directory shell.run hands the child, and that directory shipped with it.

    class FakeConfig:
        """What `HelmUtils` reads off `Config`, without a platform_config tree."""

        def __init__(self, environment, team, preload_configs=False):
            self.kubementat_main_dir = str(main_dir)
            self.platform_config_dir = str(platform_config)
            # The fake stands in for `Config`, and `Config` now carries the packaged helmfile root
            # — the one attribute `helmfile_paths` reads, so leaving it out is a hole in the
            # fake and not a fact about the code under test.
            self.helmfile_asset_dir = str(values_path())

        def get(self, key):
            return {"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "secret"}

        def component_namespace(self, component):
            return constants.DEFAULT_NAMESPACES[component]

        # The two per-release knobs helmfile is handed before argv is assembled. This fake declares
        # none, which is the answer a tree that names nothing gets from the real `Config` too, so the
        # export writes nothing and these tests stay about the one-token label filter they were
        # written for. Leaving the accessors out would be a hole in the fake and not a fact.
        def component_release_enabled(self, component):
            return None

        def component_release_version(self, component):
            return None

        # ...and the routing class helmfile's traefik release names. This scratch tree declares
        # nothing, which is what the real `Config` answers for a tree that names nothing -- L0, out of
        # `constants.py` -- so the export writes that and these tests stay about the argv they were
        # written for. Leaving the accessor out is a hole in the fake and not a fact about the apply.
        def component_ingress_class(self, component):
            return constants.static_answer(component_config_key(component, "INGRESS_CLASS"))

    # The configuration goes into the `Context` the class is built with, which is the
    # documented seam. Patching a `Config` name inside `helm_utils` only worked while the class built
    # its own, and it is a fact about a module rather than about the object under test.
    #
    # The namespaces helmfile is handed go into the process environment, which is where a child
    # process looks for them. `monkeypatch` restores it, so this helper cannot decide what a
    # later test thinks the environment exported.
    for component in constants.DEFAULT_NAMESPACES:
        monkeypatch.delenv(component_config_key(component, "DEPLOYMENT_NAMESPACE"), raising=False)

    HelmUtils(configuration_only_context("dev", "smoke",
                                         FakeConfig("dev", "smoke"))).helmfile_apply(
        environment_name="dev",
        helmfile_label_filter="app=nginx, group=core", **kwargs)
    return calls(logs["helmfile"])[-1]


def test_helmfile_label_filter_survives_as_one_token(tmp_path, monkeypatch, shims):
    """The filter is the argument that shell=True used to chop up.

    `"app=nginx, group=core"` is one label selector. Interpolated into a shell string it
    becomes two arguments and helmfile evaluates a selector nobody asked for.
    """
    call = apply_helmfile(tmp_path, monkeypatch, shims)

    asked = list(call["args"])
    manifest = asked[asked.index("-f") + 1]
    # The cluster's own flags sit between the verb and the rest, and they are not what this
    # test is about: one selector token is. So the flags and their values are left out, and what is
    # compared is the argument the shell used to chop up.
    for flag in ("--kubeconfig", "--kube-context", "--context"):
        while flag in asked:
            index = asked.index(flag)
            del asked[index:index + 2]

    assert asked == ["apply", "--color", "-f", manifest, "-l", "app=nginx, group=core"]


def test_helmfile_runs_in_the_chart_directory_without_changing_ours(tmp_path, monkeypatch, shims):
    """`cwd=` replaced the old implicit assumption that the CLI is sitting in the right dir."""
    before = os.getcwd()
    call = apply_helmfile(tmp_path, monkeypatch, shims)

    assert os.path.realpath(call["cwd"]) == os.path.realpath(values_path()), \
        "helmfile must run where the packaged manifest lives, so a relative values reference reaches"

    assert os.getcwd() == before


def test_helmfile_interactive_flag_is_still_only_there_when_asked_for(tmp_path, monkeypatch, shims):
    quiet = apply_helmfile(tmp_path, monkeypatch, shims, interactive=False)
    interactive = apply_helmfile(tmp_path, monkeypatch, shims, interactive=True)

    assert "--interactive" not in quiet["args"]
    assert "--interactive" in interactive["args"]


# ---------------------------------------------------------------------------
# 3. kubectl port-forward and pod lookup (kubernetes_utils.py)
# ---------------------------------------------------------------------------
@pytest.fixture
def kubernetes_utils(monkeypatch):
    """A `KubernetesUtils` with no cluster, and one that costs nothing to build.

    There is no kube-config load to neutralise any more: the constructor binds `TARGET_DEV`
    and stops. The two ways this fixture's tests reach a cluster are a shimmed `kubectl` argv and a
    monkeypatched pod lookup, so no client is ever built and none is needed.
    """
    return KubernetesUtils(TARGET_DEV)


def test_open_tunnel_forwards_each_port_as_its_own_argument(kubernetes_utils, monkeypatch, tmp_path, shims, capfd):
    """`{local_port}:{remote_port}` was pasted into a shell string; now it is one argv entry."""
    add, logs = shims
    add("kubectl", stdout="Forwarding from address & port\n")
    utils = kubernetes_utils

    monkeypatch.setattr(utils, "get_pod_name_for_deployment", lambda *args: "grafana-7d9-abc")
    assert utils.open_tunnel("grafana", "grafana-7d9-abc", "3001", "3000") == 0

    call = calls(logs["kubectl"])[-1]
    # The target is in the argv before the subcommand, so the port pair is not the only
    # thing asserted here: an unaddressed port-forward is the same class of bug as a captured one.
    assert call["args"] == [*kubectl_target_args(TARGET_DEV), "--namespace", "grafana",
                            "port-forward", "--address", "0.0.0.0",
                            "pod/grafana-7d9-abc", "3001:3000"]
    # The tunnel's whole failure mode was silence: the output has to reach the terminal.
    assert "Forwarding from address & port" in capfd.readouterr().out


def test_open_tunnel_reports_a_failure_as_exit_1_not_as_a_traceback(kubernetes_utils, monkeypatch, tmp_path, shims):
    add, logs = shims
    add("kubectl", exit_code=1)

    assert kubernetes_utils.open_tunnel("grafana", "grafana-7d9-abc", "3001", "3000") == 1


def test_pod_lookup_hands_the_selector_over_as_one_argument(recorded_api):
    """The selector is one argument, and now it is one *keyword*.

    `-l 'app=a,b'` needed quoting once a shell put it together; a `label_selector` keyword needs
    nothing, and the comma that used to be the risk is now one character inside one string. The
    conversion moved the assertion from argv to the recorded call, which says the same thing with
    less surface: there is no command line left for a comma to escape from.
    """
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(items=[
        SimpleNamespace(metadata=SimpleNamespace(name="pod-from-selector-1"))]))
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)

    assert utils.get_pod_name_for_deployment(
        "grafana", "app.kubernetes.io/name=grafana,instance=grafana") == "pod-from-selector-1"

    call = recorded_api.called_as("list_namespaced_pod")
    assert call["namespace"] == "grafana"
    assert call["label_selector"] == "app.kubernetes.io/name=grafana,instance=grafana"


def test_pod_lookup_reports_an_uninstalled_component_readably(recorded_api):
    """No items is a state the operator can act on, and it is still said that way.

    It used to be a non-zero exit carrying empty stdout; the client answers with an empty list,
    which is a different mechanism and must not be a different message.
    """
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(items=[]))
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)

    with pytest.raises(PodNotFoundError) as excinfo:
        utils.get_pod_name_for_deployment("grafana", "app=absent")

    assert "Is the component installed?" in str(excinfo.value)


def test_a_hostile_namespace_cannot_escape_into_the_command_line(recorded_api, tmp_path):
    """The injection case, asserted where the injection used to live.

    kubectl's argv was the risk, and the namespace reached it verbatim; a client call has no shell
    to escape into. What still has to be asserted is that nothing rewrites the value on the way:
    the API receives exactly the string the caller had.
    """
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(items=[
        SimpleNamespace(metadata=SimpleNamespace(name="pod-from-selector-1"))]))
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)

    utils.get_pod_name_for_deployment("ops; touch /tmp/command-injection-proof", "app=grafana")

    assert recorded_api.called_as("list_namespaced_pod")["namespace"] == \
        "ops; touch /tmp/command-injection-proof"
    assert not Path("/tmp/command-injection-proof").exists()
    assert not Path("/tmp/command-injection-proof").exists()


# ---------------------------------------------------------------------------
# 5. stdin-carrying permission probes (automation.check_cluster_permissions)
# ---------------------------------------------------------------------------
def test_cluster_permission_probes_ask_the_server_and_answer_themselves(recorded_api):
    """The six probes were converted to client reads, and they answer with less machinery, not more.

    `kubectl auth can-i create namespace` needed a stdin to answer its own yes/no prompt, which is
    why `input="yes"` was there. A `SelfSubjectAccessReview` is a request with a boolean in the
    reply and nothing else to answer, so no child process is started at all -- which is what this
    asserts: a run that still went out through a shell would have left calls in the log.

    The resources are asserted as the API spells them. The singular forms kubectl accepted were a
    convenience of its discovery mapping, and a review that carried them would answer "no" on
    every cluster for a permission the service account does have.
    """
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)
    automation = Automation.__new__(Automation)
    automation.kubernetes_utils = utils

    automation.check_cluster_permissions()

    reviews = [kwargs for method, kwargs in recorded_api.calls
               if method == "create_self_subject_access_review"]
    assert len(reviews) == len(automation.CLUSTER_PERMISSION_CHECK_RESOURCES)
    assert [str(review["body"].spec.resource_attributes.resource) for review in reviews] == \
        list(automation.CLUSTER_PERMISSION_CHECK_RESOURCES)
    assert all(review["body"].spec.resource_attributes.verb == "create" for review in reviews)


def test_a_denied_verb_stops_the_run_and_names_the_verbs_it_denied(recorded_api):
    """A "no" that is printed and thrown away is the same as not checking, which is what bash did."""
    def answering(**kwargs):
        resource = kwargs["body"].spec.resource_attributes.resource
        if resource == "clusterroles":
            return SimpleNamespace(status=SimpleNamespace(allowed=False))
        return SimpleNamespace(status=SimpleNamespace(allowed=True))

    recorded_api.answer("create_self_subject_access_review", answering)
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)
    automation = Automation.__new__(Automation)
    automation.kubernetes_utils = utils

    with pytest.raises(errors.CommandExecutionError) as error:
        automation.check_cluster_permissions()
    assert "clusterroles" in str(error.value)


# ---------------------------------------------------------------------------
# 6. `kmt tekton setup-triggers` — the command that used a shell until here
# ---------------------------------------------------------------------------
def test_the_tekton_commands_execute_no_script():
    """Every `tekton.py` command is Python now, so the file has no script to name.

    This is the ledger's other direction: `test_every_script_the_source_names_still_exists_in_the_checkout`
    asks whether the strings still have files behind them, and this asks whether a command that was
    converted kept its old string anyway. `setup_triggers.sh` was deleted by this migration, and a
    leftover `run_kubementat_script(...)` here would be a 127 the next test would have to explain.
    """
    tree = ast.parse((REPO_ROOT / "src/cli/commands/tekton.py").read_text())
    named = [node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and node.value.endswith(".sh")]
    called = [node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "run_kubementat_script"]

    assert named == [], named
    assert called == [], called
