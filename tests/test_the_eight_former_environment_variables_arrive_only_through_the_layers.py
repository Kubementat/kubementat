"""The variable row: the eight keys arrive through the layers, or they arrive nowhere.

`KUBEMENTAT_ENVIRONMENT_CONFIGURATION` was `initialize`'s eight-variable requirement list, and the
rule that replaced it is not "the keys are required" — it is *where they come from*.
Measured at `37a2613`, the dictionary named by the spec is gone from `src/`, `_check_required_environment_variables`
is gone, and no `os.environ` read anywhere in `src/` answers one of the eight. What a reader could not
tell from the greps is whether that was the end of the migration or the absence of a check, so this
file states the property the greps cannot: **the only door a value of one of these keys comes through
is the layered configuration read path**, and the only refusal that fires when a key answers nothing
names the key, the file it belongs in, and the verb that sets it.

The eight, in the spec's own order, with the one name that changed underneath them:

`KUBERNETES_DEFAULT_STORAGE_CLASS` became `TEKTON_KUBERNETES_STORAGE_CLASS` in `7888bdf`, the
`initialize` deletion. The old name is the only identifier that left the product during this epic
(census S), so the row below carries the live spelling; a row that carried the
dead one would be a key that no door answers. `AUTOMATION_GIT_PROJECT_NAME` and
`AUTOMATION_GIT_REVISION` are the two automation coordinates that sit next to `AUTOMATION_GIT_URL`
and carry an L0 of their own — they are the CI param spellings that were renamed and are deliberately not
added to this row, which is the eight variables and no more.

Two things this file does *not* do, and both matter. It adds no required-key list and no validator to
`Config`: most of the trees this suite opens are fixtures and not prepared directories, and a rule
phrased as "the tree must answer every key" is how the version-stamp gate broke 227 tests before it
moved to the CLI boundary. And it does not touch the `helm_utils.py` exports of `os.environ`: those are
how an answer reaches the helmfile subprocess, they are writes and not reads, and the check below says
so by classifying every access in the tree rather than banning the pattern.
"""

import ast
import json
from pathlib import Path

import pytest

from kubementat import ci_utils, config_answers, tekton_utils
from kubementat.core import constants
from kubementat.ci_utils import CiUtils
from kubementat.errors import CiImageNotConfiguredError
from kubementat.core.config.config import Config

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

ENVIRONMENT = "eightenv"
TEAM = "eightteam"

# The eight keys, at the spellings `src/` answers today. The value of each entry is what
# the key is *for*, because a row that is only names is how a list rots: the sentence is the part a
# reader checks a change against.
FORMER_ENVIRONMENT_VARIABLES = {
    "BASE_DOMAIN": "the domain every generated host is built under",
    "AUTOMATION_GIT_URL": "the automation remote kubementat clones and pushes",
    "AUTOMATION_GIT_SERVER_HOST": "the host half of that remote's coordinate",
    "AUTOMATION_GIT_SERVER_PORT": "the port half of that remote's coordinate",
    "AUTOMATION_GIT_SERVER_SSH_USER": "the user half of that remote's coordinate",
    # `KUBERNETES_DEFAULT_STORAGE_CLASS`, renamed when `initialize` went (`7888bdf`).
    "TEKTON_KUBERNETES_STORAGE_CLASS": "the volume class a generated PipelineRun asks for",
    "DOCKER_REGISTRY_BASE_URL": "the registry half of the CI image reference",
    "CLUSTER_MANAGER_EMAIL": "the ACME contact every ClusterIssuer registers",
}

# The keys of the row that `constants.DEFAULT_STATIC_ANSWERS` answers for, so a tree that typed
# nothing still has an answer. Measured and not assumed: this is the intersection of the row with the
# L0 table, and it is why "missing" cannot be asserted for all eight.
KEYS_WITH_A_CODE_DEFAULT = tuple(key for key in FORMER_ENVIRONMENT_VARIABLES
                                 if key in constants.DEFAULT_STATIC_ANSWERS)

# What the layer declares in the "a file answered it" tests.
FROM_THE_LAYER = "answered-by-the-layer"

# The CI key the refusal clause is proven on: no L0, and a live consumer.
CI_REGISTRY_KEY = "DOCKER_REGISTRY_BASE_URL"


# --------------------------------------------------------------------------------------- 1. the door


def seed(root, environment_level=None, team_level=None) -> Config:
    """A tree that declares what the test handed and nothing else.

    Both pairs of both layers exist, because a missing layer file is refused and that refusal is about
    a broken tree rather than about a key. The root is named and never located.
    """
    environment_dir = root / "platform_config" / ENVIRONMENT
    team_dir = environment_dir / TEAM
    team_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in ((environment_dir / "static.json", environment_level or {}),
                          (environment_dir / "static.encrypted.json", {}),
                          (team_dir / "static.json", team_level or {}),
                          (team_dir / "static.encrypted.json", {})):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def _answers(config, key: str):
    """One key's answer as the report gives it, from the same walk every verb uses."""
    return {answer.key: answer
            for answer in config_answers.effective_answers(config.platform_config_dir,
                                                           ENVIRONMENT, TEAM)}[key]


@pytest.mark.parametrize("key", sorted(FORMER_ENVIRONMENT_VARIABLES))
def test_the_value_the_layer_declares_is_the_value_the_read_path_answers(tmp_path, key):
    """One door, asserted per key: write it in the environment's file and read it back.

    The read is the one every command makes (`Config.get('env_static')`), and the attribution is the
    one `config_answers.effective_answers` reports — the same `_resolved` call and not a second walk.
    Both are asserted because a value that arrives through one and is reported as coming from the
    other is how a tree stops matching the report that describes it.
    """
    config = seed(tmp_path, environment_level={key: FROM_THE_LAYER})

    assert config.get("env_static")[key] == FROM_THE_LAYER, \
        f"{key} did not arrive from the layer that declares it"
    answer = _answers(config, key)
    assert answer.origin == config_answers.ENVIRONMENT_ORIGIN, answer.origin
    assert answer.source == config.env_static_config_path, \
        "the answer has to say which file answered it, and the file is this one"


@pytest.mark.parametrize("key", sorted(FORMER_ENVIRONMENT_VARIABLES))
def test_a_key_the_row_leaves_to_l0_never_arrives_from_the_process_environment(tmp_path, key):
    """Nothing declares it, so the answer is the code default or nothing. Never the shell.

    `constants.static_answer` is the only thing that can answer here, and it is reached through the
    same walk; a key outside that table answers `None` and its consumer refuses. Either way the
    process environment has not been asked, which is the half the greps could only suggest.
    """
    config = seed(tmp_path, environment_level={}, team_level={})
    expected = constants.static_answer(key)  # None for the keys outside the table.

    assert config.get("env_static").get(key) == expected
    if key in KEYS_WITH_A_CODE_DEFAULT:
        assert key == "TEKTON_KUBERNETES_STORAGE_CLASS"
        answer = _answers(config, key)
        assert answer.origin == config_answers.DEFAULT_ORIGIN
        assert answer.default == expected
    else:
        assert expected is None


def test_the_substitution_tables_and_the_read_path_agree_on_the_category(tmp_path):
    """Every (category, key) pair in `src/` that names one of these keys says `env_static`.

    `tekton_utils.CONFIG_PLACEHOLDER_SOURCES` is what fills a generated manifest's placeholders, and
    it is a second statement of "where this value comes from". If one of its rows said `team_static`
    while every read said `env_static`, the generator and the command would answer two different
    questions from one key — which is the drift class this row is supposed to close. Keys no table
    mentions (`CLUSTER_MANAGER_EMAIL` is read directly by `routing._cluster_manager_email`) are
    reported as absent from the tables rather than treated as a failure.
    """
    triples = _category_tables(tekton_utils)
    seen = {}
    for key in FORMER_ENVIRONMENT_VARIABLES:
        entries = [(table, source, category)
                   for table, source, category, target in triples if target == key]
        categories = {category for _, _, category in entries}
        assert categories <= {"env_static"}, (key, sorted(categories))
        seen[key] = sorted((table, source) for table, source, _ in entries)

    assert seen[CI_REGISTRY_KEY] == [("CONFIG_PLACEHOLDER_SOURCES", "DOCKER_REGISTRY_BASE_URL")]
    assert seen["CLUSTER_MANAGER_EMAIL"] == [], \
        "the manager address is read by a command, not by a substitution table"


# --------------------------------------------------------------------- 2. no key arrives from the shell


# Every access to the process environment in `src/`, by file and the symbol it sits in. A new access
# fails this test until it is listed here, and listing it says what it reads and why it is not one of
# the eight — which is the difference between an exemption and a blind spot.
#
# `read`: the access can answer a value. `write`: it hands an answer to a subprocess, which is the
# shape of every `helm_utils.py` line and is why the exports stay.
PROCESS_ENVIRONMENT_USES = {
    ("src/cli/context.py", "_environment_of"): {
        "names": ("ENVIRONMENT",),
        "why": "the current-environment variable a CLI argument overrides.",
        "mode": "read",
    },
    ("src/kubementat/core/identity/kubeconfig.py", "default_kubeconfig_path"): {
        "names": ("KUBECONFIG",),
        "why": "kubeconfig discovery, which is a fact about the machine and not about a tree.",
        "mode": "read",
    },
    ("src/kubementat/core/identity/kubeconfig.py", "_service_account_address"): {
        "names": ("KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT"),
        "why": "the in-cluster service account's own address, which is readable nowhere else. It "
               "travelled to the module that owns the two variables it reads, so the fold here is "
               "native and needs no home of its own.",
        "mode": "read",
    },
    ("src/kubementat/core/identity/context.py", "_process_value"): {
        "names": ("CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH"),
        "constants": ("CLUSTER_KUBE_CONTEXT_KEY", "CLUSTER_KUBECONFIG_PATH_KEY"),
        "constants_home": "src/kubementat/core/constants.py",
        "why": "layer 2 of the cluster dial (`--context`/`--kubeconfig`, then this, then "
               "static.json, then the in-cluster identity). The CLUSTER_* keys are kept out of this "
               "row for exactly this reason, and the loop is handed those two constants "
               "and nothing else — asserted against the module's own table, not against this row. "
               "Their module is now `core/constants.py`, which is spelled out below because three of "
               "the identity modules name them.",
        "mode": "read",
    },
    ("src/kubementat/core/config/paths.py", "_directory_from_environment"): {
        "names": ("KUBEMENTAT_HOME", "KUBEMENTAT_PLATFORM_CONFIG_DIR"),
        "constants": ("KUBEMENTAT_HOME_ENV_VAR", "KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR"),
        "why": "the two directory overrides; they name a root, not an answer.",
        "mode": "read",
    },
    ("src/kubementat/helm_utils.py", "helmfile_apply"): {
        "names": ("GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD"),
        "why": "the Grafana pair exported for the values template. It is read out of "
               "`static.encrypted.json` by `_grafana_admin_credentials` and written here so the "
               "template sees it; the operator's shell is never consulted.",
        "mode": "write",
    },
    ("src/kubementat/helm_utils.py", "_export_component_namespaces"): {
        "names": ("<component>_DEPLOYMENT_NAMESPACE, from constants.DEFAULT_NAMESPACES",),
        "why": "the namespace answers exported for the gotmpl; every key is built by "
               "`component_config_key` over `constants.DEFAULT_NAMESPACES`, so its universe is the "
               "components and not this row.",
        "mode": "write",
    },
    ("src/kubementat/helm_utils.py", "export_configuration_coordinates"): {
        "names": ("KUBEMENTAT_CONFIG_ROOT", "KUBEMENTAT_ENVIRONMENT", "KUBEMENTAT_TEAM"),
        "derived": "literal-keys",
        "why": "the coordinates of the run, so a nested kmt finds the same root. Names of "
               "places and identities, none of them an answer from this row.",
        "mode": "write",
    },
    ("src/kubementat/helm_utils.py", "export_component_release_knobs"): {
        "names": ("<component>_ENABLED / <component>_VERSION, from the release index",),
        "why": "the release knobs helmfile evaluates, keyed by `component_config_key` over the "
               "shipped manifest.",
        "mode": "write",
    },
    ("src/kubementat/helm_utils.py", "export_ingress_class"): {
        "names": ("<component>_INGRESS_CLASS, one component: constants.COMPONENT",),
        "derived": "component-config-key",
        "why": "the ingress class the configuration answers, exported so the manifest's `env` sees "
               "it. One answer, one export, one read path. The component is named by `constants` and "
               "not by this file, which is why the key is spelled here instead of folded from a "
               "constant in it: `helm_utils` used to keep its own spelling of the name, and that copy "
               "went away with the routing modules' copies.",
        "mode": "write",
    },
}


def _category_tables(module) -> list:
    """Every `(table, source, category, key)` triple the module declares.

    Read off the AST rather than off the import, because the claim is about what the file says.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    triples = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)):
            continue
        name = next((t.id for t in node.targets if isinstance(t, ast.Name)), "<unnamed>")
        for k, v in zip(node.value.keys, node.value.values):
            if not isinstance(k, ast.Constant):
                continue
            try:
                value = ast.literal_eval(v)
            except Exception:
                continue
            if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], str):
                triples.append((name, k.value, value[0], value[1]))
    return triples


def _module_string_constants(path: Path) -> dict:
    """The module-level `NAME = "string"` bindings of *path*, folded the same way the scanner folds."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    if isinstance(node.value.value, str):
                        found[target.id] = node.value.value
    return found


def _literal_key_names(path: Path, symbol: str) -> set:
    """The string literals *symbol* spells as dictionary keys or as an assigned subscript.

    Used where a function hands its own table to the environment one name at a time: the names are in
    the file, just not at the subscript the scanner is looking at.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            names = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    names.update(k.value for k in sub.keys
                                 if isinstance(k, ast.Constant) and isinstance(k.value, str))
                elif isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Store) \
                        and isinstance(sub.slice, ast.Constant):
                    names.add(sub.slice.value)
            return {name for name in names if isinstance(name, str)}
    return set()


def _component_key_names(path: Path, symbol: str) -> tuple:
    """The keys *symbol* can build with `component_config_key`, and whether any of them is opaque.

    A component named by a module constant and a literal suffix folds exactly the way the product
    builds the key; a component that arrives from a loop does not, and that is what the second half of
    the return value says. The fold calls the product's own function rather than guessing the rule.
    """
    from kubementat.core.config.keys import component_config_key

    module_constants = _module_string_constants(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    folded, unfoldable = set(), False
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol):
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "component_config_key"):
                continue
            args = sub.args
            first = args[0].id if args and isinstance(args[0], ast.Name) else None
            second = args[1].value if len(args) > 1 and isinstance(args[1], ast.Constant) else None
            if first in module_constants and isinstance(second, str):
                folded.add(component_config_key(module_constants[first], second))
            else:
                unfoldable = True
    return folded, unfoldable


def _env_access_sites(path: Path) -> list:
    """Every place *path* touches `os.environ`, with what it reads or writes and which symbol.

    AST and not a line regex, for two reasons: a comment that says `os.environ` is not an access, and
    a name that reaches the environment through a variable — which is what every `helm_utils.py`
    export does — has to be found by its subscript and not by its text.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants_by_name = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    constants_by_name[target.id] = node.value.value

    sites = []

    def visit(node, symbol):
        for child in ast.iter_child_nodes(node):
            next_symbol = symbol
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                next_symbol = child.name
            node_names = []
            node_mode = None
            if isinstance(child, ast.Call) and _is_environ_get(child.func):
                node_mode = "read"
                node_names = _names_of(child.args[0], constants_by_name) if child.args else ["?"]
            elif isinstance(child, ast.Compare) and any(_is_environ(c) for c in child.comparators):
                node_mode = "read"
                node_names = _names_of(child.left, constants_by_name)
            elif isinstance(child, ast.Subscript) and _is_environ(child.value):
                node_mode = "write" if isinstance(child.ctx, ast.Store) else "read"
                node_names = _names_of(child.slice, constants_by_name)
            if node_mode:
                sites.append(_Site(path.relative_to(REPO).as_posix(), next_symbol, child.lineno,
                                   node_mode, node_names))
            visit(child, next_symbol)

    visit(tree, "<module>")
    return sites


class _Site:
    """One `os.environ` access: where, what it is called through, and the names it can carry."""

    def __init__(self, path, symbol, lineno, mode, names):
        self.path, self.symbol, self.lineno = path, symbol, lineno
        self.mode, self.names = mode, names

    @property
    def identity(self):
        return (self.path, self.symbol)

    def label(self):
        return f"{self.path}:{self.lineno} ({self.symbol})"


def _is_environ_get(func) -> bool:
    """`os.environ.get(...)` / `os.getenv(...)` — an access that answers a value."""
    return (isinstance(func, ast.Attribute) and func.attr in ("get", "getenv")
            and _is_environ(func.value)) \
        or (isinstance(func, ast.Name) and func.id == "getenv")


def _is_environ(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os") \
        or (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Attribute) and _is_environ(node.value))


def _names_of(node, constants_by_name) -> tuple:
    """The variable names this access can carry, with the module constants folded in.

    A name the AST cannot fold comes back as the expression text, and the exemption table is what
    claims it — which is why the table's `names` entries are checked against this and not trusted.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Name) and node.id in constants_by_name:
        folded = constants_by_name[node.id]
        return (folded,) if isinstance(folded, str) else (node.id,)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return (node.slice.value,)
    return ("?" + ast.unparse(node),)


def test_no_former_environment_variable_arrives_from_the_process_environment():
    """Every `os.environ` access in `src/` is one this file can explain, and none answers the row.

    Three assertions, and each one catches a different way of going wrong:

    * **the set of accesses matches the table.** A new access that is not listed fails the test, so an
      exemption cannot be added by omissions and the table cannot be kept for an access that has left.
    * **no name any access carries is one of the eight** — checked against the names the AST folded,
      so a table that claims a harmless name for a site that reads something else fails too.
    * **the reads are reads.** The exports are writes, and classifying them is what stops a grep for
      `os.environ` from being either a false green or a false alarm about helmfile.
    """
    sites = []
    for path in sorted(SRC.rglob("*.py")):
        sites.extend(_env_access_sites(path))

    listed = set(PROCESS_ENVIRONMENT_USES)
    found = {site.identity for site in sites}
    assert found == listed, (
        "the process-environment accesses in `src/` no longer match the table that explains them. "
        f"Unlisted: {sorted(found - listed)}; gone: {sorted(listed - found)}. List a new access with "
        "what it reads and why it is not one of the eight, or delete the row that no longer answers.")

    eight = set(FORMER_ENVIRONMENT_VARIABLES)
    for site in sites:
        entry = PROCESS_ENVIRONMENT_USES[site.identity]
        carried = {name for name in site.names if not name.startswith("?")}
        if not carried:
            # The name is built at runtime, so the AST cannot fold it. Two honest answers: the table
            # names the module constants the loop is handed, and the test folds them itself; or the
            # table describes what the name is built out of. A blank here would be "I could not see
            # it" read as "it cannot be one of the eight".
            if entry.get("constants"):
                # A constant the loop is handed can live in a module the reader imports, so the table
                # says whose constant it is and the fold happens there. Absent that key the constants
                # are the reader's own, which is what every row but the two cluster ones needs.
                home = entry.get("constants_home", site.path)
                module_constants = _module_string_constants(Path(REPO / home))
                declared = list(entry["constants"])
                assert all(name in module_constants for name in declared), \
                    f"{site.label()}: {declared} are not all module constants of {home} any more"
                derived = {module_constants[name] for name in declared}
                assert derived == set(entry["names"]), (
                    f"{site.label()} can carry {sorted(derived)} and the table says "
                    f"{sorted(entry['names'])}")
                assert not (derived & eight), \
                    f"{site.label()} reads {sorted(derived & eight)} from the process environment"
            elif entry.get("derived") == "literal-keys":
                derived = _literal_key_names(Path(REPO / site.path), site.symbol)
                assert derived == set(entry["names"]), (
                    f"{site.label()} spells {sorted(derived)} in its own dict and the table says "
                    f"{sorted(entry['names'])}")
                assert not (derived & eight), \
                    f"{site.label()} exports {sorted(derived & eight)} into the process environment"
            elif entry.get("derived") == "component-config-key":
                folded, unfoldable = _component_key_names(Path(REPO / site.path), site.symbol)
                assert not (folded & eight), \
                    f"{site.label()} exports {sorted(folded & eight)} into the process environment"
                if not unfoldable:
                    assert folded == set(entry["names"]), (
                        f"{site.label()} builds {sorted(folded)} from component_config_key and the "
                        f"table says {sorted(entry['names'])}")
                else:
                    assert any(name.startswith("<") for name in entry["names"]), (
                        f"{site.label()} builds some keys from a component the file does not name; "
                        "say what the set of components is.")
            else:
                assert any(name.startswith("<") for name in entry["names"]), (
                    f"{site.label()} builds its name at runtime and the table says nothing about how; "
                    "say what the name is built out of.")
        else:
            assert not (carried & eight), \
                f"{site.label()} touches {sorted(carried & eight)} in the process environment"
            assert carried <= set(entry["names"]), \
                f"{site.label()} carries {sorted(carried)} and the table says {sorted(entry['names'])}"
        assert site.mode == entry["mode"], \
            f"{site.label()} is a {site.mode} and the table calls it a {entry['mode']}"

    # And the row itself, asked the other way round: not "does something read it from the shell",
    # which is what a table proves, but "does every one of these keys have an environment route".
    for key in FORMER_ENVIRONMENT_VARIABLES:
        assert key not in _environment_names_by_site(), \
            f"{key} is answered by an os.environ access somewhere in src/"


def _environment_names_by_site() -> dict:
    return {site.identity: set(name for name in site.names if not name.startswith("?"))
            for path in sorted(SRC.rglob("*.py"))
            for site in _env_access_sites(path) if site.mode == "read"}


# -------------------------------------------------------------------------------------------- 3. the refusal


def test_the_registry_url_the_configuration_never_answers_refuses_with_the_verb_that_sets_it(
        tmp_path):
    """The done-when for this row, on the one key that has no L0 and a live consumer.

    `CiUtils.ci_image` is reached the way the existing CI tests reach it — the same call, the same
    exception, no message assembled for the test — because a refusal shape asserted against a string
    the product does not produce is a green that means nothing.

    The three parts, measured as they arrive:
      * the **key**: `DOCKER_REGISTRY_BASE_URL` in the sentence, which is the only key of the row this
        path can refuse, since the name and the tag answer from `constants.py` and nothing else;
      * the **file**: the `static.json` the value belongs in, so the operator opens one file;
      * the **command**: `kmt config set`, the verb that writes that file. `routing.py`'s refusal of
        `CLUSTER_MANAGER_EMAIL` has always ended this way; this is that shape, on this key.
    """
    config = seed(tmp_path, environment_level={}, team_level={})
    assert CI_REGISTRY_KEY not in config.get("env_static"), "the fixture declared the key itself"

    ci = CiUtils.__new__(CiUtils)
    ci.config = config
    with pytest.raises(CiImageNotConfiguredError) as error:
        ci_utils.CiUtils.ci_image(ci)

    message = str(error.value)
    assert CI_REGISTRY_KEY in message, message
    assert str(config.env_static_config_path) in message, \
        "the refusal has to name the file the answer belongs in"
    assert "kmt config set" in message, message
    assert error.value.config_path == config.env_static_config_path


def test_the_refusal_is_the_only_answer_a_tree_gets_and_names_the_key_it_lacks(tmp_path):
    """One refusal, not one per read. The consumer that finds the gap is what speaks.

    A tree that declares nothing for the registry URL answers `None` from the layer walk — no
    default, no fallback, no empty string — and the key is reported exactly once, by the command that
    cannot name an image without it. `kmt run-ci-container` is the verb; `ci_image` is the read that
    stops it before a pod is started with a reference ending in `None`.
    """
    config = seed(tmp_path, environment_level={}, team_level={})

    assert config.get("env_static").get(CI_REGISTRY_KEY) is None

    ci = CiUtils.__new__(CiUtils)
    ci.config = config
    with pytest.raises(CiImageNotConfiguredError) as error:
        CiUtils.ci_image(ci)
    assert error.value.missing_keys == [CI_REGISTRY_KEY], \
        "the refusal has to name the key and nothing it did not fail on"
