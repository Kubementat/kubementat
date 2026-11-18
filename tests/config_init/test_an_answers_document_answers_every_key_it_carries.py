"""`kmt config set --from-file PATH` answers a whole document, or refuses and writes nothing."""

import json
import time
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config.init import SKIPPED
from kubementat.core.config.config import Config

ENVIRONMENT = "prod"
TEAM = "alpha"

# Two keys of one document, chosen because they are read from two different places and so say
# something true about "every key the document carries": one is a plain answer a reader resolves, and
# one sits in the encrypted half next to the pair `kmt config init` invents.
FIRST_KEY = "PIPELINE_NAMESPACE"
SECOND_KEY = "AUTOMATION_GIT_URL"


def initialized_root(tmp_path) -> Path:
    """A tree `kmt config init` laid down for one environment — the precondition `set` insists on.
    """
    from kubementat.config.init import init_configuration
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    init_configuration(root, ENVIRONMENT)
    return root


def layered(root: Path, team=None) -> Config:
    """The `Config` a reader would use for that tree, loaded, because every assert is about the answer.
    """
    return Config(ENVIRONMENT, team, platform_config_dir=root, preload_configs=True)


def stamp(root: Path) -> dict:
    """Every file's mtime, so a refusal can be proved to have touched nothing rather than asserted.
    """
    return {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


def document_of(root: Path, relative: str) -> dict:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def answers_file(tmp_path, document) -> Path:
    """The document the operator's `--from-file` points at, on disk where it was told to be."""
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def run_verb(*arguments):
    """`kmt config <verb> …`, through the group so the verb's own name is exercised."""
    # The root is the caller's second argument and it travels as `--config-dir`, which is the
    # only spelling the surface gives a root, so what is checked here is the command somebody can
    # actually type and not a private argv this file invented.
    verb, root, *rest = arguments
    result = CliRunner(mix_stderr=False).invoke(
        config_module.config,
        [verb] + [str(argument) for argument in rest] + ["--config-dir", str(root)])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def printed(result) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.strip()]


def refused(*arguments) -> str:
    """Run expecting a refusal, and return what it said — refusing with a step on stdout fails here.
    """
    result = run_verb(*arguments)
    assert result.exit_code != 0, f"{result.stdout}{result.stderr}"
    assert not printed(result), f"the refusal printed steps on stdout: {result.stdout}"
    return f"{result.stdout}{result.stderr}"


def test_the_document_answers_in_the_order_it_was_written_in(tmp_path):
    """The file's key order follows the document's, which is what makes an order claim testable."""
    root = initialized_root(tmp_path)
    answers = answers_file(tmp_path, {SECOND_KEY: "https://git.example.invalid/k.git",
                                      FIRST_KEY: "ci"})

    result = run_verb("set", root, ENVIRONMENT, "--from-file", answers)
    assert result.exit_code == 0, f"{result.stdout}{result.stderr}"
    order = list(document_of(root, f"{ENVIRONMENT}/static.json"))
    assert order.index(SECOND_KEY) < order.index(FIRST_KEY), \
        f"the file answers {SECOND_KEY} before {FIRST_KEY}, which is not the document's order: {order}"


def test_a_key_the_document_answers_the_same_way_it_already_reads_is_not_reported_as_a_change(tmp_path):
    """`SKIPPED`, not `CREATED`: an answer that is already exactly there is not an event."""
    root = initialized_root(tmp_path)
    answers_file(tmp_path, {FIRST_KEY: "ci"})
    run_verb("set", root, ENVIRONMENT, "--from-file", tmp_path / "answers.json")
    file = root / ENVIRONMENT / "static.json"
    untouched = file.stat().st_mtime_ns

    result = run_verb("set", root, ENVIRONMENT, "--from-file", tmp_path / "answers.json")
    assert result.exit_code == 0, f"{result.stdout}{result.stderr}"
    assert printed(result) == [f"{SKIPPED} {root / ENVIRONMENT / 'static.json'}"], printed(result)
    assert file.stat().st_mtime_ns == untouched


def test_a_document_answering_a_key_with_null_is_refused_and_writes_nothing(tmp_path):
    """The refusal that matters most, because it is the one three quarters right on purpose."""
    root = initialized_root(tmp_path)
    run_verb("set", root, ENVIRONMENT, f"{FIRST_KEY}=before")
    answers = answers_file(tmp_path, {FIRST_KEY: "after", "GRAFANA_SERVICE_ACCOUNT_TOKEN": None})
    before = stamp(root)
    time.sleep(0.01)

    said = refused("set", root, ENVIRONMENT, "--from-file", answers)
    assert "null" in said and "unset" in said, said
    assert document_of(root, f"{ENVIRONMENT}/static.json")[FIRST_KEY] == "before", \
        "the document's first key landed while its second was refused"
    assert stamp(root) == before, "a refusal opened and closed the tree it said it would not write"


def test_an_unreadable_or_unstructured_document_is_refused_by_name(tmp_path):
    """Three shapes of nothing-to-write, each named: the path is the answer's half."""
    root = initialized_root(tmp_path)
    before = stamp(root)

    missing = refused("set", root, ENVIRONMENT, "--from-file", tmp_path / "nope.json")
    assert "nope.json" in missing, missing
    assert stamp(root) == before

    not_json = tmp_path / "not-json.json"
    not_json.write_text("PIPELINE_NAMESPACE: ci", encoding="utf-8")
    said = refused("set", root, ENVIRONMENT, "--from-file", not_json)
    assert "not JSON" in said, said

    not_an_object = tmp_path / "not-an-object.json"
    not_an_object.write_text(json.dumps(["PIPELINE_NAMESPACE=ci"]), encoding="utf-8")
    said = refused("set", root, ENVIRONMENT, "--from-file", not_an_object)
    assert "not an object" in said, said
    assert stamp(root) == before


def test_an_empty_document_says_it_answered_nothing_rather_than_applying_the_file(tmp_path):
    """An empty object is a document that wrote nothing, and silence here would read as success."""
    root = initialized_root(tmp_path)
    answers = answers_file(tmp_path, {})
    before = stamp(root)
    time.sleep(0.01)

    said = refused("set", root, ENVIRONMENT, "--from-file", answers)
    assert "empty" in said, said
    assert stamp(root) == before, "an empty document opened and closed the tree it said it wrote nothing to"


def test_the_document_and_an_answer_on_the_command_line_are_refused_together(tmp_path):
    """One answer per run, spelled three ways and settled once: argv, `--generate`, or document."""
    root = initialized_root(tmp_path)
    answers = answers_file(tmp_path, {FIRST_KEY: "ci"})
    before = stamp(root)

    said = refused("set", root, ENVIRONMENT, f"{SECOND_KEY}=other", "--from-file", answers)
    assert "--from-file" in said, said
    assert stamp(root) == before
    assert document_of(root, f"{ENVIRONMENT}/static.json").get(SECOND_KEY) is None
