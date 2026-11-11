"""One 1:1 check, in one place, called by the command that refuses with it.

A consolidation deleted a duplication that was invisible because both copies were correct.
`Context.build`
owned the collision filter inline, a few lines below `EnvironmentRegistry.collision_check`, which
spelled the same rule a second time — and its own docstring claimed the exclusion "is applied by the
same function the report line reads". That sentence was true of the method and false of the only
caller that mattered: the report line read the inline copy, so nothing in the tree kept the two
answers together.

Nothing here can say "the filter is gone" by running the code, because both spellings behave
identically until someone changes one. So the guards are structural, in the shape the tree already
uses for deletions: sweep what shipped, and assert that the deleted thing stays deleted.

Three claims, one test each:

* **`collision_check` is defined once and called by shipped code.** A method nobody calls is a
  method the next refactor re-implements, which is how this ticket started.
* **`Context.build` is that caller** — and its body no longer contains the filter. Not a comment
  that says so: the calls, read out of the function.
* **`running_cluster_address()` has one call site, inside the check.** It is the one input the two
  copies each computed, so counting its call sites counts the duplication. One is the rule the
  docstring asserts; two would mean the exclusion is applied twice, by two functions, about one
  answer.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLUSTER_PY = REPO_ROOT / "src" / "kubementat" / "cluster.py"

# The roots that could reach the check. `templates` is not in this tuple: it moved inside the
# package, so "src" sweeps it and naming the old root would sweep nothing.
CALLER_ROOTS = ("src", "scripts", "tekton_ci", "examples", "tools")

THE_CLUSTER_MODULE = "src/kubementat/cluster.py"


def relative(path):
    """`path` as it would be named in a commit — the spelling every assertion in this file uses."""
    return str(Path(path).relative_to(REPO_ROOT))


def shipped_python_files(*roots):
    """Every `.py` under *roots*, skipping the directories nobody is reading."""
    found = []
    for root in roots:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" not in path.parts and "site-packages" not in path.parts:
                found.append(path)
    return found


def called_names(node):
    """Every function or method *name* called inside `node`, repeats included.

    Attribute calls and plain calls are folded together on purpose: what is counted is a name a
    reader would find with grep, and `registry.collision_check(...)` is the same fact as
    `running_cluster_address()`.
    """
    return [
        (call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id)
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, (ast.Attribute, ast.Name))
    ]


def call_sites(name):
    """`[(relative/path, line)]` for every call site of `name` in shipped code.

    A count of sites rather than of calls is the honest unit for this file: two copies of one rule
    is a fact about the tree, and how many times either of them runs is a fact about a run.
    """
    sites = []
    for path in shipped_python_files(*CALLER_ROOTS):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            called = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            if called == name:
                sites.append((relative(path), node.lineno))
    return sites


def method(class_name, method_name):
    """The function node for `class_name.method_name` in `cluster.py`, refusing to guess about it.

    A structural guard fails loudest when the structure it asserted moved, so the absence of either
    name is reported as the fact instead of as a `None` several assertions deep.
    """
    for node in ast.walk(ast.parse(CLUSTER_PY.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == method_name:
                    return member
    raise AssertionError(
        f"{class_name}.{method_name} is not in {THE_CLUSTER_MODULE}. If it moved, this guard moves "
        f"with it — but the ticket it locks has to be re-read then, not re-passed.")


def inside(node, sites):
    """The subset of `sites` that fall within `node`, which is how "who probes it" is answered."""
    return [site for site in sites
            if site[0] == THE_CLUSTER_MODULE
            and node.lineno <= site[1] <= (node.end_lineno or node.lineno)]


def test_the_one_collision_check_is_called_and_not_left_behind():
    """`collision_check` is defined once, and shipped code calls it.

    Two facts, because they fail differently: a method that exists but is never called is the seed
    of the next inline `any()`, and a method quietly replaced by a copy elsewhere would turn up here
    as two definitions rather than as a passing test.
    """
    assert len(call_sites("collision_check")) >= 1, \
        "collision_check is called by no shipped code: the duplication comes back"
    definitions = [f"{relative(path)}:{node.lineno}"
                   for path in shipped_python_files(*CALLER_ROOTS)
                   for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                   if isinstance(node, ast.FunctionDef) and node.name == "collision_check"]
    assert definitions == [f"{THE_CLUSTER_MODULE}:{method('EnvironmentRegistry', 'collision_check').lineno}"], \
        f"there is more than one `collision_check` in the tree: {definitions}"


def test_context_build_asks_the_registry_and_does_not_write_the_filter():
    """The caller is `Context.build`, and it asks. Both halves come out of the function body.

    The second half is the actual deletion. A build that called the method *and* kept the inline
    comprehension would still refuse correctly, and would still be two implementations — so the body
    must name `collision_check` and must not name either half of the filter it used to write: the
    running-cluster probe is the exclusion, and `_addresses_the_running_cluster` is the predicate
    that applies it.
    """
    body = called_names(method("Context", "build"))

    assert "collision_check" in body, "Context.build resolves a cluster and never checks it"
    assert "running_cluster_address" not in body
    assert "_addresses_the_running_cluster" not in body


def test_the_running_cluster_is_asked_once_for_a_build_and_not_once_per_answer():
    """One probe of the running-cluster exclusion in all of shipped code, and it sits inside the check.

    A second site — in `build`, or in whatever prints the report line — is exactly the shape this
    guard exists to refuse: two functions applying one exclusion about one answer, which is what the
    docstring promised had stopped happening.
    """
    check = method("EnvironmentRegistry", "collision_check")
    sites = call_sites("running_cluster_address")

    assert len(sites) == 1, f"the running cluster is probed from {sites}, not once"
    assert inside(check, sites), \
        f"the single probe at {sites} is not inside the one collision check, so it is not shared"
