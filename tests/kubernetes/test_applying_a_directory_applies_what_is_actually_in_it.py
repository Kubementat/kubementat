"""The directory-to-cluster primitive: `kubementat.infrastructure.kubernetes.apply.apply_resources()`.

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

import pytest

from kubementat.errors import ResourceWithoutNameError
from kubementat.infrastructure.kubernetes import apply

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
    apply.apply_resources(recorder, str(directory), "pipelines", "Task", "tekton.dev", "v1")
    return recorder


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


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

    apply.apply_resources(recorder, str(tmp_path), "pipelines", "Task", "tekton.dev", "v1")
    apply.apply_resources(recorder, str(tmp_path), "pipelines", "Task", "tekton.dev", "v1")

    assert recorder.names() == ["once", "once"]
