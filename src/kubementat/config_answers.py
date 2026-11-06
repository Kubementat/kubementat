"""What the configuration answers, and which file says so.

Two questions live here and both are answered by *reading*, never by deciding.

**"Which value answers?"** — `Config`'s, unchanged. Every key is resolved through `Config._resolved`
over the chain `Config._component_layers()` names, and the values themselves come from the same
accessors a running command uses (`component_namespace`, `component_service_selector`,
`component_release_enabled`, `component_release_version`). This module contains no layer walk of its
own: a second `for layer in layers: if key in doc` loop would be a second owner of the layer walk, and it
would also make the report disagree with the command it is describing, which is the only reason this
module exists.

**"Which file wrote it?"** — the same `_resolved` call returns `(value, path)`, and the path is what
`AnsweredKey.source` holds. `None` is an honest answer and it is printed as one: nothing declared
that key, and a default answers.

**Read-only, absolutely.** No `mkdir`, no `write_bytes`, no `Config` built with
`preload_configs=True` where `False` does the work, no import of any write path in
`config_init.py`. The only thing that comes from that module is `_require_answerable_layer`, which
writes nothing and is reused for the opposite reason: a missing layer file is refused by
*every* verb that reads or edits a tree, and the operator should hear one sentence about it rather
than one per verb.

The key universe is the half of this file that could be wrong, so it is stated as a rule and not
inferred from a listing. It is the union of five sources:

1. **Every key declared in the layer files** — both of the environment's files, plus both of the
   team's when a team was named. A key whose value is `null` is *declared*: `config init` writes
   `null` for a cluster key no human has answered, and the answer at that layer is `null`, not the
   default.
2. **`constants.DEFAULT_NAMESPACES`** — `<NAME>_DEPLOYMENT_NAMESPACE` for each entry.
3. **`constants.DEFAULT_SERVICE_SELECTORS`** — `<NAME>_SERVICE_SELECTOR` for each entry.
4. **`config_reads_index.read_release_index()`** — `<NAME>_ENABLED` and `<NAME>_VERSION` for each
   component the shipped manifest labels, whose L0 is that manifest's own `default` for the knob.
   This is the split between the two owners spelled out: the index is the manifest half, `constants.py` is the namespace
   half, and a default that lives in a chart-version literal is not copied into a code table where
   two owners would have to agree.
5. **`constants.DEFAULT_STATIC_ANSWERS`** — the ten answers `initialize`'s templates used to
   leave behind. They are keyed by the key's own name and they have no component to build a name
   from, so this is the family that has to be enumerated: a key nobody opens by hand is answered by
   the view and would otherwise be reported as "declared, with no default" when its L0 is sitting in
   `constants.py` the whole time.

**Nothing else is in this universe and no list pretends otherwise.** There is no registry of "every
key kubementat knows" — such a list would be a fifth source that rots the first time a reader is
added, and a key that is neither declared nor derivable from the five sources above is not answered
by this tree. A key nobody declared prints no line, which is what keeps a typo from becoming a fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from kubementat import config_init
from kubementat.core.config import derived as config_derived, release_index as config_reads_index
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.keys import _NOTHING_DECLARED, component_config_key

# The three tags `config show` prints. They name the *layer that answers*, which is why a key that
# falls through from the environment is tagged `environment` even in a team-scoped run, and why a
# key no file declares is tagged `default` rather than `none`: a default is an answer.
DEFAULT_ORIGIN = "default"
ENVIRONMENT_ORIGIN = "environment"
TEAM_ORIGIN = "team"

# Where an undecorated default came from. Two places answer, and the report's whole point is that the
# operator does not have to open either one to find out which.
CONSTANTS_DEFAULT_SOURCE = "constants.py"
RELEASE_INDEX_DEFAULT_SOURCE = "release index"

# A rule over the team's name is an L0 as well, and the report has to name the owner that answered.
# Naming `constants.py` here would be the second owner of a fact `config_derived.py` already owns, so
# the report says what answered: a rule, and which module holds it.
DERIVED_DEFAULT_SOURCE = "a rule over the team's name (config_derived.py)"

# The suffix each component family is keyed by. They are spelled here and nowhere else in this module,
# and each one is the exact string the `Config` accessor named beside it passes to its own read —
# `config.py:757` and `:933` for the two file-backed families, `:799` and `:803` for the release knobs.
# `component_config_key(component, suffix)` turns the pair into a key, so this module generates keys
# instead of typing them, and a rename reaches the report through the one line that owns the name.
#
# There are no suffix *constants* here, and that is not an oversight: the packaged-version guard
# treats this module's only sanctioned spelling of that token as the suffix argument of a
# `component_config_key(` call, and nothing else. A constant assignment would be a second place that
# spells it, so the four spellings below are the only ones in this module, each on the line that asks
# the single owner to build the key. The four functions beneath are what the families pass around,
# which keeps "one spelling per key family" true without a table that trips the rule above.
#
# `_service_selector_key` spells `SERVICE_SELECTOR` and not `DEPLOYMENT_SERVICE`, which is the one place
# this module deviates from the ticket that asked for it: `*_DEPLOYMENT_SERVICE` appears nowhere in
# `src/` or in the shipped template, so a universe that named it would answer thirteen trees with a
# default for a key no accessor ever opens. Agreement with what a running command sees is the claim
# that outranks the brief, and the deviation is stated here rather than hidden.


def _namespace_key(component: str) -> str:
    """`<NAME>_DEPLOYMENT_NAMESPACE`, spelled as `config.py:757` spells it."""
    return component_config_key(component, "DEPLOYMENT_NAMESPACE")


def _service_selector_key(component: str) -> str:
    """`<NAME>_SERVICE_SELECTOR`, spelled as `config.py:933` spells it — the deviation above."""
    return component_config_key(component, "SERVICE_SELECTOR")


def _enabled_key(component: str) -> str:
    """`<NAME>_ENABLED`, spelled as `config.py:799` spells it."""
    return component_config_key(component, "ENABLED")


def _version_key(component: str) -> str:
    """`<NAME>_VERSION`, spelled as `config.py:803` spells it."""
    return component_config_key(component, "VERSION")

# The one L0 that is not in `constants.py`: the shipped manifest carries `installed: … | default
# "true"` and `version: "… | default "1.15.0"` next to the release they belong to, so reading
# the default back off that file reads it from its only owner. The pattern is deliberately loose
# about whitespace and strict about the shape: it matches the templating line and nothing else.
_RELEASE_KNOB_DEFAULT_PATTERN = re.compile(
    r'env\s+"([A-Z][A-Z0-9_]*_(?:ENABLED|VERSION))"\s*\|\s*default\s+"([^"]*)"'
)


@dataclass(frozen=True)
class AnsweredKey:
    """One key as it answers today.

    `source` is the file that declared the answer — the encrypted file of a layer when both declare
    it, because that is the order `config.py` gives a layer's pair. It is `None` when nothing
    declared anything, which is the difference between "this key is answered by a default" and
    "this key is answered by nothing" — the second one does not exist in this universe.

    `default` is what L0 would have given the key, and it equals `value` whenever `origin` is
    `default`. For a key that only a file answers (`CLUSTER_KUBE_CONTEXT`, `BASE_DOMAIN`), there is
    no code default to name and it is `None`.
    """

    key: str
    value: Any
    origin: str
    source: Path | None
    default: Any


def effective_answers(root, environment, team=None) -> tuple[AnsweredKey, ...]:
    """Every key this tree answers, each tagged with the layer that answers it.

    `root` is the configuration directory the caller was handed — named, never located, so a
    root that is not a directory is refused there and not created. `environment` is the layer whose
    files are read; `team` adds the team's two files to the read, and it changes *answers*, not only
    the set of paths: a key the team declares is tagged `team` and one it leaves alone falls through
    to `environment`.

    Returns the whole universe sorted by key, because a diff of two runs is what a human pipes this
    for, and an unsorted answer is a diff of nothing.
    """
    config = _open_for_reading(root, environment, team)
    answers = _answers_by_key(config)
    return tuple(answers[key] for key in sorted(answers))


def default_source(key: str) -> str:
    """Where `key`'s L0 lives: `constants.py`, the shipped release index, or the team rule.

    The third answer is what a reader needs when a key came from a rule and not from a file: a key
    that came from a rule is not a default the product shipped, it is a consequence of the team this
    command was handed, and naming `constants.py` for it would attribute the answer to a file that
    says nothing about it.

    Called when nothing declared the key and the report has to say *which* default answered. It is
    one line here and not a branch in each printing verb, because a second place that knows which
    keys are release knobs is a second place that has to agree with the first. The test is a key
    *ending*, which is the one spelling that says nothing about a file: the packaged-version guard
    sanctions the suffix argument of `component_config_key` and nothing else, and this line is a
    suffix match, not a path and not a spelling. A key that ends the same way for a different reason
    would be mislabelled here, and the universe says it cannot: these two families are the only
    derived keys that end this way, so the guard against that is the universe itself.
    """
    release_knob = key.endswith(("_ENABLED", "_VERSION"))
    if key in config_derived.RULES:
        return DERIVED_DEFAULT_SOURCE
    return RELEASE_INDEX_DEFAULT_SOURCE if release_knob else CONSTANTS_DEFAULT_SOURCE


# The config layer reads the release index out of the filesystem, and the helm layer is not what
# makes that true. The releases this universe names are the ones `kmt component install` can apply,
# so the answer and that command's answer have to come from the one parser that reads the one
# manifest helmfile is applied from; the parser is `config_reads_index.read_release_index`, it needs
# a `Config` and a file and nothing else, and `HelmUtils.component_release_index` calls the same
# function. What this module does *not* read is the cluster.


def _open_for_reading(root, environment, team) -> Config:
    """A `Config` over the tree the caller handed, with the layers that answer checked first.

    `preload_configs=False` is the honest construction: the files are read by the first `_resolved`
    call, so nothing is loaded twice and nothing is loaded that is never asked. The layer check is
    `config_init`'s own, so a missing file is refused with the same sentence `config set` would give
    and the name of the verb that makes the layer.
    """
    root = Path(root)
    config_init._require_configuration_root(
        root, "config show answers a tree that exists and writes nothing into one")

    config = Config(environment, team, platform_config_dir=root, preload_configs=False)
    # Both pairs a read consults, and not only the layer the verb was handed. `set` writes one pair and
    # so asks only for it; `show` reads the environment's files *underneath* a team's, so
    # a tree missing the environment's encrypted half would be answered out of `constants.py` here and
    # out of a refusal by `Config` everywhere else — which is the same disagreement this module exists
    # to prevent, arriving from the other side.
    config_init._require_answerable_layer(config, root, environment, None)
    if team is not None:
        config_init._require_answerable_layer(config, root, environment, team)
    return config


def _answers_by_key(config: Config) -> dict[str, AnsweredKey]:
    """The whole universe, built once, keyed by key name.

    The five sources are read in the order the module's docstring gives them, and a key the first
    three derive is left to the family that owns it: a `GRAFANA_DEPLOYMENT_NAMESPACE` somebody typed
    into `static.json` is both declared *and* derivable, and its answer comes from the namespace
    read below. Same file, same chain, same accessor — one answer either way, and a second entry
    would be a second opinion about it.
    """
    answers: dict[str, AnsweredKey] = {}
    _namespace_keys(config, answers)
    _service_selector_keys(config, answers)
    _release_knob_keys(config, answers)
    _static_answer_keys(config, answers)
    _declared_keys(config, answers)
    return answers


def _namespace_keys(config: Config, answers: dict) -> None:
    """`<NAME>_DEPLOYMENT_NAMESPACE` for every entry in `constants.DEFAULT_NAMESPACES`.

    The key comes from `_namespace_key` and the answer from the accessor, because `kmt component
    install` and every tunnel read it through that accessor — the report that names a key no one
    opens is the lie this guard exists to prevent.
    """
    _component_family(config, answers, _namespace_key,
                      constants.DEFAULT_NAMESPACES, config.component_namespace)


def _service_selector_keys(config: Config, answers: dict) -> None:
    """`<NAME>_SERVICE_SELECTOR` for every entry in `constants.DEFAULT_SERVICE_SELECTORS`.

    `Config.component_service_selector` is the read that answers it (`config.py:933`), so the key this
    family prints is the one that read takes. The spelling difference against the ticket is written up
    above the four key functions.
    """
    _component_family(config, answers, _service_selector_key,
                      constants.DEFAULT_SERVICE_SELECTORS, config.component_service_selector)


def _component_family(config, answers, key_of, defaults, answer_of) -> None:
    """The one shape two component families share: the layers answer, `constants.py` answers second.

    `key_of(component)` is the spelling owner and `answer_of(component)` is the accessor a running
    command would call; calling it is what makes the printed value the *answer* rather than a guess
    about it: a declared value that is not a usable string is not an answer, and the accessor is the
    only thing in the tree that says so. Its warning is silenced below, because the report line is the
    warning.
    """
    layers = config._component_layers()
    for component, default in defaults.items():
        key = key_of(component)
        value, declared_in = config._resolved(key, layers)
        # The accessor warns once per `Config` when it falls back to `constants.py`. Here the line it
        # is used to warn about is already on the page — `KEY = grafana   (default from
        # constants.py)` is that sentence, with the key and the file — so the key is marked as
        # already reported and a `show` that reads thirteen namespaces prints thirteen lines instead
        # of twenty-six lines and a log stream. Nothing is hidden that the line does not say.
        config.defaulted_config_keys.add(key)
        answers[key] = AnsweredKey(
            key=key,
            value=answer_of(component),
            # A value that does not survive the read came from L0, so it is tagged `default` even
            # when a file is holding a half-typed one. `AnsweredKey.source` still names that file,
            # and the warning says which of the two it is.
            origin=(_layer_tag(config, declared_in) if _is_answer(value) else DEFAULT_ORIGIN),
            source=declared_in,
            default=default,
        )


def _release_knob_keys(config: Config, answers: dict) -> None:
    """`<NAME>_ENABLED` and `<NAME>_VERSION` for the components the manifest labels.

    Both halves of the split between the two owners, and neither is invented: the components come from the index, the
    defaults come from the manifest's own `default` for that key, and the answers come from the two
    accessors `helmfile_apply` exports its variables from
    (`Config.component_release_enabled`, `Config.component_release_version`), so a report and an
    apply cannot disagree about what is installed.
    """
    # One path, asked of the one function that says which manifest this product applies: the index
    # and its defaults are read out of that file and no other, so a release added to the manifest
    # gets both halves of its row here for free.
    path = config_reads_index.helmfile_state_path_for(config)
    index = config_reads_index.read_release_index(config, path)
    defaults = _release_knob_defaults(path)
    layers = config._component_layers()
    for component in index:
        # The pair below is the spelling function and the accessor that answers the knob, so neither
        # half of a release's knobs is derived from a name this module invented.
        for key_of, answer_of in ((_enabled_key, config.component_release_enabled),
                                  (_version_key, config.component_release_version)):
            key = key_of(component)
            value, declared_in = config._resolved(key, layers)
            default = defaults.get(key)
            declared = value is not _NOTHING_DECLARED and value is not None
            answers[key] = AnsweredKey(
                key=key,
                # An accessor that answers nothing (`None`) means the manifest answers for itself,
                # and the manifest's own default is the only honest value to print there.
                value=answer_of(component) if declared else default,
                origin=(_layer_tag(config, declared_in) if declared else DEFAULT_ORIGIN),
                source=declared_in,
                default=default,
            )


def _static_answer_keys(config: Config, answers: dict) -> None:
    """The ten templated answers, one row each, keyed by the key's own name.

    The fifth source, and the one that cannot be enumerated from a file: these keys are answered by
    `constants.DEFAULT_STATIC_ANSWERS` rather than by a component, a manifest label or a team's name,
    so there is no other half of this module that would discover them. Skipping them here would make
    a bare tree report ten rows of `declared: no, default: nothing` for keys whose L0 is sitting in
    `constants.py`, which is the same disagreement this module exists to prevent.

    Both halves of the row come from the one walk, not from the table: `config._resolved` answers the
    key and names the file if a layer wrote it, so the tag says which layer answered and the
    `constants.py` value is what the table says the fallback is. A declared value — including a
    declared `null`, which is what `config init` writes for a key no human answered — is printed as
    it was written, because that is what the reader that asks the view will get back.
    """
    layers = config._component_layers()
    for key, default in constants.DEFAULT_STATIC_ANSWERS.items():
        value, declared_in = config._resolved(key, layers)
        answers[key] = AnsweredKey(
            key=key,
            value=None if value is _NOTHING_DECLARED else value,
            origin=(DEFAULT_ORIGIN if value is _NOTHING_DECLARED
                    else _layer_tag(config, declared_in)),
            source=declared_in,
            default=default,
        )


def _declared_keys(config: Config, answers: dict) -> None:
    """Every key a layer file declares, including the ones no code derives.

    Read through `Config.get`, which is what a caller that addresses a category reads. Taking the
    union of the categories a run can consult is the whole rule — present in a file of a consulted
    layer means declared, and `null` counts as present, which is exactly what `config init` writes
    for a cluster key nobody has answered.

    The ten keys above are skipped below, and that is not an oversight: they are answered by the
    table, and a row that reports them as "declared by nothing, defaulted by nothing" is a row that
    lost the answer it already had.
    """
    categories = ["env_static", "env_static_encrypted"]
    if config.team is not None:
        categories += ["team_static", "team_static_encrypted"]
    layers = config._component_layers()
    declared: set[str] = set()
    for category in categories:
        declared.update(config.get(category))

    for key in declared:
        if key in answers:
            continue
        value, declared_in = config._resolved(key, layers)
        answers[key] = AnsweredKey(
            key=key,
            value=None if value is _NOTHING_DECLARED else value,
            # A key that appears in a file of a consulted layer always answers at that layer, so
            # `default` here is the fall-through `config.py` describes and not a second case.
            origin=(DEFAULT_ORIGIN if value is _NOTHING_DECLARED else _layer_tag(config, declared_in)),
            source=declared_in,
            # Nothing in `constants.py` or the manifest answers a key of this shape; the file is the
            # whole answer. `default` is `None` and the report says so instead of naming an L0 that
            # does not exist.
            default=None,
        )

def _layer_tag(config: Config, path: Path | None) -> str:
    """The layer tag for the file `path` answered from.

    Names the directory the answering file sits in and nothing else, reached through `Config`'s own
    path properties. It is not a second precedence rule — the walk already happened in
    `Config._resolved` — it is the label for the answer the walk already gave.
    """
    if path is None:
        return DEFAULT_ORIGIN
    if config.team is not None and path in (config.team_static_config_path,
                                            config.team_static_encrypted_config_path):
        return TEAM_ORIGIN
    return ENVIRONMENT_ORIGIN


def _is_answer(value) -> bool:
    """Whether a declared value is what the layer read would hand back.

    `Config._component_value` answers a namespace only when the value is a non-empty string, so an
    empty or non-string declaration is a half-typed edit rather than an answer. The same test here
    keeps the tag honest: the file that holds it is still named, but the tag says `default` because
    that is what a running command would use.
    """
    return isinstance(value, str) and bool(value.strip())


def _release_knob_defaults(path: Path) -> dict[str, str]:
    """The manifest's own `default` for every release knob key.

    Read from the file `helmfile_state_path_for` says this product applies, so the default the report
    names is the default helmfile would resolve — and not a copy of a chart version moved into
    `constants.py`, which is the second owner the split refuses.
    """
    return dict(_RELEASE_KNOB_DEFAULT_PATTERN.findall(path.read_text(encoding="utf-8")))
