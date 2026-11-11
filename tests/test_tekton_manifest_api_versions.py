"""The manifest inventory that says the `v1beta1` -> `v1` conversion is *finished*, not applied.

Pipelines 1.0 removed the beta API and the field shapes that came with it: a `PipelineRun` hides
its service account under `taskRunTemplate` and its deadline under `timeouts`, and a Task step
says `computeResources` where it used to say `resources`. The dangerous part is not the renames —
it is that the same word `resources` also names a Kubernetes `PersistentVolumeClaim` block
(`volumeClaimTemplate.spec.resources`) and an `EventListener` block, and a transform that renames
by name alone takes the storage requests with it. A PVC whose `requests` went missing does not fail
to apply; it applies, and the pipeline waits forever on a volume that was never claimed. That is
the failure this file is written to make impossible.

So, over every manifest in `tekton_ci/` and `examples/tekton_ci/` — read today from two places,
because the product's own half of `tekton_ci/` is package data while its pipeline runs are
not: nothing in the `tekton.dev` group is still on the beta API, every `PipelineRun` puts its two
moved fields where v1 reads them,
every `volumeClaimTemplate` still asks for storage, every `EventListener` still has the `resources`
block that is its own, no step container says `resources`, and no Task declares `spec.resources`.

The exceptions are part of the contract too, so they are asserted rather than tolerated: Triggers
stays on `triggers.tekton.dev/v1beta1` (the pinned controller is 0.33, and rewriting that string
would emit a manifest no controller here serves), and no manifest may name a `PipelineResource`,
which v1 does not serve at all. The counts below are the population of the beta tree, measured off
`git show HEAD`; they are here so that a block which was renamed *away* rather than renamed shows up
as a count change and not as a passing assertion.
"""

from pathlib import Path

import yaml

from asset_paths import tekton_ci_path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# The product's manifests are package data and the examples are not, which is why the two
# roots are named differently: `examples/tekton_ci/` is a tree a human reads and `kubectl apply` -f
# paths at, and `tekton_ci/` is what `setup_pipelines` applies. A guard that read the old location
# would sweep three fewer manifest trees than it proves for.
# Three roots, because `tekton_ci/` is not one place any more. The product's Tasks, Pipelines and
# generator templates ship inside the package and are asked of the resolver; the pipeline
# runs under this checkout are output and stay where they were written; `examples/tekton_ci/` is a
# tree a human reads and `kubectl apply -f` paths at, so it never moved. Dropping the second would
# drop four `PipelineRun` manifests out of a proof that is only worth something when it reads every
# manifest in the tree, and the counts below would come out short.
MANIFEST_ROOTS = (tekton_ci_path(),
                  REPOSITORY_ROOT / "tekton_ci/pipeline-runs",
                  REPOSITORY_ROOT / "examples/tekton_ci")

PIPELINES_GROUP = "tekton.dev"
PIPELINES_VERSION = "v1"
TRIGGERS_GROUP = "triggers.tekton.dev"
TRIGGERS_VERSION = "v1beta1"

# What the beta tree held, measured off `git show HEAD` before the conversion. A count that
# moves away from one of these is a block that was renamed out of existence, not a rename.
#
# Two of them moved deliberately on 2026-10-27, when the MySQL/Redis/NGINX install automation
# was deleted: the `nginx-reverse-proxy` example
# pipeline lost a `volumeClaimTemplate`, and its pipeline run left the tree with it. A count that
# still moves away from *these* numbers is the same defect this file was written for; only these
# two lines were re-measured, and `git show HEAD~1:examples/tekton_ci/pipelines/` is the diff.
#
# The third moved for the same reason and by the same kind of evidence: `tekton_ci/tasks/
# run-automation-script-for-environment.yml` is deleted, and it was one Task with one step
# and one `computeResources` block, so the population of the tree this count reads is 18 and not 19.
# Nothing was renamed out of existence — `grep -c computeResources tekton_ci/tasks/*.yml` still finds
# 16 files, all of them the same steps that were counted before.
#
# It is 19 again because a Task was added, and a Task that runs a tool states its limits like every
# other one: `git-crypt-unlock.yml` is one step carrying one block. Measured and not guessed, which is why
# the sentence says so -- this is the one count in the file a *new* file is allowed to move, so a bump here
# has to arrive with the file that caused it and not by somebody rounding to make it green.
PIPELINE_RUNS_EXPECTED = 13
VOLUME_CLAIM_TEMPLATES_EXPECTED = 9
STEP_COMPUTE_RESOURCES_EXPECTED = 19
TRIGGER_TEMPLATE_FILES_EXPECTED = 6

# 54 manifests under the two trees today, 60 before the backing-service deletion above. The
# floor is what says the inventory is still an inventory — see the test that reads it.
MANIFEST_MINIMUM = 50


def manifest_files():
    """Every manifest under the two trees, so a new file is covered the moment it lands."""
    found = []
    for root in MANIFEST_ROOTS:
        assert root.is_dir(), f"{root} is where the manifests live"
        found.extend(path for path in root.rglob("*") if path.suffix in (".yml", ".yaml"))
    return sorted(found)


def documents():
    """`(<file>, <document index>, <document>)` for every mapping document in the tree."""
    for path in manifest_files():
        for index, document in enumerate(yaml.safe_load_all(path.read_text())):
            if isinstance(document, dict):
                yield path, index, document


def nodes(data, path=()):
    """Every mapping in a document, tagged with the path that leads to it.

    Recursing instead of walking only the top of a document is not decoration: a TriggerTemplate
    carries its `PipelineRun` inside `spec.resourcetemplates`, and a field that moved only there
    is exactly the kind of thing a top-level-only check misses.
    """
    if isinstance(data, dict):
        yield path, data
        for key, value in data.items():
            yield from nodes(value, path + (key,))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            yield from nodes(item, path + (index,))


def group_of(api_version):
    """The API group an `apiVersion` string names, or None when it is not `group/version`."""
    if not isinstance(api_version, str) or "/" not in api_version:
        return None
    return api_version.split("/", 1)[0]


def version_of(api_version):
    """The part after the group, which is what says whether a resource was migrated."""
    if not isinstance(api_version, str) or "/" not in api_version:
        return None
    return api_version.split("/", 1)[1]


def all_nodes():
    return [(path, node_path, node)
            for path, _index, document in documents()
            for node_path, node in nodes(document)]


def is_step_container(container):
    """True when `container` is the path of a step, a sidecar, or a stepTemplate mapping.

    The shape is what decides, not the surrounding kind or the word `resources`: a mapping at
    the top of a `spec` fails this test, which is the whole reason
    `volumeClaimTemplate.spec.resources` and `EventListener.spec.resources` survive a rename
    that targets steps.
    """
    if not container:
        return False
    if container[-1] == "stepTemplate":
        return True
    return (len(container) >= 2 and container[-2] in ("steps", "sidecars")
            and isinstance(container[-1], int))


def label(path, key_path):
    return f"{path.relative_to(REPOSITORY_ROOT)}: {'/'.join(str(part) for part in key_path)}"


def pipeline_runs():
    """Every `PipelineRun` node, wherever it is nested."""
    return [(path, node_path, node)
            for path, node_path, node in all_nodes() if node.get("kind") == "PipelineRun"]


def test_the_manifest_inventory_reads_both_manifest_trees():
    """The inventory has to be an inventory. A tree that shrank silently shrinks the proof too."""
    files = manifest_files()
    # Keyed by the root as a reader would name it, not by `root.name`: two of these three are
    # directories called `tekton_ci`, and a dict keyed by name would report one count for both.
    per_root = {str(root.relative_to(REPOSITORY_ROOT)): sum(1 for path in files if root in path.parents)
                for root in MANIFEST_ROOTS}
    assert all(per_root.values()), f"a manifest tree contributes nothing: {per_root}"
    assert len(files) >= MANIFEST_MINIMUM, (
        f"only {len(files)} manifests were found under tekton_ci/ and examples/tekton_ci/; "
        "this file proves the conversion for both trees, so it is only worth something when it reads both"
    )


def test_every_resource_in_the_pipelines_group_speaks_the_version_the_code_writes():
    """No `tekton.dev` manifest may still say `v1beta1`, wherever the document sits.

    The group is what decides this, not the file: `triggers.tekton.dev/v1beta1` contains the
    beta string and is not part of the move, which is why a plain substring grep over the
    tree says nothing either way.
    """
    offenders = sorted(
        label(path, node_path)
        for path, node_path, node in all_nodes()
        if group_of(node.get("apiVersion")) == PIPELINES_GROUP
        and version_of(node.get("apiVersion")) != PIPELINES_VERSION
    )

    assert offenders == [], "the pipelines group is not uniformly v1: " + "; ".join(offenders)


def test_the_triggers_templates_keep_the_version_the_pinned_triggers_controller_serves():
    """The pinned Triggers controller serves `v1beta1`, so its API version is an exception that has to stay.

    The same `v1beta1` string here looks like the thing the migration removed. Getting it
    "helpfully" rewritten produces a manifest no controller in this tree serves, so the
    exception is asserted instead of assumed.
    """
    offenders = sorted(
        label(path, node_path)
        for path, node_path, node in all_nodes()
        if group_of(node.get("apiVersion")) == TRIGGERS_GROUP
        and version_of(node.get("apiVersion")) != TRIGGERS_VERSION
    )
    assert offenders == [], "the Triggers API was pulled into the Pipelines move: " + "; ".join(offenders)

    files = sorted(str(path.relative_to(REPOSITORY_ROOT))
                   for path in manifest_files()
                   if f"{TRIGGERS_GROUP}/{TRIGGERS_VERSION}" in path.read_text())
    assert len(files) == TRIGGER_TEMPLATE_FILES_EXPECTED, (
        f"{len(files)} files carry {TRIGGERS_GROUP}/{TRIGGERS_VERSION}, expected "
        f"{TRIGGER_TEMPLATE_FILES_EXPECTED} generator templates: {files}"
    )


def test_a_v1_tree_declares_no_pipeline_resource():
    """`PipelineResource` is not served by v1, so the tree that claims v1 must not name one.

    This is the shape a name-for-name transform leaves behind: the apiVersion can be bumped and
    the kind cannot be applied, so the file looks migrated and fails at the cluster.
    """
    offenders = sorted(
        label(path, node_path)
        for path, node_path, node in all_nodes() if node.get("kind") == "PipelineResource"
    )

    assert offenders == [], "a PipelineResource survived into a tree v1 cannot apply: " + "; ".join(offenders)


def test_no_step_argument_still_reads_the_removed_resource_field():
    """`$(resources.inputs.<name>.path)` is a string in a step argument, not a key.

    Renaming keys cannot reach it, which is why the hand-written conversion of an example task
    shows up here rather than in a diff of key names.
    """
    offenders = sorted(str(path.relative_to(REPOSITORY_ROOT))
                       for path in manifest_files() if "$(resources." in path.read_text())

    assert offenders == [], "a step still asks for a resource v1 does not mount: " + ", ".join(offenders)


def test_every_pipeline_run_hides_its_service_account_and_deadline_where_v1_reads_them():
    """`spec.serviceAccountName` and `spec.timeout` are not dropped by a v1 controller.

    They are ignored, which is worse than an error: a pipeline that used to run with a
    namespace-crossing service account now runs with the default one, and the deadline reverts
    to the controller default. Both belong nested.
    """
    offenders = []
    for path, node_path, node in pipeline_runs():
        spec = node.get("spec") or {}
        for removed in ("serviceAccountName", "timeout"):
            if isinstance(spec, dict) and removed in spec:
                offenders.append(f"{label(path, node_path)} still has spec.{removed}")

    assert offenders == [], "; ".join(offenders)


def test_every_pipeline_run_that_needs_a_service_account_can_reach_its_task_runs_with_it():
    """The nesting is what carries, so the account has to resolve one level down.

    The count is the other half: a `PipelineRun` whose `taskRunTemplate` is empty applied fine
    at the beta version, so an empty block is not caught by the shape check above.
    """
    runs = pipeline_runs()
    with_account = [
        (path, node_path) for path, node_path, node in runs
        if ((node.get("spec") or {}).get("taskRunTemplate") or {}).get("serviceAccountName")
    ]
    with_deadline = [
        (path, node_path) for path, node_path, node in runs
        if ((node.get("spec") or {}).get("timeouts") or {}).get("pipeline")
    ]

    assert len(runs) == PIPELINE_RUNS_EXPECTED, (
        f"{len(runs)} PipelineRuns found, {PIPELINE_RUNS_EXPECTED} in the beta tree — the count includes the "
        "runs nested inside a TriggerTemplate's resourcetemplates"
    )
    assert len(with_account) == PIPELINE_RUNS_EXPECTED, (
        f"{len(with_account)} of {len(runs)} runs resolve a service account through taskRunTemplate; "
        "the nesting was applied to a run that had none, or a run lost its account"
    )
    assert len(with_deadline) == PIPELINE_RUNS_EXPECTED, (
        f"{len(with_deadline)} of {len(runs)} runs carry timeouts.pipeline"
    )


def test_a_volume_claim_template_still_asks_for_the_storage_it_wants():
    """The test that says the transformer did not eat a PVC.

    `volumeClaimTemplate.spec.resources.requests` is a Kubernetes field that v1 says nothing
    about. Renamed away, the claim is created with no storage request; it still applies, still
    schedules nothing, and never says why.
    """
    templates = [(path, node_path, node.get("volumeClaimTemplate"))
                 for path, node_path, node in all_nodes() if "volumeClaimTemplate" in node]

    assert templates, "the tree must contain the volume claims this proves about"

    broken = [
        label(path, node_path)
        for path, node_path, template in templates
        if "requests" not in ((template or {}).get("spec") or {}).get("resources", {})
    ]
    renamed = [
        label(path, node_path)
        for path, node_path, template in templates
        if "computeResources" in ((template or {}).get("spec") or {})
    ]
    assert len(templates) == VOLUME_CLAIM_TEMPLATES_EXPECTED, (
        f"{len(templates)} volumeClaimTemplates found, {VOLUME_CLAIM_TEMPLATES_EXPECTED} in the beta tree"
    )
    assert broken == [], "a volume claim lost its storage request: " + "; ".join(broken)
    assert renamed == [], "a volume claim was renamed into a Kubernetes field v1 never asks for: " + "; ".join(renamed)


def test_an_event_listener_keeps_the_resources_block_that_is_its_own():
    """An `EventListener` is a Triggers object. Its `resources` is a KubernetesResource block.

    The container limits under it and its pod-level `serviceAccountName` are equally not the
    Pipelines fields the migration moved, so all three stay exactly where they are.
    """
    listeners = [(path, node_path, node)
                 for path, node_path, node in all_nodes() if node.get("kind") == "EventListener"]
    assert listeners, "the trigger generator templates ship an EventListener"

    for path, node_path, listener in listeners:
        spec = listener.get("spec") or {}
        assert "resources" in spec, f"{label(path, node_path)}: EventListener.spec.resources is what configures the pod"
        resources = spec.get("resources") or {}
        kubernetes_resource = resources.get("kubernetesResource") or {}
        pod = (((kubernetes_resource.get("spec") or {}).get("template") or {}).get("spec") or {})
        assert pod.get("serviceAccountName"), (
            f"{label(path, node_path)}: the listener's own service account belongs to the pod v1 does not own"
        )
        containers = pod.get("containers") or []
        assert containers and containers[0].get("resources"), (
            f"{label(path, node_path)}: the listener container lost the limits that keep it off a busy node"
        )


def test_a_task_step_states_its_limits_under_compute_resources():
    """The rename the migration asked for, plus the count that proves it landed everywhere.

    A step that still says `resources` is accepted by v1 — but its limits are ignored, and a
    step that quietly needs more memory than the node offers is scheduled anyway and
    OOMKilled, which reads like a bug in the step and is a bug in the manifest.
    """
    renamed_back = []
    with_limits = 0
    for path, node_path, node in all_nodes():
        if not is_step_container(node_path):
            continue
        if "resources" in node:
            renamed_back.append(label(path, node_path))
        if "computeResources" in node:
            with_limits += 1

    assert renamed_back == [], "a step still says resources, which v1 reads as no limits: " + "; ".join(renamed_back)
    assert with_limits == STEP_COMPUTE_RESOURCES_EXPECTED, (
        f"{with_limits} steps declare computeResources; the beta tree declared limits in "
        f"{STEP_COMPUTE_RESOURCES_EXPECTED} places, so either a limit block moved in or one disappeared"
    )


def test_a_task_does_not_declare_input_or_output_resources_at_spec_level():
    """`spec.resources` on a Task is gone, not renamed — the one field with no v1 successor.

    A workspace replaces it. This is the case the plan flagged as needing a human, because the
    replacement is a different shape and not a string change.
    """
    offenders = sorted(
        label(path, node_path + ("spec",))
        for path, node_path, node in all_nodes()
        if node.get("kind") in ("Task", "ClusterTask")
        and isinstance(node.get("spec"), dict)
        and "resources" in node["spec"]
    )

    assert offenders == [], "a task still declares task resources v1 removed: " + "; ".join(offenders)
