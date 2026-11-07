"""The layering lives in `core/config/keys.py`, left every call site alone, and has one owner.

Census E's whole finding was *where* the answer came from, so the answer to it has to be keepable in
one place — and a rule that lives in one place is a rule a later ticket can move without noticing
unless something here is structural. P2-2d moved that one place out of `config.py` and into `keys.py`,
which is why the exclusions below name two files instead of one: the owner is `keys.py` and
`core/config/config.py`
is the module that reaches it.

Four claims, one test each, in the shape the tree already uses for deletions and single-owner rules
(`ast.parse` over the shipped roots, method bodies read out of the syntax tree rather than
paragraph-matched):

* **Two files name the layering, and no more.** The tables and the walk are `keys.py`'s vocabulary and
  `core/config/config.py` is the only reader of it; a third file that names `CATEGORY_LAYERS` or the pair
constants
  is a second owner of the order, and two owners of one rule is how a listing and a command start
  disagreeing about which layer answered.
* **The raw category dictionaries are private to `core/config/config.py`.** A caller that reaches past
the
  layered view and reads `env_static` out of the loaded configuration answers for itself again,
  which is the census E bug with a nicer name.
* **`_component_value` is still the one accessor for the keys it owns** — namespace, selector,
  ingress class — and it goes through the shared resolution path instead of `get('env_static')`,
  so it cannot drift out of the rule it was subsumed into.
* **The four filenames and the four category names are unchanged**: layering is about which
  layer answers, not about a fifth category, a new directory under the configuration root, or a
  renamed file.
"""

import ast
import inspect
from pathlib import Path

from kubementat.core.config import config as config_module
from kubementat.core.config import keys as keys_module

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PY = REPO_ROOT / "src" / "kubementat" / "core" / "config" / "config.py"
KEYS_PY = REPO_ROOT / "src" / "kubementat" / "core" / "config" / "keys.py"

# The roots that could hold a second owner of the rule. `templates` is not here: it moved inside the
# package, so "src" sweeps it and naming the old root would sweep nothing.
CALLER_ROOTS = ("src", "scripts", "tekton_ci", "examples", "tools")

# The files that are allowed to name the layering: the module that owns it and the module that reads
# it. A third name here is a second owner, and that is what the first test is for.
LAYERING_OWNERS = (CONFIG_PY, KEYS_PY)

# The vocabulary the layering is spelled in. A file outside `core/config/config.py` that names any
# of these has
# not called the rule, it has *re-implemented* it.
LAYERING_VOCABULARY = (
    "CATEGORY_LAYERS", "COMPONENT_LAYERS", "TEAM_PAIR", "ENVIRONMENT_PAIR",
    "_declare_in_layers", "_layered_view", "LAYER_TEAM", "LAYER_ENVIRONMENT",
)

# The category names, and no more than these. The layering rule keeps the filenames where they are,
# so this
# is also the guard that no new category — and with it no new directory under the root — appeared.
CATEGORIES = {"team_static", "team_static_encrypted", "env_static", "env_static_encrypted"}


def relative(path):
    """`path` as it would be named in a commit — the spelling every assertion here uses."""
    return str(Path(path).relative_to(REPO_ROOT))


def shipped_python_files(*roots):
    """Every `.py` under *roots*, skipping the directories nobody is reading."""
    found = []
    for root in roots:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" not in path.parts and "site-packages" not in path.parts:
                found.append(path)
    return found


def config_method(name):
    """The `ast.FunctionDef` for a method of `Config`, found in the tree rather than the import."""
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Config":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"Config.{name} is not in {relative(CONFIG_PY)}")


def calls_inside(node):
    """Every function or method *name* called inside `node`, folded across attribute calls."""
    return [(call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id)
            for call in ast.walk(node) if isinstance(call, ast.Call)]


def statements_of(function):
    """A function body without its docstring, because a paragraph is not a statement."""
    return [statement for statement in function.body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))]


# --- the layering has one owner ----------------------------------------------------------


def test_only_keys_py_and_config_py_name_the_layering():
    """A rule with two owners is a rule that rots the day one spelling is edited.

    `keys.py` owns the tables and `core/config/config.py` is the reader they were written for, and both are
    excluded below for that one reason. Excluding a *directory* or a name pattern would be the
    weaker check and would pass the day a third file copies a chain into itself.
    """
    offenders = {}
    for path in shipped_python_files(*CALLER_ROOTS):
        if path in LAYERING_OWNERS:
            continue
        named = [term for term in LAYERING_VOCABULARY if term in path.read_text(encoding="utf-8")]
        if named:
            offenders[relative(path)] = named
    assert not offenders, \
        f"the layering is spelled outside its owner and its reader: {offenders}"


def test_the_configurations_own_dictionaries_are_private_to_config_py():
    """Nothing reaches past the layered view to the raw categories.

    `config_sources` is what the four files were read into; the view is the layered answer. A caller
    that reads the raw dictionary has found a way to answer without the rule — which is precisely
    how `get('env_static').get(key)` once became the whole of a two-layer read.
    """
    reachers = [relative(path) for path in shipped_python_files(*CALLER_ROOTS)
                if path != CONFIG_PY and "config_sources" in path.read_text(encoding="utf-8")]
    assert not reachers, f"these files read the raw categories: {reachers}"


def test_every_config_get_in_src_addresses_a_known_category():
    """No call site invented a category to get at a layer.

    A new category would mean a new directory under `platform_config/{environment}/`, and the ticket
    is explicit that nothing is renamed and no directory appears. This reads the arguments out of
    the syntax tree so the claim is about the tree and not about a grep that matched a comment.
    """
    offenders = {}
    for path in shipped_python_files(*CALLER_ROOTS):
        if path == CONFIG_PY:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get" or len(node.args) != 1:
                continue
            argument = node.args[0]
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                continue
            if not argument.value.endswith("_static") and not argument.value.endswith("_encrypted"):
                continue
            if argument.value not in CATEGORIES:
                offenders.setdefault(relative(path), []).append(argument.value)
    assert not offenders, f"categories that do not exist are read from: {offenders}"


# --- `_component_value` is still the accessor, through the shared path --------------------


def test_the_component_accessors_are_one_call_apart_from_the_accessor_that_resolves():
    """`component_namespace` and `component_service_selector` still delegate, nothing more."""
    for name in ("component_namespace", "component_service_selector"):
        body = config_method(name)
        assert calls_inside(body) == ["_component_value"], \
            f"{name} grew a second opinion about the value: {calls_inside(body)}"
        assert len(statements_of(body)) == 1, f"{name} is no longer one delegation"


def test_component_value_resolves_through_the_shared_path_and_reads_no_category():
    """The accessor census E found alone in the tree now goes through the one path.

    Two things are asserted, and both are load-bearing: the lookup is `self._resolved(...)` — the
    same function `get()` materializes — and there is no `self.get('env_static')` left in it, which
    is the sentence that used to make the team's layer invisible.
    """
    body = config_method("_component_value")
    names = calls_inside(body)
    assert "_component_value" not in names, "the accessor recursed"
    assert "_resolved" in names, f"no shared resolution call in `_component_value`: {names}"
    assert "get" not in names, f"`_component_value` still reads a category directly: {names}"
    assert not any(isinstance(node, ast.Attribute) and node.attr == "config"
                   for node in ast.walk(body)), \
        "`_component_value` reads the loaded dictionaries directly"


def test_the_resolution_path_is_the_only_walk_of_the_layers():
    """One function walks the layers, and everything else asks it."""
    assert callable(getattr(keys_module, "_declare_in_layers")), "the walk has been renamed"
    assert "_declare_in_layers" in calls_inside(config_method("_resolved")), \
        "Config._resolved does not use the shared walk"
    assert "_declare_in_layers" in keys_module._layered_view.__code__.co_names, \
        "_layered_view answers keys without the shared walk"


# --- the files and the categories did not move ------------------------------------


def test_the_four_categories_and_their_layers_are_what_the_ticket_said_they_are():
    """`L2 > L1 > L0`, spelled once, with nothing new under the configuration root.

    A chain names one file and walks the layers, so an addressed read keeps answering from the file
    it was addressed to and only *whose copy* of it moves. A chain that
    mixed a plain and an encrypted category would let a git-crypt secret be answered out of the
    plaintext file beside it, which is why the last two asserts are about the table's shape rather
    than about its order.
    """
    assert set(keys_module.CATEGORY_LAYERS) == CATEGORIES
    assert config_module.TEAM_SCOPED_CATEGORIES == ("team_static", "team_static_encrypted")
    assert keys_module.CATEGORY_LAYERS["env_static"] == (("L1", "env_static"),)
    assert keys_module.CATEGORY_LAYERS["env_static_encrypted"] == (("L1", "env_static_encrypted"),)
    assert keys_module.CATEGORY_LAYERS["team_static"] == (
        ("L2", "team_static"), ("L1", "env_static"))
    assert keys_module.CATEGORY_LAYERS["team_static_encrypted"] == (
        ("L2", "team_static_encrypted"), ("L1", "env_static_encrypted"))
    # Within a pair the encrypted file comes first, because that is the half that answers a read that
    # was addressed to neither file -- a component's namespace, the class an Ingress names.
    assert keys_module.TEAM_PAIR == (("L2", "team_static_encrypted"), ("L2", "team_static"))
    assert keys_module.ENVIRONMENT_PAIR == (
        ("L1", "env_static_encrypted"), ("L1", "env_static"))

    for category, chain in keys_module.CATEGORY_LAYERS.items():
        kinds = {("_encrypted" in name) for _, name in chain}
        assert len(kinds) == 1, (
            f"the `{category}` chain mixes an encrypted and a plain file, so a secret addressed to "
            f"one could be answered by the other: {chain}")
    assert keys_module.COMPONENT_LAYERS == (
        keys_module.TEAM_PAIR + keys_module.ENVIRONMENT_PAIR)


def test_config_get_still_takes_a_category_alone():
    """The signature 52 callers use did not change shape, which is the whole design claim.

    A `get(category, key)` — or a `get()` that grew a `layer=` — would be the design that edits the
    call sites, and census E counted exactly how much of that is possible.
    """
    signature = inspect.signature(config_module.Config.get)
    assert [parameter.name for parameter in signature.parameters.values()] == ["self", "category"], \
        str(signature)
