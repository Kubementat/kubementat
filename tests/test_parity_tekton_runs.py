"""Parity: the three `scripts/automation/tekton` rows the plan calls already migrated.

`configure_secrets.sh` and `configure_docker_registry_access.sh` are in
`test_parity_secrets.py`; this file covers the other three — uninstall, listing, running.

All three are deleted, so all three are records and not runs. `uninstall_pipelines.sh` and
`list_tekton_resources.sh` were recorded earlier (`UNINSTALL_PIPELINES_ASKED`,
`LIST_TEKTON_RESOURCES_ASKED`); `run_pipeline.sh` is recorded here, in the same commit that deletes
it, for the same reason the other two were: it is the only way the comparison keeps being a
comparison after the file is gone. What it was held back for was the Tekton Task that `source`d it
inside a pod, and the Task was repointed at `kmt tekton run-pipeline --wait`, so the reference
is no longer load-bearing and one of its two jobs is now a Python call.

The record is not free-form: `RUN_PIPELINE_SHELL_SUBLIMATIONS` is checked against the script's own
`sed` table out of `git show 40f281b:scripts/automation/tekton/run_pipeline.sh` in
`test_the_substitution_record_is_the_scripts_own_table`, so a record that drifted from the script
it claims to describe is caught rather than believed.

What these scripts have in common is that they *print* things: namespaces left behind, pipeline
and task names, a generated run name. So the comparison has a second job beyond the cluster
operations — the kind of thing a migration drops silently is a listing that stopped listing. The
kinds each side prints are asserted as its own check, in the plan's words, rather than assumed.

The branch that used to be broken twice over is here too, once: `run_pipeline.sh` refuses to start a
second run of the same pipeline while one is Running, and the command that replaced it *refused to
say that it had*. Its exit code is asserted in the refusal case, and it is no longer 0.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from cluster_parity import (ClusterOperation, cluster_named, compare,
                            with_generated_run_name_normalized, payload_facts)

from conftest import (ENVIRONMENT_STATIC, FIXTURE_KUBE_CONTEXT, PIPELINE_NAMESPACE,
                      TEAM_STATIC)

# The cluster both sides of every comparison here addresses: the fixture's, named by the
# context its `static.json` declares. The script's half is a record rather than a run, so this is
# the half that has to say it out loud — and a Python path that addressed another cluster is now a
# difference the comparison finds rather than a detail the record used to leave out.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)
# The substitution table moved out with the generation group, so the read points at its new home.
from kubementat.infrastructure.tekton.generation import CONFIG_PLACEHOLDER_SOURCES

REPO_ROOT = Path(__file__).resolve().parent.parent

TEKTON = "tekton.dev"
NO_RUNS = json.dumps({"items": []})
RUNNING = json.dumps({"items": [{"metadata": {"name": "hello-run-running",
                                                "creationTimestamp": "2026-01-01T00:00:00Z"},
                                 "spec": {"pipelineRef": {"name": "hello"}},
                                 "status": {"conditions": [{"reason": "Running"}]}}]})

# The last commit `run_pipeline.sh` was in. It is deleted here, so every bash half of the four
# comparisons below is a record from this revision and not a run.
SCRIPT_REVISION = "40f281b"
SCRIPT = "scripts/automation/tekton/run_pipeline.sh"

# What `uninstall_pipelines.sh` sent, recorded from the script while it was still in the tree
# (`git show ccb6976:scripts/automation/tekton/uninstall_pipelines.sh`): the namespace delete,
# then the `kubectl get all` it printed to show what was left. That listing is the declared
# difference below, so only the delete — the act — is compared in full.
UNINSTALL_PIPELINES_ASKED = [
    ClusterOperation("delete", "namespace", None, "fixture-pipelines", cluster=CLUSTER),
    ClusterOperation("read", "all", "fixture-deployments", None, cluster=CLUSTER),
]

# What `list_tekton_resources.sh` read: one `tkn … list` per kind, in the team's pipeline
# namespace. The kinds are the whole contract of this row, so they are the record too.
LIST_TEKTON_RESOURCES_ASKED = [
    ClusterOperation("read", "pipeline", "fixture-pipelines", None, cluster=CLUSTER),
    ClusterOperation("read", "task", "fixture-pipelines", None, cluster=CLUSTER),
    ClusterOperation("read", "taskrun", "fixture-pipelines", None, cluster=CLUSTER),
    ClusterOperation("read", "pipelinerun", "fixture-pipelines", None, cluster=CLUSTER),
]

# The eleven substitutions `run_pipeline.sh` made, recorded from the `seds+=` lines it assembled —
# `git show 40f281b:scripts/automation/tekton/run_pipeline.sh`, and re-measured against that blob by
# `test_the_substitution_record_is_the_scripts_own_table`. Each entry is the token the `sed` looked
# for and the `static.json` its `jq -r` read the value out of, so a token that changed source on the
# Python side shows up as a mismatch here and not as a manifest that is quietly wrong.
RUN_PIPELINE_SHELL_SUBLIMATIONS = {
    "STORAGE_CLASS_PLACEHOLDER": ("env_static", "TEKTON_KUBERNETES_STORAGE_CLASS"),
    "DOCKER_REGISTRY_BASE_URL_PLACEHOLDER": ("env_static", "DOCKER_REGISTRY_BASE_URL"),
    "TEKTON_CI_IMAGE_NAME_PLACEHOLDER": ("env_static", "TEKTON_CI_IMAGE_NAME"),
    "TEKTON_CI_IMAGE_TAG_PLACEHOLDER": ("env_static", "TEKTON_CI_IMAGE_TAG"),
    "AUTOMATION_GIT_URL_PLACEHOLDER": ("env_static", "AUTOMATION_GIT_URL"),
    "AUTOMATION_GIT_PROJECT_NAME_PLACEHOLDER": ("env_static", "AUTOMATION_GIT_PROJECT_NAME"),
    "AUTOMATION_GIT_REVISION_PLACEHOLDER": ("env_static", "AUTOMATION_GIT_REVISION"),
    "AUTOMATION_GIT_SERVER_HOST_PLACEHOLDER": ("env_static", "AUTOMATION_GIT_SERVER_HOST"),
    "AUTOMATION_GIT_SERVER_PORT_PLACEHOLDER": ("env_static", "AUTOMATION_GIT_SERVER_PORT"),
    "AUTOMATION_GIT_SERVER_SSH_USER_PLACEHOLDER": ("env_static", "AUTOMATION_GIT_SERVER_SSH_USER"),
    "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME_PLACEHOLDER":
        ("team_static", "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"),
}

# The cluster operations the script performed to start one run: two version reads it printed before
# it applied anything, the `tkn pipelinerun list` its parallel guard asked, and the `tkn pipelinerun
# list` it printed afterwards. The write is the fifth, and it comes from `_the_run_the_script_applied`
# rather than from here, because the manifest is what is being compared.
RUN_PIPELINE_SHELL_ASKED = [
    ClusterOperation("read", "version", None, None, cluster=CLUSTER),
    ClusterOperation("read", "version", None, None, (("tool", "tkn"),), cluster=CLUSTER),
    ClusterOperation("read", "pipelinerun", PIPELINE_NAMESPACE, None, cluster=CLUSTER),
    ClusterOperation("read", "pipelinerun", PIPELINE_NAMESPACE, None, cluster=CLUSTER),
]

# Where each of the script's two version reads came from, for the declared difference in the
# comparison below: bash printed both, Python asks the API server nothing.
BASH_VERSION_READS = [ClusterOperation("read", "version", None, None, cluster=CLUSTER),
                      ClusterOperation("read", "version", None, None, (("tool", "tkn"),), cluster=CLUSTER)]


def _applied_body(recorder):
    """The body of the last custom-object write, whatever method it used.

    `create_or_replace_custom_resource` creates and then patches, so "the last one" is the state
    that actually landed — which is what the substitution check is about.
    """
    writes = [kwargs["body"] for method, kwargs in recorder.calls
              if method.endswith("custom_object") and "body" in kwargs]
    assert writes, "no custom object was written: " + ", ".join(recorder.methods())
    return writes[-1]


def kmt(command_module, command_name, arguments):
    from click.testing import CliRunner

    result = CliRunner().invoke(getattr(command_module, command_name), arguments)
    assert result.exit_code == 0, f"{command_name} {arguments}: {result.output}\n{result.exception}"
    return result


def _substitute_tokens(document, values):
    """The script's `sed`, on a parsed document: replace the token wherever it sits.

    bash sed-ed the *text*, so a token inside one long line and a token in its own field were the
    same substitution. A walk over the parsed document is the same statement, and it is written here
    rather than borrowed from the implementation so the comparison cannot go round in a circle.
    """
    if isinstance(document, dict):
        return {key: _substitute_tokens(value, values) for key, value in document.items()}
    if isinstance(document, list):
        return [_substitute_tokens(item, values) for item in document]
    if isinstance(document, str):
        for token, value in values.items():
            document = document.replace(token, str(value))
        return document
    return document


def _the_run_the_script_applied(fixture_repo, run_file):
    """The document `run_pipeline.sh` would have applied, rebuilt from the record.

    Its `sed` table is `RUN_PIPELINE_SHELL_SUBLIMATIONS`, and the eleven values in it are the ones
    its `jq -r` reads would have read out of this fixture's own `platform_config`. `metadata.name`
    gets the generated postfix here too, because both sides did that — and `payload_facts` drops the
    name field before comparing, so the random half cannot make either side look right.
    """
    document = yaml.safe_load(run_file.read_text())
    document["metadata"]["name"] = "hello-run-<postfix>"
    values = {token: _fixture_value(category, key)
              for token, (category, key) in RUN_PIPELINE_SHELL_SUBLIMATIONS.items()}
    return _substitute_tokens(document, values)


def _fixture_value(category, key):
    """The value the fixture's `static.json` holds, from the same table `conftest` wrote it from."""
    return (ENVIRONMENT_STATIC if category == "env_static" else TEAM_STATIC)[key]


def _paths_holding(document, token, path=()):
    """Where in a document a token sits, as field paths. `()` is the root.

    A substitution is judged on the field that carried the token, not anywhere in the document,
    because two of the fixture's values are the same string and a check on the whole serialised body
    could not tell them apart.
    """
    if isinstance(document, dict):
        return [found for key, value in document.items()
                for found in _paths_holding(value, token, path + (key,))]
    if isinstance(document, list):
        return [found for index, value in enumerate(document)
                for found in _paths_holding(value, token, path + (index,))]
    if isinstance(document, str):
        return [path] if token in document else []
    return []


def _at(document, path):
    """The value at a field path, or `KeyError`/`IndexError` if the walk runs off the document."""
    for step in path:
        document = document[step]
    return document


def _bash_operations(fixture_repo, run_file):
    """The script's side of the comparison: four reads and one write, as operations."""
    return RUN_PIPELINE_SHELL_ASKED + [
        ClusterOperation("ensure", "pipelinerun", PIPELINE_NAMESPACE, "hello-run-<postfix>",
                         payload_facts("pipelinerun", _the_run_the_script_applied(fixture_repo,
                                                                                 run_file)), cluster=CLUSTER)]


def _recorded_script_blob():
    """The script's own text, out of git history. Skips, loudly, if the history is missing."""
    result = subprocess.run(["git", "show", f"{SCRIPT_REVISION}:{SCRIPT}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{SCRIPT_REVISION}:{SCRIPT} exited {result.returncode} "
                    f"({result.stderr.strip()}). The parity below is a comparison, and without the "
                    f"other side of it there is nothing to compare.")
    return result.stdout


def _sed_table_the_script_assembled():
    """The `seds+=` lines, as `{TOKEN_PLACEHOLDER: the variable it substituted}`.

    What is read is what the script wrote, line by line, so this is the record's own evidence and
    not a restatement of the record.
    """
    table = {}
    for line in _recorded_script_blob().splitlines():
        line = line.strip()
        if not line.startswith("seds+="):
            continue
        statement = line[line.index("seds+=") + len("seds+="):].strip().strip('"')
        for entry in statement.split(";"):
            entry = entry.strip()
            if not entry.startswith("s|"):
                continue
            _, token, value, *rest = entry.split("|")
            del rest  # the `g` flag, which is not part of what was substituted
            table[token] = value.strip("{}").strip("$").strip("{}")
    return table


def test_uninstalling_pipelines_deletes_the_pipeline_namespace(fixture_repo, dry_run_env,
                                                               recorded_api):
    """Same delete, same tolerance for a namespace that is not there.

    The script ran `kubectl delete namespace $PIPELINE_NAMESPACE || true`; the command runs
    `delete_namespace()` with `ignore_not_found=True`, which is its *deliberate* choice (the
    plan's tolerance rule: every other status still reaches the caller). The script's `|| true`
    swallows real errors too, and the next test is where that difference is pinned down.

    `uninstall_pipelines.sh` is deleted, so the script's side is `UNINSTALL_PIPELINES_ASKED`.
    """
    from cli.commands import tekton

    kmt(tekton, "uninstall_pipelines", ["dev", "fixture", "--yes"])
    python_ops = recorded_api.operations()

    compare(UNINSTALL_PIPELINES_ASKED, python_ops,
            bash_only=[ClusterOperation("read", "all", "fixture-deployments", None, cluster=CLUSTER)],
            context="kmt tekton uninstall-pipelines against what uninstall_pipelines.sh used to send")


def test_a_namespace_that_cannot_be_deleted_is_not_mistaken_for_success(
        fixture_repo, dry_run_env):
    """The caveat `|| true` hid, and the reason the Python flag is narrower.

    A namespace that is stuck (finaliser, permission) exits non-zero from kubectl. The script
    said nothing and continued to the `get all`, so it reported a clean uninstall; the Python
    path tolerates only 404 and re-raises anything else. That is the one place this pair is *not*
    equivalent, and it is the good direction to be different in — so the command's half is
    asserted here, and the script's half (exit 0, no complaint) is what the record above exists
    to keep honest.
    """
    from kubernetes import client

    from kubementat.infrastructure.kubernetes import KubernetesUtils

    class StuckNamespaceCoreApi:
        """`delete namespace`, refused — the finaliser case, and every permission case."""

        def __init__(self):
            self.calls = []

        def delete_namespace(self, **kwargs):
            self.calls.append(kwargs)
            raise client.exceptions.ApiException(status=403, reason="forbidden")

    utils = KubernetesUtils.__new__(KubernetesUtils)  # no cluster, no config, just the primitive
    utils.core_api = StuckNamespaceCoreApi()

    with pytest.raises(client.exceptions.ApiException) as error:
        utils.delete_namespace(PIPELINE_NAMESPACE, ignore_not_found=True)
    assert error.value.status == 403


def test_listing_reports_the_same_four_kinds(fixture_repo, dry_run_env, recorded_api):
    """`tkn … list` four times, versus one method that reads four custom-resource kinds.

    The plan's caveat for this row was "diff the printed kinds first", and that is exactly what is
    asserted: pipelines, tasks, pipelineruns, taskruns. A kind that disappeared would be a command
    that quietly stopped telling you about half your cluster — and the record carries all four,
    because the listing *was* the script.
    """
    from cli.commands import tekton

    recorded_api.answer("list_namespaced_custom_object", lambda **kwargs: {"items": []})
    kmt(tekton, "list", ["dev", "fixture"])
    python_ops = recorded_api.operations()

    # Python checks once per process that the controller serves `tekton.dev/v1`. The
    # script assumed it did, which is why its answer could be wrong and nobody noticed.
    compare(LIST_TEKTON_RESOURCES_ASKED, python_ops,
            python_only=[ClusterOperation("read", "api-resources", None, None,
                                          (("group", TEKTON), ("version", "v1")), cluster=CLUSTER)],
            context="kmt tekton list against what list_tekton_resources.sh used to read")

    kinds = {op.kind for op in python_ops if op.is_read}
    assert kinds >= {"pipeline", "task", "pipelinerun", "taskrun"}, kinds
    assert {op.namespace for op in python_ops if op.is_read and op.kind != "api-resources"} == \
           {PIPELINE_NAMESPACE}


def test_the_substitution_record_is_the_scripts_own_table(fixture_repo):
    """The record that replaces the bash half, checked against the script it was read from.

    A record is only worth what its source is, and this one is used by three of the four cases
    below. So the `sed` table is pulled out of `git show` line by line and compared with the table
    in this file: same eleven tokens, and the token's `static.json` half read straight from the
    script's own `jq` lines (`${PLATFORM_CONFIG_DIRECTORY}/${ENVIRONMENT}/${TEAM}/static.json` is
    the team's, anything else is the environment's).
    """
    assembled = _sed_table_the_script_assembled()
    recorded = {token: token for token in RUN_PIPELINE_SHELL_SUBLIMATIONS}
    assert recorded == {token: token for token in assembled}, \
        "the script's sed table and the record no longer name the same tokens"

    for token, variable in assembled.items():
        category, key = RUN_PIPELINE_SHELL_SUBLIMATIONS[token]
        assert variable == key, f"{token}: the script substituted ${variable}, the record says {key}"
        # The team's static.json is the only file whose path contains the team, which is the whole
        # difference between an environment key and a team key in that script.
        reads_from_team = bool(
            [line for line in _recorded_script_blob().splitlines()
             if variable in line and "${TEAM}" in line])
        assert reads_from_team == (category == "team_static"), \
            f"{variable} is read from the {'team' if category == 'team_static' else 'environment'} " \
            "static.json in the script, and not in the record"


def test_the_substitutions_python_makes_are_the_substitutions_the_script_made():
    """Both tables, side by side. This is the substitution, and it has one declared difference.

    Python substitutes one token the script never met — `BASE_DOMAIN_PLACEHOLDER`, which belongs to
    the generators rather than to a run file — and nothing else. That is stated rather than
    tolerated, because a token that went missing the other way is the defect this comparison exists
    to catch.
    """
    python = {f"{token}_PLACEHOLDER": source
              for token, source in CONFIG_PLACEHOLDER_SOURCES.items() if token != "BASE_DOMAIN"}

    assert RUN_PIPELINE_SHELL_SUBLIMATIONS == python
    assert {"BASE_DOMAIN_PLACEHOLDER"} == {
        token for token in (f"{token}_PLACEHOLDER" for token in CONFIG_PLACEHOLDER_SOURCES)
        if token not in RUN_PIPELINE_SHELL_SUBLIMATIONS}


def test_running_a_pipeline_applies_the_substituted_manifest_the_script_applied(
        fixture_repo, dry_run_env, recorded_api):
    """The substitution *is* the script, so the substitution is still what is compared.

    `run_pipeline.sh` read a template, rewrote `metadata.name`, and sed-ed eleven placeholders into
    place. Python loads the same file and replaces the same placeholders in the same dict. Both send
    the result, and the two are compared field by field — minus the random name suffix, which is not
    a decision either side made — and as operations.

    This is the highest-value parity test of the lot: a placeholder renamed on one side, or a
    substitution that no longer matches what the template says, is otherwise invisible until a
    pipeline fails to start on a cluster that is not yours. The script's half is now the record
    rather than a run, which is why `_the_run_the_script_applied` is built from the sed table and
    the fixture's own configuration.
    """
    from cli.commands import tekton

    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    recorded_api.answer("list_namespaced_custom_object", lambda **kwargs: {"items": []})

    kmt(tekton, "run_pipeline", ["dev", "fixture", run_file.name])
    python_ops = with_generated_run_name_normalized(recorded_api.operations(), "hello-run")

    compare(_bash_operations(fixture_repo, run_file), python_ops,
            bash_only=BASH_VERSION_READS,
            python_only=[ClusterOperation("read", "api-resources", None, None,
                                          (("group", TEKTON), ("version", "v1")), cluster=CLUSTER)],
            context="kmt tekton run-pipeline against what run_pipeline.sh used to apply")

    # The same document, read as a document rather than as a fact tuple, so a failure says which
    # field moved instead of showing two renders of a whole manifest.
    expected = _the_run_the_script_applied(fixture_repo, run_file)
    applied = _applied_body(recorded_api)
    assert applied["metadata"]["name"].startswith("hello-run-"), applied["metadata"]["name"]
    assert {k: v for k, v in applied.items() if k != "metadata"} == \
           {k: v for k, v in expected.items() if k != "metadata"}


def test_the_manifest_python_applies_contains_no_unsubstituted_placeholder(
        fixture_repo, dry_run_env, recorded_api):
    """The failure mode the substitution comparison exists to catch.

    A placeholder that survives substitution still *applies* — the cluster accepts a literal
    `STORAGE_CLASS_PLACEHOLDER` string, and the pipeline fails later, somewhere else. So every token
    the deleted script's sed table knew about has to be gone from the body that was sent, and the
    value it stood for has to be in there instead.

    The script's half of this used to be "and its own stdout was clean". That half left with the
    file; what remains is the claim that matters, checked against the whole substitution table
    instead of the one fixture this one file used to run. Each token is judged on the field that
    carried it, so a value that landed in the wrong field is caught even when two of the fixture's
    configured values are the same string.
    """
    from cli.commands import tekton

    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    recorded_api.answer("list_namespaced_custom_object", lambda **kwargs: {"items": []})

    kmt(tekton, "run_pipeline", ["dev", "fixture", run_file.name])
    applied = _applied_body(recorded_api)
    template = yaml.safe_load(run_file.read_text())

    for token, (category, key) in RUN_PIPELINE_SHELL_SUBLIMATIONS.items():
        paths = _paths_holding(template, token)
        assert paths, f"the fixture's run file carries no {token}, so this proves nothing about it"
        for path in paths:
            field = _at(applied, path)
            assert token not in str(field), f"{token} reached the cluster unsubstituted at {path}"
            assert str(field) == str(_fixture_value(category, key)), \
                f"{path}: expected the value of {key}, got {field!r}"
    assert "PLACEHOLDER" not in json.dumps(applied), applied


def test_running_a_pipeline_that_is_already_running_is_refused_and_now_says_so(
        fixture_repo, dry_run_env, recorded_api, caplog):
    """The branch that used to be broken twice: a second run of a pipeline that is Running.

    bash listed the runs, found one whose `pipelineRef` matches and whose status is Running, printed
    `CANCELLED TO AVOID PARALLEL RUN!` and exited 1 — deliberately, before applying anything, with no
    `kubectl apply` anywhere between the guard and the `exit 1` (`git show
    40f281b:scripts/automation/tekton/run_pipeline.sh`, which is where the refusal is recorded).
    Python does the same through `check_running_pipelines`, so neither of them applies a
    PipelineRun, which is the point.

    The other half used to be the defect this file had to record: the command *did the refusing* and
    exited 0, because the CLI threw away the value `TektonUtils.run_pipeline` handed back. That is
    closed — a refused run is a non-zero exit, which is what the outer pipeline needs or it reports
    success over a run that never started.
    """
    import logging

    from cli.commands import tekton

    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    recorded_api.answer("list_namespaced_custom_object", lambda **kwargs: json.loads(RUNNING))

    with caplog.at_level(logging.INFO):
        result = CliRunner().invoke(tekton.run_pipeline,
                                    ["dev", "fixture", run_file.name])

    assert result.exit_code == 1, f"a refused run reported success: {result.output}"
    assert "CANCELLED TO AVOID PARALLEL RUN" in caplog.text, caplog.text
    assert not [op for op in recorded_api.operations() if op.verb == "ensure"], \
        "Python applied a run anyway, after saying it would not"


def test_a_pipeline_run_with_parallel_allowed_is_applied_even_while_one_runs(
        fixture_repo, dry_run_env, recorded_api):
    """The other half of the same guard: `--parallel` means "yes, I meant that".

    bash took it as a fourth positional argument and exited 0, having applied the run; the CLI takes
    it as a flag. Both stop refusing, or the flag is a lie — so this is the same comparison as the
    substitution case above, with the cluster's answer changed from "nothing is running" to "one of
    them is", which is the only condition under which the two arguments mean anything.
    """
    from cli.commands import tekton

    run_file = fixture_repo.write_pipeline_run(name="hello-run", pipeline="hello")
    recorded_api.answer("list_namespaced_custom_object", lambda **kwargs: json.loads(RUNNING))

    kmt(tekton, "run_pipeline", ["dev", "fixture", run_file.name, "--parallel"])
    python_ops = with_generated_run_name_normalized(recorded_api.operations(), "hello-run")

    assert any(op.verb == "ensure" for op in python_ops), \
        "the flag did not change the answer: nothing was applied"
    compare(_bash_operations(fixture_repo, run_file), python_ops,
            bash_only=BASH_VERSION_READS,
            python_only=[ClusterOperation("read", "api-resources", None, None,
                                          (("group", TEKTON), ("version", "v1")), cluster=CLUSTER)],
            context="kmt tekton run-pipeline --parallel against what "
                    "run_pipeline.sh did with a fourth argument of true")
