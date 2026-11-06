"""Parity: `generate_pipeline_run.sh` against `TektonUtils.generate_pipeline_run`, and where the
generated run is allowed to live.

The script had no caller. `grep -rl generate_pipeline_run src tests/e2e tekton_ci README.md` answers
nothing outside the script's own usage block, so it was reachable only by somebody who knew the file
name and — because every path in it was `../../../tekton_ci/...` — which directory they were standing
in. Making it a command is therefore the migration rather than decoration: a library call nobody can
reach is how a thing quietly stops getting done.

Where the output goes is the half that is not a port. bash wrote `tekton_ci/pipeline-runs/<TEAM>/`,
under `Config.tekton_global_pipeline_run_dir` — the directory `list_available_pipeline_runs` scans
non-recursively and `run_pipeline` only reads through a `global/` prefix — so the script's own output
was invisible to the two commands that consume it and reachable only by typing its whole path.
Generated runs are one team's configuration, so they go to `Config.tekton_team_pipeline_run_dir`,
which is the directory `run_pipeline` resolves a bare file name against and the first thing
`list_available_pipeline_runs` prints. That is the decision recorded for the deploy-pipeline
generator, and `.gitignore` backs it: everything under `tekton_ci/` that is not a template is
ignored, which is precisely why generated output cannot live there and stay reviewable.

What the two sides do *not* agree about is written down rather than smoothed over, and each one is a
defect in the bash half rather than a preference:

* bash wrote eleven parameters into every run, declared or not. A `PipelineRun` that passes a
  parameter its `Pipeline` never declared is rejected by the controller, so a run of a pipeline that
  takes fewer parameters was a run that could not start. Python writes one parameter per declared
  parameter, in the pipeline's own order, and no others.
* bash's parameter loop skipped nine of the eleven names but not `tekton-ci-image-name` or
  `tekton-ci-image-tag`, so generating a run of the pipeline that *has* those parameters wrote the same
  parameter twice, once with a value and once with `TODO`.
* bash always bound one workspace called `pipeline-workspace`, whatever the pipeline declared. Six of
  the nine pipelines in `tekton_ci/pipelines/` declare exactly that one; the three that declare none
  got a binding they never asked for. Python binds what the source declares, so a run of a Task-only
  pipeline declares no workspace.
* bash wrote `STORAGE_CLASS_PLACEHOLDER` as a literal and never read `Config` for it. The generator
  substitutes through `CONFIG_PLACEHOLDER_SOURCES` and refuses what is unfilled, so a key that has been
  renamed or dropped is an error that names it rather than a manifest that recites it.
* bash `cat`ed the file it was handed after a `../../../` prefix and checked nothing else about it, so
  a `Task` produced a run of it, a multi-document file produced a run of its first document, and a
  `metadata.name` that was not there produced a run called `null-run`. All four wrote a file and
  printed a success line. Its parameter loop was the other half of the same carelessness: `jq` over
  `.spec.params[]` errors on a `spec` that declares no parameters, `set -e` stopped the script, and a
  pipeline that takes nothing was a pipeline nobody could generate a run of.
* bash needed `tekton_ci/pipeline-runs/<TEAM>/` to already exist, because `echo >` does not make
  directories, and its `set -e` stopped it there mid-run.
* bash's `apiVersion` was a literal in the middle of a heredoc. The generator's is read from
  `Config.TEKTON_API_GROUP`/`TEKTON_API_VERSION`, so both agree on `v1` today for different reasons.

`INTENDED_DIFFERENCES` below is that list as data, one entry per row, and each row is proven by running
both halves — the record out of `git show`, the generator as a Python call — and asserting the pair.

Comments are the loss that is real rather than claimed: the review guidance bash left in the file does
not survive a document built from data. The command says what those comments said, out loud, where a
later edit to a template cannot make it stale.
"""

import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT, run_bash, run_kmt
from cluster_fixture import configuration_only_context
from asset_paths import tekton_ci_path

# The record is the commit that still holds the script. Pinning it rather than asking for `HEAD` is
# what lets this file keep its claim after the deletion lands: `git show HEAD:<script>` starts
# answering `fatal: path ... does not exist` in exactly the commit this change is committed in.
SCRIPT_REVISION = "894f587"
GENERATOR_SCRIPT = "scripts/automation/tekton/generate_pipeline_run.sh"

PIPELINE = "build-pipeline-ci-images"

# Where each half writes, spelled once. bash's target is the record; Python's is the rule.
SCRIPT_OUTPUT_DIR = "tekton_ci/pipeline-runs/fixture"
TEAM_PIPELINE_RUN_DIR = "platform_config/dev/fixture/tekton/pipeline-runs"

# The eleven parameters bash wrote into every file, in the order its heredoc wrote them.
SCRIPT_PARAMETERS = ("environment", "team", "docker-registry-base-url", "tekton-ci-image-name",
                     "tekton-ci-image-tag", "automation-git-project-name", "automation-git-revision",
                     "automation-git-server-host", "automation-git-server-port",
                     "automation-git-server-ssh-user", "automation-git-url")


def deleted_script():
    """The script's own text, out of git history. Skips, loudly, if the history is missing."""
    result = subprocess.run(["git", "show", f"{SCRIPT_REVISION}:{GENERATOR_SCRIPT}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{SCRIPT_REVISION}:{GENERATOR_SCRIPT} exited {result.returncode} "
                    f"({result.stderr.strip()}). This is a comparison, and without the bash side "
                    f"there is nothing to compare it against.")
    return result.stdout


@pytest.fixture
def checkout(fixture_repo, dry_run_env):
    """The fixture checkout, with the pipelines both halves read put back.

    `fixture_repo` seeds scripts and configuration and no `tekton_ci` pipelines, but both halves read
    them — bash from `../../../tekton_ci/pipelines/`, the generator from the path the operator passes.
    They are tracked product assets, so the fixture uses the real ones; a pipeline invented for the
    test would be a pipeline nobody generates a run of.

    The `pipeline-runs/<team>` directory is created because bash `echo >`s into it and stops with `No
    such file or directory` when it is not there — which is one of the two things the record shows
    about this script's failure modes, the other being the redirect that writes a file holding the
    heredoc's tail before the parameter loop runs.
    """
    # The bytes come from where the product carries them (inside the package), and they are
    # laid down in the fixture checkout so that bash's `../../../` spelling resolves. The copy is
    # fixture scaffolding, not a claim about where the product reads them from.
    shutil.copytree(tekton_ci_path("pipelines"),
                    fixture_repo.root / "tekton_ci/pipelines", dirs_exist_ok=True)
    (fixture_repo.root / "tekton_ci/pipeline-runs/fixture").mkdir(parents=True, exist_ok=True)
    return fixture_repo


def utils(checkout):
    from kubementat.tekton_utils import TektonUtils

    return TektonUtils(configuration_only_context(checkout.environment, checkout.team))


def pipeline_source(checkout, pipeline):
    """The pipeline's file — one file, spelled two ways, which is what the comparison is about.

    bash `cat`s its argument from the directory its own script sits in, so the honest argument there
    is `../../../tekton_ci/pipelines/<name>.yml`; the generator resolves what it is handed against the
    working directory. Pointing both at one file is the whole comparison, and the difference in the
    spelling is the difference in the port.
    """
    return checkout.root / "tekton_ci/pipelines" / f"{pipeline}.yml"


def run_generator_script(checkout, pipeline):
    """The script's text, placed where it can resolve its `../../../` paths, then run."""
    return record_writes(checkout, pipeline,
                         f"../../../tekton_ci/pipelines/{pipeline}.yml")["result"]


def record_writes(checkout, run_name, source):
    """The record, run, and the file it left behind — which is the half a comparison reads.

    `run_name` is what the target file is named after, so for a file whose `metadata.name` is not a
    pipeline's this is the name bash read out of it and would have generated under. `source` is spelled
    the way the script needs it, because resolving that spelling was the script's half of the work.
    """
    script = (checkout.scripts / "automation" / "tekton"
              / GENERATOR_SCRIPT.rsplit("/", 1)[-1])
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(deleted_script())
    result = run_bash(script, args=[checkout.environment, checkout.team, source],
                      cwd=script.parent,
                      env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")})
    target = checkout.root / SCRIPT_OUTPUT_DIR / f"{run_name}-run.yml"
    return {"result": result, "output": result.stdout + result.stderr, "target": target,
            "document": documents_of(target)[0] if target.is_file() else None}


def raw_parameters_of(path):
    """The `spec.params` entries of a file, as written: order kept, duplicates kept.

    `parameters_of` collapses to a mapping and so keeps the *last* entry for a repeated name, which is
    right for comparing values and blind for counting entries. One of the differences below is a count.
    """
    return [{"name": entry["name"], "value": entry["value"]}
            for entry in documents_of(path)[0]["spec"].get("params") or []]


def generated_pipeline_run(checkout, pipeline):
    return utils(checkout).generate_pipeline_run(str(pipeline_source(checkout, pipeline)))


def documents_of(path):
    """The documents of one manifest file, empty ones dropped the way `apply_resources` drops them."""
    return [document for document in yaml.safe_load_all(Path(path).read_text())
            if document is not None]


def parameter_names(document):
    return [parameter["name"] for parameter in document["spec"].get("params") or []]


def parameters_of(document):
    return {parameter["name"]: parameter["value"]
            for parameter in document["spec"].get("params") or []}


def declared_parameter_names(checkout, pipeline):
    """The parameter names the pipeline itself declares, in the order it declares them."""
    return parameter_names(documents_of(pipeline_source(checkout, pipeline))[0])


def written_run(checkout, pipeline):
    """Where a run of `pipeline` would be written, for a test that expects nothing to be there."""
    return Path(checkout.root) / TEAM_PIPELINE_RUN_DIR / f"{pipeline}-run.yml"


def resolved(checkout, document):
    """What a document means once `run_pipeline`'s own substitution has run over it.

    The script's file and the generator's file are only comparable this way: bash left the placeholder
    tokens in place and the substitution is what turns them into the values both halves read out of the
    same configuration — which is also the exact point where the two halves' claims about
    configuration diverge.
    """
    utils(checkout)._replace_placeholders_in_dict(document)
    return document


# --- what the generator writes ------------------------------------------------------------------


def test_a_generated_run_lands_where_the_run_commands_read_it(checkout):
    """The location is the point, so it is asserted as a path rather than as a description.

    `run_pipeline` resolves a bare file name against `Config.tekton_team_pipeline_run_dir` and
    `list_available_pipeline_runs` globs that same directory, so a generated run that is reviewed and
    committed is a run the team can start by name — which is what the script's output never was.
    """
    target = generated_pipeline_run(checkout, PIPELINE)

    assert target == checkout.root / TEAM_PIPELINE_RUN_DIR / f"{PIPELINE}-run.yml"
    assert target.is_file()
    assert not (checkout.root / SCRIPT_OUTPUT_DIR / f"{PIPELINE}-run.yml").exists()


def test_the_command_generates_a_run_and_lists_it_by_name_alone(checkout, dry_run_env):
    """Both halves of "reachable", run the way an operator runs them rather than imported.

    `kmt tekton-generate-pipeline-run` is the command this ticket exists to add, and
    `kmt tekton run-pipeline <env> <team>` with no identifier prints what can be started. The script
    wrote into the global directory, where that listing does not look, so the file it generated never
    appeared in its own tool's answer.
    """
    generated = run_kmt(["tekton-generate-pipeline-run", checkout.environment, checkout.team,
                         str(pipeline_source(checkout, PIPELINE))], dry_run_env)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    assert (checkout.root / TEAM_PIPELINE_RUN_DIR / f"{PIPELINE}-run.yml").is_file()

    listing = run_kmt(["tekton", "run-pipeline", checkout.environment, checkout.team], dry_run_env)

    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert f"{PIPELINE}-run.yml" in listing.stdout + listing.stderr


def test_a_generated_run_is_the_run_the_pipeline_asks_for(checkout):
    """Shape, read from the v1 contract rather than out of a heredoc.

    `taskRunTemplate.serviceAccountName` and `timeouts.pipeline` both moved in v1, which is why the
    apiVersion they are stamped with comes from `Config`: a template or a script that writes
    `tekton.dev/v1beta1` and the v1 field names is a manifest no controller in this tree answers to.
    """
    document = documents_of(generated_pipeline_run(checkout, PIPELINE))[0]

    assert document["apiVersion"] == "tekton.dev/v1"
    assert document["kind"] == "PipelineRun"
    assert document["metadata"]["name"] == f"{PIPELINE}-run"
    spec = document["spec"]
    assert spec["pipelineRef"]["name"] == PIPELINE
    assert spec["taskRunTemplate"]["serviceAccountName"] == "fixture-helm-deployer"
    assert spec["timeouts"]["pipeline"] == "10m"
    assert spec["workspaces"] == [{"name": "pipeline-workspace",
                                   "volumeClaimTemplate": {"spec": {
                                       "storageClassName": "fixture-storage",
                                       "accessModes": ["ReadWriteOnce"],
                                       "resources": {"requests": {"storage": "100Mi"}}}}}]


def test_a_generated_run_declares_every_parameter_the_pipeline_takes_and_not_one_more(checkout):
    """One parameter per declared parameter, in the pipeline's own order.

    This is the difference a reviewer should not have to find. bash appended its fixed eleven and then
    anything else, so a pipeline that declares three parameters got eleven, and the controller rejects
    a run that passes what the pipeline never declared. Here the set is exactly the declaration — which
    doubles as the assertion that the generator invents no parameter of its own.
    """
    document = documents_of(generated_pipeline_run(checkout, PIPELINE))[0]

    assert parameter_names(document) == declared_parameter_names(checkout, PIPELINE)


@pytest.mark.parametrize("pipeline", ["build-pipeline-ci-images", "hello-world-pipeline",
                                      "docker-build-pipeline", "delete-pipeline-helm-deployment"])
def test_the_generated_run_binds_only_the_workspaces_the_pipeline_declares(checkout, pipeline):
    """The workspace name comes from the source now, and a pipeline that declares none gets nothing.

    bash wrote `pipeline-workspace` for every pipeline in the tree, including the three that declare no
    workspace at all, so the run of a Task-only pipeline asked for a volume that nowhere in the
    pipeline is mounted.
    """
    document = documents_of(generated_pipeline_run(checkout, pipeline))[0]
    source = documents_of(pipeline_source(checkout, pipeline))[0]
    declared = [workspace["name"]
                for workspace in (source.get("spec") or {}).get("workspaces") or []]

    assert [workspace["name"] for workspace in document["spec"].get("workspaces") or []] == declared


def test_a_generated_run_holds_no_leftover_placeholder(checkout):
    """What the substitution owes and what it pays, read off the written bytes.

    A leftover `STORAGE_CLASS_PLACEHOLDER` is a key that stopped being configured, and the generator's
    refusal rule is what catches it; this is the happy path's half of the same claim, so the file a
    team would commit is the file that is checked.
    """
    target = generated_pipeline_run(checkout, PIPELINE)

    assert "PLACEHOLDER" not in target.read_text()
    assert documents_of(target)[0]["spec"]["params"], "a run with no parameters proves nothing here"

def test_values_configuration_knows_arrive_and_values_it_cannot_are_marked(checkout):
    """The generator's two answers, kept apart.

    A parameter kubementat has a key for arrives filled, because leaving the token would put a word
    that only becomes a value at apply time in front of whoever reviews the file. A parameter it has no
    key for is marked rather than guessed: `builder-image` belongs to the app, and inventing a value for
    it is a run that silently builds the wrong thing.
    """
    values = parameters_of(documents_of(generated_pipeline_run(checkout, PIPELINE))[0])
    declared = set(declared_parameter_names(checkout, PIPELINE))

    assert declared & set(values) == declared
    for name in ("environment", "team", "docker-registry-base-url", "tekton-ci-image-name",
                 "tekton-ci-image-tag", "automation-git-project-name", "automation-git-revision",
                 "automation-git-server-host", "automation-git-server-port",
                 "automation-git-server-ssh-user", "automation-git-url"):
        if name in declared:
            assert values[name] not in ("", None), name
            assert "PLACEHOLDER" not in str(values[name]), name
            assert str(values[name]) != "null", name
    for name, value in values.items():
        if name not in SCRIPT_PARAMETERS:
            assert value == "TODO", f"{name} is not a parameter kubementat can name: {value!r}"


def test_a_run_generated_twice_writes_the_same_bytes_into_a_fresh_target(checkout):
    """Idempotence, measured as bytes: same pipeline, same configuration, same file.

    bash could not promise this at all, because it refused to run against an existing target — the
    second run, the one that happens after a review, needed an `rm` first. So the bytes are compared
    against a target that has been removed, which is the state a rerun reaches on its own.
    """
    first = generated_pipeline_run(checkout, PIPELINE)
    expected = first.read_bytes()

    first.unlink()
    second = generated_pipeline_run(checkout, PIPELINE)

    assert second == first
    assert second.read_bytes() == expected


# --- what the generator refuses -----------------------------------------------------------------


def test_an_existing_run_is_not_written_over(checkout):
    """The rule the two sibling generators dropped and this one keeps, deliberately.

    A generated deploy pipeline is a function of a template, so overwriting it loses nothing. A
    generated *run* is the starting point a team edits: the parameters kubementat cannot know are
    written into it for whoever generated it to fill in, so a rerun that overwrote them would delete a
    team's work without a word — which is why this generator's refusal rule is the one bash had.
    """
    from kubementat.errors import GenerationTargetExistsError

    generated_pipeline_run(checkout, PIPELINE)
    target = written_run(checkout, PIPELINE)
    target.write_text("adjusted by hand, by a team that knows what its app needs\n")

    with pytest.raises(GenerationTargetExistsError) as error:
        generated_pipeline_run(checkout, PIPELINE)

    assert target.read_text() == "adjusted by hand, by a team that knows what its app needs\n"
    assert str(target) in str(error.value)


def test_a_pipeline_file_that_is_not_there_names_the_path_it_reached_for(checkout):
    from kubementat.errors import GenerationInputNotFoundError

    missing = checkout.root / "tekton_ci/pipelines/no-such-pipeline.yml"

    with pytest.raises(GenerationInputNotFoundError) as error:
        utils(checkout).generate_pipeline_run(str(missing))

    assert "no-such-pipeline.yml" in str(error.value)
    assert not written_run(checkout, "no-such-pipeline").parent.joinpath("null-run.yml").exists()


@pytest.mark.parametrize("body, what", [
    ("apiVersion: tekton.dev/v1\nkind: Task\nmetadata:\n  name: a-task\nspec: {}\n", "Task"),
    ("apiVersion: tekton.dev/v1\nkind: Pipeline\nmetadata:\n  name: one\nspec: {}\n"
     "---\napiVersion: tekton.dev/v1\nkind: Pipeline\nmetadata:\n  name: two\nspec: {}\n",
     "2 documents"),
])
def test_a_file_that_is_not_one_pipeline_is_refused_by_name(checkout, body, what):
    """The two states bash accepted without comment.

    bash took whatever `yq` and `jq` handed it and interpolated the result, so a Task produced a run of
    something that cannot be started as a pipeline and a multi-document file produced a run of its
    first document, silently. Both wrote the file; both printed a success line.
    """
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/not-a-pipeline.yml"
    source.write_text(body)

    with pytest.raises(GenerationInputNotUsableError) as error:
        utils(checkout).generate_pipeline_run(str(source))

    assert what in str(error.value)
    assert not written_run(checkout, "not-a-pipeline").exists()


def test_a_pipeline_with_no_name_is_refused(checkout):
    """`jq -r '.metadata.name'` on a name that is not there prints `null`, and bash interpolated it.

    The run's own name and its `pipelineRef.name` both said `null`, the file was written, and the
    command printed its success line. Here the file at fault is named instead.
    """
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/nameless.yml"
    source.write_text("apiVersion: tekton.dev/v1\nkind: Pipeline\n"
                      "metadata:\n  labels:\n    what: nothing\n"
                      "spec:\n  params:\n    - name: docker-registry-base-url\n")

    with pytest.raises(GenerationInputNotUsableError) as error:
        utils(checkout).generate_pipeline_run(str(source))

    assert "nameless.yml" in str(error.value)
    assert not list(Path(checkout.root).rglob("null-run.yml"))


def test_a_configuration_key_that_is_gone_is_an_error_naming_it_and_writes_nothing(checkout):
    """The rule that makes this a generator rather than a heredoc that reads a file.

    bash wrote `STORAGE_CLASS_PLACEHOLDER` into the file whatever `static.json` said, so a key that had
    been renamed left a run that `run_pipeline` applied with the token still intact. Nothing is written
    here, and the message carries the key and the file it is read from.
    """
    from kubementat.errors import GenerationPlaceholderNotConfiguredError

    checkout.environment_static(TEKTON_KUBERNETES_STORAGE_CLASS=None)

    with pytest.raises(GenerationPlaceholderNotConfiguredError) as error:
        generated_pipeline_run(checkout, PIPELINE)

    assert "STORAGE_CLASS_PLACEHOLDER" in str(error.value)
    assert "static.json" in str(error.value)
    assert not written_run(checkout, PIPELINE).exists()


def test_a_parameter_declared_with_no_name_is_refused(checkout):
    """The second `null` bash was happy with.

    `jq -r '.spec.params[].name'` prints `null` for a parameter that declares no name, and the loop
    appended it: a run passing a parameter called `null` is a run the pipeline rejects, and bash printed
    the success line anyway. What makes this the harder of the two `null`s is that nothing downstream
    could have caught it — the run is otherwise well formed, and its file looks finished.
    """
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/nameless-parameter.yml"
    source.write_text("apiVersion: tekton.dev/v1\nkind: Pipeline\n"
                      "metadata:\n  name: nameless-parameter\n"
                      "spec:\n  params:\n    - default: nothing\n")

    with pytest.raises(GenerationInputNotUsableError) as error:
        utils(checkout).generate_pipeline_run(str(source))

    assert "nameless-parameter.yml" in str(error.value)
    assert not written_run(checkout, "nameless-parameter").exists()


# --- parity with the script ---------------------------------------------------------------------


def test_the_generated_run_carries_the_values_the_script_read_off_the_configuration(checkout):
    """The fields both sides had to get right, against the script's own output.

    bash read the storage class and the deployer account out of the `static.json` files with `jq` —
    except that it did not: it wrote the placeholder tokens and left `run_pipeline` to substitute them.
    So this compares what the two files *mean* once that substitution has happened, which is exactly
    what a run is: a document the same code path resolves before it applies it.
    """
    script_run = run_generator_script(checkout, PIPELINE)
    assert script_run.returncode == 0, script_run.stdout + script_run.stderr
    script_target = checkout.root / SCRIPT_OUTPUT_DIR / f"{PIPELINE}-run.yml"
    assert script_target.is_file(), "the script's record writes into the global directory"

    python_target = generated_pipeline_run(checkout, PIPELINE)
    script_document = resolved(checkout, documents_of(script_target)[0])
    python_document = documents_of(python_target)[0]

    assert script_document["apiVersion"] == python_document["apiVersion"]
    assert script_document["kind"] == python_document["kind"]
    assert script_document["metadata"] == python_document["metadata"]
    for field in ("pipelineRef", "taskRunTemplate", "timeouts", "workspaces"):
        assert script_document["spec"][field] == python_document["spec"][field], field


def test_both_halves_write_the_same_run_name_in_two_different_places(checkout):
    """The location difference, asserted rather than smoothed over.

    The name is what a team reads back, so it is compared as a string; the directory is the record
    against the rule, so it is compared as a difference — and asserting it is what keeps a later edit
    from quietly writing the output back into the tree that is ignored.
    """
    script_run = run_generator_script(checkout, PIPELINE)
    assert script_run.returncode == 0, script_run.stdout + script_run.stderr

    python_target = generated_pipeline_run(checkout, PIPELINE)

    assert python_target.name == f"{PIPELINE}-run.yml"
    assert (checkout.root / SCRIPT_OUTPUT_DIR / python_target.name).is_file()
    assert python_target.parent == checkout.root / TEAM_PIPELINE_RUN_DIR


def test_every_parameter_the_script_named_is_filled_the_same_way(checkout):
    """The parameters bash knew the name of, and the values it read for them.

    bash filled them from `static.json` through the placeholder tokens; the generator fills them from
    the same table, so agreeing here is agreeing about configuration rather than about text. What this
    does not compare is the parameter *set*, which is the one field the two halves genuinely disagree
    about — that is asserted above, against the pipeline itself, where a wrong answer is visible.
    """
    script_run = run_generator_script(checkout, PIPELINE)
    assert script_run.returncode == 0, script_run.stdout + script_run.stderr
    entries = raw_parameters_of(checkout.root / SCRIPT_OUTPUT_DIR / f"{PIPELINE}-run.yml")
    script_parameters = parameters_of(resolved(
        checkout, documents_of(checkout.root / SCRIPT_OUTPUT_DIR / f"{PIPELINE}-run.yml")[0]))
    python_parameters = parameters_of(documents_of(generated_pipeline_run(checkout, PIPELINE))[0])

    # The names the record wrote *once* are the names this test may compare, and that is not a way of
    # hiding the rest: the record wrote `tekton-ci-image-name` and `tekton-ci-image-tag` twice, once as
    # a value and once as `TODO`, so its own answer for those two is a contradiction rather than a
    # value. They are asserted as a difference in the list below, not smoothed over here — and a dict
    # that keeps the last entry would have said `TODO` and turned a real difference into a failing
    # string comparison, which is the sort of thing that gets a test "fixed" by deleting the claim.
    written_once = [name for name, count in Counter(entry["name"] for entry in entries).items()
                    if count == 1]
    shared = sorted(name for name in SCRIPT_PARAMETERS
                    if name in written_once and name in python_parameters)
    assert shared, "the two sides share not one parameter name, which is not what the script did"
    for name in shared:
        assert script_parameters[name] == python_parameters[name], name


def test_a_generated_run_is_not_invisible_to_a_git_status(checkout):
    """Generated configuration has to be seen, which is why it is not written under `tekton_ci/`.

    `git check-ignore` is the rule `git status` runs, so it is asked about real paths rather than about
    an opinion - and asking about this checkout's tree rather than the fixture's is deliberate: the rule
    under test is the repository's, not anything the generator can influence.

    What the bash half shows is weaker than the migration note's line that `tekton_ci/` is where output
    is ignored. The blanket rule exists for `tekton_ci/triggers/` and never for this directory;
    only `tekton_ci/pipeline-runs/smoke/` ever got a line, which is what a generated run that had to be
    tidied out of `git status` looks like afterwards. So this asks about the one ignored case that
    exists and about every path that has to stay visible.
    """
    for path in (f"{TEAM_PIPELINE_RUN_DIR}/{PIPELINE}-run.yml",
                 f"{SCRIPT_OUTPUT_DIR}/{PIPELINE}-run.yml"):
        visible = subprocess.run(["git", "check-ignore", "-q", path], cwd=str(REPO_ROOT))
        assert visible.returncode == 1, f"{path} would be invisible to a git status"

    swallowed = subprocess.run(["git", "check-ignore", "-q",
                               "tekton_ci/pipeline-runs/smoke/build-pipeline-ci-images-run.yml"],
                              cwd=str(REPO_ROOT))
    assert swallowed.returncode == 0, ("a run written under `tekton_ci/` is the kind that "
                                       "disappears from a status, which is the half of the move that "
                                       "has to hold")


# --- every difference the port claims, as a list --------------------------------------------------


def _difference_the_parameters_are_the_pipelines(checkout):
    record = record_writes(checkout, PIPELINE,
                           f"../../../tekton_ci/pipelines/{PIPELINE}.yml")
    assert record["result"].returncode == 0, record["output"]
    declared = declared_parameter_names(checkout, PIPELINE)
    written = [entry["name"] for entry in raw_parameters_of(record["target"])]

    # Its eleven, plus whatever else the pipeline declared, plus two names this pipeline never declared
    # at all: `environment` and `team` are in every run the script wrote, and the controller rejects a
    # run that passes what the pipeline never took.
    assert set(written) - set(declared) == {"environment", "team"}
    assert parameter_names(documents_of(generated_pipeline_run(checkout, PIPELINE))[0]) == declared


def _difference_a_parameter_is_passed_once(checkout):
    record = record_writes(checkout, PIPELINE,
                           f"../../../tekton_ci/pipelines/{PIPELINE}.yml")
    entries = raw_parameters_of(record["target"])
    twice = sorted(name for name, count in Counter(entry["name"] for entry in entries).items()
                   if count > 1)

    assert twice == ["tekton-ci-image-name", "tekton-ci-image-tag"]
    for name in twice:
        filled, todo = [entry for entry in entries if entry["name"] == name]
        assert filled["value"].endswith("_PLACEHOLDER") and todo["value"] == "TODO", name
    python_entries = raw_parameters_of(generated_pipeline_run(checkout, PIPELINE))
    python_values = {entry["name"]: entry["value"] for entry in python_entries}
    assert [entry["name"] for entry in python_entries].count("tekton-ci-image-name") == 1
    assert python_values["tekton-ci-image-name"] != "TODO"
    assert python_values["tekton-ci-image-tag"] != "TODO"


def _difference_the_workspaces_are_the_pipelines(checkout):
    for pipeline in ("hello-world-pipeline", "deploy-pipeline-helm-chart"):
        record = record_writes(checkout, pipeline, f"../../../tekton_ci/pipelines/{pipeline}.yml")
        assert [workspace["name"]
                for workspace in record["document"]["spec"].get("workspaces") or []] == [
                    "pipeline-workspace"], pipeline
        document = documents_of(generated_pipeline_run(checkout, pipeline))[0]
        assert document["spec"].get("workspaces") is None, pipeline


def _difference_the_api_version_is_read_and_not_written(checkout):
    record = record_writes(checkout, PIPELINE,
                           f"../../../tekton_ci/pipelines/{PIPELINE}.yml")
    assert record["document"]["apiVersion"] == "tekton.dev/v1", "the record's is a literal"

    config = utils(checkout).config
    assert documents_of(generated_pipeline_run(checkout, PIPELINE))[0][
        "apiVersion"] == f"{config.TEKTON_API_GROUP}/{config.TEKTON_API_VERSION}"


def _difference_a_file_that_is_not_a_pipeline_is_refused(checkout):
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/a-task.yml"
    source.write_text("apiVersion: tekton.dev/v1\nkind: Task\n"
                      "metadata:\n  name: a-task\n"
                      "spec:\n  params:\n    - name: some-thing\n")
    record = record_writes(checkout, "a-task", "../../../tekton_ci/pipelines/a-task.yml")

    # bash looked at the argument's existence and nothing else, so it wrote a run *of a Task* —
    # named, pointed at, timed out, and announced as generated.
    assert record["result"].returncode == 0, record["output"]
    assert record["document"]["spec"]["pipelineRef"]["name"] == "a-task"

    with pytest.raises(GenerationInputNotUsableError):
        utils(checkout).generate_pipeline_run(str(source))


def _difference_a_pipeline_that_declares_no_parameters_is_generated(checkout):
    source = checkout.root / "tekton_ci/pipelines/no-parameters.yml"
    source.write_text("apiVersion: tekton.dev/v1\nkind: Pipeline\n"
                      "metadata:\n  name: no-parameters\n"
                      "spec:\n  tasks: []\n")
    record = record_writes(checkout, "no-parameters",
                           "../../../tekton_ci/pipelines/no-parameters.yml")

    # `jq -r '.spec.params[].name'` errors on a `spec` that declares no parameters, and bash's `set -e`
    # took the script with it — after the loading had printed, before anything was written. A pipeline
    # that takes nothing is a real thing a team can run, so this is a refusal to generate at all.
    assert record["result"].returncode != 0, record["output"]
    assert "Cannot iterate over null" in record["output"], record["output"]
    assert not record["target"].exists(), "bash died before its redirect, so there is no file"

    document = documents_of(utils(checkout).generate_pipeline_run(str(source)))[0]
    assert document["spec"]["pipelineRef"]["name"] == "no-parameters"
    assert document["spec"]["params"] == []


def _difference_the_directory_is_created_and_not_demanded(checkout):
    shutil.rmtree(checkout.root / "tekton_ci/pipeline-runs")
    record = record_writes(checkout, PIPELINE,
                           f"../../../tekton_ci/pipelines/{PIPELINE}.yml")

    assert record["result"].returncode != 0, record["output"]
    assert not record["target"].exists(), "bash's redirect fails, and `set -e` stops the script there"
    assert generated_pipeline_run(checkout, PIPELINE).is_file()


def _difference_the_output_lands_where_the_readers_look(checkout):
    record = record_writes(checkout, PIPELINE,
                           f"../../../tekton_ci/pipelines/{PIPELINE}.yml")
    assert record["target"] == checkout.root / SCRIPT_OUTPUT_DIR / f"{PIPELINE}-run.yml"
    assert generated_pipeline_run(checkout, PIPELINE).parent == checkout.root / TEAM_PIPELINE_RUN_DIR


INTENDED_DIFFERENCES = [
    {"difference": "the parameters a run passes are the pipeline's, not the script's eleven",
     "why": "bash wrote the same eleven into every file and appended the rest, so a run of a pipeline "
            "that takes fewer parameters was a run the controller would not start.",
     "proven_by": _difference_the_parameters_are_the_pipelines},
    {"difference": "a parameter is passed once, with one value",
     "why": "bash's loop skipped nine of its own eleven names and not `tekton-ci-image-name` or "
            "`tekton-ci-image-tag`, so a pipeline that declared them got them twice, once filled and "
            "once as `TODO`.",
     "proven_by": _difference_a_parameter_is_passed_once},
    {"difference": "the workspaces are the pipeline's, not one name written for every pipeline",
     "why": "bash bound `pipeline-workspace` whatever the source declared, so the three pipelines in "
            "the tree that declare none asked for a volume nothing in them mounts.",
     "proven_by": _difference_the_workspaces_are_the_pipelines},
    {"difference": "the apiVersion is read from Config instead of recited",
     "why": "both answer `tekton.dev/v1` today. The record's came from a literal in the middle of a "
            "heredoc, so a version bump that missed this file would keep writing a shape no controller "
            "in this tree answers to.",
     "proven_by": _difference_the_api_version_is_read_and_not_written},
    {"difference": "a file that is not one named pipeline is refused",
     "why": "bash `cat`ed the argument, `jq`'d `metadata.name` out of it and interpolated the answer, "
            "so a `Task`, a multi-document file and a nameless pipeline each produced a written file "
            "and a success line.",
     "proven_by": _difference_a_file_that_is_not_a_pipeline_is_refused},
    {"difference": "a pipeline that declares no parameters is generated instead of aborted",
     "why": "its parameter loop was a `jq` over `.spec.params[]`, so a `Pipeline` that takes nothing "
            "made `jq` error, `set -e` stopped the script, and nothing was written for a pipeline that "
            "needs no parameters to run.",
     "proven_by": _difference_a_pipeline_that_declares_no_parameters_is_generated},
    {"difference": "the output directory is created instead of required",
     "why": "bash's `echo >` needed `tekton_ci/pipeline-runs/<TEAM>/` to be there already, and its "
            "`set -e` ended the run there — after the file had been opened, which is how a half-written "
            "run could survive a failed generation.",
     "proven_by": _difference_the_directory_is_created_and_not_demanded},
    {"difference": "the run is written where the run commands read",
     "why": "the script wrote under `Config.tekton_global_pipeline_run_dir`, which "
            "`list_available_pipeline_runs` does not look in and `run_pipeline` only reads through a "
            "`global/` prefix, so its own output was invisible to its own tool.",
     "proven_by": _difference_the_output_lands_where_the_readers_look},
]


def _difference_id(difference):
    return difference["difference"]


@pytest.mark.parametrize("difference", INTENDED_DIFFERENCES, ids=_difference_id)
def test_every_difference_this_port_claims_is_a_difference_in_the_output(difference, checkout):
    """The differences, listed once and proven by both halves' own bytes.

    A migration note that says "the two sides differ here" is an assertion; running the record against
    the generator and asserting the pair is the evidence. Listing them as data is what keeps the claim
    honest over time: a row that stops showing a difference — because someone made the two sides agree,
    or because one of them changed — fails, which is the same rule the claim is supposed to obey.

    Nothing here is a preference. Each row is a defect in the bash half that this ticket chose not to
    inherit, and each `why` says which.
    """
    difference["proven_by"](checkout)


# Every pipeline the checkout ships is a real input to this generator, and the nine that are here now
# are the ones whose parameter contracts differ enough to be worth testing: some declare `environment`
# and `team`, two declare no workspace, and one declares parameters nobody in `static.json` answers for.
# Every pipeline the *installed* kubementat ships is a real input here, not every pipeline a
# checkout happens to carry: this list drives a parametrize, so reading it out of the wrong tree
# would silently drop a pipeline from the run and still print green.
PIPELINES_ON_DISK = sorted(path.stem for path in tekton_ci_path("pipelines").glob("*.yml"))


@pytest.mark.parametrize("pipeline", PIPELINES_ON_DISK)
def test_every_parameter_a_pipeline_declares_is_in_the_run_generated_from_it(checkout, pipeline):
    """`spec.params[].name`, read off the source file and asked of the generated run, nine times.

    Those names are the whole contract between a pipeline and a run of it. One missing from the run is
    a parameter the pipeline fills from its own default or not at all, and one the run invents is a run
    the controller rejects — so the answer is exactly the declaration, in the pipeline's own order, for
    every pipeline the checkout has rather than for the one this ticket happened to copy out of bash.
    """
    generated = parameter_names(documents_of(generated_pipeline_run(checkout, pipeline))[0])
    declared = declared_parameter_names(checkout, pipeline)

    assert declared, f"{pipeline} declares no parameters, which is not what the tree says"
    assert set(declared) <= set(generated)
    assert generated == declared
