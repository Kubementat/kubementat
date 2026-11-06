"""Which layer answers a key, and the shape of the key's name.

Two things live here, and they are the two the other three splits were ordered for. The first is the
precedence: `L2` is the team's own pair of files, `L1` is the environment's, and `L0` is what
`constants.py` ships and what a team's name already determines. The table that says so is the whole of
this module's reason for existing — census E found every read of layered configuration in `src/` and
not one of them fell through, so the order was not a rule but 50-odd call sites each answering for
itself. `CATEGORY_LAYERS`, the two pairs and the walk that reads them are where that falls through
now, spelled once.

The second is the vocabulary: `component_config_key` is how a `static.json` key is built out of a
component's name rather than typed per call site, and it travels with the layer table because the two
are asked together by the same readers — a component's namespace, its selector, the class an Ingress
names, the two knobs that say whether it was released.

What this module is not. It holds no `Config` and reads no file: the dictionaries it resolves and the
callable that turns a category into a path are both handed in, which is what keeps the edge pointing
one way. It imports `constants` and `derived`, both of which are below it, and never `config` —
`config.py` imports this file, and a back-edge would be the cycle the census warned about, the same
one that keeps `constants.py` from importing `component_config_key` back. It re-exports nothing to
that module either: `config.CATEGORY_LAYERS` raises `AttributeError`, and that is the state that makes
the next split safe.

`_NOTHING_DECLARED` is the sentinel this module owns and every other site imports. It is compared with
`is`, so one definition is not a style preference — two objects would make "nobody declared this key"
two different facts depending on which module asked, and no test would notice.
"""

from kubementat.core import constants
from kubementat.core.config import derived as config_derived

# ---------------------------------------------------------------------------------------------
# The layers a key is answered from. Census E counted every read of layered configuration in
# `src/` and found none that
# implemented `L2 > L1 > L0`: `Config.get()` handed back one file's dict and no code path fell
# through to the layer beneath it. This table is where that falls through now, and it is the only
# place the order is spelled, so a read of a team key that the team never declared can answer from
# the environment without one more caller deciding the order for itself.
#
# A *category* is a file; a *layer* is the pair a directory holds. Within one layer a key declared
# in either file is present at that layer, and when both declare it the encrypted file is what
# answers — the pair is one answer with two spellings, not two answers, which is why an entry in
# the pair is ordered encrypted-first. Above that, the team's pair outranks the environment's,
# key by key and shallow: a layer never contributes half an object, and a key is never merged out
# of two files.
#
# L0 is `src/kubementat/constants.py`, plus — and only for the keys that are not defaults but
# consequences of a team's name — the rules in `config_derived.py`. `constants.py` answers the
# keys kubementat ships a default for; `config_derived.py` answers the five the team's name already
# determines, which is why they are L0 and not a template some verb copies into the tree. A key that
# is absent
# from `constants.py` is not thereby derivable, and a team-shaped key is not thereby a default: the
# difference is whether one answer is correct.
#
# The packaged `defaults/static.json` is *not* consulted here and does not exist in
# this tree: the environment template it would come from is 40 keys of which 8 are `REPLACEME_*`, so
# promoting it to a default source would ship placeholders as answers and give the namespaces a second
# owner beside `constants.py`. That template is phase 3's problem, the phase where the template-copy
# habit dies. It is written down here as well as at the fallback, so nobody "completes" this table
# from the spec.
# ---------------------------------------------------------------------------------------------
LAYER_TEAM = "L2"
LAYER_ENVIRONMENT = "L1"

# One layer's two files, encrypted first for the precedence stated above.
TEAM_PAIR = ((LAYER_TEAM, "team_static_encrypted"), (LAYER_TEAM, "team_static"))
ENVIRONMENT_PAIR = ((LAYER_ENVIRONMENT, "env_static_encrypted"), (LAYER_ENVIRONMENT, "env_static"))

# Which layers answer for a category, highest first. A chain walks *one file across the layers*, not
# one layer's two files, and that is deliberate: a category is how a reader says which file it is
# asking, and the only thing a layer may change is *whose* copy of that file answers. So
# `get("env_static_encrypted")` is answered by an encrypted file at the environment layer or by
# nobody, and a private key that belongs in git-crypt cannot be answered out of the plain file that
# sits beside it — which is what `GIT_DEPLOYER_PRIVATE_KEY_BASE64`, Grafana's admin credential and
# the webhook secrets all require, and what an operator is told when they run `kmt config show`.
# Within a layer the two files of a pair are still one answer for a read that is not addressed to
# either of them; that is `COMPONENT_LAYERS`, below.
#
# A team-scoped category falls through to the environment's copy of the same file, because a key the
# team never declared is not a key the team forbade. An environment category has no layer above it to
# fall through to: a read addressed to `{env}/static.json` answers for this environment, and the team
# that has not been asked about is not part of that question.
CATEGORY_LAYERS = {
    "team_static": ((LAYER_TEAM, "team_static"), (LAYER_ENVIRONMENT, "env_static")),
    "team_static_encrypted": ((LAYER_TEAM, "team_static_encrypted"),
                              (LAYER_ENVIRONMENT, "env_static_encrypted")),
    "env_static": ((LAYER_ENVIRONMENT, "env_static"),),
    "env_static_encrypted": ((LAYER_ENVIRONMENT, "env_static_encrypted"),),
}

# The chain for a read that is not addressed to a file: a component's namespace, its selector, the
# class an Ingress names. Whoever declares it answers, so the team's pair is consulted before the
# environment's, and with no team named there is no team layer to consult.
COMPONENT_LAYERS = TEAM_PAIR + ENVIRONMENT_PAIR

# Nothing declared the key. Distinct from a `None`, which is a value a file is allowed to hold.
_NOTHING_DECLARED = object()


def _answers_from_a_team_layer(layers) -> bool:
    """Whether a chain reaches the team's own files, which is the only place a rule may answer.

    A rule is a fact about one team, so a chain that consults no team layer has nothing for it to be
    a rule about — `get('env_static')` answers for this environment and for no team in it. This is
    that test, spelled once so the read path and the view agree about when derivation is in scope.
    """
    return any(layer == LAYER_TEAM for layer, _ in layers)


def _declare_in_layers(key, layers, sources, path_of, derived=None):
    """The one resolution path: walk *layers* for *key*, highest layer first.

    Returns `(value, path)` where `path` is the file the answer came from — the encrypted file of a
    layer when both declare it, because the layer is ordered that way — or
    `(_NOTHING_DECLARED, None)` when no file of any layer named it. `sources` is the loaded
    dictionaries keyed by category; a layer whose file was never read is simply absent and is
    skipped, which is what keeps "the team named no file" (a `ClickException` from `get`) a
    different fact from "no layer declared this key" (a fall-through to L0).

    `derived` is `(team, rules)` for the chain — the rules that answer what a team's name determines,
    consulted in exactly two places, both below and both after the layers have had their
    say: a value a layer *declared* gets the rules that reach inside a value
    (`config_derived.derived_declared`, which today means the namespace inside a deploy-key entry),
    and a key no layer declared gets the rules that compute it. The order inside this function is the
    whole override rule, so it is written here once: **something the operator wrote always answers
    first**, and a rule only ever fills a hole. A rule that fired ahead of a declared value would be
    a rule that became a lie, and it would still pass every derivation test there is.

    After the rules comes the other half of L0: the ten templated answers in
    `constants.DEFAULT_STATIC_ANSWERS`. It sits *last* for the same reason the rules sit last — a key
    the walk found in a file is answered by that file and the table never sees it — and the two L0
    halves cannot collide, because a computed answer is keyed by a team and a default is keyed by a
    key. A key that is neither declared, nor derived, nor in this table reaches
    `_NOTHING_DECLARED`, which is what makes "no answer" a different fact from "answered `null`".
    """
    team, rules = derived if derived is not None else (None, {})
    team_in_scope = bool(team) and _answers_from_a_team_layer(layers)
    for layer, category in layers:
        values = sources.get(category)
        if values and key in values:
            value = values[key]
            if team_in_scope:
                value = config_derived.derived_declared(key, value, team)
            return value, path_of(category)
    if team_in_scope:
        rule = rules.get(key)
        if rule is not None:
            return rule(team), None
    default = constants.static_answer(key)
    if default is not None:
        return default, None
    return _NOTHING_DECLARED, None


def _layered_view(layers, sources, path_of, derived=None, defaults=None):
    """The dictionary `get()` hands back for a chain: the keys these layers declare, each answered
    by `_declare_in_layers` rather than by the file it happens to sit in. Built once per load, so
    the 50-odd callers that do `get(category).get(key)` see a layered answer without knowing it.

    The team-shaped keys are in the view even when no file declares them, because they are answered
    and not stored: a reader that asks `get('team_static').get('PIPELINE_NAMESPACE')` gets
    the team's pipelines namespace from a tree that never wrote the key. No name is put into the
    view for a team nobody named, so a `Config` built without a team has nothing extra in it — that
    refusal is the category gate in `get()`, and it still arrives first.

    The ten templated answers are offered to every chain `get()` builds, through `defaults`.
    They have to be, because that is how most of them are read: a caller that asks
    `get('env_static').get('TEKTON_VERSION_PIPELINE')` is asking *a file*, and a default that only
    answered the single-key walk would be a default with no reader — reported as working and failing
    in the guest. Which file of a chain a key would have sat in is not a question about the value, and
    nothing in the table names one, so the whole table is offered and `_declare_in_layers` answers it
    or does not. `declared()` passes no `defaults`, and that is what keeps the two doors apart: the
    table adds no names to that view, so its keys are never iterated and its lookup inside
    `_declare_in_layers` never sees them — a default is an address, and a default is not a deed.
    """
    keys = []
    for layer, category in layers:
        for key in (sources.get(category) or {}):
            if key not in keys:
                keys.append(key)
    team, rules = derived if derived is not None else (None, {})
    if team and _answers_from_a_team_layer(layers):
        keys.extend(key for key in rules if key not in keys)
    if defaults:
        keys.extend(key for key in defaults if key not in keys)
    view = {}
    for key in keys:
        value = _declare_in_layers(key, layers, sources, path_of, derived)[0]
        view[key] = None if value is _NOTHING_DECLARED else value
    return view


def component_config_key(component: str, suffix: str) -> str:
    '''
    The `static.json` key a component's `suffix` is read from.

    Derived from the component name instead of spelled out per call site, so a component can
    only ever be read through the key its own name produces — the alternative is one more
    place that has to agree with the first about what a component is called.

    >>> component_config_key('grafana', 'DEPLOYMENT_NAMESPACE')
    'GRAFANA_DEPLOYMENT_NAMESPACE'
    >>> component_config_key('tekton-dashboard', 'DEPLOYMENT_NAMESPACE')
    'TEKTON_DASHBOARD_DEPLOYMENT_NAMESPACE'
    '''
    return f"{component.upper().replace('-', '_')}_{suffix}"

