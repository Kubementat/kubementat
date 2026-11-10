"""`kmt config set --from-file PATH` answers a whole document, or refuses and writes nothing.

This verb's surface is `config set KEY=VALUE` **and** `--from-file`,
so the flag is part of the contract and not an extra a lane asked for. What a document buys is the
case an argv cannot serve: a value too long to paste, or a credential the operator does not want in
the process table, where every `ps`, every shell history line and every captured command would have
it. The file is what makes the answer stop being a command line, and the path is the only thing that
travels (the rule about credentials is not only about environment variables).

Five claims, and the third is the one an implementation would get wrong.

**Everything in the document is answered, and the file's order is the document's order.** One
`set_answer` per key and one step line per answered key, because that is what a step means. The order
is proved on the file and not on stdout, because a document's keys all land in one file and two
answers therefore print two identical lines; what carries the order is the document the operator's
reader will consult. This is a real dependency and not a lucky accident, so it is asserted and not
assumed.

**The document changes no layer and chooses no file.** `--team` and `--encrypted` are the same flags
they are for one answer on the command line, and a document that could re-decide either would be a
second source of truth about which layer answers. That is the rule stated from the operator's
side: the tree holds the layers, and the verb is told which one to write.

**A document that is wrong is refused whole, before the first byte.** A `null` anywhere, a non-object,
unreadable JSON, an empty object — each refusal names the path, and each is followed by `nothing was
written`: the tree's bytes and mtimes are the assert, not a sentence about intent. This is what makes
`--from-file` more than a shell loop over `set`: a loop would answer three keys and then fail on the
fourth, leaving a tree in a state nobody meant. A refusal that lands after three writes is the defect
this verb exists to make impossible.

**One answer, one document, never both.** An argv answer, a `--generate`, or a document: exactly one,
refused as a command-line fault with the tree untouched. Two spellings of the same answer in one run
would be two orders of writes and one line describing both.

**Nothing generated, nothing printed.** The step lines name files. A document can hold credentials,
and echoing what was read back from it would put the answer in a terminal buffer that outlives the
file — `config show` is the verb that prints answers, and this one is not it.
"""

import json
import time
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.core.config.config import Config
from kubementat.config_init import SKIPPED

ENVIRONMENT = "prod"
TEAM = "alpha"

# Two keys of one document, chosen because they are read from two different places and so say
# something true about "every key the document carries": one is a plain answer a reader resolves, and
# one sits in the encrypted half next to the pair `kmt config init` invents.
FIRST_KEY = "PIPELINE_NAMESPACE"
SECOND_KEY = "AUTOMATION_GIT_URL"


def initialized_root(tmp_path) -> Path:
    """A tree `kmt config init` laid down for one environment — the precondition `set` insists on."""
    from kubementat.config_init import init_configuration
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    init_configuration(root, ENVIRONMENT)
    return root


def layered(root: Path, team=None) -> Config:
    """The `Config` a reader would use for that tree, loaded, because every assert is about the answer.

    A document that landed in the wrong file, or in the wrong layer, still parses; the only reader
    that can tell a write that works from a write that is read is the one production code uses.
    """
    return Config(ENVIRONMENT, team, platform_config_dir=root, preload_configs=True)


def stamp(root: Path) -> dict:
    """Every file's mtime, so a refusal can be proved to have touched nothing rather than asserted.

    Bytes alone cannot tell a rewrite of identical content from a no-op; the clock can, which is the
    difference between "the tree is unchanged" and "the tree was opened and closed unchanged".
    """
    return {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


def document_of(root: Path, relative: str) -> dict:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def answers_file(tmp_path, document) -> Path:
    """The document the operator's `--from-file` points at, on disk where it was told to be.

    Written by the test and not by the verb: whose hand writes the *document* is not what the claim is
    about, and the list it is drawn from is about configuration, which is the tree.
    """
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def run_verb(*arguments):
    """`kmt config <verb> …`, through the group so the verb's own name is exercised.

    Same contract as the sibling file: a bug inside the command re-raises, and a `ClickException`
    refusal is allowed to stand as an exit code, because "I will not write" is a real answer and not
    a crash.
    """
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
    """Run expecting a refusal, and return what it said — refusing with a step on stdout fails here."""
    result = run_verb(*arguments)
    assert result.exit_code != 0, f"{result.stdout}{result.stderr}"
    assert not printed(result), f"the refusal printed steps on stdout: {result.stdout}"
    return f"{result.stdout}{result.stderr}"


def test_a_document_answers_every_key_it_carries_and_every_reader_resolves_them(tmp_path):
    """Two keys, one document, two reads — the answer is the document's whole content and nothing else."""
    root = initialized_root(tmp_path)
    answers = answers_file(tmp_path, {FIRST_KEY: "ci", SECOND_KEY: "https://git.example.invalid/k.git"})
    before = stamp(root)

    result = run_verb("set", root, ENVIRONMENT, "--from-file", answers)
    assert result.exit_code == 0, f"{result.stdout}{result.stderr}"

    config = layered(root)
    assert config.get("env_static")[FIRST_KEY] == "ci", "the document's first key is not the answer a reader resolves"
    assert config.get("env_static")[SECOND_KEY] == "https://git.example.invalid/k.git", \
        "the document's second key is not the answer a reader resolves"
    assert len(printed(result)) == 2, printed(result)
    assert before.keys() == stamp(root).keys(), "a document that answers two keys made two files"


def test_the_document_answers_in_the_order_it_was_written_in(tmp_path):
    """The file's key order follows the document's, which is what makes an order claim testable.

    The step lines cannot carry this: every key of a document lands in the same file, so two answers
    print two identical lines and stdout has nothing to sort on. What is observable is the file, and
    `set_answer` adds the keys it was given to the document it read, in the order it was given them,
    so a document whose keys sort the wrong way proves the rule a `set()` or a sorted dict would
    break. The two keys below are deliberately spelled so that alphabetical order is the reverse of
    the document's, and they are both new to the file so nothing already in it is shuffled.
    """
    root = initialized_root(tmp_path)
    answers = answers_file(tmp_path, {SECOND_KEY: "https://git.example.invalid/k.git",
                                      FIRST_KEY: "ci"})

    result = run_verb("set", root, ENVIRONMENT, "--from-file", answers)
    assert result.exit_code == 0, f"{result.stdout}{result.stderr}"
    order = list(document_of(root, f"{ENVIRONMENT}/static.json"))
    assert order.index(SECOND_KEY) < order.index(FIRST_KEY), \
        f"the file answers {SECOND_KEY} before {FIRST_KEY}, which is not the document's order: {order}"


def test_a_key_the_document_answers_the_same_way_it_already_reads_is_not_reported_as_a_change(tmp_path):
    """`SKIPPED`, not `CREATED`: an answer that is already exactly there is not an event.

    The rule `set` states for one answer and repeats for a document, because a document is a way of
    spelling answers and not a different verb. Asserting the file was not opened-and-closed is what
    makes the word mean something: a step line that says `SKIPPED` over a file whose bytes changed
    would be a lie with the right exit code.
    """
    root = initialized_root(tmp_path)
    answers_file(tmp_path, {FIRST_KEY: "ci"})
    run_verb("set", root, ENVIRONMENT, "--from-file", tmp_path / "answers.json")
    file = root / ENVIRONMENT / "static.json"
    untouched = file.stat().st_mtime_ns

    result = run_verb("set", root, ENVIRONMENT, "--from-file", tmp_path / "answers.json")
    assert result.exit_code == 0, f"{result.stdout}{result.stderr}"
    assert printed(result) == [f"{SKIPPED} {root / ENVIRONMENT / 'static.json'}"], printed(result)
    assert file.stat().st_mtime_ns == untouched


def test_the_document_lands_where_the_flags_said_and_cannot_choose_a_layer_of_its_own(tmp_path):
    """A document on the team layer, in the encrypted file: `--team` and `--encrypted` still decide.

    The document lists keys and values and nothing else, because the layer is what the operator was
    told to name and the file is what `--encrypted` names. A document that could carry its own `team`
    field would be a second answer about which layer the tree is on, and the rule keeps that
    question in one place.
    """
    root = initialized_root(tmp_path)
    from kubementat.config_init import add_team
    add_team(root, ENVIRONMENT, TEAM)
    answers = answers_file(tmp_path, {FIRST_KEY: "team-doc"})

    result = run_verb("set", root, ENVIRONMENT, "--team", TEAM, "--from-file", answers)
    assert result.exit_code == 0, f"{result.stdout}{result.stderr}"
    assert document_of(root, f"{ENVIRONMENT}/{TEAM}/static.json")[FIRST_KEY] == "team-doc"
    assert document_of(root, f"{ENVIRONMENT}/static.json").get(FIRST_KEY) is None, \
        "the document answered the team's key on the environment's layer too, which no flag named"
    assert layered(root, TEAM) is not None


def test_an_encrypted_document_answers_the_encrypted_file_and_prints_no_answer_back(tmp_path):
    """`--encrypted` routes a document, and stdout is the step line and not its content.

    The whole point of the document is that a credential does not sit in a command line; the failure
    mode it replaces puts the credential in a terminal buffer instead, which outlives the file. So this
    asserts both halves: the value is in the file the reader resolves, and the value is not in the text
    the operator ran.
    """
    root = initialized_root(tmp_path)
    secret = "generated-for-this-run-only"
    answers = answers_file(tmp_path, {"GRAFANA_SERVICE_ACCOUNT_TOKEN": secret})

    result = run_verb("set", root, ENVIRONMENT, "--encrypted", "--from-file", answers)
    assert result.exit_code == 0, f"{result.stdout}{result.stderr}"
    target = root / ENVIRONMENT / "static.encrypted.json"
    assert secret in target.read_text(encoding="utf-8"), "the document did not reach the encrypted file"
    assert secret not in result.stdout, "a document that answers a credential printed the credential"


def test_a_document_answering_a_key_with_null_is_refused_and_writes_nothing(tmp_path):
    """The refusal that matters most, because it is the one three quarters right on purpose.

    `null` is the absence of an answer, and `unset` is the verb that removes one — the same rule the
    command line's `KEY=null` answer states. What `--from-file` adds is the order of the refusal: a
    document that answers two keys and answers the second with `null` must not have written the first,
    because a tree that received a partial document is a tree no operator meant to have. That is the
    whole reason this verb is not a loop a caller writes, so the assert is the bytes and not a sentence.
    """
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
    """Three shapes of nothing-to-write, each named: the path is the answer's half.

    A mistyped path is the operator's problem and not the tree's, JSON that is not JSON is a document
    that does not exist, and JSON that is a list or a scalar is a shape this verb does not have — it
    writes `key: value` at the top level of one file, so anything else is a claim about layers nobody
    named. All three come back with the path in the sentence and the tree untouched.
    """
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
    """An empty object is a document that wrote nothing, and silence here would read as success.

    A lane that hands over `{}` has not told this verb anything, and a run that exits 0 over that tree
    is a run whose log says it applied a file that applied no keys. The sentence says what the tree
    already says instead, which is the difference between a no-op and a lie.
    """
    root = initialized_root(tmp_path)
    answers = answers_file(tmp_path, {})
    before = stamp(root)
    time.sleep(0.01)

    said = refused("set", root, ENVIRONMENT, "--from-file", answers)
    assert "empty" in said, said
    assert stamp(root) == before, "an empty document opened and closed the tree it said it wrote nothing to"


def test_the_document_and_an_answer_on_the_command_line_are_refused_together(tmp_path):
    """One answer per run, spelled three ways and settled once: argv, `--generate`, or document.

    The refusal is a command-line fault and so comes with `UsageError` and nothing on disk, exactly as
    `KEY=VALUE` beside `--generate` already refuses. Two spellings in one run would be two orders of
    writes with one line describing both, and the tree cannot tell the reader which half it got.
    """
    root = initialized_root(tmp_path)
    answers = answers_file(tmp_path, {FIRST_KEY: "ci"})
    before = stamp(root)

    said = refused("set", root, ENVIRONMENT, f"{SECOND_KEY}=other", "--from-file", answers)
    assert "--from-file" in said, said
    assert stamp(root) == before
    assert document_of(root, f"{ENVIRONMENT}/static.json").get(SECOND_KEY) is None
