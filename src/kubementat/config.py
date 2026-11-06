import json
import logging
import os
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Optional

import click
import yaml

from kubementat import config_derived, constants
from kubementat.assets import asset_path
from kubementat.config_reads import read_decrypted
from kubementat.errors import TeamNotSuppliedError

# Importing this module configures logging and nothing else. The `logging.basicConfig`
# call that used to sit here ran for every library consumer — importing `kubementat`
# for a unit test or a script silently installed a root handler and raised the level to
# INFO (plan D10). The CLI opts in to the same configuration in `cli.kmt:main`, which is
# the only place that has a human reading stdout.

# One API version for the whole `tekton.dev` group, with no override: kubementat writes
# v1 and reads v1. `tekton.dev/v1beta1` was removed, so a tree that mixed the two would
# apply half its manifests and 404 on the rest.
TEKTON_API_VERSION = "v1"
TEKTON_API_GROUP = "tekton.dev"

# The Triggers group is the one beta API kubementat still speaks, and it is still a
# constant with no override. Triggers 0.37.2 serves `v1alpha1` and `v1beta1` and no `v1`
# (measured from its release.yaml: every CRD lists v1beta1 as served and stored, several
# still carry a v1alpha1 entry), so writing `v1` here would produce six trigger manifests
# no controller in this tree serves. See the shipped trigger templates under
# `Config.tekton_trigger_template_dir`.
TRIGGERS_API_VERSION = "v1beta1"
TRIGGERS_API_GROUP = "triggers.tekton.dev"

# `Path(__file__).parents[2]` is the repository root for a checkout and *site-packages*
# for an installed wheel, so it is only a default and never the answer on its own (D11).
# The environment variables below are the override: the same tree can be read from
# anywhere, which is what the CI container needs (`kmt` in site-packages, checkout in
# /workspace).
KUBEMENTAT_HOME_ENV_VAR = "KUBEMENTAT_HOME"
KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR = "KUBEMENTAT_PLATFORM_CONFIG_DIR"

# Directories under `platform_config/{environment}/` that are not a team. A team is a directory
# that carries a `static.json` of its own, because that file is what makes it addressable: no
# file, no namespaces to name, nothing to install or uninstall. So "every subdirectory" is not the
# same question as "every team", and the directory this rule names is the one kubementat used to
# write itself.
#
# That directory is product data inside the package, and no verb writes anything by that
# name into a configuration tree, which is why this constant is now a belt and not a brace: the
# `static.json` test in `Config.teams` already refuses a `kubementat_components/` an older tree left
# behind, because it
# never carried one. The one case where removing it would change an answer is a stale directory
# that somehow does carry a `static.json`, and it is not worth deleting a rule for.
NON_TEAM_ENVIRONMENT_DIRECTORIES = frozenset({"kubementat_components"})

# The two categories that only exist inside a team's directory. They are spelled here so the
# refusal is one rule and not one per reader, and so a new team-scoped category has to be added to
# the list rather than remembered by whoever hits it first.
TEAM_SCOPED_CATEGORIES = ("team_static", "team_static_encrypted")

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


def _directory_from_environment(name):
    '''
    Reads the directory `name` names from the environment.

    Returns
    -------
    Path or None
        None when the variable is not set at all, meaning "use the default".

    Raises
    ------
    click.ClickException
        If the variable is set but empty. Falling back to the default there would be the
        D11 failure again, silently: an empty `KUBEMENTAT_HOME` would read config out of
        site-packages and say nothing about it.
    '''
    value = os.environ.get(name)
    if value is None:
        return None
    if not value.strip():
        raise click.ClickException(
            f"{name} is set but empty. Give it the path of the directory it names, "
            f"or unset it to use the built-in default."
        )
    return Path(value).resolve()


def default_kubementat_main_dir() -> Path:
    '''
    The project root when no caller asked for a particular one: `KUBEMENTAT_HOME` if it is
    set, the checkout this module lives in otherwise. An explicit `kubementat_main_dir`
    argument outranks both — see `Config.__init__`, which is where that precedence lives.
    '''
    return _directory_from_environment(KUBEMENTAT_HOME_ENV_VAR) or Path(__file__).resolve().parents[2]


def default_platform_config_dir() -> Path:
    '''
    The configuration root when no caller named one: `KUBEMENTAT_PLATFORM_CONFIG_DIR`, and
    nothing else.

    A configuration root is **named, never located**. Two spellings name it — the `--config-dir`
    flag a command carries and the variable a container already sets — and when neither is
    present this function refuses. It used to answer with `platform_config` under
    `default_kubementat_main_dir()`, which meant a command that had been told nothing still
    answered, from wherever the wheel happened to be installed. That answer is the D11 bug in its
    last remaining form, and v2 is a rewrite rather than a migration, so there is no
    compatibility branch behind it: the operator gets one line naming the two spellings, and the
    run stops before it reaches a cluster.

    `kubementat_main_dir` is not consulted. It names the tree a checkout hangs its *output* off
    (`tekton_ci/pipeline-runs/`); everything the product ships — `templates/`, the Tekton Tasks and
    Pipelines, the generator templates — is package data and reaches no root at all.
    An asset root is a different question with a different override;
    letting it stand in for the configuration root is how one command ended up reading two trees
    while believing it read one.

    Returns
    -------
    Path
        The root the variable names, resolved.

    Raises
    ------
    click.ClickException
        If the variable is unset, or set but empty. The empty case is
        `_directory_from_environment`'s rule verbatim: falling back when someone set the variable
        to nothing is the silent default again, just further from view.
    '''
    declared = _directory_from_environment(KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR)
    if declared is not None:
        return declared
    raise click.ClickException(
        f"no configuration root is named. Give this command --config-dir PATH, or set "
        f"{KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR} to the directory that holds the "
        f"environments' static.json. kubementat does not read configuration out of the directory "
        f"it was installed from, and it has no default root to fall back to."
    )


def resolve_platform_config_dir(config_dir=None) -> Path:
    '''
    The configuration root a caller gets, spelled once: argument, then variable, then refusal.

    `default_platform_config_dir` is the whole rule below the argument, and this function is the
    one place that decides whether an argument was handed at all. Earlier the same precedence
    had two owners in `src/` — here, and a hand-written `default_platform_config_dir() if
    config_dir is None else Path(config_dir).resolve()` inside `kmt environments list`. The two
    agreed, which is exactly why nobody noticed the duplication: an agreement can rot, and it rots
    the day one spelling is edited and the other is not. Two owners of one rule is how a listing
    and a command's 1:1 check start to disagree about which tree this operator configured.

    `config_dir` is `None` for "the operator named no root", which is the same meaning
    `Context.build` gives the value it threads, so a CLI flag and a library argument are one dial
    here and not two.

    Parameters
    ----------
    config_dir : Path or str, optional
        The root this caller was handed: a `--config-dir` flag, or the `platform_config_dir`
        argument of `Config`. `None` means nothing was handed, and then the variable answers.

    Returns
    -------
    Path
        The root the argument names, resolved; or the variable's, resolved.

    Raises
    ------
    click.ClickException
        When neither names one, with `default_platform_config_dir`'s sentence verbatim.
    '''
    if config_dir is not None:
        return Path(config_dir).resolve()
    return default_platform_config_dir()


# The read side of the tree's version stamp, and the reason it had to be written is that the write side has existed
# since `config init` began stamping. Two keys landed in every prepared tree and nothing read them,
# which is worse than having no stamp at all: a person skimming this tree finds a version gate and
# assumes it gates. From here the stamp is asked before the tree is trusted.
SCHEMA_VERSION_WINDOW = 1
_PRODUCT_DISTRIBUTION = "kubementat"
_CONSTRAINT_OPERATORS = (">=", "<=", "==", ">", "<")


def product_version() -> Optional[str]:
    '''What this build of kubementat is, asked of the packaging metadata and not of a literal.

    A literal here would be a second owner of the version, and `pyproject.toml:3` already has the
    first one. When the metadata is unavailable this answers `None` rather than inventing a version:
    The rule is warn-and-continue, and an unknown version is exactly the case that must not be
    silently treated as "fine" — it warns that it could not compare.
    '''
    try:
        return metadata.version(_PRODUCT_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return None


def _version_parts(text: str) -> tuple:
    """A dotted numeric version as a tuple of ints, stopping a component at its first non-digit.

    Truncating instead of raising on `1.2.3rc1` is deliberate: a release candidate is not a different
    version from the point of view of a constraint that only ranges, and refusing to read a product
    version because it carries a suffix would brick the very warning that is supposed to be gentle.
    """
    parts = []
    for component in str(text).strip().split("."):
        digits = ""
        for char in component:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            raise ValueError(f"{text!r} is not a dotted numeric version")
        parts.append(int(digits))
    return tuple(parts)


def _compare_versions(one: tuple, two: tuple) -> int:
    """`0.4` and `0.4.0` are the same version, which is why both sides are padded before comparing."""
    width = max(len(one), len(two))
    left = tuple(one) + (0,) * (width - len(one))
    right = tuple(two) + (0,) * (width - len(two))
    return (left > right) - (left < right)


def satisfies_version_constraint(constraint: str, version: str) -> bool:
    '''Does `version` fall inside `constraint`, which is a comma-separated list of clauses?

    `>=0.4,<2` is the shape this answers, because it is the shape `constants` stamps and the shape
    `pyproject.toml` uses. It is about 30 lines, which is the honest cost of not adding
    `packaging` to the dependencies for two operators — `packaging` is importable here only as a
    transitive neighbour, and `test_the_declared_dependencies_are_what_the_tree_needs.py` refuses a
    dependency the manifest does not name. If this ever needs `!=`, a wildcard, or a prerelease
    comparison, that is the signal to declare the dependency and not to grow this parser.
    '''
    got = _version_parts(version)
    for clause in (part.strip() for part in str(constraint).split(",")):
        if not clause:
            continue
        operator = next((op for op in _CONSTRAINT_OPERATORS if clause.startswith(op)), None)
        if operator is None:
            raise ValueError(f"{clause!r} in {constraint!r} names no supported operator")
        order = _compare_versions(got, _version_parts(clause[len(operator):]))
        holds = {
            ">=": order >= 0,
            "<=": order <= 0,
            "==": order == 0,
            ">": order > 0,
            "<": order < 0,
        }[operator]
        if not holds:
            return False
    return True


def read_configuration_stamp(root: Path) -> tuple:
    '''The two keys of `<root>/kubementat.yaml`, or a refusal that names the verb that writes them.

    A missing stamp is a refusal and not a default. No backwards compatibility lives in this epic,
    and the alternative — assume `schema_version: 1` when the file is absent — rescues a tree that
    was never stamped, which is the same mistake as a library function silently bumping it.
    '''
    path = Path(root) / constants.CONFIG_MANIFEST_FILENAME
    if not path.is_file():
        raise click.ClickException(
            f"{path} is absent, so {root} does not say which product it was prepared for. "
            "kmt config init writes this file; a directory that predates the stamp has never been "
            "stamped and cannot be assumed to agree with this build.")
    try:
        stamp = yaml.safe_load(read_decrypted(path))
    except yaml.YAMLError as error:
        raise click.ClickException(f"{path} is not readable YAML: {error}")
    if not isinstance(stamp, dict):
        raise click.ClickException(
            f"{path} must be a mapping of the two keys this product stamps, and reads as "
            f"{type(stamp).__name__}")
    constraint, schema = stamp.get("kubementat"), stamp.get("schema_version")
    if constraint is None or schema is None:
        raise click.ClickException(
            f"{path} declares kubementat={constraint!r} and schema_version={schema!r}; both keys are "
            "required, one because it is the constraint this tree was validated against and one "
            "because it is the only thing that says what its answers mean.")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise click.ClickException(f"{path} declares schema_version={schema!r}; it must be an integer")
    return str(constraint), int(schema)


@dataclass(frozen=True)
class ConfigurationStamp:
    '''A tree's stamp, and what this build says about it.

    One owner for the window. Two verbs ask the same two questions of the same two keys:
    `check_configuration_stamp`, which every cluster command runs, and `config validate`, which gets
    to refuse (it is the only strict home for version drift). Two spellings of one comparison is how a
    tree ends up with a verdict that depends on which verb was asked, which is the state that has
    to be refused outright.

    The conditions are properties and the sentences stay with the verbs. A fact that carried its own
    wording would be prose in three files again, and what a warning rule wants is the same fact everywhere and
    not the same paragraph.

    Two distinctions this object is the place for: `schema_is_too_old`, the refusal, is not the
    same fact as `schema_is_behind_window` (one step behind, which is inside the window and needs no
    remedy), and a version this build cannot determine is not the same fact as a constraint this build
    falls outside. Only the second is the tree's mistake.
    '''

    root: Path
    constraint: str
    schema: int

    @classmethod
    def read(cls, root) -> "ConfigurationStamp":
        """The stamp of `root`, read through the one function that refuses to guess it."""
        constraint, schema = read_configuration_stamp(root)
        return cls(root=Path(root), constraint=constraint, schema=schema)

    @property
    def current(self) -> int:
        """What this build is. The same number `kmt --version` answers, from one source."""
        return constants.CONFIG_SCHEMA_VERSION

    @property
    def window(self) -> int:
        """How many steps back this build reads. One, and no fixture lane makes a wider window
        honest."""
        return SCHEMA_VERSION_WINDOW

    @property
    def oldest_accepted(self) -> int:
        return self.current - self.window

    @property
    def schema_is_too_old(self) -> bool:
        return self.schema < self.oldest_accepted

    @property
    def schema_is_behind_window(self) -> bool:
        return self.oldest_accepted <= self.schema < self.current

    @property
    def running_version(self) -> Optional[str]:
        return product_version()

    @property
    def version_is_determinable(self) -> bool:
        return self.running_version is not None

    @property
    def version_is_outside_constraint(self) -> bool:
        """The tree's constraint against this build. An undeterminable version answers `False`, and
        the reader has to have asked `version_is_determinable` to know why nothing was said.

        A clause that names no operator raises out of `satisfies_version_constraint`. A verb that
        collects faults rather than raising should turn that into a finding; a verb that warns can
        let it pass, which is what `check_configuration_stamp` has always done with it.
        """
        running = self.running_version
        if running is None:
            return False
        return not satisfies_version_constraint(self.constraint, running)

    @property
    def version_gap_sentence(self) -> str:
        """The version half, spelled once: both versions, and the fact that it warns."""
        return (f"this kubementat is {self.running_version} and {self.root} declares it was validated "
                f"against {self.constraint!r}. Continuing because a version string is not a migration.")


def check_configuration_stamp(root: Path) -> list:
    '''Refuse what is too old, warn about what is merely outside the range, hand back the warnings.

    Two checks with two different severities, and the split is the requirement and not a taste:

    * **The version string warns and continues.** A running cluster must not be bricked because a
      product version drifted past a range. `config validate` is the verb that gets to be strict
      about it.
    * **The schema version refuses.** Two or more steps behind and every answer this tree gives that
      is not named in its own layer is coming from a built-in default, so the cluster is configured
      by kubementat's assumptions and not by the operator's.

    It returns warnings instead of printing them, because "warn once" is a property of how many
    times the command ran and not of this function. A module-level memo would make the first test in
    a process the only one that can see the warning, and a test that asserts a warning only passes
    by accident when it runs first.

    The refusal must be reached before anything touches the cluster, which is why the only
    filesystem work here is the stamp itself.
    '''
    constraint, schema = read_configuration_stamp(root)
    stamp = ConfigurationStamp(root=root, constraint=constraint, schema=schema)
    if stamp.schema_is_too_old:
        raise click.ClickException(
            f"{root} declares schema_version {schema} and this kubementat is at "
            f"{stamp.current}, which is more than {stamp.window} step(s) "
            "behind. Answers this tree does not name for itself would come from built-in defaults, "
            "which configures the cluster by kubementat's assumptions and not by yours. "
            f"Raise schema_version in {root / 'kubementat.yaml'} to close the gap; that bump is a "
            "decision, and no command will make it for you.")

    warnings = []
    if not stamp.version_is_determinable:
        warnings.append(
            f"warning: kubementat could not determine its own version, so it cannot check the "
            f"{constraint!r} this tree declares. Continuing; run kmt --version to see what this is.")
    elif stamp.version_is_outside_constraint:
        warnings.append(f"warning: {stamp.version_gap_sentence}")
    return warnings


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


class Config:
    '''
    The Config class loads configuration values from JSON files stored in specific directories based on environment and team.

    Parameters
    ----------
    environment : str
        The environment name (e.g., 'dev', 'prod')
    team : str
        The team name. None is legal and means "no team was named": the environment-level
        files still resolve, the team-scoped paths are not built, and a read of a team-scoped
        category raises `TeamNotSuppliedError` rather than reading a path made out of the word
        `None`.
    kubementat_main_dir : Path, optional
        Main directory path for Kubementat project. If not provided, it defaults to
        `KUBEMENTAT_HOME`, and to the checkout this module lives in when that is unset.
    platform_config_dir : Path, optional
        Directory where platform configuration files are stored. If not provided, it
        defaults to `KUBEMENTAT_PLATFORM_CONFIG_DIR` and to nothing else: a run that names
        neither that variable nor `--config-dir` gets a refusal, not a root guessed out of
        where the package was installed from.
    tekton_global_pipeline_run_dir : Path, optional
        Global Directory for Tekton pipeline runs. If not provided, it defaults to
        'tekton_ci/pipeline-runs' directory within kubementat_main_dir.
    tekton_team_pipeline_run_dir : Path, optional
        Team Directory for Tekton pipeline runs for the according team. If not provided, it defaults to
        'tekton_ci/pipeline-runs/{TEAM_NAME}' directory within kubementat_main_dir.
    preload_configs : bool, optional
        If set to False, configuration values are not loaded from JSON files directly and are lazy loaded on access later. Default is True.

   Important Methods
    -------
    get(category):
        Returns configuration values for specified category (team_static, team_static_encrypted,
        env_static, env_static_encrypted).

    '''

    def __init__(self, environment, team, kubementat_main_dir=None, platform_config_dir=None, tekton_global_pipeline_run_dir=None, preload_configs = True):
        '''
        Initializes the Config instance with given parameters and sets up default paths.

        Parameters
        ----------
        environment : str
            Environment name (e.g., 'dev', 'prod')
        team : str
            Team name
        kubementat_main_dir : Path, optional
            Main directory path for Kubementat project. If not provided, it defaults to
            `KUBEMENTAT_HOME`, and to the checkout this module lives in when unset.
        platform_config_dir : Path, optional
            Directory where platform configuration files are stored. If not provided, it
            defaults to `KUBEMENTAT_PLATFORM_CONFIG_DIR`; with neither the argument and the
            variable, there is no root and the constructor refuses. `kubementat_main_dir` is
            never consulted for it.
        tekton_global_pipeline_run_dir : Path, optional
            Directory for Tekton pipeline runs. If not provided, it defaults to
            'tekton_ci/pipeline-runs' directory within kubementat_main_dir.
        '''
        self.TEKTON_API_VERSION = TEKTON_API_VERSION
        self.TEKTON_API_GROUP = TEKTON_API_GROUP
        self.TRIGGERS_API_VERSION = TRIGGERS_API_VERSION
        self.TRIGGERS_API_GROUP = TRIGGERS_API_GROUP
        self.environment = environment
        self.team = team
        
        # The root a caller handed is the answer, and the environment is consulted only when no
        # caller handed one. The order matters more than it looks: this used to call
        # `default_kubementat_main_dir()` first and override it second, so a `Config` that had
        # *been told* which tree to use still asked the process's own `KUBEMENTAT_HOME` on the way
        # through — and an empty-but-set `KUBEMENTAT_HOME` refused a command that had a root
        # threaded into it, over a dial nobody had consulted. Reading a dial you
        # were not asked about is also what makes the answer unstable: the same object means two
        # different things in two different shells.
        #
        # Precedence is therefore argument > environment variable > checkout-relative default,
        # so a test or a one-off run can point one instance somewhere without setting the variable
        # for the whole machine. Nothing here softens the refusal rule: with neither the argument
        # nor the variable, `default_kubementat_main_dir()` answers with the checkout this module
        # was installed from (D11's honest default for the *output* root), and the configuration
        # root below is the one that has no default to fall back to.
        if kubementat_main_dir is not None:
            self.kubementat_main_dir = Path(kubementat_main_dir).resolve()
        else:
            self.kubementat_main_dir = default_kubementat_main_dir()

        # `resolve_platform_config_dir` is the whole rule: the argument is the `--config-dir`
        # route, the variable is the container's route, and with neither there is no root to build
        # paths under. It is one function and not two spellings because this rule had a second
        # owner in `src/cli/commands/environments.py`, which spelled the same precedence beside its
        # own `EnvironmentRegistry` with no `Config` in sight — agreement is not the same thing as
        # one rule, and the day either one moves, the listing and a command's refusal start
        # disagreeing about which tree this operator configured.
        #
        # `kubementat_main_dir` is not consulted for this — it names the tree that still holds
        # checkout-relative *output* (`tekton_ci/pipeline-runs/`), while everything the product
        # ships is package data and resolves from inside the package. Reading
        # configuration out of it is the bug this rule is here to stop.
        self.platform_config_dir = resolve_platform_config_dir(platform_config_dir)

        # Pipeline runs are **output**: a run is written by whoever generated it and read back by
        # `kmt tekton run`, so it lives under the root it was generated under and never ships inside
        # the package. Do not "correct" this to `asset_path`: the asset side of `tekton_ci/` moved
        # under `src/kubementat/assets/` and this line is the half that stayed.
        self.tekton_global_pipeline_run_dir = Path(f"{self.kubementat_main_dir}/tekton_ci/pipeline-runs").resolve()
        if tekton_global_pipeline_run_dir is not None:
            self.tekton_global_pipeline_run_dir = Path(tekton_global_pipeline_run_dir).resolve()
        
        # The four `tekton_ci/**` directories split two ways, and the split is what these accessors
        # are for. `tasks/`, `pipelines/` (with their `templates/`) and `triggers/templates/` are
        # product assets: tracked files that belong to everybody and to no checkout in particular,
        # so they ship inside the package and resolve from inside it, exactly the way `templates/`
        # has always been package data. `pipeline-runs/` is output, and it is the line above that says so.
        #
        # Reading them through `kubementat_main_dir` was the bug this ticket fixes: `setup_pipelines`
        # applied `{root}/tekton_ci/{tasks,pipelines}`, so a `kmt` installed from a wheel applied a
        # directory that does not exist there, logged "Global Tasks applied successfully!", and
        # installed nothing. `apply_resources` raises `FileNotFoundError` for a missing directory,
        # so an asset root that is really there is the only thing this can be built from.
        self.templates_dir = asset_path("templates")

        # The install's entry point and its shipped defaults, one attribute because the two are one
        # directory: helmfile resolves a release's relative `values:` path against the directory of
        # the manifest it was handed, so the manifest and the ten values files it names travel
        # together or a release installs with chart defaults. Nothing writes under it and nothing
        # reads it out of a checkout: this is the manifest `helmfile_apply` applies, and
        # `config_reads_index.helmfile_paths_for` is its only reader in `src/` -- reached from
        # `HelmUtils.helmfile_paths` and from the config report alike, which is the one reason a
        # read of this attribute cannot end up pointed at two files.
        self.helmfile_asset_dir = asset_path("values")

        # The two roots a global `setup_pipelines` applies, as accessors for the same reason the
        # team dirs are accessors: there has to be one sentence about where the product's own
        # manifests are, or the next reader re-derives them off a root that is not there.
        self.tekton_task_asset_dir = asset_path("tekton_ci", "tasks")
        self.tekton_pipeline_asset_dir = asset_path("tekton_ci", "pipelines")

        # The generator *templates*, spelled as accessors too: the two generators below read them,
        # and `apply_resources` deliberately skips any directory named `templates/` (it is generator
        # input, not a resource to apply). The pipeline templates sit *inside* the pipeline root the
        # previous line names, which is why that skip is load-bearing rather than defensive — the
        # walk reaches `templates/` on its way through a real resource root. The pair of *output*
        # dirs next to them (`tekton_team_pipeline_dir`, `tekton_team_trigger_dir`) is therefore
        # never the same directory, and collapsing them would put a half-finished manifest in front
        # of a cluster.
        self.tekton_pipeline_template_dir = self.tekton_pipeline_asset_dir / "templates"
        self.tekton_trigger_template_dir = asset_path("tekton_ci", "triggers", "templates")

        # team dirs — built on access, never here. `Config(environment, None)` is a legal object —
        # a command that sweeps every team belongs to none of them — and interpolating the
        # word `None` into these six strings is the defect, so with no team named they are not
        # built at all and reading one raises `TeamNotSuppliedError`. See the properties below.
        self.env_static_config_path = Path(f"{self.platform_config_dir}/{self.environment}/static.json").resolve()
        self.env_static_encrypted_config_path = Path(f"{self.platform_config_dir}/{self.environment}/static.encrypted.json").resolve()

        # Populated by _load_config with the categories whose team-level file was missing,
        # so that get() can fail with an error that names the file instead of handing a None
        # namespace to the Kubernetes client.
        self.missing_team_config_files = {}

        # The dictionaries the four categories were read from, kept apart from the layered views
        # `self.config` holds. `None` means "nothing has been read yet", which is the same
        # statement `self.config[category] is None` made before the views existed.
        self.config_sources = None

        # Keys this instance has already reported falling back for, so a component that is
        # read by five commands produces one warning instead of five.
        self.defaulted_config_keys = set()

        self.config = self._load_config(preload_configs)

    def _team_scoped_path(self, relative: str, what: str) -> Path:
        '''
        One of the paths that only exist inside a team's directory.

        With no team named this class has nothing to resolve, and every alternative is worse than
        a refusal: the string `None` interpolated into the path is the defect, and a path built
        from an empty team silently lands on the *environment's* own files, which is the same
        reading of the wrong file wearing a cleaner name. So the answer is a refusal that says what
        was wanted, where it would have lived, and which name is missing.
        '''
        if self.team is None:
            raise TeamNotSuppliedError(
                f"{what} lives in the team directory of environment '{self.environment}', and this "
                f"configuration was built for no team, so no path under "
                f"platform_config/{self.environment}/<team>/ can be named. A command that reads it "
                f"was not given a team; kubementat will not name one for it. Pass --team (or set "
                f"TEAM) to name it."
            )
        return Path(f"{self.platform_config_dir}/{self.environment}/{self.team}").joinpath(
            *relative.split("/")).resolve()

    @property
    def team_static_config_path(self) -> Path:
        """`static.json` for this team — not built when no team is named."""
        return self._team_scoped_path("static.json", "The team's static configuration")

    @property
    def team_static_encrypted_config_path(self) -> Path:
        """`static.encrypted.json` for this team — not built when no team is named."""
        return self._team_scoped_path("static.encrypted.json",
                                      "The team's encrypted static configuration")

    # The middle apply layer, the directory that had no home for a while. There are three, and the
    # order is the
    # whole rule: product (`tekton_task_asset_dir`/`tekton_pipeline_asset_dir`, package data), then
    # these two, then the team's own (`tekton_team_*`, generated output). A Task that appears in two
    # of them is not resolved by code — the later `apply_resources` call patches the earlier object,
    # because `create_or_replace_custom_resource` patches on 409. So a name index here would be a
    # second source of truth about an order that already exists in the call sequence.
    #
    # `triggers/` is deliberately absent here, and that is a measured decision and not an
    # omission: the read path that applies a trigger directory is team-only
    # (`tekton_utils.py:1488-1514`), so an environment `triggers/` directory would be a directory
    # that exists and is read by nothing — the dead `kubementat_components/` tree, recreated. The
    # *team* trigger directory sits under `tekton/` (`tekton_team_trigger_dir` below);
    # the environment half lands only when something actually opens one.
    @property
    def tekton_env_task_dir(self) -> Path:
        """This environment's own Tasks — the layer between the product and its teams."""
        return Path(f"{self.platform_config_dir}/{self.environment}/tekton/tasks").resolve()

    @property
    def tekton_env_pipeline_dir(self) -> Path:
        """This environment's own Pipelines — the layer between the product and its teams."""
        return Path(f"{self.platform_config_dir}/{self.environment}/tekton/pipelines").resolve()

    @property
    def tekton_team_pipeline_dir(self) -> Path:
        """Where this team's generated pipelines are written."""
        return self._team_scoped_path("tekton/pipelines", "The team's generated pipelines")

    @property
    def tekton_team_task_dir(self) -> Path:
        """Where this team's generated tasks are written."""
        return self._team_scoped_path("tekton/tasks", "The team's generated tasks")

    @property
    def tekton_team_pipeline_run_dir(self) -> Path:
        """Where this team's pipeline runs are written, and read back from by `kmt tekton run`."""
        return self._team_scoped_path("tekton/pipeline-runs", "The team's pipeline runs")

    @property
    def tekton_team_trigger_dir(self) -> Path:
        """Where this team's trigger configurations are written, and read back from.

        Under `tekton/`, like its three siblings. A tree that still has the old `<team>/triggers/`
        directory is refused by name, at
        the point of reading (`tekton_utils.py`'s `_apply_team_trigger_resources`) and at the
        point of scaffolding (`config_init`), because silently applying nothing is the failure
        the move would otherwise create. kubementat records no rename table and rewrites nothing, so
        moving that content is the operator's and is decided beside this path, not by it.
        """
        return self._team_scoped_path("tekton/triggers", "The team's trigger configurations")

    def _load_config(self, preload_configs=True):
        '''
        Loads configuration values from JSON files into a dictionary.

        The function loads both static and encrypted configuration files for the environment
        and team. If no team-specific files are found, it logs a warning naming the path that
        was expected and proceeds with only the environment-level configurations; the
        team-level categories are then empty mappings, and a later read of one of them fails
        with an error naming that same file (see get()).

        Returns
        -------
        dict
            Configuration values organized by category (team_static, team_static_encrypted,
            env_static, env_static_encrypted)

        Raises
        ------
        FileNotFoundError
            If an environment-level configuration file is not found
        '''
        
        # skip early if preloading is not activated (e.g. for a verb that writes a tree
        # without resolving a cluster, which is what `config init` is)
        if not preload_configs:
            self.missing_team_config_files = {}
            self.config_sources = None
            return {
                'team_static': None,
                'team_static_encrypted': None,
                'env_static': None,
                'env_static_encrypted': None
            }
        
        try:
            # Load static and encrypted environment configs
            env_static = self._load_json_file(self.env_static_config_path)
            env_static_encrypted = self._load_json_file(self.env_static_encrypted_config_path)

            # Team-level files are optional: bind both names up front, because leaving them
            # unbound made the return below crash for every team that has no team-level
            # config (UnboundLocalError, plan D6).
            team_static = {}
            team_static_encrypted = {}
            self.missing_team_config_files = {}

            # With no team named there is nothing to scan, and nothing *missing* either: a
            # `missing_team_config_files` entry would be a claim that a file the run never asked
            # for is absent. The refusal belongs to the read, and `get()` is where it lives.
            if self.team is not None:
                if os.path.exists(self.team_static_config_path):
                    team_static = self._load_json_file(self.team_static_config_path)
                else:
                    logging.warning(f"No team static config found at {self.team_static_config_path}")
                    self.missing_team_config_files['team_static'] = str(self.team_static_config_path)

                if os.path.exists(self.team_static_encrypted_config_path):
                    team_static_encrypted = self._load_json_file(self.team_static_encrypted_config_path)
                else:
                    logging.warning(
                        f"No team static encrypted config found at {self.team_static_encrypted_config_path}")
                    self.missing_team_config_files['team_static_encrypted'] = str(
                        self.team_static_encrypted_config_path)

            logging.info(f"Loaded configuration for environment: {self.environment} and team: {self.team}")
            logging.info(f"Kubementat Main Directory: {self.kubementat_main_dir}")
            logging.info(f"Platform Config Directory: {self.platform_config_dir}")
            logging.info(f"Kubementat Templates Directory: {self.templates_dir}")
            logging.info(f"Tekton Global Pipeline Run Directory: {self.tekton_global_pipeline_run_dir}")
            # The environment layer of the apply order, printed here for the same reason the team's
            # dirs are: it is the directory a reader of this log is trying to find, and it exists as
            # a path whether or not anyone has ever put a manifest in it.
            logging.info(f"Tekton Environment Tasks Directory: {self.tekton_env_task_dir}")
            logging.info(f"Tekton Environment Pipelines Directory: {self.tekton_env_pipeline_dir}")
            # The four team directories are what a reader of this log is looking for, and with no
            # team named there is nothing to print — so the log says the one true thing instead of
            # raising on its way past a command that has done nothing wrong.
            if self.team is not None:
                logging.info(f"Tekton Team Pipelines Directory: {self.tekton_team_pipeline_dir}")
                logging.info(f"Tekton Team Tasks Directory: {self.tekton_team_task_dir}")
                logging.info(f"Tekton Team Pipeline Runs Directory: {self.tekton_team_pipeline_run_dir}")
                logging.info(f"Tekton Team Triggers Directory: {self.tekton_team_trigger_dir}")

            # The raw dictionaries are kept beside the views, because the layered answer is a
            # function of *both* files of a layer and the view throws that distinction away. The
            # views are what `get()` returns, so the layering happens here — where the categories
            # are loaded — and no call site has to know it exists.
            sources = {
                'team_static': team_static,
                'team_static_encrypted': team_static_encrypted,
                'env_static': env_static,
                'env_static_encrypted': env_static_encrypted,
            }
            self.config_sources = sources
            derived = (self.team, config_derived.RULES)
            return {category: _layered_view(CATEGORY_LAYERS[category], sources,
                                           self._config_path_of, derived,
                                           constants.DEFAULT_STATIC_ANSWERS)
                    for category in CATEGORY_LAYERS}

        except FileNotFoundError as e:
            logging.error(f"Configuration file not found - {e}")
            raise
    
    def get(self, category) -> dict:
        '''
        Returns configuration values for the specified category.

        The team-shaped rules answer here, because this is the door every placement question
        comes through. For what the files themselves say, use `declared`.

        Parameters
        ----------
        category : str
            The category of configuration to retrieve. Options are:
                - team_static
                - team_static_encrypted
                - env_static
                - env_static_encrypted

        Returns
        -------
        dict
            Configuration values for the specified category

        Raises
        ------
        click.ClickException
            If the category comes from a team-level file that does not exist. The warning
            already named that file when the config was loaded; this is the same file named
            in an error the user can act on.
        TeamNotSuppliedError
            If the category is a team-scoped one and this `Config` was built for no team.
            Returning an empty mapping would be the quieter failure, and it is the failure this
            refuses: a namespace that comes back empty is a namespace nobody chose.
        '''
        if self.team is None and category in TEAM_SCOPED_CATEGORIES:
            raise TeamNotSuppliedError(
                f"{category} is the team's own configuration, and this command was given no team "
                f"for environment '{self.environment}'. It is not missing a file: a team-level read "
                f"needs platform_config/{self.environment}/<team>/, which is where namespaces, "
                f"service accounts and deploy keys are named. kubementat will not name a team a "
                f"command did not name. Pass --team (or set TEAM) to name it."
            )
        self._ensure_config_loaded()
        missing_file = self.missing_team_config_files.get(category)
        if missing_file is not None:
            raise click.ClickException(
                f"No {category} config found at {missing_file}. "
                f"Environment: {self.environment}, team: {self.team} - create the file or "
                f"pass the environment and team you actually mean."
            )
        return self.config[category]

    def declared(self, category) -> dict:
        '''
        What `category`'s **files** answer, with no rule filling a hole. Same layers, same order.

        This is the question a destructive path has to ask, and it is a different question from
        `get()`. Five answers are consequences of a team's name, which is the right thing when
        kubementat is deciding *where to put something*: `pandas` team's pipelines namespace is
        `pandas-pipelines`, and asking the operator to type that is asking for a typo. It is the wrong
        thing when kubementat is deciding *what it may delete*, because the rule yields an address and
        not a deed — a cluster can hold a namespace called `pandas` that this configuration never
        created, and a name derived from a directory's name is not evidence that anybody here made it.
        So a namespace that no file named is never swept, and the log says that is why.

        It is deliberately not a flag on `get()`. A `derived=False` would be a knob any future caller
        could reach for to mean several different things, and the sentence that needs to exist is
        "this code only deletes what a file named" — a name carries that, and it is one method longer
        than a boolean.

        Same three guards as `get()` — no team, no file, nothing loaded — so the two doors cannot give
        two different failures for one problem.
        '''
        if self.team is None and category in TEAM_SCOPED_CATEGORIES:
            raise TeamNotSuppliedError(
                f"{category} is the team's own configuration, and this command was given no team "
                f"for environment '{self.environment}'. A deletion needs the namespace a file names, "
                f"and a team-less read has no file to name it from. Pass --team (or set TEAM)."
            )
        self._ensure_config_loaded()
        missing_file = self.missing_team_config_files.get(category)
        if missing_file is not None:
            raise click.ClickException(
                f"No {category} config found at {missing_file}. "
                f"Environment: {self.environment}, team: {self.team} - create the file or "
                f"pass the environment and team you actually mean."
            )
        return _layered_view(CATEGORY_LAYERS[category], self.config_sources,
                             self._config_path_of)

    def _ensure_config_loaded(self):
        """Read the files once, before any answer is given.

        The check used to be spelled inside `get()` against the requested category. It is a method
        now because a component read no longer goes through `get()`, and a lazy `Config` must not
        answer a namespace out of `constants.py` on the strength of a call that never loaded
        anything: one gate for both readers, and it cannot be skipped by the reader that forgot.
        """
        if self.config_sources is None:
            self.config = self._load_config(True)

    def _config_path_of(self, category):
        """The file `category` was read from, or `None` when this instance could not build one.

        A team-scoped category of a team-less `Config` has no path, and a resolution that
        needs no path is a resolution that says nothing about where an answer came from — the
        message is what this is for, so it is built here and never by a caller.
        """
        if category == "team_static":
            return self.team is not None and self.team_static_config_path or None
        if category == "team_static_encrypted":
            return self.team is not None and self.team_static_encrypted_config_path or None
        if category == "env_static":
            return self.env_static_config_path
        return self.env_static_encrypted_config_path

    def _resolved(self, key, layers):
        """The answer for `key` over `layers`, plus the file it came from. One path, two readers.

        `get()` materializes this for whole categories at load time; `_component_value` calls it for
        one key, because a component's namespace belongs to no file the caller was told about. Either
        way the walk is `_declare_in_layers`' and the order is `CATEGORY_LAYERS`' — a second
        implementation of the order is the bug census E found, so there is only this one. The team-
        shaped rules ride along on the same walk, which is the whole reason they answer the same way
        whichever door a caller came through.
        """
        self._ensure_config_loaded()
        return _declare_in_layers(key, layers, self.config_sources, self._config_path_of,
                                  (self.team, config_derived.RULES))

    def component_namespace(self, component: str) -> str:
        '''
        The namespace `component` is deployed into.

        Reads `<COMPONENT>_DEPLOYMENT_NAMESPACE` from the environment-level `static.json` and
        falls back to `kubementat.constants.DEFAULT_NAMESPACES`, warning once per instance
        when it does. Earlier the answer lived in the code (`automation.py` had four
        `namespace = '...'` lines, `uninstall_kubementat.sh` had fifteen), so renaming one
        namespace meant finding every copy of it — which is exactly what the
        `namespace-rename-tunnel-still-correct` lane assert used to catch.

        Parameters
        ----------
        component : str
            Component name, lower-case with hyphens, as `constants.DEFAULT_NAMESPACES` is
            keyed ('grafana', 'cert-manager', 'tekton').

        Returns
        -------
        str
            The namespace to address.

        Raises
        ------
        KeyError
            If `component` is not a kubementat component. A typo has to say so rather than
            silently deploy into a namespace named after the typo.
        '''
        return self._component_value(component, 'DEPLOYMENT_NAMESPACE', constants.DEFAULT_NAMESPACES)

    def component_release_enabled(self, component: str):
        r'''
        Whether `component`'s release is installed, answered by `<COMPONENT>_ENABLED`.

        One half of the pair the owner decision of 2026-11-03 settled: a release carries two
        facts an operator has to be able to state without editing a template — whether it runs,
        and which chart version of it — and both become `static.json` answers the same way the
        namespace already did. The read is `_resolved` over the component layers and
        nothing else, so the team's `false` outranks the environment's `"true"` the same way a
        team's namespace does.

        **L0 for this key is the shipped manifest and not `constants.py`, and that is the one
        break in an otherwise uniform rule.** The shipped `helmfile.yaml.gotmpl` is the entry point
        that applies the releases, so every chart-version literal is already owned there; copying
        those literals into `constants.py` would give one version string two owners and guarantee
        they disagree the first time a pin moves. The `default "true"` the manifest carries is
        this key's L0, and `None` from here is what makes it answer. The absence of a code table
        is the design rather than an unfinished one, so it is stated here and not "completed".

        Parameters
        ----------
        component : str
            Component name, lower-case with hyphens, as the manifest's `component_name` labels
            are spelled ('grafana', 'cert-manager', 'prometheus-blackbox-exporter').

        Returns
        -------
        str or None
            `"true"` or `"false"`, lower-cased — helmfile's `installed:` field resolves only
            those two, and a quoted `"False"` is a string that lands nowhere. `None` when no
            layer declares the key, which is a decision not to override and so is answered
            silently: there is no "you forgot" sentence to tell.

        Raises
        ------
        click.ClickException
            When the key is declared as a number, a list or a dict. There is no valid answer for
            this knob that is not a boolean or a string, so a mis-typed value is refused where it
            was typed and not papered over with the default.
        '''
        return self._release_knob(component_config_key(component, 'ENABLED'), component)

    def component_release_version(self, component: str):
        r'''
        The chart version `component`'s release installs, answered by `<COMPONENT>_VERSION`.

        The other half of the pair, read through the same one path so that the two knobs a
        release carries cannot disagree about which layer answered. Nothing in `constants.py`
        is consulted, and that is deliberate rather than a gap: the version kubementat ships is
        spelled in the manifest that applies it (see
        `component_release_enabled` for why that is the owner), and this function's only job is
        to say what the operator overrode. `None` here is the manifest answering for itself.

        Parameters
        ----------
        component : str
            Component name, lower-case with hyphens, as the manifest's `component_name` labels
            are spelled.

        Returns
        -------
        str or None
            The version string as declared, stripped. `None` when no layer declares the key.

        Raises
        ------
        click.ClickException
            When the key is declared as a number, a list or a dict. A version that reads like
            `1.0` is the shape this refusal exists for: JSON would have stored it as the number
            `1.0`, which is not the string `"1.0"` and resolves to a chart helm has never seen.
            `helmfile` quotes the manifest's own default for the same reason.
        '''
        return self._release_knob(component_config_key(component, 'VERSION'), component)

    def _release_knob(self, key: str, component: str):
        r'''
        The one implementation behind both release-knob accessors: the layers answer, or nothing does.

        `key` is spelled by the accessor that owns it, through `component_config_key`, so there is no
        third place that has to agree with what a component is called. `component` rides along for the
        one sentence that has to be actionable: which knob was refused, and where the edit belongs.

        This deliberately does **not** go through `_component_value`, which is the accessor that
        owns namespaces and selectors. That function tests `isinstance(value, str)` on the way to
        its default, and a JSON `false` is not a string — so the highest layer that declared
        `<COMPONENT>_ENABLED: false` would fall straight through to the default of `true` and log a
        warning about defaulting, i.e. an operator who said "do not install this" would get it
        installed and told the opposite. An accessor that answers a boolean is therefore a different
        reader, and this is the only one. `_resolved` is still the walk, `COMPONENT_LAYERS` is still
        the order, and `_component_layers` is still what decides whether the team half exists.

        There is no default to fall back to here, and that is the second half of the same choice:
        the fallback lives in the manifest's `default`, next to the literal it belongs to, so a
        layer that declares nothing costs nothing and prints nothing.

        Raises
        ------
        click.ClickException
            A declared value that is a number, list or dict. The message names the key, the file
            that declared it, and the two files of that layer the key could have lived in, since
            "which file did I edit" is the only question left once the type is known to be wrong.
        '''
        value, declared_in = self._resolved(key, self._component_layers())

        # An absent key and a key declared `null` are the same answer to this question — nobody
        # decided — and both reach the manifest's default. `False` is not in this branch, which is
        # the whole point: a declared `false` is a decision and has to survive the walk.
        if value is _NOTHING_DECLARED or value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            # Blank is the half-typed edit `_component_value` warns about; here it is the same
            # nothing, because there is no code default to warn about.
            return value.strip() or None
        where = (str(declared_in) if declared_in is not None
                 else "a file this configuration read")
        pair = (" or ".join(str(declared_in.parent / name)
                            for name in ("static.json", "static.encrypted.json"))
                if declared_in is not None
                else self._layers_to_check(self._component_layers()))
        raise click.ClickException(
            f"{key} is a {type(value).__name__} in {where}. kubementat reads that knob as a "
            f"boolean or a string and nothing else, so there is no default here to rescue it and "
            f"the run stops instead of installing something nobody chose. The key can only live in "
            f"{pair}, and the encrypted file of a layer answers before the plain one. Set it to "
            f"true or false (for ENABLED) or to a quoted chart version (for VERSION), or delete "
            f"the entry to let the manifest answer."
        )

    def teams(self) -> list:
        '''
        Every team that has configuration under this environment.

        The answer is a directory listing, not a list written in code. `uninstall_kubementat.sh`
        named `dev1` and `smoke` because those were the teams whoever wrote it had, and a third
        team's namespaces therefore survived `kmt uninstall` while its own documentation claimed
        otherwise. Enumerating `platform_config/{environment}/*/static.json` says what is true
        about this installation instead.

        Returns
        -------
        list[str]
            Team names, sorted, empty when the environment has no teams configured yet.

        Raises
        ------
        click.ClickException
            If there is no configuration directory for the environment at all. An environment
            that has never been prepared is not the same thing as an environment with no
            teams, and guessing the difference would delete nothing while reporting success.
        '''
        environment_directory = self.platform_config_dir / self.environment
        if not environment_directory.is_dir():
            raise click.ClickException(
                f"No configuration directory for environment '{self.environment}' at"
                f" {environment_directory}. Run kmt config init <dir> --environment"
                f" {self.environment}, or pass the environment you actually mean."
            )
        return sorted(
            entry.name
            for entry in environment_directory.iterdir()
            if entry.is_dir()
            and entry.name not in NON_TEAM_ENVIRONMENT_DIRECTORIES
            and (entry / "static.json").is_file()
        )

    def component_service_selector(self, component: str) -> str:
        '''
        The selector that finds `component`'s pods, read the same way as the namespace.

        The tunnels need both to reach a pod, so both come from the same place. A key that
        exists but is not read anywhere would be the second place to disagree.
        '''
        return self._component_value(component, 'SERVICE_SELECTOR', constants.DEFAULT_SERVICE_SELECTORS)

    def component_ingress_class(self, component: str) -> str:
        r'''
        The `ingressClassName` `component`'s controller serves, read the same way as the namespace.

        This key is the one answer the tree had three spellings of and one reader. `constants.py`
        answered it as L0, the Python readers reached it through `_component_value`, and
        `helmfile.yaml.gotmpl` answered it a third time with an inline `default` of its own -- which
        is why an environment that declared a class got a release configured with `traefik`.
        The manifest half is settled there: the variable this answer is exported into is what the
        release reads, so there is one owner, the layering walk below, and no second one next to the
        line that uses the value.

        The read is spelled as an accessor for the same reason `component_namespace` is: a caller that
        assembles `component_config_key` and a fallback table by hand is a caller that drifts, and this
        key's fallback table used to live in `automations/routing/routing.py` for exactly that reason.
        Both readers that already existed keep their own call for now, and all three paths go through
        this method's one table, so the answer is the same whichever door a caller came through.

        Raises
        ------
        KeyError
            When `constants.py` answers nothing for `component`'s class. That is not a missing
            declaration an environment could fix by editing a file: it is the table the walk consults
            last, and a default invented here would be the second owner this method exists to remove.
        '''
        key = component_config_key(component, 'INGRESS_CLASS')
        default = constants.static_answer(key)
        if default is None:
            raise KeyError(
                f"No default is defined for kubementat component '{component}' under {key}. Add it to"
                f" kubementat.constants.DEFAULT_STATIC_ANSWERS, which is the table this read's"
                f" resolution walk consults last, or answer it in the environment's static.json"
                f" with kmt config set. Writing a value here instead would put a second owner on"
                f" an answer that has one.")
        return self._component_value(component, 'INGRESS_CLASS', {component: default})

    def _component_value(self, component: str, suffix: str, defaults: dict) -> str:
        '''
        The one implementation behind the `component_*` accessors: the layers answer first, the
        code answers second, and only one of them gets to say which file to edit.

        Earlier this was the tree's only two-layer read: `env_static`, then `constants.py`,
        never the team's pair. It is now one of two readers of `_resolved`; the other is `get()`,
        which materializes the same walk into the category dictionaries. Which file a caller
        addresses is not a question about the value, so the accessor that owns a component's
        namespace asks the layers instead, and stays the only place that answers for those keys.
        '''
        try:
            default = defaults[component]
        except KeyError:
            raise KeyError(
                f"No default is defined for kubementat component '{component}'. Add it to"
                f" kubementat.constants, and answer {component_config_key(component, suffix)} in"
                f" this environment's own static.json (which is what this read answers from)."
            )

        key = component_config_key(component, suffix)
        layers = self._component_layers()
        value, declared_in = self._resolved(key, layers)
        if isinstance(value, str) and value.strip():
            if declared_in is None:
                # No layer declared it and an L0 answered, which is the same fact the branch below
                # reports. It looks different now because the walk itself can answer: an
                # L0 that lives in `constants.DEFAULT_STATIC_ANSWERS` comes back through
                # `_declare_in_layers`, and the table under this call's `defaults` is what the
                # namespace and selector families still use. Silence here would be a regression
                # against the operator-visible sentence `ingress_generator.py` relies on.
                self._warn_once_about_default(key, layers, None, value.strip(), component)
            return value.strip()

        self._warn_once_about_default(key, layers, declared_in, default, component)
        return default

    def _warn_once_about_default(self, key, layers, declared_in, default, component) -> None:
        """The once-per-`Config` sentence that says an answer did not come from a file.

        One gate for both shapes of that fact — nothing declared it, which is how an L0 answer
        arrives, and something
        declared it unusably (`_defaulted_warning`'s original reason) — so the count stays what
        `config_answers.py` silences it with and one read cannot print it twice.
        """
        if key in self.defaulted_config_keys:
            return
        self.defaulted_config_keys.add(key)
        logging.warning(self._defaulted_warning(key, layers, declared_in, default, component))

    def _component_layers(self) -> tuple:
        """The chain a component's keys are answered from: L2 over L1, with no L2 when no team
        was named. A `Config` built without a team has no team layer to consult, and trying to
        name that layer's paths would raise the team-not-named refusal over a question about a
        namespace.
        """
        return COMPONENT_LAYERS if self.team is not None else ENVIRONMENT_PAIR

    def _layers_to_check(self, layers) -> str:
        """Where this read looked, highest first, spelled for whoever has to act on it.

        Paths and not categories, because the sentence is a direction: "`GRAFANA_DEPLOYMENT_NAMESPACE`
        is not set in .../dev/static.json" is actionable and "L1 env_static" is not.
        """
        parts = []
        for layer, category in layers:
            path = self._config_path_of(category)
            if path is not None:
                parts.append(f"{layer} {path}")
        if not parts:
            return "nowhere - no configuration file was read"
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " or " + parts[-1]

    def _defaulted_warning(self, key, layers, declared_in, default, component) -> str:
        '''
        The warning that fires once per `Config` when nothing declared `key` in a usable form.

        Two facts it has to carry, and census E is why: **which layers were consulted**, so the
        operator edits the layer that answers rather than the one they were thinking of, and **which
        file of a layer would have answered**, since a layer is a pair and the encrypted file of it
        wins. Naming the key once is also load-bearing: the tree's own once-per-config test counts
        occurrences of the key in the log, and a sentence that recites it twice reads as a bug.

        The fallback below is L0. For the keys this warning covers, L0 is
        `src/kubementat/constants.py` and nothing else; the exception is the five team-shaped answers
        in `config_derived.py`, which are not defaults and do not warn, because they were
        never a question. The packaged `defaults/static.json` is not consulted here
        and does not exist in this tree, and must not be added in this phase: its source is the
        environment template, 40 keys of which 8 are `REPLACEME_*`, so promoting it to a default
        source would answer with placeholders and give the namespaces a second owner beside
        `constants.py`. Phase 3 is where that template stops being copied. Do not "complete" this
        fallback from the spec.
        '''
        if declared_in is None:
            opening = f"{key} is not set in {self._layers_to_check(layers)}"
            which = ("A team's pair answers before the environment's, and inside one layer the "
                     "encrypted file answers before the plain one, so the highest layer that "
                     "declares it is what this run would have used")
        else:
            opening = (f"{key} is set but empty in {declared_in}, so no layer of "
                       f"{self._layers_to_check(layers)} answers")
            which = (f"the layer that declared it is {declared_in.name} - an empty value is a "
                     f"half-typed edit, not a decision to use the default")
        return (
            f"{opening} - using kubementat's built-in default '{default}' for {component}. {which}."
            f" Edit that file to run {component} in a different namespace; the default only exists"
            f" so a config written before this was configurable still works."
        )
    
    def _load_json_file(self, file_path):
        # One read, one sentence. A file whose bytes will not decode is refused as
        # git-crypt's and not as malformed JSON, and the sentence is the seam's, because four sites
        # used to say this four different ways. `FileNotFoundError` still comes from here, since the
        # optional-team-file handling in `_load_config` below is what makes a missing layer degrade
        # and not explode.
        return json.loads(read_decrypted(file_path, "static layer file"))
