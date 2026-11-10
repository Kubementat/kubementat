"""A refusal that tells an operator what to run has to print a command that runs.

This is the same bug class as a false sentence, and the spec says so on purpose: *a printed instruction
that exits 2 is the same bug class as a printed sentence that is false* — the Decided section's reason
that `config init`'s closing line spells `kmt install <env> <team> --config-dir <root>` as a command and
not as advice.

Measured while the fix landed. `routing._cluster_manager_email` refuses a tree that never answered the
ACME contact and ends with the verb that fixes it, and it spelled the answer as

    kmt config set <dir> <environment> CLUSTER_MANAGER_EMAIL <address>

which is Click's **two positionals**, and the command declares one. Typing it does not set the key; it
answers `Error: Got unexpected extra argument`, measured. `kmt config set` takes `KEY=VALUE` as a single
answer (`metavar='[KEY=VALUE]'`), and both `docs/README_PLATFORM_CONFIG.md` and
`kubementat.errors.CiImageNotConfiguredError`, raised from `automations/ci.py`, already spell it that
way. The guard here is that shape, checked by running what the refusal printed rather than by searching
for an `=`.

The same guard catches the root's spelling now that `--config-dir` is a flag on every verb here: a
sentence that types the directory as the first positional is a usage error, and a sentence that types
the flag without a value is answered by the next token and so reads as an option instead of a refusal.
The placeholder set below is what keeps either spelling from rotting quietly — an unrecognised
placeholder fails the run, because an instruction that stops at a `<something>` nobody can fill in is
not an instruction.

A usage error is what fails this file. A refusal that objects to the directory is a pass, because that
is the command recognising its own arguments and disagreeing about the tree — a different sentence from
the one this file is about.
"""

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.commands import config as config_module
import kubementat.automations.ci as ci_utils
from kubementat import errors
from kubementat import config_init, config_validate
from kubementat.automations.routing import routing
from kubementat.core.config.config import Config

ENVIRONMENT = "instructionenv"
TEAM = "instructionteam"

SRC = Path(__file__).resolve().parents[1] / "src"

# The instruction sentence as it arrives in a refusal: `kmt config set …  is the verb`.
INSTRUCTION = re.compile(r"kmt config set\b(?P<arguments>.*?)\s+is the verb", re.S)
PLACEHOLDER = re.compile(r"^<[^>]+>$")

# Adjacent f-string literals, joined so a sentence split across three of them is one sentence.
CONTINUED = re.compile(r'"\s*\n\s*f?"')
# A key and its value on either side of a space: what `config set` counts as two arguments. The key
# may arrive as a literal or through an f-string's braces, and the sentence may be split across
# adjacent literals, so both spellings and the joining step below are part of the check.
SPLIT_ANSWER = re.compile(
    r"kmt config set\b.{0,200}?(?:\{[A-Za-z_]+\}|[A-Z][A-Z0-9_]{5,})\s+<[a-z][a-z-]*>", re.S)

SUBSTITUTIONS = {
    "<dir>": lambda root: str(root),
    "<configuration-root>": lambda root: str(root),
    "<environment>": lambda root: ENVIRONMENT,
    "<env>": lambda root: ENVIRONMENT,
    "<address>": lambda root: "someone@example.test",
    "<registry-url>": lambda root: "registry.example.test",
    "<team>": lambda root: TEAM,
}


def seed(root, environment_level=None) -> Config:
    """Both pairs of both layers present, so the refusal is about the key and not about a missing file."""
    environment_dir = root / "platform_config" / ENVIRONMENT
    team_dir = environment_dir / TEAM
    team_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in ((environment_dir / "static.json", environment_level or {}),
                          (environment_dir / "static.encrypted.json", {}),
                          (team_dir / "static.json", {}),
                          (team_dir / "static.encrypted.json", {})):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def _argv_from(message, root) -> list:
    """The argv the refusal is telling a human to run, placeholders resolved, group name taken off.

    The first two elements come back as `["config", "set", …]` because the sentence names the group;
    `config_module.config` is already that group, so the invoke below drops them. That is the only
    editing this function does — a token that is not a placeholder is handed over untouched, which is
    what lets an invented flag fail instead of matching quietly.
    """
    match = INSTRUCTION.search(message)
    assert match, f"no instruction sentence in {message!r}. This file's rule is that the sentence is a " \
                  "command; one that only describes the verb has no argv to check and needs saying so."
    argv = ["set"]
    for token in match.group("arguments").split():
        if PLACEHOLDER.match(token):
            maker = SUBSTITUTIONS.get(token.lower())
            assert maker, f"the refusal prints an unrecognised placeholder {token!r}"
            argv.append(maker(root))
        elif token == "KEY=VALUE":
            argv.append("CLUSTER_MANAGER_EMAIL=someone@example.test")
        elif token == "KEY":
            argv.append("CLUSTER_MANAGER_EMAIL")
        else:
            argv.append(token)
    return argv


def _assert_parses(argv, root, where):
    """Click's verdict on argv as a human would type it, and nothing else.

    `exit_code == 2` is Click's usage error: the sentence kubementat printed is not the command
    kubementat takes. Anything else is a pass.
    """
    result = CliRunner(mix_stderr=False).invoke(config_module.config, argv)
    assert result.exit_code != 2, \
        f"{where}: the instruction {argv[1:]!r} is a usage error and not a command. A human following " \
        f"that sentence gets {result.output.strip()[-180:]!r} and no answer written."


def test_the_refusal_that_names_config_set_prints_the_answer_as_one_argument(tmp_path):
    """`routing`'s ACME refusal, end to end: the key it reads is the key it tells you to set."""
    config = seed(tmp_path)
    with pytest.raises(Exception) as error:
        routing._cluster_manager_email(config)
    root = Path(config.platform_config_dir)
    _assert_parses(_argv_from(str(error.value), root), root, "routing._cluster_manager_email")


def test_the_ci_image_refusal_prints_the_answer_as_one_argument(tmp_path):
    """`ci_utils`' refusal, the same rule on the other message that prints the verb.

    `DOCKER_REGISTRY_BASE_URL` has no L0, so a tree that declares nothing is the state that raises.
    """
    config = seed(tmp_path)
    ci = ci_utils.CiUtils.__new__(ci_utils.CiUtils)
    ci.config = config
    with pytest.raises(errors.CiImageNotConfiguredError) as error:
        ci_utils.CiUtils.ci_image(ci)
    root = Path(config.platform_config_dir)
    _assert_parses(_argv_from(str(error.value), root), root, "errors.CiImageNotConfiguredError")


def test_no_message_in_src_prints_an_answer_config_set_would_read_as_two_arguments():
    """The rule stated over `src/`, so the next message cannot drift the way this one did.

    Adjacent f-strings are joined first, because every one of these sentences is split across two or
    three of them and a line-by-line scan would miss the half that lands on the next line. The set is
    pinned: if `kmt config set` stops appearing in any printed instruction the check has gone vacuous
    and this assertion is the note that says so.
    """
    joined, violations = {}, []
    for path in sorted(SRC.rglob("*.py")):
        text = CONTINUED.sub("", path.read_text(encoding="utf-8"))
        joined[path] = text
        for match in SPLIT_ANSWER.finditer(text):
            violations.append(f"{path.name}: …{text[match.start():match.end()]}…")

    instructions = [name for name, text in joined.items() if "kmt config set" in text]
    assert instructions, "no printed `kmt config set` instruction anywhere in src/, so this guard " \
                         "asserts nothing — re-read it against the messages that exist"
    assert not violations, "these instructions spell the answer as two arguments, so running them as " \
                           f"printed is a usage error and not an answer written: {violations}"
