"""A generated `PipelineRun` is the run the pipeline asks for, written where the run commands read.

The generator is `generation.generate_pipeline_run()`, and it is the only way a run gets written. A run
is generated from three things — a pipeline from the package, the configuration as the layering resolves
it, and a name — and what comes out is the YAML a review is meant to read.

The rules this file exists for are the ones that make that review mean something:

* **one parameter per declared parameter, in the pipeline's own order.** A `PipelineRun` that passes a
  parameter its `Pipeline` never declared is rejected by the controller, so a generated run that guesses
  is a run that cannot start.
* **a parameter kubementat can name arrives filled; one it cannot is marked.** The names come from
  `RESOLVABLE_KEYS` and `CONFIG_PLACEHOLDER_SOURCES`; anything else — `builder-image`, which belongs to
  the app — is `<GENERATE_ME>`, because inventing a value for it is a run that silently builds the wrong
  thing.
* **the input is a pipeline, checked before anything is written.** A `Task`, a multi-document file and a
  file whose `metadata.name` is missing are three different refusals that name the file, because a run
  generated from any of them is a run of something nobody wrote.
* **the output is the team's directory**, `Config.tekton_team_pipeline_run_dir` — the one `run_pipeline`
  resolves a bare file name against and `list_available_pipeline_runs` prints first. A generated run that
  is reviewed and committed is a run the team can start by name.
* **an existing run is not written over**, which is the difference between regenerating a run and
  silently editing the one somebody is about to start.
* **the `apiVersion` is read from the configuration the install used**, not written as a literal, and a
  generated run is not invisible to a `git status`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT, run_kmt
from cluster_fixture import configuration_only_context
from asset_paths import tekton_ci_path
from kubementat.infrastructure.tekton import generation


PIPELINE = "build-pipeline-ci-images"

TEAM_PIPELINE_RUN_DIR = "platform_config/dev/fixture/tekton/pipeline-runs"

@pytest.fixture
def checkout(fixture_repo, dry_run_env):
    """The fixture checkout, with the product's own pipelines laid down in it.

    `fixture_repo` seeds scripts and configuration and no `tekton_ci` pipelines. The pipelines are
    tracked product assets, so the fixture copies the real ones from the package; a pipeline invented
    for the test would be a pipeline nobody generates a run of.

    """
    # The bytes come from inside the package and are laid down in the fixture checkout so the paths a
    # team would type resolve. The copy is fixture scaffolding, not a claim about where the product
    # reads them from.
    shutil.copytree(tekton_ci_path("pipelines"),
                    fixture_repo.root / "tekton_ci/pipelines", dirs_exist_ok=True)
    return fixture_repo


def generation_config(checkout):
    """The configuration the generator reads, with no `TektonUtils` in front of it.

    The generation group moved out of the class, so this is the parameter the ticket's
    conversion gave the function — what the old method read from the instance, and
    nothing else about the call changed.
"""
    return configuration_only_context(checkout.environment, checkout.team).config


def pipeline_source(checkout, pipeline):
    """The pipeline's file — one file, spelled two ways, which is what the comparison is about.

    The generator resolves what it is handed against the working directory, so the honest argument is
    the path a team would type: `tekton_ci/pipelines/<name>.yml` from the checkout root.
    """
    return checkout.root / "tekton_ci/pipelines" / f"{pipeline}.yml"


def generated_pipeline_run(checkout, pipeline):
    return generation.generate_pipeline_run(generation_config(checkout), str(pipeline_source(checkout, pipeline)))


def documents_of(path):
    """The documents of one manifest file, empty ones dropped the way `apply_resources` drops them."""
    return [document for document in yaml.safe_load_all(Path(path).read_text())
            if document is not None]


def parameter_names(document):
    return [parameter["name"] for parameter in document["spec"].get("params") or []]


def declared_parameter_names(checkout, pipeline):
    """The parameter names the pipeline itself declares, in the order it declares them."""
    return parameter_names(documents_of(pipeline_source(checkout, pipeline))[0])


def written_run(checkout, pipeline):
    """Where a run of `pipeline` would be written, for a test that expects nothing to be there."""
    return Path(checkout.root) / TEAM_PIPELINE_RUN_DIR / f"{pipeline}-run.yml"


# --- what the generator writes ------------------------------------------------------------------


def test_the_command_generates_a_run_and_lists_it_by_name_alone(checkout, dry_run_env):
    """Both halves of "reachable", run the way an operator runs them rather than imported.

    `kmt tekton generate-pipeline-run` is the command this ticket exists to add, and
    `kmt tekton run-pipeline <env> <team>` with no identifier prints what can be started. The script
    wrote into the global directory, where that listing does not look, so the file it generated never
    appeared in its own tool's answer.
    """
    generated = run_kmt(["tekton", "generate-pipeline-run", checkout.environment, checkout.team,
                         str(pipeline_source(checkout, PIPELINE))], dry_run_env)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    assert (checkout.root / TEAM_PIPELINE_RUN_DIR / f"{PIPELINE}-run.yml").is_file()

    listing = run_kmt(["tekton", "run-pipeline", checkout.environment, checkout.team], dry_run_env)

    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert f"{PIPELINE}-run.yml" in listing.stdout + listing.stderr


def test_a_run_generated_twice_writes_the_same_bytes_into_a_fresh_target(checkout):
    """Idempotence, measured as bytes: same pipeline, same configuration, same file.

    The second run is the one that follows a review, so the bytes are compared against a target that
    has been removed: that is the state a rerun into a clean tree reaches on its own.
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
    team's work without a word.
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
        generation.generate_pipeline_run(generation_config(checkout), str(missing))

    assert "no-such-pipeline.yml" in str(error.value)
    assert not written_run(checkout, "no-such-pipeline").parent.joinpath("null-run.yml").exists()


@pytest.mark.parametrize("body, what", [
    ("apiVersion: tekton.dev/v1\nkind: Task\nmetadata:\n  name: a-task\nspec: {}\n", "Task"),
    ("apiVersion: tekton.dev/v1\nkind: Pipeline\nmetadata:\n  name: one\nspec: {}\n"
     "---\napiVersion: tekton.dev/v1\nkind: Pipeline\nmetadata:\n  name: two\nspec: {}\n",
     "2 documents"),
])
def test_a_file_that_is_not_one_pipeline_is_refused_by_name(checkout, body, what):
    """A `Task` and a multi-document file are not pipelines, and neither is guessable.

    A run of a `Task` cannot be started as a pipeline, and a multi-document file names whichever
    document happened to come first. Both are well formed enough that the controller accepts whatever
    was generated from them, so the check is on the way in and it names the file at fault.
    """
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/not-a-pipeline.yml"
    source.write_text(body)

    with pytest.raises(GenerationInputNotUsableError) as error:
        generation.generate_pipeline_run(generation_config(checkout), str(source))

    assert what in str(error.value)
    assert not written_run(checkout, "not-a-pipeline").exists()


def test_a_pipeline_with_no_name_is_refused(checkout):
    """A pipeline with no `metadata.name` produces a run called `null`, which is a file, not an error.

    `pipelineRef.name` would carry the same four characters, and the controller accepts the run — so
    the refusal is here, and it names the file at fault.
    """
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/nameless.yml"
    source.write_text("apiVersion: tekton.dev/v1\nkind: Pipeline\n"
                      "metadata:\n  labels:\n    what: nothing\n"
                      "spec:\n  params:\n    - name: docker-registry-base-url\n")

    with pytest.raises(GenerationInputNotUsableError) as error:
        generation.generate_pipeline_run(generation_config(checkout), str(source))

    assert "nameless.yml" in str(error.value)
    assert not list(Path(checkout.root).rglob("null-run.yml"))


def test_a_configuration_key_that_is_gone_is_an_error_naming_it_and_writes_nothing(checkout):
    """The rule that makes this a generator rather than a heredoc that reads a file.

    A run applied with the token still intact starts with a storage class named
    `STORAGE_CLASS_PLACEHOLDER`, which is a binding that fails at pod scheduling rather than at
    review. Nothing is written here, and the message carries the key and the file it is read from.
    """
    from kubementat.errors import GenerationPlaceholderNotConfiguredError

    checkout.environment_static(TEKTON_KUBERNETES_STORAGE_CLASS=None)

    with pytest.raises(GenerationPlaceholderNotConfiguredError) as error:
        generated_pipeline_run(checkout, PIPELINE)

    assert "STORAGE_CLASS_PLACEHOLDER" in str(error.value)
    assert "static.json" in str(error.value)
    assert not written_run(checkout, PIPELINE).exists()


def test_a_parameter_declared_with_no_name_is_refused(checkout):
    """A parameter that declares no name is the harder `null`, because nothing downstream can catch it.

    The run is otherwise well formed and its file looks finished, and a run that passes a parameter
    called `null` is a run the pipeline rejects at start time.
    """
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/nameless-parameter.yml"
    source.write_text("apiVersion: tekton.dev/v1\nkind: Pipeline\n"
                      "metadata:\n  name: nameless-parameter\n"
                      "spec:\n  params:\n    - default: nothing\n")

    with pytest.raises(GenerationInputNotUsableError) as error:
        generation.generate_pipeline_run(generation_config(checkout), str(source))

    assert "nameless-parameter.yml" in str(error.value)
    assert not written_run(checkout, "nameless-parameter").exists()


# --- where the output is allowed to live --------------------------------------------------------


def test_a_generated_run_is_not_invisible_to_a_git_status(checkout):
    """Generated configuration has to be seen, which is why it is not written under `tekton_ci/`.

    `git check-ignore` is the rule `git status` runs, so it is asked about real paths rather than about
    an opinion - and asking about this checkout's tree rather than the fixture's is deliberate: the rule
    under test is the repository's, not anything the generator can influence.

    The repository's ignore rules are narrower than `tekton_ci/`: the blanket rule exists for
    `tekton_ci/triggers/`, and the only generated-run path ever given its own line was a smoke-run file
    that had to be tidied out of `git status`. So this asks about that one ignored case as well as about
    every path that has to stay visible.
    """
    for path in (f"{TEAM_PIPELINE_RUN_DIR}/{PIPELINE}-run.yml",
                 "tekton_ci/pipeline-runs/fixture/build-pipeline-ci-images-run.yml"):
        visible = subprocess.run(["git", "check-ignore", "-q", path], cwd=str(REPO_ROOT))
        assert visible.returncode == 1, f"{path} would be invisible to a git status"

    swallowed = subprocess.run(["git", "check-ignore", "-q",
                               "tekton_ci/pipeline-runs/smoke/build-pipeline-ci-images-run.yml"],
                              cwd=str(REPO_ROOT))
    assert swallowed.returncode == 0, ("a run written under `tekton_ci/` is the kind that "
                                       "disappears from a status, which is the half of the move that "
                                       "has to hold")


# --- every difference the port claims, as a list --------------------------------------------------


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
    every pipeline the checkout has rather than for one example.
    """
    generated = parameter_names(documents_of(generated_pipeline_run(checkout, pipeline))[0])
    declared = declared_parameter_names(checkout, pipeline)

    assert declared, f"{pipeline} declares no parameters, which is not what the tree says"
    assert set(declared) <= set(generated)
    assert generated == declared
