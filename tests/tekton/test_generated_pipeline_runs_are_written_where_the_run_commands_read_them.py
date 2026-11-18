"""A generated `PipelineRun` is the run the pipeline asks for, written where the run commands read.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from asset_paths import tekton_ci_path
from cluster_fixture import configuration_only_context
from asset_paths import REPO_ROOT
from conftest import run_kmt

from kubementat.infrastructure.tekton import generation

PIPELINE = "build-pipeline-ci-images"

TEAM_PIPELINE_RUN_DIR = "platform_config/dev/fixture/tekton/pipeline-runs"

@pytest.fixture
def checkout(fixture_repo, dry_run_env):
    """The fixture checkout, with the product's own pipelines laid down in it."""
    # The bytes come from inside the package and are laid down in the fixture checkout so the paths a
    # team would type resolve. The copy is fixture scaffolding, not a claim about where the product
    # reads them from.
    shutil.copytree(tekton_ci_path("pipelines"),
                    fixture_repo.root / "tekton_ci/pipelines", dirs_exist_ok=True)
    return fixture_repo


def generation_config(checkout):
    """The configuration the generator reads, with no `TektonUtils` in front of it."""
    return configuration_only_context(checkout.environment, checkout.team).config


def pipeline_source(checkout, pipeline):
    """The pipeline's file — one file, spelled two ways, which is what the comparison is about."""
    return checkout.root / "tekton_ci/pipelines" / f"{pipeline}.yml"


def generated_pipeline_run(checkout, pipeline):
    return generation.generate_pipeline_run(generation_config(checkout), str(pipeline_source(checkout, pipeline)))


def documents_of(path):
    """The documents of one manifest file, empty ones dropped the way `apply_resources` drops them.
    """
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
    """Both halves of "reachable", run the way an operator runs them rather than imported."""
    generated = run_kmt(["tekton", "generate-pipeline-run", checkout.environment, checkout.team,
                         str(pipeline_source(checkout, PIPELINE))], dry_run_env)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    assert (checkout.root / TEAM_PIPELINE_RUN_DIR / f"{PIPELINE}-run.yml").is_file()

    listing = run_kmt(["tekton", "run-pipeline", checkout.environment, checkout.team], dry_run_env)

    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert f"{PIPELINE}-run.yml" in listing.stdout + listing.stderr


def test_a_run_generated_twice_writes_the_same_bytes_into_a_fresh_target(checkout):
    """Idempotence, measured as bytes: same pipeline, same configuration, same file."""
    first = generated_pipeline_run(checkout, PIPELINE)
    expected = first.read_bytes()

    first.unlink()
    second = generated_pipeline_run(checkout, PIPELINE)

    assert second == first
    assert second.read_bytes() == expected


# --- what the generator refuses -----------------------------------------------------------------


def test_an_existing_run_is_not_written_over(checkout):
    """The rule the two sibling generators dropped and this one keeps, deliberately."""
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
    """A `Task` and a multi-document file are not pipelines, and neither is guessable."""
    from kubementat.errors import GenerationInputNotUsableError

    source = checkout.root / "tekton_ci/pipelines/not-a-pipeline.yml"
    source.write_text(body)

    with pytest.raises(GenerationInputNotUsableError) as error:
        generation.generate_pipeline_run(generation_config(checkout), str(source))

    assert what in str(error.value)
    assert not written_run(checkout, "not-a-pipeline").exists()


def test_a_pipeline_with_no_name_is_refused(checkout):
    """A pipeline with no `metadata.name` produces a run called `null`, which is a file, not an error.
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
    """The rule that makes this a generator rather than a heredoc that reads a file."""
    from kubementat.errors import GenerationPlaceholderNotConfiguredError

    checkout.environment_static(TEKTON_KUBERNETES_STORAGE_CLASS=None)

    with pytest.raises(GenerationPlaceholderNotConfiguredError) as error:
        generated_pipeline_run(checkout, PIPELINE)

    assert "STORAGE_CLASS_PLACEHOLDER" in str(error.value)
    assert "static.json" in str(error.value)
    assert not written_run(checkout, PIPELINE).exists()


def test_a_parameter_declared_with_no_name_is_refused(checkout):
    """A parameter that declares no name is the harder `null`, because nothing downstream can catch it.
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
    """Generated configuration has to be seen, which is why it is not written under `tekton_ci/`."""
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
    """`spec.params[].name`, read off the source file and asked of the generated run, nine times."""
    generated = parameter_names(documents_of(generated_pipeline_run(checkout, pipeline))[0])
    declared = declared_parameter_names(checkout, pipeline)

    assert declared, f"{pipeline} declares no parameters, which is not what the tree says"
    assert set(declared) <= set(generated)
    assert generated == declared
