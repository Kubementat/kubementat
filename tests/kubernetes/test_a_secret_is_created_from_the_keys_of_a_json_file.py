"""A Secret's data entries are written from a flat JSON file, by a command and not by a shell."""

import json

import pytest
from click.testing import CliRunner
from conftest import FIXTURE_ENVIRONMENT
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes


@pytest.fixture(autouse=True)
def _an_environment_the_command_can_resolve(resolvable_environment):
    """The command resolves a cluster before it writes, so this module needs one declared."""
    return resolvable_environment

NAMESPACE = "dev1-pipelines"
SECRET_NAME = "terraform-main-automation-secret"


def write_json(directory, document, name="secrets.json"):
    """The file an operator would have handed the script, as a path."""
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def run(json_file_path):
    """The command, on four positionals and nothing else."""
    return CliRunner().invoke(kubernetes.create_secret_from_json,
                              [FIXTURE_ENVIRONMENT, NAMESPACE, SECRET_NAME, json_file_path])


def body_of(recorded_api, method="create_namespaced_secret"):
    """The Secret the cluster was handed, from the one call the command makes."""
    calls = [kwargs for name, kwargs in recorded_api.calls if name == method]
    assert len(calls) == 1, f"{method} was called {len(calls)} times; the command writes one Secret"
    return calls[0]["body"]


def test_a_boolean_is_written_the_way_a_program_reads_it_and_not_the_way_python_writes_it(
        tmp_path, recorded_api):
    """`true`/`false` and not `True`/`False`, which is the trap `str()` walks straight into."""
    result = run(write_json(tmp_path, {"TF_VAR_enabled": True, "TF_VAR_disabled": False}))

    assert result.exit_code == 0, result.output
    assert body_of(recorded_api).string_data == {"TF_VAR_enabled": "true",
                                                 "TF_VAR_disabled": "false"}


def test_a_null_is_written_as_the_word_null_and_not_as_the_word_none(tmp_path, recorded_api):
    """Same trap, second shape: `jq tostring` on `null` is `null`, Python's is `None`."""
    result = run(write_json(tmp_path, {"TF_VAR_unset": None}))

    assert result.exit_code == 0, result.output
    assert body_of(recorded_api).string_data == {"TF_VAR_unset": "null"}


def test_an_existing_secret_is_replaced_and_not_left_alone(tmp_path, recorded_api):
    """The 409 path, which is the path that re-runs a pipeline and expects the new values."""
    recorded_api.answer("create_namespaced_secret", ApiException(status=409, reason="Conflict"))

    result = run(write_json(tmp_path, {"TF_VAR_region": "eu-central-1"}))

    assert result.exit_code == 0, result.output
    replaced = body_of(recorded_api, method="replace_namespaced_secret")
    assert replaced.string_data == {"TF_VAR_region": "eu-central-1"}
    assert replaced.metadata.labels == {"managed-by": "kubementat"}


def test_a_nested_object_names_the_key_it_refuses_and_writes_nothing(tmp_path, recorded_api):
    """The one input the script would have accepted and could not have delivered."""
    result = run(write_json(tmp_path, {"TF_VAR_nested": {"b": 1}, "TF_VAR_flat": "x"}))

    assert result.exit_code != 0
    assert "TF_VAR_nested" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == [], "the refusal must come before the cluster is asked"


def test_a_file_that_is_not_there_says_so_in_one_line(tmp_path, recorded_api):
    """The script took the JSON as a string and so could not fail this way; the command can."""
    result = run(str(tmp_path / "no-such-file.json"))

    assert result.exit_code != 0
    assert "no-such-file.json" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_json_that_does_not_parse_says_which_line_it_broke_on(tmp_path, recorded_api):
    """A missing comma in a file a human edits is the ordinary case, and it used to be a stack trace.
    """
    path = tmp_path / "broken.json"
    path.write_text('{"a": 1, "b"}', encoding="utf-8")

    result = run(str(path))

    assert result.exit_code != 0
    assert "broken.json" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_a_document_that_is_not_an_object_says_so(tmp_path, recorded_api):
    """A top-level array is valid JSON and has no keys, so there is nothing to write."""
    path = tmp_path / "list.json"
    path.write_text('["a", "b"]', encoding="utf-8")

    result = run(str(path))

    assert result.exit_code != 0
    assert "list.json" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_an_empty_object_is_refused_because_writing_it_is_the_dangerous_case(tmp_path,
                                                                            recorded_api):
    """`{}` is a valid flat document, and this command creates *or replaces*."""
    result = run(write_json(tmp_path, {}))

    assert result.exit_code != 0
    assert "no keys" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_a_cluster_answer_that_is_not_409_becomes_a_sentence_and_not_a_frame(tmp_path,
                                                                            recorded_api):
    """The namespace is not there, or the name is not a name, and the operator has to know which."""
    recorded_api.answer("create_namespaced_secret", ApiException(status=404, reason="Not Found"))

    result = run(write_json(tmp_path, {"a": "b"}))

    assert result.exit_code != 0
    assert SECRET_NAME in result.output and NAMESPACE in result.output, result.output
    assert "Traceback" not in result.output, result.output


def test_the_command_prints_one_line_about_what_it_wrote(tmp_path, recorded_api):
    """What landed, where it landed, and which keys. Nothing else."""
    result = CliRunner(mix_stderr=False).invoke(
        kubernetes.create_secret_from_json,
        [FIXTURE_ENVIRONMENT, NAMESPACE, SECRET_NAME,
         write_json(tmp_path, {"TF_VAR_region": "eu-central-1",
                               "TF_VAR_count": 3})])

    assert result.exit_code == 0, result.output + result.stderr
    assert result.stdout.count("\n") == 1, result.stdout
    assert SECRET_NAME in result.output and NAMESPACE in result.output
    assert "TF_VAR_count" in result.output and "TF_VAR_region" in result.output
