"""`config set --generate` invents a value, and **no key is special**."""

import hashlib
import inspect
import json
import secrets
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import init as config_init
from kubementat.config.init import GENERATED_ANSWER_BYTES, init_configuration
from kubementat.core.config.config import Config

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

# A key named like a secret that is not one. `--encrypted` chooses the file, never the key's name,
# so this key must land in the plain file.
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
    """`kmt config set --generate`, through the verb and not the library, for the printed lines too.
    """
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
    """One hash of the whole tree — bytes, names, and directories."""
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
    """`kmt config <verb> …`, invoked through the group so the verb's own name is exercised."""
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
    """The stdout lines, which are the steps and nothing else. Stderr is guidance, not output."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def reported(result) -> str:
    return f"{result.stdout}{result.stderr}"


def refused(*arguments) -> str:
    """Run a verb expecting a refusal, and return the sentence it said."""
    result = run_verb(*arguments)
    assert result.exit_code != 0, reported(result)
    assert not printed(result), f"the refusal printed steps on stdout: {reported(result)}"
    return reported(result)


def added_team(root: Path) -> None:
    """The team layer, made the way the surface makes it: `kmt config add-team`."""
    result = run_verb("add-team", root, ENVIRONMENT, TEAM)
    assert result.exit_code == 0, reported(result)


def generated_answer_value(root: Path, relative: Path, key) -> str:
    """What `--generate` left in one file, asserted to be a string and nothing else."""
    value = document(root, relative)[key]
    assert isinstance(value, str), f"the generated answer is a {type(value).__name__}: {value!r}"
    assert value, "the generated answer is the empty string, which answers nothing"
    assert len(value) == len(secrets.token_urlsafe(GENERATED_ANSWER_BYTES)), (
        f"{value!r} does not read like the generator's output: expected "
        f"{len(secrets.token_urlsafe(GENERATED_ANSWER_BYTES))} characters from nbytes="
        f"{GENERATED_ANSWER_BYTES}")
    return value


# --- (1) generate onto a team layer that answers nothing ------------------------------------------


# --- (2) the same command again, which is the refusal ---------------------------------------------


def test_generating_a_second_time_refuses_and_touches_not_one_byte(tmp_path):
    """The centre of the rule, not a detail. Nothing is written, and the tree is byte-identical."""
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


# --- (4) null answers nothing, so this one succeeds -----------------------------------------------


# --- (5) the layer and the flag choose the file, never the key -------------------------------------


# --- (5b) the flag chooses the encrypted file, and the key never does ------------------------------


# --- (6) no key class, said as a test and not as a comment ----------------------------------------


def test_a_key_with_nothing_to_do_with_secrets_generates_exactly_the_same_way(tmp_path):
    """`BASE_DOMAIN` is answered identically, which is the deliverable rather than the example."""
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
    """Nothing on disk can fix two spellings of one answer, so the answer is a `UsageError`."""
    root = initialized_root(tmp_path)
    kept = digest(root)

    result = run_verb("set", root, ENVIRONMENT, "PIPELINE_NAMESPACE=ci", "--generate", KEY)
    assert result.exit_code == 2, reported(result)
    assert "both" in result.stderr, reported(result)
    assert digest(root) == kept


def test_generate_with_no_key_and_generate_with_no_answer_are_both_refused(tmp_path):
    """`--generate` with nothing after it, and neither spelling at all."""
    root = initialized_root(tmp_path)
    kept = digest(root)

    empty = run_verb("set", root, ENVIRONMENT, "--generate", "")
    assert empty.exit_code == 2, reported(empty)
    none = run_verb("set", root, ENVIRONMENT)
    assert none.exit_code == 2, reported(none)
    assert digest(root) == kept


# --- (8) the value is never printed ---------------------------------------------------------------


def test_the_generated_value_is_never_printed_by_the_command_that_made_it(tmp_path):
    """No sentence about secrets, and no echo. The step line is the whole stdout."""
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
