"""The library half of the verbs that lay a configuration directory's *files* down, once, and of
the two that put one answer into a file that is already there.

Everything here is a library call and none of it is a command. `init_configuration` turns a root plus
an environment into the minimum tree that answers every product default elsewhere — the manifest and
the two static layers — `add_environment` and `add_team` add one of those layers to a tree that already
exists, `set_answer` and `unset_answer` put one answer on one layer of such a tree or take one away,
`generate_answer` invents one on a layer where nothing currently answers, and
`warn_cluster_not_addressed` is the sentence that explains why a fresh environment addresses
nothing. No `--force` parsing, no `config` group: the verbs live in `src/cli/commands/`, and this
module is deliberately usable from the preparation module in `config_bootstrap` as well.

The halves differ in one way a reader should not have to discover from a test. The verbs that lay files
down plan whole documents, so they decide every byte before the first one is put down; the verbs that
answer keys edit a document that belongs to somebody else, which makes them the only read-modify-write
functions in this codebase and gives them the opposite duty — keep every key nobody mentioned.
Everything above refuses a file that differs; `set_answer` overwrites an answer that differs, because
that is the act the operator asked for.

The rule this module exists to be tested against is one sentence: **init writes keys, not product
shape.**
Everything it writes is either the manifest or an answer, and what it must not write is anything a
default already answers — no namespaces, no domains, no registry, no templates, no
`mirrored_docker_images.json`, no app `values.encrypted.yaml`, no `.gitattributes`, no `.gitkeep`, no
marker file. That line is now split by which verb ran, and it is worth spelling the split rather
than letting it be read as a contradiction: a plain `config init` tree still gets no
`.gitattributes`, because `init_configuration` writes no such file to a tree of its own; a
*prepared* tree does, and the file is written by `config_bootstrap.prepare_configuration_directory`
three steps before `init_configuration` is called. git-crypt lives inside the configuration
directory, and a filter declaration is not product shape — it is the mechanism that
takes the encrypted files out of the object store in plaintext. `own_paths` below is the seam that
makes the two halves agree: it names files kubementat wrote somewhere else so the foreign-material
guard accounts for them instead of refusing them, and its default is empty, so the phase-3 contract
is untouched for every caller that does not need it. That is a claim about the *write set*, so it is asserted by enumerating the paths that
appear on disk, not by counting them, and by reading the tree back through `Config` and showing the
default still has to warn (the key came from `constants.py`, not from a file init wrote).

Idempotence is a second claim about the same write set: the plan is known before the
first byte, and applying it twice is a no-op. Three cases decide every path:

* missing → write it, step `created`;
* present and byte-identical → write nothing, step `skipped`;
* present and **different** → refuse. Loudly, naming the path and what to do.

The third does not become permissive under `force`. `force` means "the tree has foreign material in
it and I accept that" and never "overwrite what I did not write" — kubementat cannot tell an
operator's edited file from a half-written one, so an answer that is already there and already wrong
is a human question and not a flag.

The write set is not only files. Init also creates the layer directories its own readers look for
(census G: `tekton/tasks` and `tekton/pipelines` are opened at two sites in `src/` and made by
nothing, so a layer that is correct in code lands in a tree that is empty there). They are created
through `Config`'s properties and never a spelled path, because the property is the reader's own name
for the place. The candidates deliberately left alone — the environment's `tekton/triggers` and
`tekton/pipeline-runs`, `values/`, `values.encrypted/`, `secrets/` — are refused *silently* and each
reason is written where the choice is made.

A layer's *name* is decided before the first byte: a name that is not exactly one path element, or
that collides with a name kubementat already uses at the root or as a layer, is refused rather than
happily mkdir'd. Nothing is written on the way out of that refusal.

`add_environment` and `add_team` are those two primitives applied to a tree that already exists: one
environment layer, and one team layer under a named environment. Neither writes a manifest and
neither bootstraps — `init_configuration` owns the manifest, `add_environment` owns an environment,
and `add_team` refuses an environment that was never laid down. What they share with init is the plan
loop and the directory table and nothing else: no second function creates a layer, so
neither of them gets an `mkdir` or a `json.dump` of its own.

Layer placement is fixed rather than asked: both answer sets go to the **environment** layer, always,
and the team's two files carry nothing. A team layer with answers is `config set --team`'s job, which is `set_answer` below, so this function
writes no team answers and no parameter pretends otherwise. Neither cluster key goes into the team's
file either: one directory is exactly one cluster, and the claim that resolves them reads the
environment's file and no other.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import click

from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.reads import read_decrypted
from kubementat.core.identity.kubeconfig import IN_TREE_KUBECONFIG_FILENAME

# Step actions. `existed` is the one that belongs to foreign material: `force=True` accepts it and
# touches nothing of it, so recording it as `created` or `skipped` would both be false.
CREATED = "created"
SKIPPED = "skipped"
EXISTED = "existed"

# The manifest's filename, spelled once here. `Config` names no property for it (census G found
# nothing in `src/` that reads `kubementat.yaml` at all), and inventing one would be a property
# without a reader. Phase 9's stamp reader is the ticket that adds it, and this comment is the note
# it will need. That reader landed: `stamp.check_configuration_stamp` is what asks these two
# questions, and the name of the file is `constants.CONFIG_MANIFEST_FILENAME` now because both
# writers and readers touch it. This is the alias that keeps `config_validate`'s spelling working.
CONFIG_MANIFEST_FILENAME = constants.CONFIG_MANIFEST_FILENAME

# The directory names a prepared tree accumulates from other commands. They are named for the
# record and are consulted by nothing: a directory is never foreign material (see
# `_foreign_material`), so a list of them would be a list of things that are not evidence.
ALLOWED_DIRECTORY_NAMES = ("tekton", "values", "values.encrypted", "secrets")

# Names of the five things init can do, one per write target. The step carries the path, so these
# say *which job* was done and not where.
MANIFEST_STEP = "configuration manifest"
ENVIRONMENT_STATIC_STEP = "environment static file"
ENVIRONMENT_ENCRYPTED_STEP = "environment encrypted static file"
TEAM_STATIC_STEP = "team static file"
TEAM_ENCRYPTED_STEP = "team encrypted static file"
FOREIGN_STEP = "foreign material"

# The step names for the two kinds of layer directory. They say which layer, so the printed line
# reads as a fact about the tree and not as a filesystem call.
ENVIRONMENT_TEKTON_STEP = "tekton layer directory"
TEAM_TEKTON_STEP = "team tekton layer directory"

# The names kubementat itself uses at the *root* of a configuration directory (census A). An
# environment with one of them would be read as that and not as a layer — the plain case is an
# environment that wants to be called `secrets`, and the whole list is refused for that reason.
# Spelled once, here, so a new root file is one edit and not a rumour.
RESERVED_ROOT_NAMES = ("kubementat.yaml", "secrets", "deployer_ssh_key", "deployer_ssh_key.pub",
                       "gpg_private.key", "gpg_public_key.gpg", "git_crypt_symmetric.key")

# kubementat's own *layer* directory names inside every environment, which a team must not be named.
# `kubementat_components` belongs here for the reason it belongs in `NON_TEAM_ENVIRONMENT_DIRECTORIES`
# (`config.py:51`): the team listing already filters it out as a stale directory, so a team wearing
# that name would be a directory two rules both claim.
LAYER_DIRECTORY_NAMES = ("values", "values.encrypted", "tekton", "triggers", "pipeline-runs",
                         "kubementat_components")


@dataclass(frozen=True)
class InitStep:
    """One thing `init_configuration` did to one path.

    `action` is `"created"` (the path was missing, it is not), `"skipped"` (it was already exactly
    what init would have written, so no byte moved), or `"existed"` (foreign material accepted under
    `force`, touched by nothing). Steps come back in the order they were applied, which is the order
    the plan was built in: accepted foreign material first, then the manifest, then the layers.
    """

    name: str
    path: Path
    action: str

    def __str__(self) -> str:
        return f"{self.name}: {self.action} {self.path}"


def _canonical(path: Path | str) -> str:
    """The comparison key for a path: absolute, without resolving symlinks.

    `Config` resolves its own accessors, so `os.path.abspath` of the same file is the identical
    string, while `Path.resolve()` would disagree about a tree under a symlinked `/tmp`. Two owners
    of "is this the same file" is one too many, so every membership test here goes through this.
    """
    return os.path.abspath(str(path))


def _json_bytes(document: dict) -> bytes:
    """A JSON document as bytes, stable enough that a second run is byte-identical to the first.

    `indent=2` and a trailing newline: the files are working material an operator reads and edits,
    and a compact blob that survives `git diff` is the point of a static layer. Key order is the
    dictionary's, so a tuple of answers keeps the order it was given in.
    """
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _manifest_bytes() -> bytes:
    """`kubementat.yaml`: the two keys that say which product this tree is for.

    Both values come from `constants.py` and nothing else, so a constraint bump is one edit and not
    a search. `schema_version` is *stamped* here and never *bumped* here — bumping it belongs to a
    later migration, and a library function silently raising it is how an operator
    loses a configuration they cannot read.
    """
    return (f'kubementat: "{constants.CONFIG_KUBEMENTAT_CONSTRAINT}"\n'
            f"schema_version: {constants.CONFIG_SCHEMA_VERSION}\n").encode("utf-8")


def parse_answer_argument(text, flag='--set') -> tuple[str, Any]:
    """`KEY=VALUE` into `(key, value)`: the value is JSON when it parses, otherwise the string.

    One function rather than a loop inside `config init`, because `config set` has the same flag and
    the same typo, and two parsers is two ways to accept `--set GRAFANA_ENABLED=false` differently.

    Split on the **first** `=`: a value that contains one (`URL=https://x/?a=b`) is a value and not
    two answers. A chunk with no `=` at all is refused by the caller that named the flag, because the
    useful sentence is the one that says which flag was mis-typed.

    The value is handed to `json.loads` and kept as the raw string when that fails, so `false` and
    `10` land typed as JSON says they should and `example.test` lands as a string. `str()`-ing every
    value here would store the boolean as the word `"false"`, which is the same bug as quoting every
    YAML value. An empty value (`KEY=`) is the string `""` and is stored: it is a half-typed edit and
    the layer's own problem, not the parser's.
    """
    key, separator, value = str(text).partition('=')
    if not separator:
        raise click.ClickException(
            f"{flag} takes KEY=VALUE and got {text!r}. Pass the key, an equals sign, and the "
            f"answer -- the same shape config set will take.")
    if not key:
        raise click.ClickException(
            f"{flag} takes KEY=VALUE and got {text!r}: there is no key before the equals sign.")
    try:
        return key, json.loads(value)
    except ValueError:
        return key, value


def _answers_to_document(pairs: Iterable[tuple[str, Any]]) -> dict:
    """A tuple of `(key, value)` pairs, in order, into a document.

    The parameter is a tuple and not a dict so a duplicate key is *visible* here instead of being
    resolved by the last one winning. JSON objects cannot carry a key twice, and neither can an
    answer, so a repeat is refused rather than picked one — a silent choice would be a wrong answer.
    """
    document: dict = {}
    for key, value in pairs:
        if key in document:
            raise click.ClickException(
                f"answers declare {key} twice. One key has one answer per file, and a JSON object "
                f"cannot say otherwise; pass each key once.")
        document[key] = value
    return document


def _name_is_unusable(name: str) -> str | None:
    """Why `name` cannot name a layer directory, or `None` when it can.

    One path element, and nothing else: a layer is exactly one directory, so a name that is empty,
    that is `.` or `..`, that carries a `/` or a NUL, or that is padded with whitespace is not a
    shortened path but a name no reader can find again. The check runs on both layer names, and the
    reason it returns is the half of the sentence that tells an operator which of their two problems
    this is — *not a name*, and not *already taken*.
    """
    if not name:
        return "the name is empty"
    if name in (".", ".."):
        return "it is a path and not a name"
    if "\x00" in name:
        return "it contains a NUL byte"
    if "/" in name:
        return "it carries a '/', so it is two directories and not one"
    if name != name.strip():
        return "it begins or ends with whitespace"
    return None


def _validate_layer_names(environment: str, team) -> None:
    """Refuse a layer name the tree cannot hold, before anything is planned or written.

    Two kinds of refusal, and the sentence says which one it is.

    **Not a name** (`_name_is_unusable`): a layer is one directory under a configuration root, so its
    name is one path element. A name with a `/` in it is an attempt to write two layers deep, and a
    name padded with spaces is a directory that every reader looks for and does not find.

    **Already taken**: an environment named after one of `RESERVED_ROOT_NAMES` would be read as that
    root file or root directory instead of as a layer (the case that taught the rule is an environment
    called `secrets`); a team named after one of `LAYER_DIRECTORY_NAMES` would sit on kubementat's own
    directory and make two rules claim the same path. Neither is fixable by a flag, because the tree
    is what is wrong.
    """
    unusable = _name_is_unusable(environment)
    if unusable:
        raise click.ClickException(
            f"environment name {environment!r} is not a name kubementat can use: {unusable}. A "
            f"layer is exactly one directory under the configuration root, so its name is that one "
            f"directory's name: not empty, not '.' or '..', with no '/' and no NUL inside it and no "
            f"whitespace at either end.")
    if environment in RESERVED_ROOT_NAMES:
        raise click.ClickException(
            f"environment name {environment!r} is already taken. kubementat uses "
            f"{', '.join(RESERVED_ROOT_NAMES)} at the root of a configuration directory, and an "
            f"environment wearing one of those names is read as that root entry instead of as a "
            f"layer — the same reason an environment cannot be called 'secrets'. Pick a name "
            f"that is not one of them.")
    if team is None:
        return
    unusable = _name_is_unusable(team)
    if unusable:
        raise click.ClickException(
            f"team name {team!r} is not a name kubementat can use: {unusable}. A team is exactly one "
            f"directory under an environment, so its name is that one directory's name: not empty, "
            f"not '.' or '..', with no '/' and no NUL inside it and no whitespace at either end.")
    if team in LAYER_DIRECTORY_NAMES:
        raise click.ClickException(
            f"team name {team!r} is already taken. {', '.join(LAYER_DIRECTORY_NAMES)} are kubementat's "
            f"own layer directories inside every environment, and a team that shares one of them is a "
            f"directory two rules both claim — kubementat_components is already filtered out of the "
            f"team listing for the same reason. Pick a name that is not one of them.")


def _refuse_obsolete_team_trigger_dir(config: Config) -> None:
    """Refuse a tree that still has the old `<team>/triggers/` directory, naming both paths.

    The team's generated trigger configuration moved under `tekton/`, and a tree laid down
    before the move — or one an operator upgraded and re-runs `config init` on — can hold both
    directories, of which only the new one is ever read. This function must not leave that tree
    with both, so the old directory is refused rather than scaffolded beside: say what was found,
    say where the path lives now, and stop. Not silently migrated (the content is the operator's
    to review) and not deleted (deleting a directory is a decision, not a side effect of adding
    one); kubementat rewrites no operator tree, so the refusal is the whole of the safety here. The
    reader refuses the same directory, in `tekton_utils.py`, so the two sentences name the same
    two paths.
    """
    if config.team is None:
        return
    obsolete = config.team_static_config_path.parent / "triggers"
    if obsolete.is_dir():
        raise click.ClickException(
            f"{obsolete} is the trigger directory a pre-move kubementat wrote, and the team's trigger "
            f"configuration now lives under {config.tekton_team_trigger_dir}. This verb will not "
            f"leave a tree holding both, because only the new path is ever read and the old one "
            f"would stand there silently applying nothing. Move the manifests you are keeping into "
            f"the new directory by hand and remove the old one, then run the same command again. "
            f"Nothing will do this for you: kubementat records no rename table and rewrites no "
            f"operator tree.")


def init_configuration(root, environment, team=None, answers=(), encrypted_answers=(),
                       force=False, own_paths=()) -> tuple[InitStep, ...]:
    """Create a configuration directory's files and layer directories, once, and report what was done.

    Writes exactly three things for an environment — `<root>/kubementat.yaml`,
    `<root>/<environment>/static.json`, `<root>/<environment>/static.encrypted.json` — plus the
    team's two files when `team` is given. Then creates, through `Config`'s properties and in the
    order below, the environment's two `tekton/` layer directories and the team's four, so the
    directories the readers look in exist. `answers` go to the environment's `static.json` and
    `encrypted_answers` to its `static.encrypted.json`, after the two cluster keys that are always
    null until a human names a cluster; the team's `static.json` is empty, because one directory
    is exactly one cluster and the keys that resolve it are read from the environment's file and no
    other, and its encrypted file is `{}`.

    Nothing is printed. The caller (`config init`'s verb half, or the preparation module) owns the
    prose, and so owns whether a
    step is worth mentioning at all.

    Parameters
    ----------
    root : Path
        The configuration directory to write under. Never `kubementat_main_dir` — nothing under that
        root is written by this function, and the reason it refuses to write there is recorded with
        the rule that owns that root.
    environment : str
        Required. `None` is a refusal and not a default: without an environment there is no layer to
        write, and guessing one would be `config init` answering a question nobody asked.
    team : str, optional
        Creates the team's two empty files. No team answers are written.
    answers, encrypted_answers : tuple of (str, object)
        Environment-layer answers, kept in order. Tuples so a duplicate is visible.
    force : bool
        Accept a tree containing material kubementat did not write. Never overwrite it.
    own_paths : iterable of Path
        Paths kubementat owns but this plan does not write — the two git configuration files and
        the keypair files a *prepared* directory accumulates around its tree. They are excluded from
        the foreign-material scan for one reason and not a second: they were written by kubementat,
        by this epic's own steps, at a point in the order that this function cannot see. Declaring
        them is not the same as passing `force`, which says "accept what I cannot account for" and
        would turn a real foreign file into a step that reads `existed`. The byte-identity rule the
        guard protects is not lifted by this keyword either: the owner of each declared path checks
        it with `CREATED` / `SKIPPED` / refusal when it writes it, and that check runs before this
        function is handed the tree at all. An empty `own_paths` is the phase-3 shape.

    Returns
    -------
    tuple of InitStep
        In application order: the files, then the layer directories. A second call on a tree nothing
        touched returns all `skipped` and writes no bytes and no directories.

    Raises
    ------
    click.ClickException
        No environment; an environment or team name that is not a name, or that is already taken by
        a root entry or a layer directory; a repeated answer key; foreign material without `force`; a
        file init would write that already differs; a tree that still has the old
        `<team>/triggers/` directory. Only the differing file is not lifted by `force`; a refused
        name writes nothing at all, and the obsolete trigger directory is refused before the plan
        is built, so it writes nothing either.
    """
    root = Path(root)
    if not environment:
        raise click.ClickException(
            "creating a configuration tree needs an environment: every file but kubementat.yaml is "
            "written under <root>/<environment>/, and no environment means 'all of them'.")
    # Names first, before a plan exists: the two refusals here are claims about the tree and not
    # about the write set, so deciding them late would mean deciding them after writing.
    _validate_layer_names(environment, team)
    if team is not None and not environment:
        # Spelled for whoever reads it off a terminal: a team is one layer under one environment, so
        # a team without an environment is a question the tree cannot answer. Named from the library,
        # because both `kmt config init` and the legacy `initialize` reach this one sentence.
        raise click.ClickException(
            "a team is a layer under one environment, so a team needs an environment too.")

    team_static: dict = {}

    # No `kubementat_main_dir`: `Config` answers that one with the product checkout when nothing is
    # passed, which is the honest answer even though this function reads no product path out of it.
    # Passing `root` would make `tekton_global_pipeline_run_dir` claim the *configuration* tree holds
    # the product's pipeline runs — false, and unread today rather than harmless.
    config = Config(environment, team, platform_config_dir=root, preload_configs=False)

    # A tree that still has the old trigger directory is refused before the plan is built:
    # scaffolding the new one beside it would leave both, and only the new one is ever read.
    _refuse_obsolete_team_trigger_dir(config)

    # The plan, before any write: five targets, each with the exact bytes it wants on disk. Building
    # it whole is what makes the idempotence claim checkable — a per-path write-as-you-decide loop
    # cannot promise that a refusal halfway through has not already changed the tree.
    environment_static: dict = {constants.CLUSTER_KUBE_CONTEXT_KEY: None, constants.CLUSTER_KUBECONFIG_PATH_KEY: None}
    environment_static.update(_answers_to_document(answers))
    # The encrypted document is seeded first and answered second, in that order and for a reason: a key
    # an operator named with `--encrypted-set` has to win over the table, and reversing the two lines
    # would make `init --encrypted-set GRAFANA_ADMIN_USER=alice` quietly write `grafadmin` anyway. Only
    # `constants.DEFAULT_ENCRYPTED_ANSWERS` is seeded — the four `REPLACEME_` keys in the old template
    # are not, because a sentinel that reads as a chosen value in a credential file is worse than the
    # absence it replaced. See that table for why exactly one key is in it.
    environment_encrypted = dict(constants.DEFAULT_ENCRYPTED_ANSWERS)
    environment_encrypted.update(_answers_to_document(encrypted_answers))
    # Generation is third, and that is the whole rule: a key is generated only where nothing answered
    # it, so a named value or a seeded default both outrank a value kubementat made up. `helmfile` reads
    # `GRAFANA_ADMIN_PASSWORD` with `requiredEnv`, which aborts the entire file rather than skipping the
    # release, so a tree that reached `install` without one was guaranteed to fail on it -- and the only
    # honest alternatives were a shared constant shipped in the code, or the refusal this is avoiding.
    #
    # What is read is the file that is already there, by the same primitive `generate_answer` uses,
    # and that is what keeps a re-run byte-identical. Generating into a document built from the plan
    # alone asks for a fresh random value every time, and the second run then finds a file that differs
    # from what it would write -- which the write rule correctly refuses. So the question this
    # answers is the one `config set --generate` asks: does *anything* answer this key, and if it does,
    # it is not ours to invent.
    existing_encrypted: dict = {}
    if config.env_static_encrypted_config_path.exists():
        existing_encrypted = _read_document(config.env_static_encrypted_config_path)
    for key in constants.GENERATED_ENCRYPTED_ANSWERS:
        already_there = str(existing_encrypted.get(key) or "").strip()
        if already_there:
            environment_encrypted[key] = already_there
        elif not str(environment_encrypted.get(key) or "").strip():
            environment_encrypted[key] = secrets.token_urlsafe(GENERATED_ANSWER_BYTES)
    # The team's document is empty. Its two cluster keys stay in the environment's file: one directory
    # is exactly one cluster, and what resolves them reads
    # `<root>/<environment>/static.json` and no other path (`EnvironmentRegistry.resolve` and
    # `_environment_declaration`, both in `core/identity/registry.py`), so
    # writing them one layer down buys nothing today and teaches the next reader that a team can
    # address a cluster of its own.
    plan: list[tuple[str, Path, bytes]] = [
        (MANIFEST_STEP, root / CONFIG_MANIFEST_FILENAME, _manifest_bytes()),
        (ENVIRONMENT_STATIC_STEP, config.env_static_config_path, _json_bytes(environment_static)),
        (ENVIRONMENT_ENCRYPTED_STEP, config.env_static_encrypted_config_path,
         _json_bytes(environment_encrypted)),
    ]
    if team:
        plan.append((TEAM_STATIC_STEP, config.team_static_config_path, _json_bytes(team_static)))
        plan.append((TEAM_ENCRYPTED_STEP, config.team_static_encrypted_config_path,
                     _json_bytes({})))

    # Material kubementat wrote is not foreign material, and the difference is decided by *who* wrote
    # it rather than by whether this plan happens to include it — so `own_paths` joins `owned` here
    # and nowhere else. One place, so the two lists cannot disagree about what the tree is made of.
    owned = ({_canonical(path) for _, path, _ in plan}
             | {_canonical(path) for path in tuple(own_paths)}
             # The environment's own kubeconfig is expected material and not foreign material, and
             # the difference is that kubementat reads this file and never writes it. It is the file
             # the resolution order answers from when nothing declared a path, so an operator who
             # followed the advice and put a kubeconfig where it belongs must not be told to move it
             # before the tree can be prepared -- a guard that quarantines its own configuration
             # would be a guard that refuses the thing it documents.
             | {_canonical(config.platform_config_dir / environment / IN_TREE_KUBECONFIG_FILENAME)})
    layer_directories = [root, root / environment] + (
        [config.team_static_config_path.parent] if team else [])

    steps: list[InitStep] = []
    foreign = _foreign_material(layer_directories, owned)
    if foreign and not force:
        raise click.ClickException(
            f"{root} contains material kubementat did not write: "
            f"{', '.join(str(p.relative_to(root)) for p in sorted(foreign, key=str))}. "
            f"init_configuration will not lay its own files beside files it cannot account for. "
            f"Move them out, or pass force=True to accept this tree as it is and write only the "
            f"files listed here.")
    steps.extend(InitStep(FOREIGN_STEP, path, EXISTED) for path in sorted(foreign, key=str))

    steps.extend(_apply_layer_plan(plan))

    # The layer directories, after the files, in the order the layers apply: the environment's two,
    # then the team's four. Every one is named by a `Config` property and never spelled here — the
    # property is what the reader opens, so a path written alongside it is a second claim about where
    # a layer lives, and census G's whole finding is that these six were read in two places and made
    # by nothing.
    #
    # Two candidates from the same census are deliberately not created, for the same reason each:
    #   * `<environment>/tekton/triggers` — no property names it and nothing in `src/` opens it: the
    #     apply path that reads a trigger directory is team-only (`tekton_utils.py:1488-1514`, and
    #     the note above `Config.tekton_env_task_dir` says so). A directory that exists and is read by
    #     nothing is the shape of the tree the epic deleted.
    #   * `<environment>/tekton/pipeline-runs` — same: no reader. This one is also output rather than
    #     input, written when a run happens and not looked for as a layer.
    # `values/` and `values.encrypted/` are not created either: helmfile's `isFile` guard exists
    # precisely because they may be absent, and `secrets/` is made with the keys in phase 5.
    layer_directories: list[tuple[str, Path]] = [
        (ENVIRONMENT_TEKTON_STEP, config.tekton_env_task_dir),
        (ENVIRONMENT_TEKTON_STEP, config.tekton_env_pipeline_dir),
    ]
    if team:
        # The team's triggers go where `Config.tekton_team_trigger_dir` says, under `tekton/` since
        # the move. A tree that still has the pre-move `<team>/triggers/` was refused above —
        # creating the new directory beside it would produce exactly the both-directories tree
        # whose old half nothing reads.
        layer_directories += [
            (TEAM_TEKTON_STEP, config.tekton_team_task_dir),
            (TEAM_TEKTON_STEP, config.tekton_team_pipeline_dir),
            (TEAM_TEKTON_STEP, config.tekton_team_pipeline_run_dir),
            (TEAM_TEKTON_STEP, config.tekton_team_trigger_dir),
        ]

    # Same rule as the files, same byte-identical meaning: a directory that is there was put there,
    # by this init or by someone else, and says `skipped` either way. Only a missing one is made.
    steps.extend(_make_layer_directories(layer_directories))

    return tuple(steps)


def _foreign_material(directories: list[Path], owned: set[str]) -> list[Path]:
    """Files in `directories` that kubementat did not write and this init does not own.

    Two rules make the set both honest and small. **A directory is never foreign.** kubementat's own
    readers tolerate an absent layer, so an existing `tekton/`, `values/`, `values.encrypted/` or
    `secrets/` directory is not evidence of anything, and demanding `force` for one would train the
    operator to pass `force` — which is exactly the flag that must mean something.

    **A file is foreign.** It was put there by a tool with a different write set, and init has to say
    so before it lays files beside it. The scan is one level deep per layer (the root, the
    environment, the team), which is as far as the write set reaches; a file deeper down sits under a
    directory init has no business judging.

    A file *at* one of the owned paths is not foreign — it is either about to be skipped or refused
    for a different and more specific reason.
    """
    foreign: list[Path] = []
    seen: set[str] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                continue
            key = _canonical(entry)
            if key in owned or key in seen:
                continue
            seen.add(key)
            foreign.append(entry)
    return foreign


def _apply_layer_plan(plan: list[tuple[str, Path, bytes]]) -> list[InitStep]:
    """Apply a layer plan and say what happened to each of its paths.

    One owner for the three cases that decide every write target — missing, byte-identical, different
    — because the idempotence rule is worth one sentence and not one sentence per function that adds
    a layer.
    The whole plan is decided before the first byte is written, so a refusal on the third target has
    changed neither the first nor the second.

    A target that is there and differs is refused, not overwritten: kubementat cannot tell an
    operator's edited answer from a half-written file, so the difference goes back to a human. No
    caller passes `force` here, because the flag is about *foreign* material and never about answers.
    """
    steps: list[InitStep] = []
    to_write: list[tuple[Path, bytes]] = []
    for name, path, payload in plan:
        if not path.exists():
            to_write.append((path, payload))
            steps.append(InitStep(name, path, CREATED))
            continue
        if path.read_bytes() == payload:
            steps.append(InitStep(name, path, SKIPPED))
            continue
        raise click.ClickException(
            f"{path} already exists and is not what kubementat would write there. That is not a "
            f"conflict to overwrite: someone wrote that file, and the difference between an edited "
            f"answer and a half-written one is not something a flag can tell apart. Edit or remove "
            f"the file and run the same command again. No flag lifts this: force is about foreign "
            f"material in the tree, never about an answer that is already there.")

    # Reached only when every target is either missing or identical, so no refusal can have written
    # part of the tree on the way.
    for path, payload in to_write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return steps


def _make_layer_directories(layer_directories: list[tuple[str, Path]]) -> list[InitStep]:
    """Make the layer directories that are missing, and report the ones that were already there.

    The directory half of the same rule, so that no second function here creates a layer of its own:
    a directory that is there was put there — by init, by this verb, or by an operator — and says
    `skipped` either way. Nothing here prunes one either: every reader tolerates an absent layer, so
    deleting a directory is a decision and not a side effect of adding one.
    """
    steps: list[InitStep] = []
    for name, directory in layer_directories:
        if directory.is_dir():
            steps.append(InitStep(name, directory, SKIPPED))
        else:
            directory.mkdir(parents=True, exist_ok=True)
            steps.append(InitStep(name, directory, CREATED))
    return steps


# The sentence every verb that writes *into* a tree says about a root that is not there. The claim is
# the one thing that differs per verb — `add-environment`/`add-team` add one layer, `set`/`unset` put
# one answer on one layer — so only it is a parameter and the rest of the refusal has one owner.
_CONFIGURATION_ROOT_CLAIM = ("kmt config add-environment and kmt config add-team add one layer to a "
                             "tree that already exists and write nothing else")


def _require_configuration_root(root: Path,
                                claim: str = _CONFIGURATION_ROOT_CLAIM) -> None:
    """Refuse a root that is not a directory, naming the root that was given.

    Both layer verbs are additive: one layer on a tree, and nothing else. The tree is therefore a
    precondition and not something either verb would create, which is what makes this a refusal and
    not a bootstrap — and the sentence that says so is the same sentence in both verbs, because an
    operator who guessed the directory should hear the same thing either way. `kmt config set` and
    `kmt config unset` ask the same question and name themselves in it, so that a wrong directory is
    told *which* verb to come back with rather than being handed a sentence about a verb they did not
    run.
    """
    if not root.is_dir():
        raise click.ClickException(
            f"{root} is not a configuration directory. {claim}, so a root that is not there is not a "
            f"tree to write into. Pass the directory kmt config init wrote, or make one: kmt config "
            f"init <dir> --environment <environment>.")


def add_environment(root, environment) -> tuple[InitStep, ...]:
    """Add one environment layer to a configuration tree, and report what was done.

    Writes the two static files under `<root>/<environment>/` and creates the environment's two
    `tekton/` layer directories — the same four targets, the same step names, and the same rules as
    the matching part of `init_configuration`, reached through the same two primitives. It writes no
    manifest and copies no template: `kubementat.yaml` says which product a tree is for and is written
    by exactly one verb, so a root without one is refused and nothing is laid down.

    A layer that is already there is `skipped` and byte-for-byte untouched, and a file that is there
    and differs is refused by the same rule init enforces, naming the path. `config set` is the verb
    that answers keys in a new layer; this one makes the layer.
    """
    root = Path(root)
    _require_configuration_root(root)
    # Names before the plan, exactly as in init: this half of the decision is about the tree and not
    # about the write set, so deciding it late would mean deciding it after writing.
    _validate_layer_names(environment, None)

    if not (root / CONFIG_MANIFEST_FILENAME).is_file():
        raise click.ClickException(
            f"{root / CONFIG_MANIFEST_FILENAME} is absent, so {root} is not a kubementat "
            f"configuration tree yet. kmt config add-environment adds a layer to a tree and lays no "
            f"tree down: kubementat.yaml is what says which product this tree is for, and one verb "
            f"writes it. Run kmt config init <dir> --environment <environment> first, then come back "
            f"with the environments you want beside that one.")

    # No `kubementat_main_dir`, for the reason init_configuration gives: `Config` answers that one
    # with the product checkout, and passing `root` would make the global pipeline-run paths claim
    # the configuration tree holds them.
    config = Config(environment, None, platform_config_dir=root, preload_configs=False)

    # The two cluster keys go in with the empty document, exactly where init puts them: a new
    # environment addresses nothing until a human names a context, and a file without the keys is a
    # different file from the one init would write, which the next `config init` would refuse.
    environment_static = {constants.CLUSTER_KUBE_CONTEXT_KEY: None, constants.CLUSTER_KUBECONFIG_PATH_KEY: None}
    steps = _apply_layer_plan([
        (ENVIRONMENT_STATIC_STEP, config.env_static_config_path, _json_bytes(environment_static)),
        (ENVIRONMENT_ENCRYPTED_STEP, config.env_static_encrypted_config_path, _json_bytes({})),
    ])
    steps += _make_layer_directories([
        (ENVIRONMENT_TEKTON_STEP, config.tekton_env_task_dir),
        (ENVIRONMENT_TEKTON_STEP, config.tekton_env_pipeline_dir),
    ])
    return tuple(steps)


def add_team(root, environment, team) -> tuple[InitStep, ...]:
    """Add one team layer under one environment, and report what was done.

    Writes the team's two empty files (`static.json` and `static.encrypted.json` are both `{}`) and
    creates the team's four layer directories, through the same primitives and the same table
    `init_configuration` uses for `--team`. It writes nothing else: no answers, no values file, no
    template — a team layer with answers is `config set --team`'s job, and the cluster keys stay in
    the environment's file, because one directory is exactly one cluster and what resolves them reads
    that file and no other (`EnvironmentRegistry.resolve` and `_environment_declaration`, both in
    `core/identity/registry.py`).

    Refuses when the environment's `static.json` is not there: a team is one layer under one
    environment, which is the same fact `init_configuration` refuses `--team` without `--environment`
    for, and `kmt config add-environment` is the verb that makes an environment. Nothing is written
    on the way out of that refusal. A team directory that still has the old `triggers/`
    directory is refused by the same helper init uses, before either file is planned, for the same
    reason: adding the new layer beside it would leave both, and only the new path is read.
    """
    root = Path(root)
    _require_configuration_root(root)
    if team is None:
        raise click.ClickException(
            f"add-team needs a team: a team is exactly one directory under {environment!r}, and "
            f"naming one for the operator would be inventing a layer nobody asked for.")
    _validate_layer_names(environment, team)

    config = Config(environment, team, platform_config_dir=root, preload_configs=False)
    if not config.env_static_config_path.is_file():
        raise click.ClickException(
            f"{config.env_static_config_path} is absent, so environment {environment!r} is not a "
            f"layer of {root} yet. kmt config add-team puts a team under an environment and lays no "
            f"environment down — the same reason config init refuses --team without --environment. "
            f"Run kmt config add-environment <dir> {environment} first, then add the team.")
    # Same rule as init: the old trigger directory is refused, not scaffolded beside.
    _refuse_obsolete_team_trigger_dir(config)

    steps = _apply_layer_plan([
        (TEAM_STATIC_STEP, config.team_static_config_path, _json_bytes({})),
        (TEAM_ENCRYPTED_STEP, config.team_static_encrypted_config_path, _json_bytes({})),
    ])
    steps += _make_layer_directories([
        (TEAM_TEKTON_STEP, config.tekton_team_task_dir),
        (TEAM_TEKTON_STEP, config.tekton_team_pipeline_dir),
        (TEAM_TEKTON_STEP, config.tekton_team_pipeline_run_dir),
        (TEAM_TEKTON_STEP, config.tekton_team_trigger_dir),
    ])
    return tuple(steps)


# What the two answer verbs say about themselves when the root they were handed is not a tree. One
# sentence's subject differs per verb, so it is a parameter of the one check that is shared.
_ANSWER_CLAIM = "kmt config set and kmt config unset put one answer on one layer of a tree that " \
                "already exists and write nothing else"


# ---------------------------------------------------------------------------------------------
# One key, in one file, everything else exactly as it was.
#
# These two functions are the first read-modify-write in this codebase. Everything above writes whole
# documents it composed itself — `initialize`'s four writers `json.load` the *template* and emit the
# file, and `init_configuration`/`add_environment`/`add_team` plan whole bytes per path — so they can
# be written before the first byte is put down. Here nothing is composed: a document already exists,
# and it belongs to the operator. What this module owes them is the only claim that matters, that
# **every key it was not told about keeps its value and its position**, and that is decided by
# `json.loads` + one assignment + `json.dumps` rather than by a merge step someone could forget.
#
# The statuses are the three layer verbs' and there is no fourth word: `CREATED` says this
# file changed, `SKIPPED` says it was already exactly this. Neither verb creates a file, a directory,
# a template or a manifest, and neither resolves a root — the directory is positional.
# ---------------------------------------------------------------------------------------------

def _layer_files(config: Config, team) -> list[tuple[str, Path]]:
    """The two files of the layer a verb was given, encrypted first.

    The order is the one `config.py` gives a layer's two files, and for the reason it gives: a layer's
    two files are *one answer with two spellings*, and when both declare a key the encrypted one is what
    answers. Nothing here re-declares that order — `config.py` is the only file that spells it, and this
    function takes the pair as it is given. So the steps a pair verb returns come back in the order the
    reader resolves them, and the printed lines say which file an answer lives in without spelling a
    path. The step names are `init_configuration`'s, which is the point rather than a coincidence: a
    verb that edits a layer and a verb that lays one down use the same word for the same file.
    """
    if team is None:
        return [(ENVIRONMENT_ENCRYPTED_STEP, config.env_static_encrypted_config_path),
                (ENVIRONMENT_STATIC_STEP, config.env_static_config_path)]
    return [(TEAM_ENCRYPTED_STEP, config.team_static_encrypted_config_path),
            (TEAM_STATIC_STEP, config.team_static_config_path)]


def _require_answerable_layer(config: Config, root: Path, environment: str, team) -> list[tuple[str, Path]]:
    """The layer's two files, when the layer exists; a refusal naming the verb that makes it, when it does not.

    The layer is the pair, so **both** files must be there and not only the one about to be written.
    A team directory with a `static.json` and no `static.encrypted.json` is not a half-written pair,
    it is a layer `add_team` never finished, and writing the plain file beside it would produce an
    answer that is read *at the team layer* by a chain that reads the missing file first — which is
    exactly the "the team file just isn't there" bug the empty pair exists to prevent.

    The sentence names the verb that creates the layer (`kmt config init` for the first environment,
    `kmt config add-environment` for every one after it, `kmt config add-team` for a team), because
    the missing thing is a *layer* and not a file an operator should be told to touch. Nothing is
    written on the way out of this refusal, in either verb.
    """
    pair = _layer_files(config, team)
    missing = [path for _, path in pair if not path.is_file()]
    if not missing:
        return pair
    if team is None:
        raise click.ClickException(
            f"{missing[0]} is absent, so environment {environment!r} is not a layer of {root}. "
            f"config set and config unset put one answer on one layer that already exists and lay no "
            f"layer down, so a file that is not there is refused and not created — the same "
            f"anti-bootstrap claim kmt config add-environment and kmt config add-team make. Run "
            f"kmt config add-environment {root} {environment} (or kmt config init {root} "
            f"--environment {environment}, if this tree has no manifest yet) and come back.")
    raise click.ClickException(
        f"{missing[0]} is absent, so team {team!r} is not a layer of environment {environment!r} in "
        f"{root}. config set and config unset put one answer on one layer that already exists and lay "
        f"no layer down, and a team layer that is only half there is read through the environment's "
        f"file — so a hand-made directory is an answer that comes back from the layer below. Run "
        f"kmt config add-team {root} {environment} {team} first, then answer the key.")


def _read_document(path: Path) -> dict:
    """The document one layer file holds, or a refusal that says which file is unreadable.

    Both verbs operate on the operator's own working copy, so a file that is not JSON, not an object,
    or not readable is a refusal and not a reason to start from `{}`. Silently treating an unreadable
    file as empty is how a read-modify-write verb becomes a delete verb: the document it "did not
    exist" is the document that no longer has the operator's keys in it.
    """
    try:
        # The read is the seam's, so a file holding ciphertext is refused by name and asked
        # about git-crypt rather than about the utf-8 codec. `LockedConfigFileError` is a
        # `ClickException` and not an `OSError`, so it walks past the handler below untouched — which
        # is what the docstring's promise needed to mean for this input.
        raw = read_decrypted(path, "static layer file")
    except OSError as error:
        raise click.ClickException(
            f"{path} cannot be read ({error}). config set and config unset edit the file that is "
            f"there and write nothing else, so they cannot go on without it: fix the file or its "
            f"permissions and run the same command again.")
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise click.ClickException(
            f"{path} is not valid JSON ({error}), so there is no document here to put one answer "
            f"into. kubementat will not rewrite a file it cannot read and call the result a "
            f"configuration; repair the file and run the same command again.")
    if not isinstance(document, dict):
        raise click.ClickException(
            f"{path} holds a JSON {type(document).__name__} and not the object a static layer is, so "
            f"there is no key in it to answer or remove. A layer file is one object keyed by answer "
            f"name; repair the file and run the same command again.")
    return document


def _require_keyable_team_layer(key: str, team, root: Path, environment: str) -> None:
    """Refuse a cluster key on `--team`, and nothing else.

    This is the only key a team-scoped answer is refused for, and the reason is not a
    classification table: **one directory is exactly one cluster.** What resolves a cluster reads the
    environment's `static.json` and no other path (`EnvironmentRegistry.resolve` and
    `_environment_declaration`, both in `core/identity/registry.py`), so a team
    that answers `CLUSTER_KUBE_CONTEXT` writes a document no reader consults, and the tree now has
    two accounts of one null. Census H mapped this boundary. What answers "who reads this key, and
    from which file" is `config show`, which tags each key with its layer and its file; putting that
    answer in here would be a guess about every call site in `src/`, and the tagging bar lives in
    the read verbs rather than in a write verb, so no table of "keys a team reader actually
    consults" appears in this module and none is needed.
    """
    if team is None or key not in (constants.CLUSTER_KUBE_CONTEXT_KEY, constants.CLUSTER_KUBECONFIG_PATH_KEY):
        return
    raise click.ClickException(
        f"{key} on --team is refused. One directory is exactly one cluster: what resolves a "
        f"cluster reads the environment's own static file, "
        f"{Config(environment, None, platform_config_dir=root, preload_configs=False).env_static_config_path}, "
        f"and no other path, so a team-layer "
        f"answer here is read by nothing and leaves the tree with two accounts of the same key. "
        f"Answer it on the environment instead, with no --team: kmt config set {root} {environment} "
        f"{key}=…")


def set_answer(root, environment, key, value, team=None, encrypted=False) -> tuple[InitStep, ...]:
    """Put one answer into one layer's file, keeping every key this verb was not told about.

    `--set`'s parser typed the value (`parse_answer_argument`), this function owns the file. One file
    and not the pair: the operator says which spelling the answer takes by `--encrypted`, and writing
    the sibling too would put the same answer in two places and turn "where did it land" into a second
    question. Removal is the verb that has to honour the pair, and `unset_answer` below is it.

    A value that is already there and is **different** is overwritten, not refused — that is what this
    verb is for. It is the opposite of `init`'s rule for an existing *file*, which refuses because an
    edited answer and a half-written file are not tellable apart, and it is an *answer* rule rather
    than a *file* rule, so it must not be read as the same claim. Setting a value that is already
    exactly there is not an event: the step is `SKIPPED` and no byte moves, which is the same rule the
    layer verbs report a byte-identical file under.

    Refusals, all before the first byte: the layer's files are not there (`_require_answerable_layer`);
    a `null` value, which is the absence of an answer and is `unset`'s business and not this verb's;
    and a cluster key on `--team` (`_require_keyable_team_layer`).

    Returns the one step that describes the one file it was given, in the pair's own order —
    `CREATED` if it changed, `SKIPPED` if it was already exactly this. No directory is made, no
    template is copied, no manifest is written.
    """
    root = Path(root)
    _require_configuration_root(root, _ANSWER_CLAIM)
    if value is None:
        raise click.ClickException(
            f"{key} = null is not an answer, and this verb writes answers. null is the absence of one, "
            f"and only one verb should be able to create it: kmt config unset {root} {environment} "
            f"{key}{' --team ' + team if team else ''} takes it away, and a key that answers nothing "
            f"should not be held open with a placeholder. To store a real answer, pass a value that "
            f"is not null.")
    config = Config(environment, team, platform_config_dir=root, preload_configs=False)
    _require_keyable_team_layer(key, team, root, environment)
    pair = _require_answerable_layer(config, root, environment, team)

    # The file the answer lands in, picked by name out of the pair rather than by spelling a second
    # path: the property is the reader's own name for the place, and this module already said so about
    # the layer directories.
    chosen = (TEAM_ENCRYPTED_STEP if encrypted else TEAM_STATIC_STEP) if team is not None else (
        ENVIRONMENT_ENCRYPTED_STEP if encrypted else ENVIRONMENT_STATIC_STEP)
    name, path = next(entry for entry in pair if entry[0] == chosen)

    document = _read_document(path)
    updated = dict(document)
    updated[key] = value
    payload = _json_bytes(updated)
    if payload == path.read_bytes():
        return (InitStep(name, path, SKIPPED),)
    if updated == document:
        # The same answer, written by a tool that formats the file differently. Rewriting it would be
        # this verb taking credit for a write it made for looks, so it says `skipped` and leaves the
        # operator's layout alone. Byte equality above is the rule; this is the same claim read
        # semantically, and it is the only reason a hand-formatted file is not reformatted.
        return (InitStep(name, path, SKIPPED),)
    path.write_bytes(payload)
    return (InitStep(name, path, CREATED),)


def unset_answer(root, environment, key, team=None) -> tuple[InitStep, ...]:
    """Take one answer away from a layer, from **both** of its files.

    The pair rule, made honest: the encrypted file is ordered first, so clearing only the plain file
    is a no-op on the read side and the operator watches the value they just removed come back. Both
    files of the named layer are cleared, which is what lets the layer above answer — the stated
    `unset` contract, and the reason this verb is not `set_answer(key=None)` wearing a different name
    (that spelling is refused above, and deliberately).

    Like `set_answer`, this writes into files that already exist and creates nothing: no layer, no
    directory, no manifest. A key that **no layer of the named scope declares** is refused rather than
    ignored, and the sentence lists the files it searched. The scope is what decides the search: a
    `--team` answer sits inside an environment, so the environment's files are searched for the question
    "is this key declared anywhere here" even though only the team's pair is written, and a team-less
    `unset` addresses one environment and no team's copy of it. That distinction is the whole refusal —
    an undeclared key is a typo the operator should hear about, while a key that lives in the layer
    below is answered by that layer and is told so by two `skipped` lines rather than by a lie.

    The refusal set is `set_answer`'s, minus the `null` sentence and plus nothing: the layer must
    already exist, and a cluster key on `--team` is refused for the same reason `set_answer` refuses
    it (a team that
    carries one is material no reader consults, and clearing it belongs to the environment's layer).

    Returns one step per file of the named layer, encrypted first, so the pair's order is visible in
    the printed lines: `CREATED` where the key was removed, `SKIPPED` where the file already declared
    nothing. Both files are decided before either is written.
    """
    root = Path(root)
    _require_configuration_root(root, _ANSWER_CLAIM)
    config = Config(environment, team, platform_config_dir=root, preload_configs=False)
    _require_keyable_team_layer(key, team, root, environment)
    pair = _require_answerable_layer(config, root, environment, team)

    documents = {path: _read_document(path) for _, path in pair}

    # What counts as *declared* is the scope's business and not the write set's. A team answer is one
    # answer sitting on top of an environment answer, so the environment's pair is searched too — and
    # searched only, never written, because the layer this verb was pointed at is the layer it clears.
    searched = list(pair)
    if team is not None:
        searched += [entry for entry in _layer_files(config, None) if entry[1].is_file()]
        documents.update({path: _read_document(path) for _, path in searched[len(pair):]})

    if not any(key in document for document in documents.values()):
        reach = "" if team is not None else (
            f" A key that belongs to a team is answered by that team's layer, so pass --team to reach "
            f"it; without it this verb addresses environment {environment!r} and nothing else.")
        scope = (f"environment {environment!r}, team {team!r}" if team is not None
                 else f"environment {environment!r}")
        raise click.ClickException(
            f"{key} is declared by no layer of {scope}, so there is no answer here to take away. "
            f"kubementat searched {', '.join(str(path) for _, path in searched)} and found the key in "
            f"none of them. That is a different fact from \"the answer is null\": the file that "
            f"declares the key is the file that answers, and nothing is written for a key that was "
            f"never there.{reach}")

    # Plan both files, then write: the same decide-then-write shape `_apply_layer_plan` uses, so a
    # refusal halfway through cannot leave one half of the pair cleared and the other not.
    plan: list[tuple[str, Path, bytes | None]] = []
    for name, path in pair:
        document = documents[path]
        if key not in document:
            plan.append((name, path, None))
            continue
        plan.append((name, path, _json_bytes({k: v for k, v in document.items() if k != key})))

    steps: list[InitStep] = []
    for name, path, payload in plan:
        if payload is None:
            steps.append(InitStep(name, path, SKIPPED))
            continue
        path.write_bytes(payload)
        steps.append(InitStep(name, path, CREATED))
    return tuple(steps)


# ---------------------------------------------------------------------------------------------
# One key, in one file, invented rather than typed.
#
# `generate_answer` is `unset_answer`'s mirror and not a third implementation of answer-reading: the
# same searched-file set, the same meaning of "answers nothing", the same refusal shape. What differs
# is only *who supplied the value* — which is the whole of the rule, and the reason this function
# names no key. A list of secret key names would be the second source of truth about keys that the
# rule was rewritten to forbid, and `--generate BASE_DOMAIN` is therefore allowed to invent something
# nonsensical. That is the price and it is the right price: the alternative is code that classifies
# keys.
#
# The refusal is the ticket. A generate that overwrote would make the act of touching a directory the
# way a credential is lost, so an answer that is there stays there and rotation is the two-step the
# sentence spells. Nothing is written on the way out of that refusal.
# ---------------------------------------------------------------------------------------------

# `--generate`'s length, spelled once. `secrets.token_urlsafe(nbytes)` is the generator and `nbytes`
# is the only number that decides what comes out, so a test that hard-coded the resulting string
# length would be a test that disagrees with its own generator. `automation.py`'s legacy
# `generate_password` is the same stdlib call with the same length; it stays there until phase 8
# deletes it, because importing that object into a library module would drag a `Context` in behind a
# value. Two call sites of one stdlib function is not two owners.
GENERATED_ANSWER_BYTES = 32


def _layer_phrase(step_name: str, environment: str, team) -> str:
    """Which layer a searched file belongs to, in the words a refusal uses to say so.

    The step name is the input and not a path match: it already says *team* or *environment* and which
    of the pair's two files it is, because `_layer_files` returns the pair under `init`'s own names.
    """
    if step_name in (TEAM_STATIC_STEP, TEAM_ENCRYPTED_STEP):
        return f"team {team!r} of environment {environment!r}"
    return f"environment {environment!r}"


def generate_answer(root, environment, key, team=None, encrypted=False) -> tuple[InitStep, ...]:
    """Invent one answer for one key, and only where that key currently answers nothing.

    **The mirror.** The search that decides "answers nothing" is `unset_answer`'s and no other: the
    named layer's pair, plus the environment's pair when a team is named, read through
    `_read_document` and filtered by nothing else. So a `--team` generate sees the environment's
    answer and refuses it, exactly as a `--team` unset would clear it, and a team-less generate
    addresses one environment and no team's copy of it. A present key with a non-null value is an
    answer; absent or `null` is not, which is why a `null` cluster key from `init` is generated into
    rather than refused — `null` is the absence of an answer, and it is the only spelling here that
    differs from `unset`'s question of "is this key declared".

    **The refusal, which is the ticket.** Where something already answers, nothing is written: the
    sentence names the key, every file that answers it, the layer each of those files belongs to, and
    the two-step that rotates it — `config unset` that key on the layer that declared it, then
    `config set --generate` it again. When the answering layer is not the layer the verb was pointed
    at, the rotation names that layer, because an unset aimed at the team layer would clear a
    different answer than the one blocking the generate.

    **The write, which is `set_answer`'s.** `--encrypted` picks which of the layer's two files the
    value lands in, defaulting to the plain one, and that is the whole layer rule: no branch sends
    anything to the encrypted file because of what its key is called, since that branch is key
    classification wearing a convenience. Refusals are `set_answer`'s for the same reasons — a root
    that is not a directory, a layer that does not exist (naming the verb that makes it), a cluster
    key on `--team` — and the step vocabulary gains no fourth word.

    Returns whatever `set_answer` returns for the one file it writes, in the pair's own order.
    """
    root = Path(root)
    _require_configuration_root(root, _ANSWER_CLAIM)
    config = Config(environment, team, platform_config_dir=root, preload_configs=False)
    _require_keyable_team_layer(key, team, root, environment)
    pair = _require_answerable_layer(config, root, environment, team)

    # `unset_answer`'s searched set, spelled the same way and for the same reason: a team answer sits
    # inside an environment, so the layer above is searched for "does this answer already exist" even
    # though only the named layer would be written.
    searched = list(pair)
    if team is not None:
        searched += [entry for entry in _layer_files(config, None) if entry[1].is_file()]
    documents = {path: _read_document(path) for _, path in searched}
    answering = [(name, path) for name, path in searched
                 if key in documents[path] and documents[path][key] is not None]

    if answering:
        where = ", ".join(f"{path} ({_layer_phrase(name, environment, team)})"
                          for name, path in answering)
        # One rotation pair per answering layer, because the answer that blocks this generate is the
        # answer `unset` has to take away, and that is not always the layer this verb was pointed at.
        scopes = list(dict.fromkeys(
            f" --team {team}" if name in (TEAM_STATIC_STEP, TEAM_ENCRYPTED_STEP) else ""
            for name, _ in answering))
        rotation = "; ".join(
            f"kmt config unset {root} {environment} {key}{scope}, then kmt config set {root} "
            f"{environment} --generate {key}{scope}"
            for scope in scopes)
        raise click.ClickException(
            f"{key} already answers in {where}, so --generate refuses and writes nothing. kubementat "
            f"keeps a value that is there rather than rewriting it in place: a verb that "
            f"generated while it wrote would make the act of touching a directory the way a "
            f"credential is lost. To rotate it, take the answer away from the layer that declares it "
            f"and ask for a new one: {rotation}.")

    return set_answer(root, environment, key, secrets.token_urlsafe(GENERATED_ANSWER_BYTES),
                      team=team, encrypted=encrypted)


def cluster_is_unaddressed(static_json_path) -> bool:
    """Whether that `static.json` leaves its environment addressing no cluster.

    The sentence below is only true when the file actually says so, and `config init` can be handed
    the answer itself with `--set CLUSTER_KUBE_CONTEXT=…` -- at which point printing "is null" would
    be a false statement about a tree the command just configured. Absent, `null`, or blank all mean
    the same thing here: nobody has named a cluster yet.

    Absent is the only file that answers `True` without reading anything. A file that is **present
    and cannot be read** used to answer `True` too, and that was the one false sentence in `src/`:
    the predicate reported that the environment addresses no cluster about a file it had never
    decoded, which `config validate` and `config init` would then print as a confident fact.
    It now goes through the same seam as every other read and raises.
    """
    path = Path(static_json_path)
    if not path.is_file():
        return True
    try:
        document = json.loads(read_decrypted(path, "environment's static.json"))
    except json.JSONDecodeError:
        # Not JSON at all is still a file that declares no cluster; only the *read* got louder here,
        # and a document that is not an object keeps the answer it always kept.
        return True
    if not isinstance(document, dict):
        return True
    value = document.get(constants.CLUSTER_KUBE_CONTEXT_KEY)
    return value is None or (isinstance(value, str) and not value.strip())


def warn_cluster_not_addressed(environment, static_json_path) -> None:
    """Say that `<environment>` exists but does not yet reach a cluster.

    The one owner of that sentence, reached from `config init`'s closing prose and from a fresh
    `init_configuration` alike. It needs only the environment name and the file it is pointing at,
    which is why it is a function here and not a `Config` method: the verb half has a `Config` and a
    `Context` and this module has neither.

    It is guidance about state and not the command's output, so it goes out as a warning rather than
    as a line of the run's stdout. The sentence is the product's and the channel is not: `logging` is
    what the surrounding library uses everywhere else (`config.py:905`, `automations/routing.py:320`),
    and this was the one place under `src/kubementat/` that wrote operator-facing text straight to a
    stream. The CLI's `configure_logging` puts warnings on stderr, so a human at a terminal
    still reads this below the `created` lines; under `kmt` the difference is the record's timestamp
    and `WARNING -` prefix.

    The three facts it owes the reader — that the environment is configured, that
    `CLUSTER_KUBE_CONTEXT is null`, and which file to edit — are asserted by
    `tests/test_config_init_creates_a_tree_and_reports_what_it_touched.py` through the logging capture,
    so they survive as an API and not as prose.
    """
    logging.warning(
        f"Environment '{environment}' is configured, but it does not address a cluster yet: "
        f"CLUSTER_KUBE_CONTEXT is null in {static_json_path}. "
        "Edit that file to name the context that reaches this environment's cluster "
        "(and CLUSTER_KUBECONFIG_PATH too, if it is not the kubeconfig kubectl reads by default). "
        "kubementat will not fill it in from this machine: an environment is invalid until someone "
        "says which cluster it is, which is why init wrote null and not a guess. "
        "Run kmt environments list to see what every configured environment addresses.")
