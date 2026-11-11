"""Nothing ships that no one can reach: no module nobody imports, no script nobody runs.

A migration leaves two kinds of corpse behind, and both were in this tree until they were
deleted. They are locked here so the next merge does not put them back.

* **A dead module.** The zero-byte `utils` module sitting in `src/kubementat` had no
  definitions at all. Nothing imported it and nothing exported it; it survived because deleting
  it is not glamorous. Dead code is worse than absent code: it reads as if the helpers it is
  named after live somewhere.
* **A half-migrated entry point.** The Python `create_team_account` stub under
  `scripts/automation/tekton` said so in its own header — "UNFINISHED AND NOT WORKING YET" — and
  called
  `KubernetesUtils.create_service_account(namespace=..., sa_name=...)`, which takes a third
  `secrets` argument now that the library refuses to build a ServiceAccount without one. Its
  first real call would have been a `TypeError`, on a script no `kmt` command invokes. The
  bash `create_team_account.sh` was the implementation until the flow moved to Python.

Deleting both is housekeeping, not a feature. The team-account flow moved to Python, so the last
tests here say the opposite of what they used to: the scripts must stay
gone, and the Python flow must exist.
"""

import ast
from pathlib import Path

from kubementat.automation import Automation

REPO_ROOT = Path(__file__).resolve().parent.parent


def shipped_python_files(*roots: str) -> list:
    """Every `.py` under *roots*, skipping the directories nobody is reading."""
    found = []
    for root in roots:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" not in path.parts and "site-packages" not in path.parts:
                found.append(path)
    return found


def package_parts(path: Path) -> list:
    """The importable package of *path*, relative to the package root.

    `src/kubementat/shell.py` sits in package `kubementat`; `src/cli/commands/helm.py` sits in
    `cli.commands`. A relative import has to be resolved against that, or a file that is only
    ever imported as `from . import shell` looks dead.
    """
    try:
        parts = list(path.relative_to(REPO_ROOT / "src").parts)
    except ValueError:
        parts = list(path.relative_to(REPO_ROOT).parts)
    return parts[:-1]  # drop the module file itself


def import_edges(path: Path) -> list:
    """`(imported module, imported name)` pairs the import statements in *path* name."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = package_parts(path)
    edges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.extend((alias.name, "") for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            cut = len(package) - (node.level - 1)
            prefix = ".".join(package[:cut]) if cut > 0 else ""
            module = node.module or ""
            target = ".".join(part for part in (prefix, module) if part)
        else:
            target = node.module or ""
        edges.extend((target, alias.name) for alias in node.names)
        if target:
            edges.append((target, ""))
    return edges


def test_no_module_under_src_is_imported_by_anything_or_nobody():
    """Every shipped module is named by an import in shipped code, in the suite, or in the entry point.

    The package root is `src/`, so each module's import name is the package plus its file name.
    A name that no import statement in the tree uses is dead by construction, not by opinion.
    """
    sources = shipped_python_files("src", "tests", "tools")
    edges_by_file = {path: import_edges(path) for path in sources}
    packaging = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    dead = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        parts = path.relative_to(REPO_ROOT / "src").parts
        if parts[-1] == "__init__.py":
            continue  # a package marker is reached by importing its parent, by definition
        module = ".".join((*parts[:-1], parts[-1][:-3]))
        parent = ".".join(parts[:-1])
        referenced = any(
            imported == module
            or (imported == parent and name == parts[-1][:-3])
            for edges in edges_by_file.values()
            for imported, name in edges
        )
        if not referenced and module not in packaging:
            dead.append(module)

    assert dead == [], "imported by no one: delete it or wire it up"


def test_no_python_script_remains_under_the_scripts_tree():
    """`scripts/` is bash. A `.py` there is a migration leftover, never a second entry point.

    That is the shape the stub took: a Python script living beside the shell scripts, so it
    looked like it was called by them. `kmt` reaches scripts through `run_kubementat_script`,
    which runs `.sh` files.
    """
    strays = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "scripts").rglob("*.py")
    )

    assert strays == []


def test_no_caller_asks_for_a_service_account_without_its_secrets():
    """The branch that used to be broken: a `create_service_account` call missing `secrets`.

    The library signature is `(namespace, sa_name, secrets)`. The stub handed over two arguments
    and raised `TypeError` before writing anything, which is why it could not run at all. The
    caller that does exist hands over all three, so the rule is asserted rather than remembered.
    """
    offenders = []
    for path in shipped_python_files("src", "scripts", "tools"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", "")
            )
            if called != "create_service_account":
                continue
            hands_over_secrets = any(
                keyword.arg == "secrets" for keyword in node.keywords
            )
            if not (hands_over_secrets or len(node.args) >= 3):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == []


def test_the_team_account_bash_scripts_are_gone():
    """The move to Python deleted the shell implementation of team accounts, and all of its copies.

    `scripts/user_management/user_management_helpers.sh` was one implementation, and
    `scripts/automation/tekton/create_team_account.sh` grew a second `check_cluster_and_access`
    beside it - so the `--yes` escape hatch `390bd61` added to the helper never reached the
    script the Tekton path ran. What is left is one Python implementation, reachable as a
    command; a `.sh` reappearing here would be a second way to do this, which is how the bug
    started.
    """
    strays = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "scripts").rglob("*.sh")
        if path.name.startswith(("create_team_account", "delete_team_account"))
        or path.name == "user_management_helpers.sh"
    )
    python_strays = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "scripts").rglob("create_team_account*.py")
    )

    assert strays == [], f"team-account scripts still shipping: {strays}"
    assert python_strays == [], f"a script that is not the implementation: {python_strays}"


def test_the_pvc_usage_script_is_gone_and_nothing_reaches_for_it():
    """`kmt pvc-usage` replaced the script that started a proxy to ask, and the script is deleted.

    `list_pvc_usage.sh` was the fourth of the `scripts/utilities/kubernetes/` reads and the last of
    them that anyone reached by standing in a directory and typing a filename: it backgrounded
    `kubectl proxy`, `sleep 2`'d, curled `127.0.0.1:8001/api/v1/nodes/<node>/proxy/stats/summary`
    per node, and `pkill -9`'d the proxy -- whose exit status the script then reported, whether or
    not a cluster had answered. The two halves of the deletion that are worth locking are locked
    here: the file is not in the tree, and no program that could run it names it.

    The suite is deliberately excluded from the second half. `tests/` is where the record lives --
    the parity comparison runs the blob out of `git show` and cannot do that without naming the
    path -- and a guard that counted those mentions would only teach the next reader to delete the
    comparison that makes the deletion mean something.
    """
    strays = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "scripts").rglob("*.sh")
        if "pvc_usage" in path.name or "pvc-usage" in path.name
    )

    named_by = []
    # The full path, not the filename: this is the string a caller would have to carry to run it,
    # and a bare `list_pvc_usage.sh` in a comment naming what a command replaced is prose.
    deleted_script = "scripts/utilities/kubernetes/list_pvc_usage.sh"
    for root in ("src", "scripts", "tekton_ci", "examples", "tools"):  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.
        for path in (REPO_ROOT / root).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if deleted_script in text:
                named_by.append(str(path.relative_to(REPO_ROOT)))

    assert strays == [], f"the script that started a proxy is still shipping: {strays}"
    assert named_by == [], (
        f"something carries the path of the deleted script and could still run it: {named_by}; "
        f"the command is kmt pvc-usage. A bare `list_pvc_usage.sh` in a comment naming what a "
        f"command replaced is prose, not a caller, which is why this looks for the path and not "
        f"the word")


def test_the_ingress_manifest_script_is_gone_and_the_command_answers_for_it():
    """The generator replaced the script that printed the manifest as text, and that script is deleted.

    `create_ingress_yml.sh` was the last script in the tree that answered a configuration question
    by `jq`-ing `platform_config/<environment>/static.json` out of a path built relative to its own
    location -- the same failure mode one directory further down. It made no cluster call, so an
    operation comparison cannot catch its deletion; what is locked here is that the file is gone,
    that no shipped program carries a path that could still run it, and that the command which
    replaced it is the only way to reach the thing it wrote.
    """
    strays = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "scripts").rglob("*.sh")
        if "create_ingress" in path.name
    )

    named_by = []
    # The full path, not the filename: this is the string a caller would have to carry to run it,
    # and the two places it still turns up legitimately are this file and the parity comparison.
    deleted_script = "scripts/utilities/kubernetes/create_ingress_yml.sh"
    for root in ("src", "scripts", "tekton_ci", "examples", "tools"):  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.
        for path in (REPO_ROOT / root).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if deleted_script in text:
                named_by.append(str(path.relative_to(REPO_ROOT)))

    assert strays == [], f"the manifest printer is still shipping: {strays}"
    assert named_by == [], (
        f"something carries the path of the deleted script and could still run it: {named_by}; "
        f"the command is kmt create-ingress, and the class it writes is answered by "
        f"src/kubementat/ingress_generator.py")


def test_creating_a_team_account_is_a_python_feature_now():
    """All three commands exist on `Automation`, which is what the deletion was waiting for.

    Before the Python flow existed this read `assert not hasattr(Automation, "create_team_account")`, and that
    was the honest assertion at the time: the Python version was not written, so a reader had
    to be told not to mistake a deleted corpse for a finished feature. It is finished now.
    """
    for name in ("create_team_account", "create_team_account_read_only", "delete_team_account"):
        assert callable(getattr(Automation, name, None)), f"Automation.{name} does not exist"


def test_there_is_only_one_confirmation_for_the_whole_flow():
    """Two implementations of the same prompt is how this flow got its bug.

    `check_cluster_and_access` existed twice, and the fix to one of them did not reach the
    other. So: exactly one definition in `src/`, and every call comes from the flow module,
    which is the only place allowed to answer it.
    """
    definitions, callers = [], []
    for path in shipped_python_files("src"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "check_cluster_and_access":
                definitions.append(str(path.relative_to(REPO_ROOT)))
            if isinstance(node, ast.Call):
                target = node.func
                if getattr(target, "attr", None) == "check_cluster_and_access" or \
                        getattr(target, "id", None) == "check_cluster_and_access":
                    callers.append(str(path.relative_to(REPO_ROOT)))

    assert definitions == ["src/kubementat/user_management.py"], definitions
    assert set(callers) == {"src/kubementat/automation.py"}, callers

