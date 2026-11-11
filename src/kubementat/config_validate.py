"""`config validate`'s library half — the verdict about a tree, and nothing else.

One question, answered by one function: **is the tree this command was handed usable, and if not,
in how many places?** The answer is a tuple of findings and never a raised sentence per fault,
because the established idiom when there is more than one problem is the library's own — collect
them all, then say all of them rather than the first (`helm_utils.py:303`, `cluster.py:594`). The
CLI turns the collected errors into one `click.ClickException` and prints the warnings itself with
`err=True` (`utilities.py:66`'s shape); this module raises nothing over a verdict except the two
refusals that mean "there is no tree to read".

Two things a reader of this file should not have to hunt for.

**Which rules here are decisions and which are restatements.** Most of what `validate` says is
already said somewhere: the 1:1 collision refusal (`cluster.py:768-797`, reported through the
registry and not re-decided), the environment file's shape (`cluster.py:800-830`), the missing
layer (`config_init._require_answerable_layer`), and the cluster-not-addressed fact
(`config_init.cluster_is_unaddressed`). Where an owner exists, this module calls it and carries its
sentence. Two rules have no owner anywhere in `src/` and are therefore decided here for the first
time: what `kubementat.yaml` means (nothing in `src/` parses it today — no `yaml.safe_load`, no key
lookup, no shape check), and whether a second manifest exists (nothing looks for one). The comments
on those checks say so rather than implying a decision made elsewhere.

**What is deliberately not checked.** Three things, stated once here and nowhere in a `--help`:

* **git-crypt's unlock status.** Phase 5 owns that dance, no `config_*` module touches git-crypt —
  it is never execed, checked, or guessed at beyond the fact that a file will not decode. A locked
  file is reported by `read_decrypted`'s seam as `locked-configuration-file`, named and not decoded.
* **"Layering cycles".** Two layers with no cross-references between them cannot form one, so a
  check for it is code that can never fire — and code that can never fire is untested code.
* **Dead keys.** A key no code reads is a fact about `src/` and not about the tree, so no verdict
  here can say it. `config show` prints the key and the file that declares it, which is as far as any
  command reaches; a second opinion about the same key is how two answers get written and one read.

And one check that was deliberately left out and is not any more: the version window. `config
validate` used to say that `schema_version` and the `kubementat:` constraint belong to a migration
that had already landed, and the tree it was handed passed with a stamp three steps stale — which is
what this verb exists to refuse. The strict home for version drift is this verb alone, since the verb
that used to share it was deleted on 2026-11-05: `validate` reads the same `ConfigurationStamp` that
every cluster command reads — its one owner — and reports the two facts it is strict about. What is
still not checked is git-crypt's lock state, which is `read_decrypted`'s seam and never a verdict here.

Read-only is a claim this module has to keep: it opens the tree's files for reading, enumerates
directories, and calls predicates that also only read. It builds no cluster client, execs nothing,
and resolves no kubeconfig — the collision answer it reports comes out of the registry, whose own
probes are file reads and (in a pod) two env vars.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import click
import yaml

from kubementat import cluster as _cluster
from kubementat import config_init
from kubementat.config import ConfigurationStamp, Config
from kubementat.config_reads import LockedConfigFileError, read_decrypted

# The finding kinds. Exactly two, because the exit code is decided by their presence and nothing
# else: `error` refuses, `warning` prints and continues. That second shape is not invented here --
# it is what `warn_cluster_not_addressed` and the once-per-run layer warnings already do, and it is
# the shape validate's own report has to keep if the exit code is still decided by one thing.
ERROR = "error"
WARNING = "warning"

# The manifest keys, spelled here rather than remembered. `config_init._manifest_bytes` writes
# these two and nothing else; no other reader exists to ask, which is the whole reason this module
# checks them at all (census Q2: zero readers of `kubementat.yaml` in `src/`).
_MANIFEST_KEYS = ("schema_version", "kubementat")

# What a shipped template leaves behind when nobody answered the key. The spec's table spells the
# family `SETME`; every template under `src/kubementat/assets/templates/` and every reader that
# recognises one (`routing.py:561`, `automation.py:475-477`) spells it `REPLACEME_`, and an honest
# check matches the spelling that actually ships.
_PLACEHOLDER_PREFIX = "REPLACEME"

# How far from the root a layer file may sit: depth 1 is an environment, depth 2 under one is a
# team. `EnvironmentRegistry.environments()` (`cluster.py:709-724`) and `Config.teams()`
# (`config.py:889-925`) already answer "what is there"; these checks only name the near-misses
# neither of them can see because they do not enumerate what they filter out.
_ENVIRONMENT_DEPTH = 1
_TEAM_DEPTH = 2

# The filename that must occur exactly once, reached through its one spelling in `config_init`
# rather than re-typed (loose end 1 of the census names the second spelling; this module does not
# add a third).
_MANIFEST = config_init.CONFIG_MANIFEST_FILENAME

# kubementat's own layer directories inside every environment: a directory by one of these names
# that carries a `static.json` is claimed twice, by the team listing and by the layer.
_LAYER_NAMES = config_init.LAYER_DIRECTORY_NAMES

# Where product shape is not allowed to live: no helmfile template inside a config tree.
_HELMFILE_PATTERN = "helmfile*.gotmpl"


@dataclass(frozen=True)
class Finding:
    """One statement about the tree, with enough on it to act on without opening the file.

    `code` is the stable half: tests assert the slug and not the prose, because the sentence may
    be reworded and the contract must not be. `path` points at the file (or directory) the finding
    is about, so a run of a hundred findings still reads as a to-do list and not a transcript.
    """

    kind: str
    code: str
    message: str
    path: Optional[Path]


@dataclass(frozen=True)
class ValidationReport:
    """The whole verdict, in the order the operator will read it.

    Sorting is `(kind, path, code)`, so two runs of one unchanged tree print the same bytes — the
    same reason `config show` sorts its listing. `errors` and `warnings` are views over the tuple
    and not extra state, so a report cannot contradict itself.
    """

    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.kind == ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.kind == WARNING)


def _sorted(findings: list[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(findings, key=lambda f: (f.kind, str(f.path or ""), f.code)))


def validate_tree(root, environment, team=None) -> ValidationReport:
    """Every finding the tree warrants, in the order the CLI will print them.

    The root-wide half (the rules that cannot be scoped to one environment) runs over the whole
    directory and is reported even when the named environment is clean, because "validate my
    environment" must not hide a broken root. The layer-scoped half looks only at the named
    environment, and at `team`'s files when one is named.

    Raises
    ------
    click.ClickException
        For the two states that mean there is nothing to validate: `root` is not a directory, and
        no environment was named. Every *fault* inside a tree that exists becomes a finding and
        never a raise.
    """
    root = Path(root)
    # The same root refusal the read-only verbs use, with this verb's claim spelled in it so the
    # sentence says which verb the operator came to.
    config_init._require_configuration_root(
        root, "config validate reads a tree that exists and writes nothing into one")
    if not environment:
        raise click.ClickException(
            "config validate needs an environment: it answers the files of one environment and "
            "the root that holds them, and no environment means 'all of them'.")

    findings: list[Finding] = []
    _manifest_findings(root, findings)
    _stamp_findings(root, findings)
    _single_manifest_findings(root, findings)
    _helmfile_findings(root, findings)
    _layout_findings(root, findings)
    _collision_findings(root, findings)
    _layer_findings(root, environment, team, findings)
    return ValidationReport(_sorted(findings))


# ---------------------------------------------------------------------------------------------
# The root-wide half.
# ---------------------------------------------------------------------------------------------


def _manifest_findings(root: Path, findings: list[Finding]) -> None:
    """What `kubementat.yaml` is allowed to say — decided here, for the first time.

    No owner exists in `src/`: there is no reader, no key lookup, and no shape check anywhere
    (`config.py` exposes no property for it; `config_init.py:86-89` records the absence). So this
    function does not restate a rule, it sets one, and the rule is one sentence: the file says which
    product this tree is for and nothing else. Which environments and teams exist is a fact about
    the directories, not about this file, so `environments:` or `teams:` are reported as keys
    kubementat does not know and *not* as configuration that is merely early.

    The version window is checked in `_stamp_findings`, against the same `ConfigurationStamp` every
    cluster command reads, for the reason that this verb is the one that gets to be strict
    about it. What is decided here and nowhere else is the *shape* of the manifest, which no
    other reader in `src/` has ever opened.
    """
    path = root / _MANIFEST
    if not path.is_file():
        findings.append(Finding(ERROR, "missing-manifest",
                                f"{path} is absent, so {root} is not a kubementat configuration "
                                f"tree. Run kmt config init {root} --environment <environment>.",
                                path))
        return

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        findings.append(Finding(ERROR, "unreadable-manifest",
                                f"{path} could not be read: {exc}", path))
        return
    except yaml.YAMLError as exc:
        findings.append(Finding(ERROR, "unparsable-manifest",
                                f"{path} is not valid YAML: {exc}", path))
        return

    if document is None:
        document = {}
    if not isinstance(document, dict):
        findings.append(Finding(ERROR, "manifest-is-not-a-mapping",
                                f"{path} is not a mapping, so it says nothing kubementat can read. "
                                f"It holds exactly {_MANIFEST_KEYS[0]} and {_MANIFEST_KEYS[1]} and "
                                f"nothing else.", path))
        return

    declared = set(document)
    for key in sorted(declared - set(_MANIFEST_KEYS)):
        findings.append(Finding(
            ERROR, "unknown-manifest-key",
            f"{key!r} is not a key kubementat knows about in {path}. Only "
            f"{_MANIFEST_KEYS[0]} and {_MANIFEST_KEYS[1]} are read: which environments and which "
            f"teams exist is a fact about the directories under {root} and not about this file "
            f"(kmt environments list prints the directories).", path))

    if "schema_version" not in declared:
        findings.append(Finding(ERROR, "missing-manifest-key",
                                f"{path} does not declare {_MANIFEST_KEYS[0]}, so kubementat "
                                f"cannot tell which product this tree is for. kmt config init "
                                f"stamps it.", path))
    elif isinstance(document["schema_version"], bool) or not isinstance(document["schema_version"], int):
        findings.append(Finding(
            ERROR, "manifest-key-type",
            f"{_MANIFEST_KEYS[0]} in {path} is a "
            f"{type(document['schema_version']).__name__} and not an integer, so it cannot be "
            f"compared and will not be guessed.", path))

    if "kubementat" not in declared:
        findings.append(Finding(ERROR, "missing-manifest-key",
                                f"{path} does not declare {_MANIFEST_KEYS[1]}, which is the key "
                                f"that says which product and which versions answer this tree. "
                                f"kmt config init stamps it.", path))
    elif not isinstance(document["kubementat"], str):
        findings.append(Finding(
            ERROR, "manifest-key-type",
            f"{_MANIFEST_KEYS[1]} in {path} is a "
            f"{type(document['kubementat']).__name__} and not a version-constraint string like "
            f"'>=0.4,<2', so there is nothing here to compare a release against.", path))


def _stamp_findings(root: Path, findings: list[Finding]) -> None:
    """The version window, which is this verb's job and not every cluster command's.

    Two facts, and they come from `config.ConfigurationStamp` rather than from a comparison written
    beside it: the same object answers every cluster command, and two readers that disagree about a
    window they were given from the same two keys is not a report, it is a lottery. A cluster verb
    may warn and continue, on the ground that a version string must not brick a running
    cluster. This verb's whole product is the verdict, so both facts are findings here and an
    unchanged stamp is no finding at all.

    * **The schema window** is a refusal: two steps behind and every answer this tree does not
      name for itself comes out of a built-in default, which configures the cluster by kubementat's
      assumptions instead of the operator's. The remedy sentence is the one this refusal owes.
    * **The version constraint** is a warning, said as a refusal. The gap is closed by one line
      in the manifest — the `kubementat:` constraint the tree was stamped with, which is a release's
      bump PR and not a key an operator sets. Nothing reports what the gap would cost; the
      verdict is the whole of what this verb can say.

    A tree that cannot be stamped at all is the manifest check's business and is reported there, in
    sentences that name the file and the verb that writes it; this function reads nothing a manifest
    check has not already refused.
    """
    path = root / _MANIFEST
    try:
        stamp = ConfigurationStamp.read(root)
    except click.ClickException:
        return

    if stamp.schema_is_too_old:
        findings.append(Finding(
            ERROR, "schema-version-too-old",
            f"{root} declares schema_version {stamp.schema} and this kubementat is at "
            f"{stamp.current}, which is more than {stamp.window} step(s) behind. Answers this tree "
            "does not name for itself would come from built-in defaults, which configures the cluster "
            "by kubementat's assumptions and not by yours. Raise schema_version in "
            f"{root / 'kubementat.yaml'} to close the gap; that bump is a decision, and no command "
            "will make it for you.",
            path))
    elif stamp.schema_is_behind_window:
        findings.append(Finding(
            WARNING, "schema-version-behind-window",
            f"{root} declares schema_version {stamp.schema} and this kubementat is at {stamp.current}. "
            f"One step behind is inside the window {stamp.window} step(s) wide, so answers this tree "
            "names still come from this tree. Bringing the stamp current is one line in "
            f"{root / 'kubementat.yaml'}: raise schema_version to {stamp.current}.",
            path))

    if stamp.version_is_outside_constraint:
        findings.append(Finding(
            ERROR, "version-outside-window",
            f"{root} declares it was validated against {stamp.constraint!r} and this kubementat is "
            f"{stamp.running_version}. A cluster verb warns about this and continues; this "
            f"verb refuses, because this verb is the strict home for version drift. Nothing here says "
            f"what the gap would cost, and the line that closes it is the kubementat: constraint this "
            "tree was stamped with — a release's bump PR, "
            "not a key to set.",
            path))
    elif not stamp.version_is_determinable:
        findings.append(Finding(
            WARNING, "version-undeterminable",
            f"kubementat could not determine its own version, so {root}'s declared "
            f"{stamp.constraint!r} could not be compared. Run kmt --version to see what this build "
            "is; nothing about the tree is wrong.",
            path))


def _single_manifest_findings(root: Path, findings: list[Finding]) -> None:
    """Exactly one `kubementat.yaml` anywhere: no manifest below the root, and today
    nothing in `src/` looks for a second one (census Q2: no merging, no layer-level acceptance, no
    refusal) — so the check has no owner to call and lives here alone.
    """
    found = sorted(_walk_files(root, _MANIFEST))
    if len(found) <= 1:
        return
    canonical = root / _MANIFEST
    for path in found:
        if path == canonical:
            continue
        where = path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else "the root"
        findings.append(Finding(
            ERROR, "duplicate-manifest",
            f"{path} is a second {_MANIFEST}, and kubementat reads exactly one: the one at the "
            f"root of {root}. A manifest carried by {where} would make that directory a different "
            f"product than its neighbours, which is what the single manifest refuses. Delete it; "
            f"what a directory declares belongs in that environment's static.json.", path))


def _helmfile_findings(root: Path, findings: list[Finding]) -> None:
    """No `helmfile*.gotmpl` inside a configuration tree — product shape belongs to the product,
    and `config validate` is the verb that refuses it. Nothing today refuses a
    hand-written helmfile in the config, so this decision is here for the first time too.
    """
    for path in sorted(_walk_files(root, None, pattern=_HELMFILE_PATTERN)):
        findings.append(Finding(
            ERROR, "helmfile-in-config-tree",
            f"{path} is a helmfile template inside the configuration tree. Product shape is "
            f"kubementat's half and configuration is yours: this file belongs to the product's "
            f"helmfile, and putting it here makes two places decide what gets applied.", path))


def _layout_findings(root: Path, findings: list[Finding]) -> None:
    """The near-misses the two enumerations cannot report.

    `EnvironmentRegistry.environments()` and `Config.teams()` say what *is* an environment or a
    team; neither says why something that looks like one was skipped. These three are the whole
    list of that kind, and each is a directory a human could hand to `config set` and be refused
    without the reason being on the page.
    """
    for path in sorted(_walk_files(root, "static.json")):
        depth = len(path.parent.relative_to(root).parts)
        if depth == _ENVIRONMENT_DEPTH:
            continue
        if depth == _TEAM_DEPTH and (path.parent.parent / "static.json").is_file():
            continue
        findings.append(Finding(
            ERROR, "static-json-wrong-depth",
            f"{path} sits at depth {depth} under {root}, and kubementat reads this file at depth "
            f"{_ENVIRONMENT_DEPTH} (an environment) or at depth {_TEAM_DEPTH} directly under one "
            f"(a team). Everything else is skipped, so the answers in it are not read by any "
            f"command.", path))

    for directory in sorted(_walk_directories(root)):
        if (directory / "static.encrypted.json").is_file() and not (directory / "static.json").is_file():
            findings.append(Finding(
                ERROR, "encrypted-only-layer",
                f"{directory} holds static.encrypted.json and no static.json. A layer is the pair "
                f"and both enumerations look for the plain half, so this directory is skipped: "
                f"nothing it declares answers anything. kmt config add-environment / kmt config "
                f"add-team lay a complete layer.", directory))

    # A team wearing one of kubementat's own layer names is claimed by two rules: `Config.teams()`
    # would list it (the listing filters `kubementat_components/` and nothing else that needs a
    # static.json), while the readers of that layer open it as kubementat's own. `config_init`
    # refuses the name at write time; this reports the ones that got in before the refusal, or by
    # hand.
    for environment_dir in sorted(entry for entry in root.iterdir() if entry.is_dir()):
        if not (environment_dir / "static.json").is_file():
            continue
        for entry in sorted(p for p in environment_dir.iterdir() if p.is_dir()):
            if entry.name in _LAYER_NAMES and (entry / "static.json").is_file():
                findings.append(Finding(
                    ERROR, "team-named-like-layer-directory",
                    f"{entry} is named {entry.name!r}, one of kubementat's own layer directory "
                    f"names, and holds a static.json. That makes it read twice: as a team by "
                    f"kmt config show's layer listing and as kubementat's {entry.name} layer by "
                    f"every reader that opens it. Name the team something else.", entry))


def _collision_findings(root: Path, findings: list[Finding]) -> None:
    """The 1:1 decision, carried and not re-decided.

    `EnvironmentRegistry.collision_check` owns this rule (the census' strongest owner, refusing at
    `cluster.py:1142`); what validate adds is only that it asks the question for every environment
    in the root instead of for the one about to be installed into. Each refusal sentence is taken
    from `Context._collision` verbatim — the sentence that already sends operators to
    `kmt environments list` — and each unordered pair is reported once, because two environments
    that collide describe one fault and not two.
    """
    registry = _cluster.EnvironmentRegistry(root)
    reported: set[frozenset[str]] = set()
    for name in registry.environments():
        record = registry.resolve(name)
        if not record.resolved:
            continue
        comparison = registry.collision_check(record.target)
        others = tuple(c for c in comparison.collisions
                       if frozenset((name, c.environment)) not in reported)
        if not others:
            continue
        reported.update(frozenset((name, c.environment)) for c in others)
        sentence = str(_cluster.Context._collision(record.target, others))
        findings.append(Finding(ERROR, "cluster-collision", sentence,
                                root / name / "static.json"))


# ---------------------------------------------------------------------------------------------
# The layer-scoped half: the named environment, and its team when one is named.
# ---------------------------------------------------------------------------------------------


def _layer_findings(root: Path, environment: str, team: Optional[str],
                    findings: list[Finding]) -> None:
    # The same path table a `Config` reads these files through, so validate answers about the files
    # a command actually opens and not about a hand-written list of names.
    config = Config(environment, team, platform_config_dir=root, preload_configs=False)
    pairs: list[tuple[str, Path, str]] = [
        ("environment", config.env_static_config_path, "environment"),
        ("environment encrypted", config.env_static_encrypted_config_path, "environment"),
    ]
    if team is not None:
        pairs += [("team", config.team_static_config_path, "team"),
                  ("team encrypted", config.team_static_encrypted_config_path, "team")]

    # The pair is what a layer is, so it is checked per layer: the environment's half and the team's
    # half get their own sentence from `_require_answerable_layer`, which names the verb that lays
    # the layer down. Probing once with `team` attached would report a missing *environment* file
    # with the team's refusal sentence, which is the kind of wrong-but-plausible message the whole
    # collect-then-report shape exists to avoid.
    # The pair is what a layer is, so it is checked per **layer** and not per file label: an
    # environment's plain file and its encrypted file are one layer, and `_require_answerable_layer`
    # already says so in one sentence that names the missing half. Keying this loop on the label
    # ("environment encrypted") instead of the layer made the same pair be probed twice, which
    # printed one fault as two and gave the second finding a sentence about the file the first
    # finding had just named. `pairs`' third element is the layer, so it is the loop's key.
    missing = {(layer, path) for _, path, layer in pairs if not path.is_file()}
    for layer in {name for name, _ in missing}:
        probe = Config(environment, layer if layer == "team" else None,
                       platform_config_dir=root, preload_configs=False)
        try:
            config_init._require_answerable_layer(probe, root, environment, probe.team)
        except click.ClickException as exc:
            findings.append(Finding(ERROR, "missing-layer-file",
                                    str(exc), next(p for name, p in missing if name == layer)))

    for label, path, _ in pairs:
        document = _read_object_file(path, label, findings)
        if document is None:
            continue
        _placeholder_findings(path, document, findings)
        _absolute_kubeconfig_findings(path, document, findings)

    _cluster_fact_finding(root, environment, findings)


def _read_object_file(path: Path, label: str, findings: list[Finding]) -> Optional[dict]:
    """One layer file as a mapping, with the finding attached when it is not one.

    This is the gap the census found: `Config._load_json_file` (`config.py:1025`) does `open` +
    `json.load` and no shape check, so a file holding `[1, 2]` passes straight through today and
    fails later, somewhere else, in a place nobody is looking. `cluster.py:818` is the only existing
    owner of "is an object", and it reads only the environment's plain file; the shape of the other
    three files is decided here so that all four get the same verdict.
    """
    if not path.is_file():
        return None  # already reported as `missing-layer-file`
    try:
        text = read_decrypted(path, "static layer file")
    except OSError as exc:
        findings.append(Finding(ERROR, "unreadable-layer-file",
                                f"{path} could not be read: {exc}", path))
        return None
    except LockedConfigFileError as exc:
        # A file that is present and will not decode is its own finding, and it is the
        # seam's sentence and not a rewrite of it. `collect-then-report` survives this — the run
        # keeps going and reports everything else it found.
        findings.append(Finding(ERROR, "locked-configuration-file", str(exc), path))
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        findings.append(Finding(ERROR, "layer-file-not-json",
                                f"{path} is not valid JSON: {exc}", path))
        return None
    if not isinstance(document, dict):
        findings.append(Finding(
            ERROR, "layer-file-not-an-object",
            f"{path} is the {label} layer and holds a "
            f"{type(document).__name__}, not a JSON object. kubementat reads a layer's answers "
            f"as a mapping, so nothing in this file is asked for and nothing in it is refused "
            f"until some command trips over it.", path))
        return None
    return document


def _placeholder_findings(path: Path, document: dict, findings: list[Finding]) -> None:
    """Answers the templates left blank.

    Shipped templates spell these `REPLACEME_…` and the readers that recognise them say so
    (`routing.py:561` refuses one, `automation.py:475-477` substitutes inside another). What is
    really being reported is "the template was copied and not answered", so the spelling that
    ships is the spelling that matches.
    """
    for key, value in _placeholder_values(document):
        findings.append(Finding(
            ERROR, "placeholder-value",
            f"{key} in {path} is still the template placeholder {value!r}, so the answer it "
            f"should hold is not there. kmt config set answers it, or deleting the key lets the "
            f"default answer, which is the same outcome and a smaller file.", path))


def _absolute_kubeconfig_findings(path: Path, document: dict, findings: list[Finding]) -> None:
    """The split: the config repo declares the cluster, the machine declares how to reach it.

    An absolute `CLUSTER_KUBECONFIG_PATH` therefore cannot be wrong about the cluster — and cannot
    be right about the machine either, which is why it belongs on the warning side. It is a
    portable-by-declaration file path that has been pinned to one laptop.
    """
    value = document.get(_cluster.CLUSTER_KUBECONFIG_PATH_KEY)
    if isinstance(value, str) and value.strip() and os.path.isabs(value.strip()):
        findings.append(Finding(
            WARNING, "absolute-kubeconfig-path",
            f"{_cluster.CLUSTER_KUBECONFIG_PATH_KEY} in {path} is the absolute path {value!r}. "
            f"The configuration tree says which cluster this environment is and the machine "
            f"declares how to reach it, so a path that works on this laptop is a path that will "
            f"break the next one. Leave the key unset to let the kubeconfig on the machine answer.",
            path))


def _cluster_fact_finding(root: Path, environment: str, findings: list[Finding]) -> None:
    """The cluster-not-addressed fact, warned and not errored.

    Reached through the predicate `init` and `add-environment` already use
    (`config_init.cluster_is_unaddressed`, whose own docstring says a future `config validate`
    reuses the same question). This is the single thing validate is expected to say about
    a fresh tree, which fixes both halves of the answer: it must be there, and it must not be an
    error — a tree whose cluster nobody has named yet is unfinished and not invalid.

    When the file is not readable, the file's own finding above is the honest statement; printing
    "is null" about a file that did not answer would be the same false sentence wearing a warning.
    That is why this site reads through the seam and returns without printing at all: a locked file
    is refused by `_read_object_file`, and a warning here about a file nobody decoded would put the
    lie back in.
    """
    path = root / environment / "static.json"
    if not path.is_file():
        return
    try:
        document = json.loads(read_decrypted(path, "environment's static.json"))
    except (OSError, LockedConfigFileError, ValueError):
        return
    if not isinstance(document, dict):
        return
    if config_init.cluster_is_unaddressed(path):
        findings.append(Finding(
            WARNING, "cluster-not-addressed",
            f"environment {environment!r} is configured, but it does not address a cluster yet: "
            f"{_cluster.CLUSTER_KUBE_CONTEXT_KEY} is null in {path}. Name the context there, then "
            f"kmt environments list prints what every configured environment addresses.", path))


# ---------------------------------------------------------------------------------------------
# The small reads.
# ---------------------------------------------------------------------------------------------


def _skip_directories(directory: Path) -> bool:
    """The one traversal rule that is a correctness matter and not a preference.

    A tree under git carries a `.git/` full of files with interesting names; reporting findings
    from inside it would make `validate` say things about the repository that are not about the
    configuration at all.
    """
    return directory.name == ".git"


def _walk_files(root: Path, name: Optional[str] = None, *, pattern: Optional[str] = None):
    """Every file under `root` matching `name` (exact) or `pattern` (fnmatch)."""
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if not _skip_directories(Path(directory) / d)]
        for filename in files:
            if name is not None:
                if filename == name:
                    yield Path(directory) / filename
            elif pattern is not None and fnmatch.fnmatch(filename, pattern):
                yield Path(directory) / filename


def _walk_directories(root: Path):
    for directory, subdirs, _files in os.walk(root):
        here = Path(directory)
        subdirs[:] = [d for d in subdirs if not _skip_directories(here / d)]
        if here != root:
            yield here


def _placeholder_values(document, prefix: str = _PLACEHOLDER_PREFIX):
    """`((key, value), ...)` for every string in a document that still begins with the prefix.

    Recursive because the shipped team template nests — a registry block whose child is
    `REPLACEME_DOCKER_REGISTRY_PASSWORD` is exactly as unanswered as a top-level one, and reporting
    only the outer key would name the block and hide the password.
    """
    out: list[tuple[str, Any]] = []
    stack = [("", document)]
    while stack:
        so_far, node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                stack.append((f"{so_far}.{key}" if so_far else str(key), value))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                stack.append((f"{so_far}[{index}]", value))
        elif isinstance(node, str) and node.startswith(prefix):
            out.append((so_far or node, node))
    return tuple(out)
