"""Every `kmt` step in the shipped CI templates *states* which cluster it means.

The other guard in this directory reads the first half of the same rule — every `kmt` invocation names
its configuration **root** — off the same tree. This file reads the half that landed after it, and
the two rules are siblings for the same reason: both say that a step running inside a pod has to
name the thing a pod cannot find out, because everything in this product that used to *locate* it
was deleted on purpose.

Why stating the cluster is a rule about the templates and not only about the library. A pipeline pod
has two answers to "which cluster" and v2 has no referee:

* the cloned configuration declares `CLUSTER_KUBE_CONTEXT` in `<environment>/static.json` (layer 3),
  and — since an absolute `CLUSTER_KUBECONFIG_PATH` in a shared tree is a mistake — the file
  that context names is machine-local, so in a pod it is a **dead reference**; and
* the pod's own mounted service account (layer 4) would answer for the cluster it is standing on.

v2's order puts layer 3 ahead of layer 4, and `ClusterTarget.from_kubeconfig` refuses rather than
falling through when the file it resolved is missing. So a pod that says nothing does not get to use
its own credentials: it dies on somebody's `~/.kube/config`. The answer — and explicitly *not*
teaching layer 3 to fall through, because a configuration that cannot find the cluster it declares
does not get to discover a different one — is that **the pipeline states its
cluster with v2's own layer-1 dials**, so the file that gets opened is the one the pipeline declared
and the resolution report names it. The clone's declaration is then compared against that statement
(`test_a_stated_cluster_is_checked_against_the_cluster_the_environment_declares.py`), and a
disagreement is a refusal rather than a choice.

So what is checked here is read off the shipped manifests, the way the root rule is:

1. every `uv run … kmt` step names its cluster with `--kubeconfig`, once per invocation, and the
   value is a param reference the Task declares — a literal path would be one cluster's filesystem
   baked into a Task that is supposed to move between them;
2. the param carries **no default**, because a statement that can be skipped is a statement, and
   "which cluster" is exactly the question a silent default used to answer;
3. the param's own text says what it does and does not decide, so a reader of the Task learns the
   rule instead of learning it from a refusal on a live cluster;
4. every shipped pipeline step that calls one of those Tasks actually hands the statement over; and
5. no `kmt` invocation in the pipeline execs `git-crypt` (kubementat reads configuration, it
   never unlocks it) — which is already true and is pinned here so it stays true.

One walker, two rules: both guards read the same `uv run … kmt` walk out of
`test_every_ci_kmt_invocation_names_its_configuration_root`, because a rule read off a tree is worth
nothing if the tree is walked twice and the two walks disagree about what is in it.
"""

import pathlib
import re

import yaml

from asset_paths import tekton_ci_path
import test_every_ci_kmt_invocation_names_its_configuration_root as root_guard

PIPELINES = tekton_ci_path("pipelines")
EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples" / "tekton_ci" / "pipelines"

PARAM_REF = re.compile(r"\$\(params\.([\w.\-]+)\)")

# The one dial the rule puts on the TaskRun's `kmt` step. `--context` is layer 1 too, but
# a statement that names a *name* is the half the clone's declaration already answers; the file is
# the half a pod cannot know, which is why the templates state that one.
KUBECONFIG = re.compile(r"--kubeconfig(?:=|\s+)([\"']?)((?:\$\([^)]*\)|[^\s\"'])+)\1")

# The statement's param, spelled once because five sentences below read it.
STATEMENT_PARAM = "cluster-kubeconfig"


def _param_entries(manifest):
    """The Task's declared params as ``{name: param}``, defaults and all.

    The root guard's `_param_defaults` is the right shape for resolving a reference, and the wrong
    one for this rule: it folds "no default" and "defaults to the empty string" into the same
    answer, and the difference between those two is exactly what the second test below is about.
    """
    return {param["name"]: param
            for param in (manifest.get("spec") or {}).get("params") or []}


def _kmt_task_names():
    """The Tasks that run `uv run … kmt`, by the name a `taskRef` uses."""
    return {name[:-len(".yml")] if name.endswith(".yml") else name
            for name, _manifest, _step in root_guard._uv_kmt_steps()}


def _kubeconfig_values(step):
    """Every value `--kubeconfig` is handed on this step's `kmt` line(s), comments excluded."""
    return [match.group(2) for match in KUBECONFIG.finditer(root_guard._command_text(step))]


def test_every_kmt_step_in_a_shipped_task_states_the_cluster_it_means():
    """A step that reaches a cluster says which one it means, on the line that reaches it.

    One statement per invocation, the same count rule the root guard applies to `--config-dir`, and
    for the same reason: a flag on a line the command never reaches is decoration. What differs is
    the *question* — `--config-dir` says what the command reads, `--kubeconfig` says where the work
    happens — so a step can pass the first and still install into the wrong cluster.
    """
    for name, manifest, step in root_guard._uv_kmt_steps():
        invocations = len(root_guard.UV_KMT.findall(root_guard._command_text(step)))
        values = _kubeconfig_values(step)
        assert len(values) >= invocations, \
            f"{name}: step {step.get('name')!r} runs `uv run … kmt` {invocations} time(s) and states " \
            f"a cluster {len(values)} time(s). A step that states nothing lets the cloned " \
            "configuration's CLUSTER_KUBE_CONTEXT answer, and inside a pod that is a reference to a " \
            "file nobody in this workspace has"


def test_the_cluster_a_task_states_is_a_named_param_and_not_a_path_the_step_guessed():
    """The statement comes from the Task's params, and the Task refuses to answer it alone.

    Two halves. The value has to be built out of `$(params.…)`: a literal would pin one cluster's
    filesystem into a shipped Task, which is the failure the root guard checks for `workingDir`.
    And the param must declare **no default**: a Task that could state a cluster from its own
    defaults is a Task that answers "which cluster" for an operator who said nothing, which is the
    silent default that was deleted everywhere else it could be found.
    """
    defaults = {name: _param_entries(manifest)
                for name, manifest, _step in root_guard._uv_kmt_steps()}
    assert defaults, "no shipped Task runs `uv run … kmt`, so this rule is reading an empty tree"

    for name, manifest, step in root_guard._uv_kmt_steps():
        for value in _kubeconfig_values(step):
            reference = PARAM_REF.search(value)
            assert reference, \
                f"{name}: --kubeconfig is handed {value!r}, a literal path. A Task cannot know where " \
                "a pipeline mounted a kubeconfig, so pinning one here pins the Task to a cluster"
            declared = reference.group(1)
            entries = defaults[name]
            assert declared in entries, \
                f"{name}: --kubeconfig reads params.{declared}, which the Task does not declare"
            assert "default" not in entries[declared], \
                f"{name}: params.{declared} defaults to {entries[declared].get('default')!r}, which " \
                "states a cluster for a run that named none. The statement is the pipeline's to make"


def test_the_statement_a_task_takes_says_what_it_does_not_decide():
    """The param's own text carries the rule, so a refusal on a live cluster is not how it is learned.

    A description that only said "path to kubeconfig" would leave the operator to discover the refusal
    the hard way, on a run that already deployed something. The sentences this checks are the ones that
    name the two claims and the fact that kubementat does not settle the disagreement.
    """
    for name, manifest, _step in root_guard._uv_kmt_steps():
        params = {param["name"]: param for param in manifest["spec"].get("params") or []}
        assert STATEMENT_PARAM in params, f"{name}: the Task that runs kmt should carry the statement"
        text = params[STATEMENT_PARAM].get("description", "")
        assert "10.4" in text, f"{name}: {STATEMENT_PARAM} does not say which rule it is stating"
        for key in ("CLUSTER_KUBECONFIG_PATH", "CLUSTER_KUBE_CONTEXT"):
            assert key in text, \
                f"{name}: {STATEMENT_PARAM} does not name {key}, so the reader cannot tell which side " \
                "of the comparison this dial is the other half of"


def test_every_shipped_pipeline_step_that_calls_a_kmt_task_hands_the_statement_over():
    """A required param that no caller passes is a TaskRun that never starts, so this is measured.

    The Task cannot say what a TaskRun will be told, and `examples/` is where the only shipped caller
    lives. Read across both trees: any step whose `taskRef` is one of the Tasks this file found, and
    which does not pass the statement, is a run that fails Tekton's own validation instead of
    kubementat's — a legible failure, but one this tree would be shipping knowingly.
    """
    callers = []
    for root in (PIPELINES, EXAMPLES):
        for path in sorted(root.rglob("*.yml")):
            for manifest in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if not isinstance(manifest, dict):
                    continue
                for step in (manifest.get("spec") or {}).get("tasks") or []:
                    reference = (step.get("taskRef") or {}).get("name")
                    if reference in _kmt_task_names():
                        callers.append((path, step))
    assert callers, \
        "no shipped pipeline calls a Task that runs `uv run … kmt`, so this rule has nothing to read"

    for path, step in callers:
        passed = {entry["name"] for entry in step.get("params") or []}
        assert STATEMENT_PARAM in passed, \
            f"{path}: step {step.get('name')!r} calls {step['taskRef']['name']} without " \
            f"{STATEMENT_PARAM}. The Task declares no default, so this is a PipelineRun that fails " \
            "validation rather than a run that states nothing"


def test_no_kmt_invocation_in_the_shipped_tasks_execs_git_crypt():
    """A regression guard rather than a fix: kubementat reads, it never unlocks.

    The unlock is a step of its own (`git-crypt-unlock.yml`) that pipes a symmetric key out of a
    Secret, and every `kmt` step after it reads a directory. If a `git-crypt` ever reappears on a
    `kmt` line, the product is back inside the read path and the CI lane has a second answer to
    which key opened the tree.
    """
    for name, _manifest, step in root_guard._uv_kmt_steps():
        # The command text, comments stripped: the rule is about what the step execs, and a comment
        # that mentions git-crypt is a description and not a call.
        text = root_guard._command_text(step)
        assert "git-crypt" not in text, \
            f"{name}: step {step.get('name')!r} execs git-crypt, which belongs to a step of " \
            "its own (git-crypt-unlock.yml), reading a symmetric key out of a Secret"
