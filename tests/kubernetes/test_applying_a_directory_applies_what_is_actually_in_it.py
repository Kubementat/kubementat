"""The directory-to-cluster primitive: `kubementat.infrastructure.kubernetes.apply.apply_resources()`.
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
    """Two runs of the same command have to do the same thing in the same order."""
    for name in ("delta", "alpha", "charlie", "bravo"):
        write(tmp_path / f"{name}.yml", TASK.format(name=name))

    first = an_apply(tmp_path).names()
    second = an_apply(tmp_path).names()

    assert first == second == sorted(first)


def test_an_empty_document_is_skipped_and_not_an_error(tmp_path, caplog):
    """A trailing `---` or a file that is only comments is empty, not broken."""
    write(tmp_path / "trailing.yml", "---\n\n# nothing here, just a note\n---\n")

    with caplog.at_level(logging.INFO):
        assert an_apply(tmp_path).applied == []
    assert "empty" in caplog.text.lower()


def test_a_list_document_is_skipped_rather_than_assumed_to_be_a_resource(tmp_path):
    """`- kind: Task` at the top level is not a manifest kubementat can name."""
    write(tmp_path / "wrong.yml", "- kind: Task\n  name: not-a-document\n")

    assert an_apply(tmp_path).applied == []


# ---------------------------------------------------------------------------
# the branch that used to be a KeyError
# ---------------------------------------------------------------------------
def test_a_resource_without_a_name_names_the_file_and_not_the_internals(tmp_path):
    """`metadata['name']` is not a lookup that may fail, it is what makes the object addressable."""
    write(tmp_path / "nameless.yml", "apiVersion: tekton.dev/v1\nkind: Task\nspec: {}\n")

    with pytest.raises(ResourceWithoutNameError) as error:
        an_apply(tmp_path)

    assert "nameless.yml" in str(error.value)
    assert "tekton.dev/v1" in str(error.value)
    assert "Task" in str(error.value)
    assert "metadata.name" in str(error.value)


def test_a_resource_directory_that_is_not_there_says_so(tmp_path):
    """A path built out of configuration is not the same as a directory that is empty."""
    missing = tmp_path / "platform_config" / "dev" / "ghost" / "tekton" / "tasks"

    with pytest.raises(FileNotFoundError) as error:
        an_apply(missing)

    assert str(missing) in str(error.value)


def test_a_resource_applied_twice_is_a_replacement_and_not_an_error(tmp_path):
    """The 409 branch of this primitive, which is the same branch the install path has."""
    write(tmp_path / "task.yml", TASK.format(name="once"))
    recorder = RecordingKubernetesUtils()

    apply.apply_resources(recorder, str(tmp_path), "pipelines", "Task", "tekton.dev", "v1")
    apply.apply_resources(recorder, str(tmp_path), "pipelines", "Task", "tekton.dev", "v1")

    assert recorder.names() == ["once", "once"]
