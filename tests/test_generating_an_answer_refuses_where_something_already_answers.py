"""`config set --generate` invents a value, and **no key is special**.

The rule is one sentence: kubementat keeps no list of secrets that behave differently from any
other answer. What differs is only *who supplied the value*, so the deliverable here is not the random
string — it is a verb that will not write one where something already answers, and a verb that cannot
tell what kind of key it is looking at. The nine tests below are the same claim from nine directions:
refusal, layering, the file the flag chooses, and the key that has nothing to do with secrets.

**The refusal is the ticket.** A generate that overwrote would make the act of touching a directory the
way a credential is lost, so `generate_answer` is `unset_answer`'s mirror: the same searched-file set,
the same "answers nothing", and a sentence that names the key, the file that answers, the layer that
file belongs to, and the two-step that rotates it. Test 2 asserts the tree byte-for-byte afterwards,
because "refuses" without "writes nothing" is only half of the promise.

**The null case is where "absent" and "null" are pinned.** `null` answers nothing, so
`--generate CLUSTER_KUBE_CONTEXT` on the environment layer — whose file `init` left holding that very
null — must **succeed**. It is the one place `generate_answer`'s question differs from `unset`'s
"is this key declared", and the reason it differs is that the answer to be taken away is not there.

**No key class is required, and one test would survive as the evidence that the rule held.** Test 6
generates `BASE_DOMAIN`, a key that could not be further from a credential, and checks it behaves
identically; the same test reads the function's own source for the four names the rule discusses, and a
list appearing there is what that reading is there to catch.

Falsification, so the tests say which rule broke:
* delete the already-answers refusal and **only test 2** fails;
* make generation overwrite instead of refuse and tests **2 and 3** fail (3 because the rotation pair
  is the only way a *different* value can appear twice);
* echo the value and **test 8** fails alone;
* add a branch that refuses non-secret keys, or one that forces `--encrypted` for a name containing
  `PASSWORD`, and **test 6** fails alone — which is the point of it.
"""

import hashlib
import inspect
import json
import secrets
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat import config_init
from kubementat.config import Config
from kubementat.config_init import GENERATED_ANSWER_BYTES, init_configuration

ENVIRONMENT = "prod"
TEAM = "alpha"

# The key test 1-3 and 8-9 rotate. It is one of the two webhook secrets with no special
# status, so it is an honest example of a key a random value answers as well as a human-chosen one —
# and it is *not* a precondition: every test below passes if the name is replaced by `BASE_DOMAIN`.
KEY = "GITHUB_WEBHOOK_SECRET"

# The four names this file's rule discusses, for test 6's claim that no function here holds a list of They
# live here so that a second list — the one the rule forbids — cannot creep into this file either.
KEY_NAMES_DISCUSSED_BY_R_11_8 = ("GITHUB_WEBHOOK_SECRET", "GITLAB_WEBHOOK_SECRET",
                                 "GRAFANA_ADMIN_PASSWORD", "MYSQL_ROOT_PASSWORD", "REDIS_PASSWORD")

# A key that has nothing to do with secrets, and which `--generate` is allowed to produce nonsense for.
PLAIN_KEY = "BASE_DOMAIN"

# A name that exists only so a `"PASSWORD" in key` branch can be caught here and nowhere else. It is
# not a kubementat key, it answers nothing, and the claim attached to it is the ticket's own: the
# **name** never chooses the file, `--encrypted` does. Naming it is not the list the rule forbids, which
# would have to be consulted to decide what the verb does — it is a probe that fails the day one of
# those branches appears.
PASSWORD_SHAPED_PROBE = "ROTATION_PASSWORD_PROBE"

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
    """A tree `kmt config init` made for one environment — the precondition `set` insists on."""
    root = config_root(tmp_path)
    init_configuration(root, ENVIRONMENT)
    return root


def generated(root: Path, key, *, team=None, encrypted=False) -> str:
    """`kmt config set --generate`, through the verb and not the library, for the printed lines too."""
    return run_verb("set", root, ENVIRONMENT, "--generate", key,
                    *(["--team", team] if team else []), *(["--encrypted"] if encrypted else []))


def layered(root: Path, team=None) -> Config:
    """The `Config` a reader would use for that tree, loaded — what the tree actually answers."""
    return Config(ENVIRONMENT, team, platform_config_dir=root, preload_configs=True)


def tree(root: Path) -> set[Path]:
    """Everything under `root`, relative to it. Directories included: they are part of the tree."""
    assert root.is_dir(), f"{root} is not a directory, so there is nothing to enumerate"
    return {path.relative_to(root) for path in root.rglob("*")}


def files(root: Path) -> dict[Path, bytes]:
    """Every file's bytes, keyed by its path under the root."""
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def digest(root: Path) -> str:
    """One hash of the whole tree — bytes, names, and directories.

    Test 2's claim is "nothing was written", and a tree digest is the strongest version of that claim
    available: an mtime proves when and a path set proves which, but only a digest over every byte says
    no file was touched, created, renamed, or emptied.
    """
    state = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        state.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_file():
            state.update(path.read_bytes())
        else:
            state.update(b"<dir>")
    return state.hexdigest()


def document(root: Path, relative: Path) -> dict:
    """A layer file read as JSON — for the claims that are about the file itself."""
    return json.loads((root / relative).read_text(encoding="utf-8"))


def run_verb(*arguments):
    """`kmt config <verb> …`, invoked through the group so the verb's own name is exercised.

    A bug inside the command is re-raised rather than folded into an exit code, so a refusal's `SystemExit`
    is the only thing allowed to come back as a non-zero `exit_code`. That is what makes a `UsageError`
    distinguishable from a `ValueError` in test 7.
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

    A refusal that printed stdout is caught here rather than asserted around: refusing is the answer
    that comes with `nothing is written`, and a verb that prints steps and then errors is the
    half-written tree this module keeps promising not to make.
    """
    result = run_verb(*arguments)
    assert result.exit_code != 0, reported(result)
    assert not printed(result), f"the refusal printed steps on stdout: {reported(result)}"
    return reported(result)


def added_team(root: Path) -> None:
    """The team layer, made the way the surface makes it: `kmt config add-team`."""
    result = run_verb("add-team", root, ENVIRONMENT, TEAM)
    assert result.exit_code == 0, reported(result)


def generated_answer_value(root: Path, relative: Path, key) -> str:
    """What `--generate` left in one file, asserted to be a string and nothing else.

    The length is derived from the module's own constant through the generator that uses it, because
    `secrets.token_urlsafe(nbytes)` expands to `ceil(4n/3)` URL-safe characters and a hard-coded 43
    here would be a test that disagrees with its own generator the day the constant moves.
    """
    value = document(root, relative)[key]
    assert isinstance(value, str), f"the generated answer is a {type(value).__name__}: {value!r}"
    assert value, "the generated answer is the empty string, which answers nothing"
    assert len(value) == len(secrets.token_urlsafe(GENERATED_ANSWER_BYTES)), (
        f"{value!r} does not read like the generator's output: expected "
        f"{len(secrets.token_urlsafe(GENERATED_ANSWER_BYTES))} characters from nbytes="
        f"{GENERATED_ANSWER_BYTES}")
    return value


# --- (1) generate onto a team layer that answers nothing ------------------------------------------


def test_generate_onto_a_fresh_team_layer_writes_one_value_to_the_plain_file(tmp_path):
    """The "generated when asked" half, on the team layer.

    One file and not the pair, the plain one and not the encrypted one, and no other key moved: this
    is `set_answer`'s write rule arriving with a value the operator never typed, so the file's shape
    is already decided and the only new fact is where the string came from.
    """
    root = initialized_root(tmp_path)
    added_team(root)
    before = tree(root)

    result = generated(root, KEY, team=TEAM)
    assert result.exit_code == 0, reported(result)
    assert files(root)[TEAM_STATIC] != files(root)[TEAM_ENCRYPTED], (
        "the two files of one layer are the same document, so --encrypted would have nothing to choose")

    value = generated_answer_value(root, TEAM_STATIC, KEY)
    assert printed(result) == [f"created {Config(ENVIRONMENT, TEAM, platform_config_dir=root, preload_configs=False).team_static_config_path}"], (
        f"the only line should be the step for the one file written: {printed(result)}")
    assert layered(root, TEAM).get('team_static')[KEY] == value, (
        "the team layer does not answer what its own file says")
    assert tree(root) == before, f"--generate made a file or directory: {tree(root) - before}"


# --- (2) the same command again, which is the refusal ---------------------------------------------


def test_generating_a_second_time_refuses_and_touches_not_one_byte(tmp_path):
    """The centre of the rule, not a detail. Nothing is written, and the tree is byte-identical.

    `--generate` on a key that answers is refused with the file that answers, the layer that file
    belongs to, and the rotation pair. The digest is the assertion that matters: a refusal that wrote
    a trailing newline somewhere would still be "refused" to a test that only looked at the exit code.
    """
    root = initialized_root(tmp_path)
    added_team(root)
    generated(root, KEY, team=TEAM)
    kept = digest(root)

    sentence = refused("set", root, ENVIRONMENT, "--generate", KEY, "--team", TEAM)
    assert KEY in sentence, sentence
    assert str(TEAM_STATIC) in sentence, f"the refusal does not name the file that answers: {sentence}"
    assert "config unset" in sentence and "config set" in sentence, (
        f"the refusal does not say how to rotate: {sentence}")
    assert digest(root) == kept, "a refusal wrote something"


# --- (3) rotation is the two-step, and it is the only way to get a second value -------------------


def test_unsetting_then_generating_is_the_rotation_path_and_invents_a_new_value(tmp_path):
    """The refusal is not a wall: it names the two commands, and running them produces a *different* value.

    Two random strings from `secrets` cannot collide, so a value that came back the same would mean the
    verb read the old answer instead of inventing a new one.
    """
    root = initialized_root(tmp_path)
    added_team(root)
    generated(root, KEY, team=TEAM)
    first = generated_answer_value(root, TEAM_STATIC, KEY)

    assert run_verb("unset", root, ENVIRONMENT, KEY, "--team", TEAM).exit_code == 0
    assert KEY not in document(root, TEAM_STATIC)
    again = generated(root, KEY, team=TEAM)
    assert again.exit_code == 0, reported(again)
    second = generated_answer_value(root, TEAM_STATIC, KEY)
    assert second != first
    assert layered(root, TEAM).get('team_static')[KEY] == second


# --- (4) null answers nothing, so this one succeeds -----------------------------------------------


def test_generating_a_key_the_environment_file_holds_as_null_succeeds(tmp_path):
    """Where "absent" and "null" are pinned: `init`'s null cluster key is generated *into*.

    The environment layer's `static.json` comes from `config init` declaring `CLUSTER_KUBE_CONTEXT`
    with a value that answers nothing, so the naive mirror of `unset`'s "the key is declared" question
    would refuse here. It does not, because what that question is really asking is *is there an answer*,
    and a `null` is not one.
    """
    root = initialized_root(tmp_path)
    assert document(root, ENV_STATIC)["CLUSTER_KUBE_CONTEXT"] is None, (
        "the tree this test tests no longer starts from the null init writes")
    kept = tree(root)

    result = generated(root, "CLUSTER_KUBE_CONTEXT")
    assert result.exit_code == 0, reported(result)
    generated_answer_value(root, ENV_STATIC, "CLUSTER_KUBE_CONTEXT")
    assert tree(root) == kept


# --- (5) the layer and the flag choose the file, never the key -------------------------------------


def test_generate_on_the_environment_layer_lands_in_the_layer_it_was_pointed_at(tmp_path):
    """The same verb, the same shape, one layer down.

    `--team` moves the answer's layer; nothing else about the rule moves.
    """
    root = initialized_root(tmp_path)

    result = generated(root, KEY)
    assert result.exit_code == 0, reported(result)
    generated_answer_value(root, ENV_STATIC, KEY)
    assert KEY not in document(root, ENV_ENCRYPTED), "--generate wrote both files of the pair"
    assert printed(result) == [f"created {Config(ENVIRONMENT, None, platform_config_dir=root, preload_configs=False).env_static_config_path}"]
    assert layered(root).get('env_static')[KEY] == document(root, ENV_STATIC)[KEY]


# --- (5b) the flag chooses the encrypted file, and the key never does ------------------------------


def test_the_encrypted_flag_and_not_the_key_name_chooses_the_file(tmp_path):
    """`--encrypted` is the only thing that sends a value to the encrypted half of a layer.

    A branch that decided this from the key's name would pass the two tests above and fail here, which
    is why this test exists beside them.
    """
    root = initialized_root(tmp_path)
    added_team(root)

    result = generated(root, KEY, team=TEAM, encrypted=True)
    assert result.exit_code == 0, reported(result)
    assert printed(result) == [f"created {Config(ENVIRONMENT, TEAM, platform_config_dir=root, preload_configs=False).team_static_encrypted_config_path}"]
    generated_answer_value(root, TEAM_ENCRYPTED, KEY)
    assert KEY not in document(root, TEAM_STATIC), "the plain file was written too"

    # And the refusal reads that choice: the encrypted file is what answers, so it is what gets named.
    sentence = refused("set", root, ENVIRONMENT, "--generate", KEY, "--team", TEAM, "--encrypted")
    assert str(TEAM_ENCRYPTED) in sentence, sentence


# --- (6) no key class, said as a test and not as a comment ----------------------------------------


def test_a_key_with_nothing_to_do_with_secrets_generates_exactly_the_same_way(tmp_path):
    """`BASE_DOMAIN` is answered identically, which is the deliverable rather than the example.

    The price is that `--generate BASE_DOMAIN` is allowed to invent a nonsense-looking value. It
    is the right price, because the alternative is code that classifies keys. So: the same write, the
    same refusal, the same step line — and no key name anywhere in the function that decides.
    """
    root = initialized_root(tmp_path)

    plain = generated(root, PLAIN_KEY)
    assert plain.exit_code == 0, reported(plain)
    secret = generated(root, KEY)
    assert secret.exit_code == 0, reported(secret)

    assert printed(plain) == printed(secret), (
        f"the two keys were treated differently: {printed(plain)} vs {printed(secret)}")
    plain_value = generated_answer_value(root, ENV_STATIC, PLAIN_KEY)
    assert len(plain_value) == len(document(root, ENV_STATIC)[KEY]), (
        "the generated length depends on the key's name, which is the classification rule in disguise")

    kept = digest(root)
    assert refused("set", root, ENVIRONMENT, "--generate", PLAIN_KEY)
    assert digest(root) == kept

    # A name a substring heuristic would reach for, and it is not one of kubementat's keys. It answers
    # nothing, it is not encrypted for having `PASSWORD` in it, and the step line says so.
    trip = generated(root, PASSWORD_SHAPED_PROBE)
    assert trip.exit_code == 0, reported(trip)
    assert printed(trip) == printed(plain), (
        f"the file a value lands in depended on the key's name: {printed(trip)} vs {printed(plain)}")
    assert PASSWORD_SHAPED_PROBE not in document(root, ENV_ENCRYPTED), (
        "the name chose the encrypted file, which is --encrypted's job and nothing else's")

    source = inspect.getsource(config_init.generate_answer)
    for name in KEY_NAMES_DISCUSSED_BY_R_11_8:
        assert name not in source, (
            f"generate_answer names {name}, which is the list the rule forbids")
    assert "set_answer(" in source, "the write is not set_answer's, so precedence gets a second owner"


# --- (7) the command-line shapes, both of which are usage faults -----------------------------------


def test_both_an_answer_and_generate_is_a_usage_error_that_writes_nothing(tmp_path):
    """Nothing on disk can fix two spellings of one answer, so the answer is a `UsageError`.

    `cli/context.py:150-155` draws the line this test stays on the right side of: a fault the operator
    can only fix on the command line exits 2 and says so, and it is raised before the library is
    called, so the tree never learns the command ran.
    """
    root = initialized_root(tmp_path)
    kept = digest(root)

    result = run_verb("set", root, ENVIRONMENT, "PIPELINE_NAMESPACE=ci", "--generate", KEY)
    assert result.exit_code == 2, reported(result)
    assert "both" in result.stderr, reported(result)
    assert digest(root) == kept


def test_generate_with_no_key_and_generate_with_no_answer_are_both_refused(tmp_path):
    """`--generate` with nothing after it, and neither spelling at all.

    Both are command-line questions, both exit 2, and neither writes: a verb with an optional
    positional has to say what happens when it is empty, and the answer is a refusal and not an
    implicit generate of nothing.
    """
    root = initialized_root(tmp_path)
    kept = digest(root)

    empty = run_verb("set", root, ENVIRONMENT, "--generate", "")
    assert empty.exit_code == 2, reported(empty)
    none = run_verb("set", root, ENVIRONMENT)
    assert none.exit_code == 2, reported(none)
    assert digest(root) == kept


# --- (8) the value is never printed ---------------------------------------------------------------


def test_the_generated_value_is_never_printed_by_the_command_that_made_it(tmp_path):
    """No sentence about secrets, and no echo. The step line is the whole stdout.

    Printing the value would put an answer into a terminal buffer that outlives the file, which is the
    only genuinely new security claim in this ticket. The library's own refusal is checked the same way
    for the same reason: the first run's value must not reappear in the second run's message.
    """
    root = initialized_root(tmp_path)
    added_team(root)

    first = generated(root, KEY, team=TEAM)
    value = document(root, TEAM_STATIC)[KEY]
    assert value not in reported(first), "the command echoed the value it just wrote"
    assert printed(first), "a successful generate printed nothing at all"

    second = run_verb("set", root, ENVIRONMENT, "--generate", KEY, "--team", TEAM)
    assert second.exit_code != 0
    assert value not in reported(second), "the refusal leaked the answer it refused to touch"


# --- (9) the layering question, mirrored from unset -----------------------------------------------


def test_an_environments_answer_blocks_a_teams_generate_and_names_the_environment_file(tmp_path):
    """The mirror of `unset --team`'s scope question, told from the other side.

    A team answer sits *inside* an environment, so the environment's pair is searched for "does this
    answer already exist" even though only the team's layer would be written — which is what `unset`
    does when it clears a team key that is shadowed from below. The refusal therefore names
    `prod/static.json` and not the team's own file, and its rotation step aims `config unset` at the
    layer that actually declares the key, because clearing the team's file would clear nothing.
    """
    root = initialized_root(tmp_path)
    added_team(root)
    assert run_verb("set", root, ENVIRONMENT, f"{KEY}=from-the-environment").exit_code == 0
    kept = digest(root)

    sentence = refused("set", root, ENVIRONMENT, "--generate", KEY, "--team", TEAM)
    assert str(ENV_STATIC) in sentence, f"the refusal does not name the environment's file: {sentence}"
    assert digest(root) == kept

    # ... and the rotation it names is aimed at the layer that answers, not at the one it was pointed at.
    assert f"{KEY} --team" not in sentence, sentence
    assert run_verb("unset", root, ENVIRONMENT, KEY).exit_code == 0
    assert run_verb("set", root, ENVIRONMENT, "--generate", KEY, "--team", TEAM).exit_code == 0
    generated_answer_value(root, TEAM_STATIC, KEY)
