"""The directory-to-cluster primitive: `kubementat.k8s_apply.apply_resources()`.

Everything Wave 1 and Wave 2 apply from a directory goes through this function — today the global
Tasks and Pipelines under `tekton_ci/` and the team's own two directories, tomorrow the generated
trigger configs `setup_triggers` reads out of `platform_config/{env}/{team}/tekton/triggers/`. So its
three blind spots were not three bugs in one function; they were three ways a resource somebody
wrote silently did not exist as far as kubementat was concerned:

1. `*.yaml` was not a resource file, and a `.yaml` was skipped.
2. Nothing below the given directory was scanned, so `tasks/ci/thing.yml` was skipped.
3. `metadata['name']` was dereferenced for the log line, so a manifest that was missing it crashed
   with a `KeyError` that named a Python internal instead of the file the operator has to edit.

The rules this file holds are the ones that replaced them, plus the one exclusion that keeps the
walk from being a regression: `templates/` is where generator inputs live, and a generator template
applied is a resource named after a placeholder.
"""

import logging
from pathlib import Path

import pytest

from kubementat import k8s_apply
from kubementat.k8s_apply import ResourceWithoutNameError
from asset_paths import tekton_ci_path

REPO_ROOT = Path(__file__).resolve().parents[1]

TASK = """apiVersion: tekton.dev/v1
kind: Task
metadata:
  name: {name}
spec:
  steps:
    - name: echo
      image: alpine
      command: ["echo", "hi"]
"""


class RecordingKubernetesUtils:
    """The one method `apply_resources` calls, recorded as the operation it performed."""

    def __init__(self):
        self.applied = []

    def create_or_replace_custom_resource(self, group, version, namespace, kind, body,
                                          plural_identifier=None):
        self.applied.append({
            "group": group, "version": version, "namespace": namespace, "kind": kind,
            "name": (body.get("metadata") or {}).get("name"),
            "declared_kind": body.get("kind"),
        })

    def names(self):
        return [entry["name"] for entry in self.applied]


def an_apply(directory):
    recorder = RecordingKubernetesUtils()
    k8s_apply.apply_resources(recorder, str(directory), "pipelines", "Task", "tekton.dev", "v1")
    return recorder


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# what a directory actually contains
# ---------------------------------------------------------------------------
def test_a_yaml_suffix_is_the_same_kind_of_file_as_a_yml_one(tmp_path):
    """.yaml is not a different format, and a team should not have to know kubementat's habits.

    Before this the file was invisible: no error, no log, no resource. The `skipping` branch that
    hid it is exactly the failure this file exists to delete, so it is asserted by what got applied
    rather than by what got logged.
    """
    write(tmp_path / "first.yml", TASK.format(name="first"))
    write(tmp_path / "second.yaml", TASK.format(name="second"))

    assert an_apply(tmp_path).names() == ["first", "second"]


def test_a_resource_filed_one_level_down_is_still_applied(tmp_path):
    """`tasks/ci/build.yml` is a normal thing for a team to have, and it used to be lost.

    The walk is sorted, so nesting does not make the application order depend on the filesystem —
    which matters because the order is what makes two runs of the same command comparable.
    """
    write(tmp_path / "plain.yml", TASK.format(name="plain"))
    write(tmp_path / "ci" / "nested.yml", TASK.format(name="nested"))
    write(tmp_path / "ci" / "deep" / "deeper.yaml", TASK.format(name="deep"))

    assert an_apply(tmp_path).names() == ["deep", "nested", "plain"]


def test_every_resource_file_is_applied_once_and_in_a_stable_order(tmp_path):
    """Two runs of the same command have to do the same thing in the same order.

    A `set` or an unsorted directory scan makes a rerun's log incomparable with itself, and that is
    the difference between a reproducible install and one you read the failures out of.
    """
    for name in ("delta", "alpha", "charlie", "bravo"):
        write(tmp_path / f"{name}.yml", TASK.format(name=name))

    first = an_apply(tmp_path).names()
    second = an_apply(tmp_path).names()

    assert first == second == sorted(first)


def test_the_metadata_that_matters_survives_the_walk(tmp_path):
    """Namespace, group, version and kind are what the caller declared, not what the file says.

    `apply_resources` is told which kind to create, and that is what the client is asked for — so
    a directory of the wrong kind still fails where it should, in the API server, rather than
    being applied into a kind nothing refers to.
    """
    write(tmp_path / "task.yml", TASK.format(name="one-task"))

    applied = an_apply(tmp_path).applied

    assert applied == [{"group": "tekton.dev", "version": "v1", "namespace": "pipelines",
                        "kind": "Task", "name": "one-task", "declared_kind": "Task"}]


def test_a_multi_document_file_applies_every_document_it_holds(tmp_path):
    """`---` separates resources, and a file that holds two applies two.

    bash had this behaviour and the rewrite kept it, which is the rarest kind of test: proof that a
    change did not quietly narrow what the tool reads.
    """
    write(tmp_path / "pair.yml",
          TASK.format(name="left") + "---\n" + TASK.format(name="right"))

    assert sorted(an_apply(tmp_path).names()) == ["left", "right"]


def test_an_empty_document_is_skipped_and_not_an_error(tmp_path, caplog):
    """A trailing `---` or a file that is only comments is empty, not broken.

    Skipping is not the same as ignoring: the log says so, because the other half of the failure
    this file is about was a resource that went missing with no trace of any kind.
    """
    write(tmp_path / "trailing.yml", "---\n\n# nothing here, just a note\n---\n")

    with caplog.at_level(logging.INFO):
        assert an_apply(tmp_path).applied == []
    assert "empty" in caplog.text.lower()


def test_a_list_document_is_skipped_rather_than_assumed_to_be_a_resource(tmp_path):
    """`- kind: Task` at the top level is not a manifest kubementat can name.

    It gets a warning and no crash; `KeyError` on `metadata` was the old answer, and it arrived as
    an exception on a file that could have been a comment block.
    """
    write(tmp_path / "wrong.yml", "- kind: Task\n  name: not-a-document\n")

    assert an_apply(tmp_path).applied == []


# ---------------------------------------------------------------------------
# the branch that used to be a KeyError
# ---------------------------------------------------------------------------
def test_a_resource_without_a_name_names_the_file_and_not_the_internals(tmp_path):
    """`metadata['name']` is not a lookup that may fail, it is what makes the object addressable.

    The old crash was a `KeyError: 'name'` from a log line. The message below says which file,
    which document, and which kind, which is everything a reader needs before they open the file.
    """
    write(tmp_path / "nameless.yml", "apiVersion: tekton.dev/v1\nkind: Task\nspec: {}\n")

    with pytest.raises(ResourceWithoutNameError) as error:
        an_apply(tmp_path)

    assert "nameless.yml" in str(error.value)
    assert "tekton.dev/v1" in str(error.value)
    assert "Task" in str(error.value)
    assert "metadata.name" in str(error.value)


def test_a_document_with_metadata_but_no_name_is_still_refused(tmp_path):
    """`metadata: {labels: …}` parses, has a dict, and still cannot be applied.

    The guard is on the name, not on the presence of `metadata`, because a manifest that is
    otherwise complete and nameless is exactly as unappliable as one with no metadata at all.
    """
    write(tmp_path / "anonymous.yml",
          "apiVersion: tekton.dev/v1\nkind: Task\nmetadata:\n  labels:\n    app: x\nspec: {}\n")

    with pytest.raises(ResourceWithoutNameError) as error:
        an_apply(tmp_path)

    assert "metadata.name" in str(error.value)


def test_the_nameless_document_does_not_stop_an_earlier_resource_from_reaching_the_cluster(
        tmp_path):
    """What a refusal means for the files scanned before it: they were already sent.

    Recorded rather than asserted as a guarantee, because the honest answer is that a directory
    with one bad file leaves the good ones applied. Making a directory atomic would be a different
    product decision, and nothing in the plan asked for one.
    """
    write(tmp_path / "1-good.yml", TASK.format(name="good"))
    write(tmp_path / "2-nameless.yml", "apiVersion: tekton.dev/v1\nkind: Task\nspec: {}\n")

    recorder = RecordingKubernetesUtils()

    with pytest.raises(ResourceWithoutNameError):
        k8s_apply.apply_resources(recorder, str(tmp_path), "pipelines", "Task", "tekton.dev", "v1")

    assert recorder.names() == ["good"]


# ---------------------------------------------------------------------------
# what the walk must not eat
# ---------------------------------------------------------------------------
def test_a_templates_directory_is_not_a_resource_directory(tmp_path):
    """Generator inputs are not resources, and applying one produces a placeholder-named object.

    `tekton_ci/pipelines/templates/template-deploy-pipeline.yml` is the file that makes this
    decision real: it is valid YAML, it declares a kind, and its name is
    `deploy-pipeline-APP_NAME_PLACEHOLDER`. Before the walk was recursive it was never read; after,
    it would be — so the exclusion is what keeps a recursion fix from being an install regression.
    """
    write(tmp_path / "real.yml", TASK.format(name="real"))
    write(tmp_path / "templates" / "template-thing.yml", TASK.format(name="template-thing"))
    write(tmp_path / "nested" / "templates" / "template-other.yml",
          TASK.format(name="template-other"))

    assert an_apply(tmp_path).names() == ["real"]


def test_the_shipped_asset_roots_have_no_template_inside_a_resource_root_it_is_applied_from():
    """The recursion this file just bought, checked against the tree it now has to walk.

    `setup_pipelines` applies `Config.tekton_task_asset_dir` and `Config.tekton_pipeline_asset_dir`
    unchanged, so if anything under those two roots were a generator template, this method's new
    recursion would install it. One check does the whole job: the scan of both roots, minus the
    exclusion, names every file the old flat glob found and nothing else — asked of the resolver the
    install path uses, since these are the manifests that ship inside the package and the
    checkout no longer carries them.
    """
    for root in ("tasks", "pipelines"):
        directory = tekton_ci_path(root)
        found = k8s_apply._resource_files(directory)
        flat = sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml"))

        assert found == sorted(found), f"{root}: the walk is not ordered"
        assert all(part != k8s_apply.GENERATOR_TEMPLATE_DIRECTORY
                   for path in found for part in path.relative_to(directory).parts[:-1]), \
            f"{root}: a template inside a resource root would be applied"
        assert set(flat) <= set(found), f"{root}: the flat glob found files the walk missed"


def test_a_resource_directory_that_is_not_there_says_so(tmp_path):
    """A path built out of configuration is not the same as a directory that is empty.

    The old `os.listdir` raised its own `FileNotFoundError`; now it is a message that quotes the
    path kubementat constructed, which is the half an operator cannot see when a team directory is
    named by a configuration value.
    """
    missing = tmp_path / "platform_config" / "dev" / "ghost" / "tekton" / "tasks"

    with pytest.raises(FileNotFoundError) as error:
        an_apply(missing)

    assert str(missing) in str(error.value)


def test_a_resource_applied_twice_is_a_replacement_and_not_an_error(tmp_path):
    """The 409 branch of this primitive, which is the same branch the install path has.

    `apply_resources` asks for create-or-replace, so running `kmt tekton setup-pipelines` twice is
    not two errors. This asserts the primitive's half — the same file, twice, no exception — and
    the API's own 409 handling is `test_kubernetes_resource_helpers.py`.
    """
    write(tmp_path / "task.yml", TASK.format(name="once"))
    recorder = RecordingKubernetesUtils()

    k8s_apply.apply_resources(recorder, str(tmp_path), "pipelines", "Task", "tekton.dev", "v1")
    k8s_apply.apply_resources(recorder, str(tmp_path), "pipelines", "Task", "tekton.dev", "v1")

    assert recorder.names() == ["once", "once"]


def test_the_primitive_keeps_reading_both_suffixes_it_was_offered(tmp_path):
    """The suffix set is a constant now, so a future `.YAML` is a decision and not an accident."""
    assert k8s_apply.RESOURCE_FILE_SUFFIXES == (".yml", ".yaml")
    assert k8s_apply.GENERATOR_TEMPLATE_DIRECTORY == "templates"
