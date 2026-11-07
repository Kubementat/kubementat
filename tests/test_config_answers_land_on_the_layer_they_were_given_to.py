"""`kmt config set` / `config unset` put one answer on one layer, and nothing else.

Census H's measured fact is what these verbs are first at: **nothing in `src/` used to edit one key
inside a `static.json`.** Every writer before them built a whole document — `initialize`'s four
writers `json.load` the *template* and emit the file, and `init_configuration`/`add_environment`/
`add_team` plan whole bytes per path. A read-modify-write verb has the opposite duty, and it is the
one these tests are about: **the document belongs to the operator, and every key this verb was not
told about keeps its value and its position.**

Six claims, and the fifth is the one that breaks a "simpler" implementation.

**One answer, one layer, read back through `Config`.** `set` writes the key into one file of one
layer, and the layer that declares it answers it. Where the same key is declared twice it is answered
*by the team*, which is precedence and not presence: `CATEGORY_LAYERS['team_static']` is team-then-
environment (`core/config/config.py:103-111`), so an environment answer sitting below a team answer is
invisible,
not redundant. `unset` is the other half of that — clearing the team's answer is what lets the
environment's answer appear, which is the contract stated here and the reason the verb exists.

**The pair rule is honest, not decorative.** One layer is two files, and the encrypted one is ordered
first, so a `set` writes the file the operator chose (`--encrypted` says which) while an `unset` clears
**both**: clearing only the plain file is a no-op on the read side, and the operator would watch the
value they just removed come back.

**Idempotence here is a different rule from `init`'s, and the two must not be confused.** `init`
refuses an existing *file* that differs, because an edited answer and a half-written file are not
tellable apart. `set` overwrites an *answer* that differs — that is what the verb is for — and reports
`SKIPPED` when the new document is byte-identical, because setting a value that is already there is not
an event.

**A missing layer is a refusal.** `kmt config init`, `add-environment` and `add-team` make layers;
`set` and `unset` write into them, and a team file that "just isn't there" would be answered out of the
environment. Same anti-bootstrap claim, same nothing-is-written.

**One refusal about keys, and it is the cheap true one.** The two cluster keys on `--team` are refused
for one reason — one directory is exactly one cluster, and what resolves it reads the environment's
file and no other (`EnvironmentRegistry.resolve` and `_environment_declaration`, both in
`core/identity/registry.py`). No table of "keys a team reader consults"
appears anywhere, because `config show` already answers that by tagging each key with the layer and the
file it came from, and this verb would be guessing about every call site in `src/` to hold one.

Everything is proved through `Config` where `Config` can read it, through the tree where the claim is
about the tree, and through `Config`'s path properties where the claim is about *where* — a string typed
into this file would tell a moved property from a moved directory badly.
"""

import json
import logging
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.core.config import derived as config_derived
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.config_init import (CREATED, ENVIRONMENT_ENCRYPTED_STEP, ENVIRONMENT_STATIC_STEP,
                                    SKIPPED, TEAM_ENCRYPTED_STEP, TEAM_STATIC_STEP,
                                    init_configuration, set_answer, unset_answer)

ENVIRONMENT = "prod"
TEAM = "alpha"

# The key every precedence test uses. `PIPELINE_NAMESPACE` is read by the pipeline layer, is not a
# cluster key, and is the shape of answer `config set --team` exists to make, so it is an honest
# example rather than a name invented for this file.
KEY = "PIPELINE_NAMESPACE"
ENVIRONMENT_ANSWER = "team-ci"
TEAM_ANSWER = "team-alpha"

# What `kmt config init` lays down, spelled relative to the root so a set difference reads as a write
# set and not as a guess about the layout.
INITIALIZED_LAYER = {Path("kubementat.yaml"), Path(ENVIRONMENT),
                     Path(ENVIRONMENT) / "static.json", Path(ENVIRONMENT) / "static.encrypted.json",
                     Path(ENVIRONMENT) / "tekton", Path(ENVIRONMENT) / "tekton" / "tasks",
                     Path(ENVIRONMENT) / "tekton" / "pipelines"}

ENV_STATIC = Path(ENVIRONMENT) / "static.json"
ENV_ENCRYPTED = Path(ENVIRONMENT) / "static.encrypted.json"
TEAM_STATIC = Path(ENVIRONMENT) / TEAM / "static.json"
TEAM_ENCRYPTED = Path(ENVIRONMENT) / TEAM / "static.encrypted.json"


def config_root(tmp_path) -> Path:
    """A configuration directory that exists and holds nothing."""
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    return root


def initialized_root(tmp_path) -> Path:
    """A tree `kmt config init` made for one environment — the precondition both verbs insist on."""
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT)
    return root


def layered(root: Path, team=None) -> Config:
    """The `Config` a reader would use for that tree — the same construction the library makes.

    `preload=True`, because every assertion here is about what a *loaded* configuration answers, and
    the point of reading through `Config` is that the layer order is the one in `core/config/config.py` and
not the
    one this file believes in.
    """
    return Config(ENVIRONMENT, team, platform_config_dir=root, preload_configs=True)


def properties(root: Path, team=None) -> Config:
    """The same `Config`, unloaded — for its paths, which is what a step's line must be checked against.

    The paths are `Config`'s and are resolved by `Config`, so comparing a printed line against one of
    them says "this is where a reader looks" and not "this is the string in the message".
    """
    return Config(ENVIRONMENT, team, platform_config_dir=root, preload_configs=False)


def tree(root: Path) -> set[Path]:
    """Everything under `root`, relative to it. Directories included: they are part of the tree."""
    assert root.is_dir(), f"{root} is not a directory, so there is nothing to enumerate"
    return {path.relative_to(root) for path in root.rglob("*")}


def files(root: Path) -> dict[Path, bytes]:
    """Every file's bytes, keyed by its path under the root."""
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def stamp(root: Path) -> dict[Path, int]:
    """Every file's mtime, for the claim that a verb wrote one file and left the rest alone."""
    return {path.relative_to(root): path.stat().st_mtime_ns
            for path in root.rglob("*") if path.is_file()}


def document(root: Path, relative: Path) -> dict:
    """A layer file read as JSON. For the two claims that are about the file itself: order, and keys
    that belong to someone else. Every other assertion goes through `Config`."""
    return json.loads((root / relative).read_text(encoding="utf-8"))


def run_verb(*arguments):
    """`kmt config <verb> …`, invoked through the group so the verb's own name is exercised.

    A bug inside the command is re-raised rather than folded into an exit code, and a refusal is not:
    the library raises a `ClickException` to say "I will not write", and standing up as `SystemExit` is
    how that answer reaches a terminal. The distinction is the whole test for the refusals, so a raised
    `ValueError` cannot hide behind `exit_code == 1`.
    """
    result = CliRunner(mix_stderr=False).invoke(
        config_module.config, [str(argument) for argument in arguments])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def printed(result) -> list[str]:
    """The stdout lines, which are the steps and nothing else. Stderr is guidance, not output."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def reported(result) -> str:
    return f"{result.stdout}{result.stderr}"


def refused(*arguments) -> str:
    """Run a verb expecting a refusal, and return the sentence it said.

    A refusal that wrote stdout is caught here rather than asserted around: refusing is the answer that
    comes with `nothing is written`, and a verb that prints steps and then errors is the half-written
    tree this module keeps promising not to make.
    """
    result = run_verb(*arguments)
    assert result.exit_code != 0, reported(result)
    assert not printed(result), f"the refusal printed steps on stdout: {reported(result)}"
    return reported(result)


def added_team(root: Path) -> None:
    """The team layer, made the way the surface makes it: `kmt config add-team`."""
    result = run_verb("add-team", root, ENVIRONMENT, TEAM)
    assert result.exit_code == 0, reported(result)


# --- (1) the environment's file answers, read back through `Config` --------------------------------


def test_set_on_the_environment_layer_lands_in_that_file_and_answers_a_read(tmp_path, caplog):
    """Test 1: one answer, the environment layer, and a `Config` read that agrees without a grep.

    The read is what makes this a behaviour test and not a file-format test. A team-less reader asks
    for `env_static`, which is the category the chain resolves when no team is named
    (`CATEGORY_LAYERS['env_static']` is one layer deep): asking `get('team_static')` without a team is
    a `TeamNotSuppliedError` and not a quiet empty answer, so the assertion names the category a
    team-less read is allowed to ask for rather than the one the brief's prose used.
    """
    root = initialized_root(tmp_path)

    # The sentence is logged and not echoed, so it is read out of `caplog`. Cleared first because
    # `initialized_root` logged it already, and this assertion is about the `set` run below saying it.
    with caplog.at_level(logging.WARNING):
        caplog.clear()
        result = run_verb("set", root, ENVIRONMENT, f"{KEY}={ENVIRONMENT_ANSWER}")

    assert result.exit_code == 0, reported(result)
    assert printed(result) == [f"{CREATED} {properties(root).env_static_config_path}"], printed(result)
    assert document(root, ENV_STATIC)[KEY] == ENVIRONMENT_ANSWER
    assert layered(root).get('env_static')[KEY] == ENVIRONMENT_ANSWER, \
        "the environment file declares the key but a loaded Config does not answer it"
    # An environment layer that reaches no cluster still says so, under the same condition `init` and
    # `add-environment` say it under: this verb changed nothing about that null.
    assert "does not address a cluster yet" in caplog.text
    assert tree(root) == INITIALIZED_LAYER, "one answer is one key, not one answer plus one directory"


# --- (2) precedence, asserted by who answers -------------------------------------------------------


def test_the_same_key_answered_on_the_team_layer_is_answered_by_the_team(tmp_path):
    """Test 2: team over environment, by precedence and not by presence.

    Both files declare the key, and both keep their own answer; the loaded read is the team's. That is
    the whole difference between "the value is in the tree" and "the value is what you get".
    """
    root = initialized_root(tmp_path)
    run_verb("set", root, ENVIRONMENT, f"{KEY}={ENVIRONMENT_ANSWER}")
    added_team(root)
    before = files(root)

    result = run_verb("set", root, ENVIRONMENT, f"{KEY}={TEAM_ANSWER}", "--team", TEAM)

    assert printed(result) == [f"{CREATED} {properties(root, TEAM).team_static_config_path}"], \
        printed(result)
    assert document(root, TEAM_STATIC)[KEY] == TEAM_ANSWER
    assert document(root, ENV_STATIC)[KEY] == ENVIRONMENT_ANSWER
    assert layered(root, TEAM).get('team_static')[KEY] == TEAM_ANSWER, \
        "the team layer declares the key and does not answer it"
    assert layered(root).get('env_static')[KEY] == ENVIRONMENT_ANSWER
    # The environment's bytes are untouched — same paths, same bytes, every file but the one written.
    assert {path for path, payload in files(root).items() if payload != before[path]} == {TEAM_STATIC}
    # No cluster sentence, and that absence is `add-team`'s claim repeated: a team is not a cluster.
    assert "does not address a cluster" not in result.stderr, reported(result)


# --- (3) the contract: unset on the team returns the environment's answer ------------------------


def test_unsetting_the_teams_answer_returns_the_environments_answer(tmp_path):
    """Test 3: the layer above answers, which is the whole reason this verb is not `set KEY=None`."""
    root = initialized_root(tmp_path)
    run_verb("set", root, ENVIRONMENT, f"{KEY}={ENVIRONMENT_ANSWER}")
    added_team(root)
    run_verb("set", root, ENVIRONMENT, f"{KEY}={TEAM_ANSWER}", "--team", TEAM)

    result = run_verb("unset", root, ENVIRONMENT, KEY, "--team", TEAM)

    assert result.exit_code == 0, reported(result)
    # Two steps, encrypted first: the pair is one answer, so clearing it is two files reported at once.
    config = properties(root, TEAM)
    assert printed(result) == [f"{SKIPPED} {config.team_static_encrypted_config_path}",
                              f"{CREATED} {config.team_static_config_path}"], printed(result)
    assert KEY not in document(root, TEAM_STATIC)
    assert layered(root, TEAM).get('team_static')[KEY] == ENVIRONMENT_ANSWER
    # The environment's own answer was never this verb's to remove.
    assert document(root, ENV_STATIC)[KEY] == ENVIRONMENT_ANSWER


# --- (4) the encrypted pair: one answer, two spellings, both cleared -------------------------------


def test_an_encrypted_answer_lands_once_and_is_cleared_from_both_files_of_the_layer(tmp_path):
    """Test 4: `set --encrypted` writes one file, `unset` clears the pair, and the layer above answers.

    This is the falsification the brief asked for: an implementation that clears only the plain file
    passes every other test here and fails this one, because the encrypted file is ordered first and
    keeps answering what the plain file was just cleared of. The last two lines are that claim read
    from the read side — after the unset, neither encrypted file answers, and the environment's plain
    answer is what a reader gets.
    """
    root = initialized_root(tmp_path)
    run_verb("set", root, ENVIRONMENT, "GRAFANA_ADMIN_PASSWORD=env-secret")
    added_team(root)

    result = run_verb("set", root, ENVIRONMENT, "GRAFANA_ADMIN_PASSWORD=team-secret",
                      "--team", TEAM, "--encrypted")

    assert printed(result) == [f"{CREATED} {properties(root, TEAM).team_static_encrypted_config_path}"], \
        printed(result)
    assert document(root, TEAM_ENCRYPTED)["GRAFANA_ADMIN_PASSWORD"] == "team-secret"
    assert document(root, TEAM_STATIC) == {}, "an encrypted answer does not belong in the plain file"
    assert layered(root, TEAM).get('team_static_encrypted')['GRAFANA_ADMIN_PASSWORD'] == "team-secret"

    result = run_verb("unset", root, ENVIRONMENT, "GRAFANA_ADMIN_PASSWORD", "--team", TEAM)

    config = properties(root, TEAM)
    assert printed(result) == [f"{CREATED} {config.team_static_encrypted_config_path}",
                               f"{SKIPPED} {config.team_static_config_path}"], printed(result)
    assert "GRAFANA_ADMIN_PASSWORD" not in document(root, TEAM_ENCRYPTED)
    assert "GRAFANA_ADMIN_PASSWORD" not in document(root, TEAM_STATIC)
    # What the read side must *not* show after this unset is the team's answer. It may well show an
    # answer, because that is what a layer means: `config init` generated the environment's copy of
    # this key precisely so helmfile's requiredEnv would be satisfied, so a cleared team layer reveals
    # the environment's value rather than a void. Asserting a void here would have been asserting
    # something the layering rule forbids -- and before generation it happened to pass only because the
    # environment's encrypted file was empty, which is a fact about init's output and not about unset.
    read_encrypted = layered(root, TEAM).get('team_static_encrypted')
    assert read_encrypted.get("GRAFANA_ADMIN_PASSWORD") != "team-secret", \
        "unset cleared the file and not the answer the read side gives"
    assert read_encrypted.get("GRAFANA_ADMIN_PASSWORD") == document(root, ENV_ENCRYPTED)[
        "GRAFANA_ADMIN_PASSWORD"], \
        "a cleared team layer should answer with the environment's value and nothing else"
    assert layered(root, TEAM).get('team_static')['GRAFANA_ADMIN_PASSWORD'] != "team-secret"


# --- (5) the read-modify-write duty: keys nobody told us about ------------------------------------


def test_the_keys_the_set_was_not_told_about_keep_their_value_and_their_place(tmp_path):
    """Test 5: the document survives, in its own order. The one assertion no merge-step gets right.

    `ALPHA_ONE` and `ZULU` were put there by a tool this ticket does not know about, and both keep what
    they are. Key order is asserted and not observed, because a dictionary that re-sorts the operator's
    file turns every future `git diff` of a configuration into a wall.
    """
    root = initialized_root(tmp_path)
    (root / ENV_STATIC).write_text(json.dumps(
        {"ALPHA_ONE": 1, "ZULU": "kept", "CLUSTER_KUBE_CONTEXT": None,
         "CLUSTER_KUBECONFIG_PATH": None}, indent=2) + "\n", encoding="utf-8")

    result = run_verb("set", root, ENVIRONMENT, "MIDDLE=middle")

    assert result.exit_code == 0, reported(result)
    written = document(root, ENV_STATIC)
    assert written["ALPHA_ONE"] == 1
    assert written["ZULU"] == "kept"
    assert written["MIDDLE"] == "middle"
    assert list(written) == ["ALPHA_ONE", "ZULU", "CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH",
                             "MIDDLE"], "the new key belongs at the end, not at the front"
    # The read agrees with the file about the keys the *file* answers — the ones it wrote, in the
    # order it wrote them. Since the templated defaults landed the read answers more than that:
    # `constants.py` also answers the templated defaults, so the tail of this list is code and not the
    # operator's document. The
    # prefix is the whole claim here (a re-sorted file is a wall of diff), and the tail is asserted
    # for exactly what it is and not for somebody else's keys.
    read = list(layered(root).get('env_static'))
    assert read[:len(written)] == list(written), \
        "the read agrees with the file about which keys it declares, and in what order"
    assert set(read[len(written):]) <= set(constants.DEFAULT_STATIC_ANSWERS) | set(config_derived.RULES), \
        "the only keys past the document are the ones code answers, not a key that appeared from nowhere"


def test_an_answer_that_changed_is_overwritten_and_one_that_did_not_change_is_skipped(tmp_path):
    """Test 5b: `set`'s rule over an *answer*, next to `init`'s rule over a *file*.

    The two rules look alike and are not: overwriting a different value is this verb's purpose, while
    the same value written again is not an event and says `skipped`. And `init` on that very tree —
    where the file is no longer what it would write — still refuses, because that rule is about not
    guessing whose bytes they are. A comment in either function that conflated them would make one of
    these four lines impossible.
    """
    root = initialized_root(tmp_path)
    run_verb("set", root, ENVIRONMENT, f"{KEY}={ENVIRONMENT_ANSWER}")

    result = run_verb("set", root, ENVIRONMENT, f"{KEY}=team-elsewhere")

    assert printed(result) == [f"{CREATED} {properties(root).env_static_config_path}"], printed(result)
    assert document(root, ENV_STATIC)[KEY] == "team-elsewhere"

    untouched = files(root)
    again = run_verb("set", root, ENVIRONMENT, f"{KEY}=team-elsewhere")

    assert printed(again) == [f"{SKIPPED} {properties(root).env_static_config_path}"], printed(again)
    assert files(root) == untouched, "skipped is a claim about bytes and not a claim about wording"
    assert tree(root) == INITIALIZED_LAYER

    with pytest.raises(Exception) as refused:
        init_configuration(root, ENVIRONMENT)
    assert "already exists and is not what kubementat would write" in str(refused.value), \
        "init's rule over a file that differs is what would be lost by conflating the two rules"


# --- (6) the three refusals ------------------------------------------------------------------------


def test_a_missing_layer_is_refused_and_names_the_verb_that_makes_it(tmp_path):
    """Test 6a: a layer is made by `init` / `add-environment` / `add-team`, and not by an answer.

    Both verbs, both layers, and the sentence says which verb to go and run. A team layer that is only
    half there is refused too: writing its plain file would put an answer at a layer whose encrypted
    half the reader opens first, which is the "the team file just isn't there" bug told to the tree.
    """
    root = initialized_root(tmp_path)
    before = files(root)

    sentence = refused("set", root, "staging", "X=1")
    assert "add-environment" in sentence and "config init" in sentence, sentence
    assert "staging" not in {path.parts[0] for path in tree(root)}, "the refusal made a directory"
    assert files(root) == before

    sentence = refused("unset", root, "staging", "X")
    assert "add-environment" in sentence, sentence

    sentence = refused("set", root, ENVIRONMENT, "X=1", "--team", TEAM)
    assert "add-team" in sentence, sentence
    assert "not a layer" in sentence, sentence
    assert TEAM_STATIC not in tree(root), "a refusal that writes is a tree that lied"

    sentence = refused("unset", root, ENVIRONMENT, "X", "--team", TEAM)
    assert "add-team" in sentence, sentence
    assert files(root) == before, "an unset into a layer that does not exist wrote something"

    # The half-pair, made by hand because that is how it would happen.
    (root / ENVIRONMENT / TEAM).mkdir(parents=True)
    (root / ENVIRONMENT / TEAM / "static.json").write_text("{}\n", encoding="utf-8")
    sentence = refused("set", root, ENVIRONMENT, "X=1", "--team", TEAM)
    assert "add-team" in sentence, sentence
    assert document(root, TEAM_STATIC) == {}, "the plain file was written by a refusal"


def test_a_null_value_is_refused_and_names_config_unset(tmp_path):
    """Test 6b: `null` is the absence of an answer, and only one verb should create it.

    `KEY=null` is a legal thing to type and is refused on the way past the parser, which is why the
    sentence points at the verb that removes answers instead of storing one.
    """
    root = initialized_root(tmp_path)
    before = files(root)

    sentence = refused("set", root, ENVIRONMENT, f"{KEY}=null")

    assert "config unset" in sentence, sentence
    assert "null" in sentence, sentence
    assert files(root) == before
    assert KEY not in document(root, ENV_STATIC)


def test_an_unset_of_a_key_no_layer_declares_is_refused_and_lists_what_it_searched(tmp_path):
    """Test 6c: a no-op is not a success, so the refusal says which files it opened.

    A silent `unset` would leave the operator believing a value was gone. Listing the files is the
    honest version of the same fact: the search set is exactly what this verb could have written, so a
    key that lives one layer down is told where to look instead of being called a typo.
    """
    root = initialized_root(tmp_path)
    before = files(root)
    config = properties(root)

    sentence = refused("unset", root, ENVIRONMENT, "NO_KEY_ANSWERS_THIS")

    assert str(config.env_static_config_path) in sentence, sentence
    assert str(config.env_static_encrypted_config_path) in sentence, sentence
    assert files(root) == before

    # A key the *environment* declares is declared somewhere in the scope a team answer sits in, so
    # it is not a typo: the team's own files say `skipped`, nothing is written, and the layer above is
    # still what answers. Clearing the environment's answer is the same verb with no `--team`, which is
    # the sentence the refusal above points at instead.
    run_verb("set", root, ENVIRONMENT, f"{KEY}={ENVIRONMENT_ANSWER}")
    added_team(root)
    untouched = files(root)

    result = run_verb("unset", root, ENVIRONMENT, KEY, "--team", TEAM)

    config = properties(root, TEAM)
    assert printed(result) == [f"{SKIPPED} {config.team_static_encrypted_config_path}",
                              f"{SKIPPED} {config.team_static_config_path}"], printed(result)
    assert files(root) == untouched, "a scope that declares nothing anywhere still wrote bytes"
    assert layered(root, TEAM).get('team_static')[KEY] == ENVIRONMENT_ANSWER


# --- (7) the one key refusal, for the one-directory-one-cluster reason ---------------------------


def test_the_cluster_keys_on_the_team_layer_are_refused_and_the_environment_layer_answers_for_them(tmp_path):
    """Test 7: one directory is exactly one cluster, so a team cannot answer for it.

    Both keys, both verbs that could land one. The reason is stated as one sentence,
    because the alternative — a table of keys a team reader is believed to consult — is a guess about
    every call site in `src/`, and `config show` is the verb that answers that question, key by key.
    Then the
    same key on the layer that *does* address a cluster: accepted, and the cluster sentence goes away,
    which is what proves the refusal is about the layer and not about the key.

    The refusal used to be asserted two ways: that it states the rule, and that it prints the rule's
    document number. The second of those was deleted when the refusal started stating the rule in prose
    instead of citing where it was written down, and it was the only assertion in the suite that demanded
    an identifier in an operator's error message — the one thing a reader has no way to look up. The
    sentence the refusal has to carry is still asserted, and still asserted against the whole message,
    so the removal is one claim fewer and not a weaker claim.
    """
    root = initialized_root(tmp_path)
    added_team(root)
    before = files(root)

    for key in ("CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH"):
        sentence = refused("set", root, ENVIRONMENT, f"{key}=somewhere", "--team", TEAM)
        assert "one directory is exactly one cluster" in sentence.lower(), sentence
        assert key in sentence, sentence
    assert files(root) == before, "a refused key wrote into the tree"
    assert document(root, TEAM_ENCRYPTED) == {}
    assert document(root, TEAM_STATIC) == {}

    accepted = run_verb("set", root, ENVIRONMENT, "CLUSTER_KUBE_CONTEXT=prod-cluster")
    assert accepted.exit_code == 0, reported(accepted)
    assert document(root, ENV_STATIC)["CLUSTER_KUBE_CONTEXT"] == "prod-cluster"
    assert "does not address a cluster" not in accepted.stderr, \
        "the sentence was printed about a cluster the command was just handed"


# --- (8) the write set is the key and nothing else -------------------------------------------------


def test_neither_verb_makes_a_file_a_directory_or_a_manifest(tmp_path):
    """Test 8: the whole tree, before and after both verbs. Only the changed file's mtime moved.

    The directory half is the claim that a read-modify-write verb is innocent of: it never mkdirs,
    never copies a template, never stamps a manifest. The mtime half says it wrote *one* file: the
    sibling it was not told about keeps the timestamp the layer verb gave it, which a `set` that
    rewrote the whole pair would fail.
    """
    root = initialized_root(tmp_path)
    before = tree(root)
    before_stamp = stamp(root)
    before_bytes = files(root)

    result = run_verb("set", root, ENVIRONMENT, "GRAFANA_ENABLED=false", "--encrypted")
    assert result.exit_code == 0, reported(result)
    assert tree(root) == before
    changed = {path for path, touched in stamp(root).items() if touched != before_stamp[path]}
    assert changed == {ENV_ENCRYPTED}, sorted(changed)

    result = run_verb("unset", root, ENVIRONMENT, "GRAFANA_ENABLED")
    assert result.exit_code == 0, reported(result)
    assert tree(root) == before, "one verb made a directory and said nothing about it"
    # What `unset` is supposed to leave is *not* an empty file: init seeded and generated two keys this
    # verb was never told about, and an assert on `{}` would claim that a verb which removes one key
    # also removes a different one. The honest claim is what was set is gone and everything else is
    # exactly as init left it.
    assert "GRAFANA_ENABLED" not in document(root, ENV_ENCRYPTED), \
        "the key this verb was told to remove is still answering"
    remaining = document(root, ENV_ENCRYPTED)
    assert remaining["GRAFANA_ADMIN_USER"] == "grafadmin", \
        "unset reached past the key it was given and removed material it did not write"
    assert remaining["GRAFANA_ADMIN_PASSWORD"].strip(), \
        "unset removed a generated credential it was never told about"
    assert set(remaining) == {"GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD"}, sorted(remaining)
    assert files(root)[ENV_STATIC] == before_bytes[ENV_STATIC], \
        "the sibling that declared nothing was rewritten anyway"
    assert (root / "kubementat.yaml").read_bytes() == before_bytes[Path("kubementat.yaml")], \
        "the manifest is written by one verb and this is not it"


# --- (9) `--set`'s parser is still this verb's parser ---------------------------------------------


def test_the_answer_keeps_the_type_the_parser_gave_it(tmp_path):
    """Test 9: a JSON boolean stores a boolean, and a URL with an `=` inside it is one value.

    The typing is `parse_answer_argument`'s, which is the same parser `--set` feeds `config init`, so
    the two verbs cannot disagree about what `false` means. Splitting on the *first* `=` is what makes
    a query string a value and not two answers, and `str()`-ing everything is the quoting bug that
    would store `false` as the word.
    """
    root = initialized_root(tmp_path)

    run_verb("set", root, ENVIRONMENT, "GRAFANA_ENABLED=false")
    run_verb("set", root, ENVIRONMENT, "PORT=10")
    run_verb("set", root, ENVIRONMENT, "URL=https://example.test/?a=b")

    written = document(root, ENV_STATIC)
    written_bytes = files(root)
    assert written["GRAFANA_ENABLED"] is False
    assert written["PORT"] == 10
    assert written["URL"] == "https://example.test/?a=b"
    assert layered(root).get('env_static')["GRAFANA_ENABLED"] is False

    sentence = refused("set", root, ENVIRONMENT, "GRAFANA_ENABLED")
    assert "KEY=VALUE" in sentence, sentence
    assert files(root) == written_bytes, "a mis-typed answer is refused before the tree is decided"


# --- the library functions, as the CLI's shape -----------------------------------------------------


def test_the_library_returns_the_pair_in_the_order_a_reader_resolves_it(tmp_path):
    """The step names and their order, straight from `set_answer` / `unset_answer`.

    The CLI prints what the library returns, so the pair's order is a library claim: encrypted first,
    which is the order the layer is resolved in (`TEAM_PAIR`, `ENVIRONMENT_PAIR`). Asserted on the
    functions rather than on a subprocess because this is the only place the *names* can be compared to
    the names `init_configuration` uses for the same files — a printed line shows one word, and these
    show that the word is the same verb's word for the same file.
    """
    root = initialized_root(tmp_path)

    steps = set_answer(root, ENVIRONMENT, KEY, ENVIRONMENT_ANSWER)
    assert [(step.name, step.action) for step in steps] == [(ENVIRONMENT_STATIC_STEP, CREATED)]
    assert steps[0].path == properties(root).env_static_config_path

    steps = unset_answer(root, ENVIRONMENT, KEY)
    assert [(step.name, step.action) for step in steps] == [(ENVIRONMENT_ENCRYPTED_STEP, SKIPPED),
                                                            (ENVIRONMENT_STATIC_STEP, CREATED)]

    added_team(root)
    steps = set_answer(root, ENVIRONMENT, KEY, TEAM_ANSWER, team=TEAM, encrypted=True)
    assert [(step.name, step.action) for step in steps] == [(TEAM_ENCRYPTED_STEP, CREATED)]
    steps = unset_answer(root, ENVIRONMENT, KEY, team=TEAM)
    assert [(step.name, step.action) for step in steps] == [(TEAM_ENCRYPTED_STEP, CREATED),
                                                            (TEAM_STATIC_STEP, SKIPPED)]
