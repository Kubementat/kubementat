r"""A Secret's data entries are written from a flat JSON file, by a command and not by a shell.

`kmt create-secret-from-json <ENVIRONMENT> <NAMESPACE> <SECRET_NAME> <JSON_FILE_PATH>`
replaces `scripts/secret_management/create_secret_from_json.sh`, whose only caller was
`tekton_ci/tasks/create-secret-from-json.yml`. What the script ran was:

    kubectl -n "$NAMESPACE" create secret generic --dry-run=client "$SECRET_NAME" -o yaml \
        --from-env-file <(echo "$JSON_STRING" | jq -r "to_entries|map(\"\(.key)=\(.value|tostring)\")|.[]")
    | kubectl apply -n "$NAMESPACE" -f -

so the shape of the answer is set by that: a `generic` Secret is type `Opaque`, each key of the
document is one data entry, and a value is the string `jq`'s `tostring` would have put on the
env-file line. The three arguments after the environment are the script's three arguments, in the
same order, because the Task's params hand over exactly those and no more — and the environment in
front of them is what the script and the Task together never had to say: the Secret went to
whatever cluster the step's kubeconfig was pointing at.

Three differences are recorded here rather than papered over, and all three are the command being
better-behaved and not a regression:

* **bash wrote no labels.** `kubectl create secret generic` puts nothing on the object, so a Secret
  this Task created and a Secret an operator created by hand were indistinguishable. The command
  sends kubementat's `managed-by` claim, the same one every other Secret-writing caller sends
  (`tekton_utils._trigger_labels`, `CLEANUP_JOB_LABELS`).
* **a nested value is refused.** The script piped every key through `jq tostring` and got something
  out whatever it was: `[object Object]` on the `jq` that predates 1.7, compact JSON after. Which
  spelling a pod had to read back was a function of the image, so the command names the key and
  declines.
* **the file is read here, not by the Task.** The old Task step did `cat $JSON_FILE_LOCATION` and
  passed the *contents* as an argument, which put the whole document in the step's argv. The command
  takes the path.

Nothing below runs a pod. Every assertion is against the `recorded_api` stand-in, so what is proved
is what the command asks the cluster for and what it says when the file cannot answer first. That
the Task reaches this command inside a pipeline pod is unverified on this host and stays labelled
that way in the Task's own comments.
"""

import json

import pytest
from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from cli.commands import kubernetes
from conftest import FIXTURE_ENVIRONMENT


@pytest.fixture(autouse=True)
def _an_environment_the_command_can_resolve(resolvable_environment):
    """The command resolves a cluster before it writes, so this module needs one declared.

    `@kmt_context` runs ahead of the body, so a test that has no cluster to reach still has
    to have a `platform_config` that says which cluster it is not reaching. Nothing here reads the
    answer — the Secret is addressed by the namespace the operator typed — so the fixture that
    declares a cluster and nothing else is the right size, and the write the test asserts stays the
    only thing the command does.
    """
    return resolvable_environment

NAMESPACE = "dev1-pipelines"
SECRET_NAME = "terraform-main-automation-secret"


def write_json(directory, document, name="secrets.json"):
    """The file an operator would have handed the script, as a path."""
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def run(json_file_path):
    """The command, on four positionals and nothing else.

    The environment is passed explicitly rather than left to `ENVIRONMENT`, because the shape under
    test is the argv and not the fallback.
    """
    return CliRunner().invoke(kubernetes.create_secret_from_json,
                              [FIXTURE_ENVIRONMENT, NAMESPACE, SECRET_NAME, json_file_path])


def body_of(recorded_api, method="create_namespaced_secret"):
    """The Secret the cluster was handed, from the one call the command makes."""
    calls = [kwargs for name, kwargs in recorded_api.calls if name == method]
    assert len(calls) == 1, f"{method} was called {len(calls)} times; the command writes one Secret"
    return calls[0]["body"]


def test_every_key_of_the_file_lands_in_the_secret_as_a_string(tmp_path, recorded_api):
    """The whole of the script's `to_entries`/`map`, in one call that cannot get the format wrong.

    A string is written as itself, because that is what `tostring` did with a string.
    """
    result = run(write_json(tmp_path, {"TF_VAR_region": "eu-central-1",
                                       "TF_VAR_shortname": "a-b",
                                       "TF_VAR_project": "team-a"}))

    assert result.exit_code == 0, result.output
    body = body_of(recorded_api)
    assert body.string_data == {"TF_VAR_region": "eu-central-1",
                                "TF_VAR_shortname": "a-b",
                                "TF_VAR_project": "team-a"}


def test_a_number_is_written_as_its_own_text_and_not_as_a_python_number(tmp_path, recorded_api):
    """`123` becomes `"123"`, `1.5` becomes `"1.5"`, `-3` becomes `"-3"`.

    `str(int)` and `str(float)` are what `jq tostring` answers for those two, and a tfvars file is
    mostly this branch of the dispatch.
    """
    result = run(write_json(tmp_path, {"count": 123, "ratio": 1.5, "delta": -3}))

    assert result.exit_code == 0, result.output
    assert body_of(recorded_api).string_data == {"count": "123", "ratio": "1.5", "delta": "-3"}


def test_a_boolean_is_written_the_way_a_program_reads_it_and_not_the_way_python_writes_it(
        tmp_path, recorded_api):
    """`true`/`false` and not `True`/`False`, which is the trap `str()` walks straight into.

    `jq` prints `true`, `kubectl` puts `true` in the env file, and a terraform boolean variable
    that arrives as `True` is a variable that stopped being a boolean.
    """
    result = run(write_json(tmp_path, {"TF_VAR_enabled": True, "TF_VAR_disabled": False}))

    assert result.exit_code == 0, result.output
    assert body_of(recorded_api).string_data == {"TF_VAR_enabled": "true",
                                                 "TF_VAR_disabled": "false"}


def test_a_null_is_written_as_the_word_null_and_not_as_the_word_none(tmp_path, recorded_api):
    """Same trap, second shape: `jq tostring` on `null` is `null`, Python's is `None`.

    A tfvars file that sets a variable to null is a file that means "no value", and the word that
    says that is the one the program on the other end of the Secret already expects.
    """
    result = run(write_json(tmp_path, {"TF_VAR_unset": None}))

    assert result.exit_code == 0, result.output
    assert body_of(recorded_api).string_data == {"TF_VAR_unset": "null"}


def test_the_secret_is_generic_and_carries_kubementats_label(tmp_path, recorded_api):
    """`generic` is `Opaque`, and the label is the half bash never wrote.

    Both halves are in the object that leaves, so a test that reads the object sees both. The label
    is what tells an operator that this Secret came from the platform, which is a fact the Task
    could not state while it was running the script.
    """
    result = run(write_json(tmp_path, {"TF_VAR_region": "eu-central-1"}))

    assert result.exit_code == 0, result.output
    body = body_of(recorded_api)
    assert body.type == "Opaque"
    assert body.metadata.labels == {"managed-by": "kubementat"}


def test_the_secret_is_named_and_addressed_by_the_arguments_the_command_took(
        tmp_path, recorded_api):
    """The namespace is the second argument and the name is the third, not a guess from config.

    There is no `--team` and no `--type` on this command, so nothing resolves a namespace out of
    `platform_config`: what the operator typed is what the call is addressed at. The first argument
    is the environment, which decides the cluster and not the namespace.
    """
    result = run(write_json(tmp_path, {"a": "b"}))

    assert result.exit_code == 0, result.output
    call = [kwargs for name, kwargs in recorded_api.calls if name == "create_namespaced_secret"][0]
    assert call["namespace"] == NAMESPACE
    assert body_of(recorded_api).metadata.name == SECRET_NAME


def test_an_existing_secret_is_replaced_and_not_left_alone(tmp_path, recorded_api):
    """The 409 path, which is the path that re-runs a pipeline and expects the new values.

    `create --dry-run=client | kubectl apply` was the script's way of getting this, and the
    primitive's way is a create that catches 409 and replaces. Same result, no `kubectl` in the
    middle, and a replace that sends the object the file describes.
    """
    recorded_api.answer("create_namespaced_secret", ApiException(status=409, reason="Conflict"))

    result = run(write_json(tmp_path, {"TF_VAR_region": "eu-central-1"}))

    assert result.exit_code == 0, result.output
    replaced = body_of(recorded_api, method="replace_namespaced_secret")
    assert replaced.string_data == {"TF_VAR_region": "eu-central-1"}
    assert replaced.metadata.labels == {"managed-by": "kubementat"}


def test_a_nested_object_names_the_key_it_refuses_and_writes_nothing(tmp_path, recorded_api):
    """The one input the script would have accepted and could not have delivered.

    `'{"a": {"b": 1}}'` gave the script an env-file line of `a=[object Object]` on one `jq` and
    `a={"b":1}` on the other. Neither is a value a program can read, so what was a silent
    difference between two images is one sentence here, and it names the key so the operator can
    find it in the file.
    """
    result = run(write_json(tmp_path, {"TF_VAR_nested": {"b": 1}, "TF_VAR_flat": "x"}))

    assert result.exit_code != 0
    assert "TF_VAR_nested" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == [], "the refusal must come before the cluster is asked"


def test_a_nested_array_is_refused_for_the_same_reason(tmp_path, recorded_api):
    """A list is a JSON value and not a data entry, and `jq` spelled it `[1,2]`."""
    result = run(write_json(tmp_path, {"TF_VAR_list": [1, 2]}))

    assert result.exit_code != 0
    assert "TF_VAR_list" in result.output, result.output
    assert recorded_api.calls == []


def test_a_file_that_is_not_there_says_so_in_one_line(tmp_path, recorded_api):
    """The script took the JSON as a string and so could not fail this way; the command can.

    The path is the argument now, which means the file's absence is the failure a typo makes, and
    it is the failure the operator has to be able to read without a traceback.
    """
    result = run(str(tmp_path / "no-such-file.json"))

    assert result.exit_code != 0
    assert "no-such-file.json" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_json_that_does_not_parse_says_which_line_it_broke_on(tmp_path, recorded_api):
    """A missing comma in a file a human edits is the ordinary case, and it used to be a stack trace."""
    path = tmp_path / "broken.json"
    path.write_text('{"a": 1, "b"}', encoding="utf-8")

    result = run(str(path))

    assert result.exit_code != 0
    assert "broken.json" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_a_document_that_is_not_an_object_says_so(tmp_path, recorded_api):
    """A top-level array is valid JSON and has no keys, so there is nothing to write.

    The script would have handed this to `jq to_entries`, which errors on a non-object, and the
    `set -eu` in the middle of it made that an exit 1 with a `jq` message. Here it is the same
    answer in the command's own words.
    """
    path = tmp_path / "list.json"
    path.write_text('["a", "b"]', encoding="utf-8")

    result = run(str(path))

    assert result.exit_code != 0
    assert "list.json" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_an_empty_object_is_refused_because_writing_it_is_the_dangerous_case(tmp_path,
                                                                            recorded_api):
    """`{}` is a valid flat document, and this command creates *or replaces*.

    bash sent `jq`'s empty output to `kubectl`, which is the one place the two paths could diverge
    against the operator rather than for them: a Secret that exists keeps its data, and a Secret
    that does not gets a replacement written over it with nothing in it. Refusing is the only
    answer that is safe whichever way the run lands, and it says which one it is protecting.
    """
    result = run(write_json(tmp_path, {}))

    assert result.exit_code != 0
    assert "no keys" in result.output, result.output
    assert "Traceback" not in result.output, result.output
    assert recorded_api.calls == []


def test_a_cluster_answer_that_is_not_409_becomes_a_sentence_and_not_a_frame(tmp_path,
                                                                            recorded_api):
    """The namespace is not there, or the name is not a name, and the operator has to know which.

    The primitive re-raises everything the cluster said that was not already-there, and that answer
    is the useful part. A traceback would say the same thing one screen lower.
    """
    recorded_api.answer("create_namespaced_secret", ApiException(status=404, reason="Not Found"))

    result = run(write_json(tmp_path, {"a": "b"}))

    assert result.exit_code != 0
    assert SECRET_NAME in result.output and NAMESPACE in result.output, result.output
    assert "Traceback" not in result.output, result.output


def test_the_command_takes_four_positionals_and_no_flags():
    """No `--team`, no `--type`: what the script took is what the command takes, plus the
    environment every cluster-touching command puts in front of its arguments.

    A flag naming the environment would be a second way to say which cluster the Secret lands in,
    reached by somebody who read the CLI's help instead of the Task's params — which is why it is an
    argument here and an argument there too, and not both. The Task is the only caller the migration
    had to keep working.

    The two options it does carry are not that flag: `--context`/`--kubeconfig` say which *cluster*
    the write goes to, which is what the first layer of the resolution order is for, and `--config-dir`
    says which
    *configuration tree* the write is decided from — neither one names a namespace, a team, or a
    Secret. The arguments stay the first four things in the list, so the Task's four params still
    land where the script's three arguments landed — with the environment in front of them.
    """
    from click.core import Argument, Option

    params = kubernetes.create_secret_from_json.params

    assert [param.name for param in params] == [
        "environment", "namespace", "secret_name", "json_file_path",
        "context", "kubeconfig", "config_dir"]
    assert all(isinstance(param, Argument) for param in params[:4]), \
        "a flag on this command's arguments is a second way to name the namespace it was given"
    assert all(isinstance(param, Option) for param in params[4:]), \
        "the cluster the command resolves to is a flag, not a positional an old caller would send"


def test_the_command_prints_one_line_about_what_it_wrote(tmp_path, recorded_api):
    """What landed, where it landed, and which keys. Nothing else.

    The script printed the YAML it had just generated and then a table of every Secret in the
    namespace. The first is the command's work rather than the operator's question, and the second
    is a read this command was never asked to do — so a Task step's log is one line, and a Secret's
    name is in it.

    It is counted on stdout because that is what the Task captures: the resolution report goes
    to stderr precisely so a command whose answer is a line or a file keeps that shape.
    """
    result = CliRunner(mix_stderr=False).invoke(
        kubernetes.create_secret_from_json,
        [FIXTURE_ENVIRONMENT, NAMESPACE, SECRET_NAME,
         write_json(tmp_path, {"TF_VAR_region": "eu-central-1",
                               "TF_VAR_count": 3})])

    assert result.exit_code == 0, result.output + result.stderr
    assert result.stdout.count("\n") == 1, result.stdout
    assert SECRET_NAME in result.output and NAMESPACE in result.output
    assert "TF_VAR_count" in result.output and "TF_VAR_region" in result.output
