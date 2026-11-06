"""The two cluster-read one-liners stay deleted, and the Python primitives answer for that surface.

`retrieve_node_addresses.sh` was two lines (`kubectl get nodes -o=json | jq '.items[].status.addresses'`)
and `view_cluster_resource_versions.sh` was three (`kubectl api-resources -o wide`). The cleanup
deleted both, and with them the last `.sh` files this repository shipped; `jq` left
`KUBEMENTAT_DEPENDENCIES` for the same reason, because the only thing that ever execed it was the
first of the two.

They were deleted rather than migrated because the two reads they made are already Python-side: the
nodes are `KubernetesUtils.get_nodes()`, whose `status.addresses` arrive as `V1NodeAddress` objects
and never as JSON text, and the resource types the cluster serves are
`KubernetesUtils.get_api_resources()`.

**Why this file is not the parity comparison it came out of.** A parity test compares two live
implementations, and with one side deleted there is nothing left to `compare`. What the old file
said was narrower about these two rows -- the *read* is the same, the *output* is not -- is now
carried by the primitives' own tests: `test_kubernetes_reporting_helpers.py` reads the
api-resources table, and the node readers are exercised through `pvc-usage` and `cluster-capacity`.
The contract the deletion has to keep is the one a comparison could not lock: **there is still one
way to read these two things, and it is in Python.**

**The half that is a gap and not a green check, written down so nobody reads the guard as a
claim.** `get_nodes()` has callers in `src/`; `get_api_resources()` does not, because
`kmt api-resources` is a command that was never written. The deleted script showed that
table to a human; the primitive returns it, and between the two sits a command that does not exist.
This file asserts that the read is in the library and exercised there -- it does not pretend the
operator-facing route survived.

The shape is the one the other stays-deleted guards use (see
`test_no_task_takes_a_script_path_and_the_pair_stays_deleted.py` and
`test_the_run_pipeline_script_is_deleted_and_no_manifest_sources_it.py`): assert where a re-added
file would land, measure the tree instead of remembering it, and keep the counter-claim assertable
so this cannot be read as "delete scripts".
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Addressed by path, because a path is what a caller would have to carry to run one of them.
SCRIPTS = (
    "scripts/utilities/kubernetes/retrieve_node_addresses.sh",
    "scripts/utilities/kubernetes/view_cluster_resource_versions.sh",
)

# Roots a program or a manifest could be read from. `tests/` is out of it on purpose: this file and
# `test_the_declared_dependencies_are_what_the_tree_needs.py` are where the deletion is recorded,
# and a guard that convicted its own prose trains the next reader to delete the record.
CALLER_ROOTS = ("src", "scripts", "tekton_ci", "examples", "tools")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.


def _text_files(root):
    for path in (REPO_ROOT / root).rglob("*"):
        # A build artifact is not a program: `src/*.egg-info/PKG-INFO` is generated from the README
        # and quotes it, so counting it would convict a file nobody runs. Same exclusion the
        # dependency guard makes for the same walk.
        if not path.is_file() or "__pycache__" in path.parts or "egg-info" in path.parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _call_sites(primitive):
    """`src/**/*.py` lines where something calls ``<something>.<primitive>()``, off the AST.

    Read off the call node and not off the text, so a comment that says `get_nodes()` cannot be
    counted as a reader.
    """
    sites = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        if "egg-info" in path.parts or "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == primitive:
                sites.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return sites


def test_neither_script_is_in_the_tree():
    """The deletion itself, asserted where a re-added file would land.

    Either file coming back re-opens the dependency the list just closed: `jq` is declared only for
    as long as a shipped script runs it, so a returned `retrieve_node_addresses.sh` is a returned
    `jq` requirement, and this is the assertion that says so first.
    """
    returned = [script for script in SCRIPTS if (REPO_ROOT / script).exists()]
    assert not returned, f"{' and '.join(returned)} is back; the primitives answer for that " \
                         "surface -- KubernetesUtils.get_nodes() and KubernetesUtils.get_api_resources()"


def test_the_scripts_tree_ships_no_scripts():
    """The tree measured rather than remembered, so the number in the docs cannot drift silently.

    Row 21 closed the last `.sh` files under `scripts/`, which is what this asserts: the queue is
    empty. A `.sh` anywhere under `scripts/` is something to explain, and the two names this file
    guards are exactly what used to be it.
    """
    shipped = sorted(str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "scripts").rglob("*.sh"))
    assert shipped == [], f"scripts/ ships a script again: {shipped}"


def test_a_path_to_either_script_is_only_prose_while_the_file_is_gone():
    """The rule that makes the deletion mean something, stated so that only a caller fails it.

    The checkout roots are swept for the two paths. A mention is prose while the file it names is
    absent -- that is what a comment explaining what a command replaced is for -- and turns into a
    caller the moment the file returns. So a return trips this guard and the one above together,
    and nothing in between has to be trusted.
    """
    carried = []
    for root in CALLER_ROOTS:
        for path, text in _text_files(root):
            if path == Path(__file__):
                continue
            for script in SCRIPTS:
                if script in text and (REPO_ROOT / script).is_file():
                    carried.append(f"{path.relative_to(REPO_ROOT)}: {script}")

    assert carried == [], "a script that is in the tree is carried by something that could run it: " \
                          ", ".join(sorted(set(carried)))


def test_the_primitives_that_answer_for_the_surface_are_still_in_the_library():
    """The counter-claim, assertable: what went away is a way to run bash, not a capability.

    Both reads are still methods on `KubernetesUtils`, and `get_nodes()` still has callers in `src/`
    -- which is what a migration that deleted the scripts and dropped the reads with them would
    break: the commands would keep existing and report nothing about a cluster that is there.
    """
    from kubementat.infrastructure.kubernetes import KubernetesUtils

    for primitive in ("get_nodes", "get_api_resources"):
        assert callable(getattr(KubernetesUtils, primitive, None)), \
            f"KubernetesUtils.{primitive}() is not a callable any more"

    assert _call_sites("get_nodes"), \
        "nothing in src/ calls get_nodes() any more, so the node read went out with the script"
