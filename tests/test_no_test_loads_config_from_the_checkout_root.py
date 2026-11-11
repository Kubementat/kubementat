"""No test may build a `kubementat.config.Config` against this checkout and let it read.

Row 14, measured. `tests/test_tekton_api_version_preflight.py` asked one thing of a `Config` call
that passed no root and left the load on — a module constant assigned at `config.py:153`, three
lines before
`_load_config()` opens its first file. The constructor took no root, so it resolved to *this*
checkout and opened `platform_config/dev/static.json`, which `kmt initialize` writes and git does
not track. The suite was green here for weeks and red on any tree that had never run `initialize`.
That is the shape of this whole bug class: a test that is true because of something on the machine
rather than true of the code.

The fix is one keyword. A guard is what stops it coming back from a different file, so the rule is
derived from the source of every test in `tests/` rather than from a list written down here — a list
is a list that goes stale the day someone adds a file.

The rule: every `Config(...)` call that resolves to `kubementat.config.Config` must say where the
configuration lives, or say not to load it. Concretely it must pass `preload_configs=False`, or
`kubementat_main_dir=` / `platform_config_dir=`, or the enclosing test must have redirected
`KUBEMENTAT_HOME` / `KUBEMENTAT_PLATFORM_CONFIG_DIR` in the environment before the call —
`tests/test_config_path_resolution.py:155` is the honest version of that last case, and it is why
the rule cannot be a bare keyword check.

What this cannot catch, said plainly so nobody reads it as more than it is:

* **the keyword's *value***. `kubementat_main_dir=str(REPO_ROOT)` passes the rule and reads the
  checkout anyway. The guard asks whether a dial was turned, not where it points.
* **`**kwargs` splats** pass without anything being checked, because the call site says a dial is
  being forwarded and the scan cannot see the dictionary.
* **the indirections**: a `Config(...)` built inside `src/` on behalf of a test
  (`Context.build` does exactly this at `cluster.py:724`, with `preload_configs=False`, and the
  service classes receive that object inside the `Context` rather than building one of their own), a
  helper defined in `conftest.py` that defaults the root, or a test that leaves `KUBEMENTAT_HOME`
  unset and lets a command construct one for itself. None of those are a `Config` call in `tests/`,
  so none are seen.
* **the other `Config` in the tree**: there is none — `cli.kmt.Config` was deleted with the
  32 commands that ignored it, so the only `Config` any test can construct is this one,
  and the path-resolution test that *deliberately* lands on the checkout
  (`tests/test_config_path_resolution.py:155`) is exempt for the reason above and not because a
  second class is hiding the call.

"""

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

CONFIG_MODULES = ("kubementat.config",)

# The environment dials that move the root. A call made by a test that turned one of these is not
# reading this checkout, whatever the constructor's arguments look like.
REDIRECT_VARS = frozenset({"KUBEMENTAT_HOME", "KUBEMENTAT_PLATFORM_CONFIG_DIR"})

# The constructor parameters that say "the configuration lives here" or "do not read any of it".
ROOT_OR_SKIP_KEYWORDS = frozenset({"kubementat_main_dir", "platform_config_dir", "preload_configs"})


def _bindings(tree):
    """name -> module, for every `from <module> import <name>` anywhere in the file.

    Later bindings win, which is the file's own scoping for the common case: a test that rebinds
    `Config` inside a function is the one this guard is about, whatever it named it first.
    """
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in CONFIG_MODULES:
            for alias in node.names:
                if alias.name == "Config":
                    # `alias.name` is what the class is called in its own module, so a test that
                    # renames it (`from kubementat.config import Config as Cfg`) is still caught.
                    bound[alias.asname or alias.name] = node.module
    return bound


def _constants(tree):
    """module-level `NAME = "string"` bindings, so a redirect spelled as a constant resolves."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value.value
    return found


def _setenv_names(node, constants):
    """The redirect variables set by `monkeypatch.setenv` anywhere in `node`'s subtree."""
    names = set()
    for child in ast.walk(node):
        if not (isinstance(child, ast.Call) and getattr(child.func, "attr", "") == "setenv"):
            continue
        if not child.args:
            continue
        first = child.args[0]
        value = first.value if isinstance(first, ast.Constant) else constants.get(
            first.id) if isinstance(first, ast.Name) else None
        if value in REDIRECT_VARS:
            names.add(value)
    return names


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _nearest_function(node, parents):
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _violations(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound = _bindings(tree)
    config_names = {name for name, module in bound.items() if module in CONFIG_MODULES}
    if not config_names:
        return []

    constants = _constants(tree)
    parents = _parents(tree)
    problems = []

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in config_names:
            continue

        keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
        splatted = any(keyword.arg is None for keyword in node.keywords)
        if keywords & ROOT_OR_SKIP_KEYWORDS or splatted:
            continue

        enclosing = _nearest_function(node, parents)
        if enclosing is not None and _setenv_names(enclosing, constants):
            continue

        problems.append(f"{path.relative_to(TESTS_DIR.parent)}:{node.lineno}")

    return problems


def test_no_test_loads_config_from_the_checkout_root():
    """Every direct `Config(...)` in `tests/` names its root or says not to load it."""
    offenders = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        offenders.extend(_violations(path))

    assert offenders == [], (
        "these tests build a kubementat.config.Config with no root and the load left on, so the run"
        " opens this checkout's platform_config — written by `kmt initialize`, untracked by git,"
        " and so a test that is green only where someone has run it:"
        f"\n  {'  '.join(offenders)}\n"
        "Pass `preload_configs=False` where the assertion reads a constant, or point"
        " `kubementat_main_dir=` at a `tmp_path` tree the test seeded.")
