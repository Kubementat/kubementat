#!/usr/bin/env python3
"""Static import walk over src/kubementat: edges, cycles, layer violations.

Usage: python3 tools/import_graph.py [root-dir]
Prints EDGES, CYCLES, LAYER VIOLATIONS. Exit 1 when cycles or violations exist.
Function-level (deferred) imports count as edges, which is what makes cycles
visible: a cycle that exists only inside a function body is still a cycle.

The tree checked is resolved from this script's own location, so it reads the repository it is
checked out in and not the working directory it was invoked from. A root that does not exist is an
error and not an empty walk: a checker that silently finds nothing prints "none" twice and exits 0,
which is the failure this script exists to make impossible.
"""
import ast
import os
import sys
from collections import defaultdict

# tools/import_graph.py -> <repo>/tools -> <repo>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `src` names the repository's own src/ wherever this is run from; an absolute path is honoured as given.
ROOT = sys.argv[1] if len(sys.argv) > 1 else "src"
if not os.path.isabs(ROOT):
    ROOT = os.path.join(REPO_ROOT, ROOT)
ROOT = os.path.abspath(ROOT)
PKG = "kubementat"
# Bottom of the stack first: `core` is pure data and filesystem, `infrastructure` is what touches a
# cluster or a subprocess, `automations` is the product's own logic. Nothing below may reach up, so a
# legal import edge always runs from a later name in this list to an earlier one.
LAYERS = ["core", "infrastructure", "automations"]

# Resolve a dotted name to the longest prefix that is a real module on disk.
def module_exists(dotted):
    p = os.path.join(ROOT, *dotted.split("."))
    return os.path.exists(p + ".py") or os.path.isdir(p)


def resolve(dotted):
    while dotted and not module_exists(dotted):
        parent = dotted.rsplit(".", 1)[0]
        if parent == dotted:
            return None
        dotted = parent
    return dotted or None


def layer(mod):
    parts = mod.split(".")
    if parts[0] != PKG or len(parts) < 2:
        return "root" if parts[0] == PKG else "other"
    return parts[1] if parts[1] in LAYERS else "root"


def module_of(path):
    rel = os.path.relpath(path, ROOT)
    rel = rel[:-3] if rel.endswith(".py") else rel
    rel = rel.replace(os.sep, ".")
    if rel.endswith(".__init__"):
        rel = rel[: -len(".__init__")]
    return rel if rel != PKG + "." + PKG else rel


def collect():
    graph = defaultdict(set)
    root_dir = os.path.join(ROOT, *PKG.split("."))
    for dirpath, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            me = module_of(path)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        _edge(graph, me, a.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = me.rsplit(".", 1)[0] if not node.module else me
                        base = me if node.module else me.rsplit(".", 1)[0]
                        for _ in range(node.level - 1):
                            base = base.rsplit(".", 1)[0]
                        mod = f"{base}.{node.module}" if node.module else base
                        mod = mod.strip(".")
                    else:
                        mod = node.module or ""
                    if not mod:
                        continue
                    if module_exists(mod) and os.path.isdir(os.path.join(ROOT, *mod.split("."))):
                        # `from pkg import submodule` — the names are modules, not attributes
                        hit = False
                        for n in node.names:
                            sub = f"{mod}.{n.name}"
                            if module_exists(sub):
                                if sub != me:
                                    graph[me].add(sub)
                                hit = True
                        if not hit:
                            parent = resolve(mod)
                            if parent and parent != me:
                                graph[me].add(parent)
                    else:
                        parent = resolve(mod)
                        if parent and parent != me:
                            graph[me].add(parent)
    return graph


def _edge(graph, me, dotted):
    r = resolve(dotted)
    if r and r != me:
        graph[me].add(r)


def find_cycles(graph):
    seen, stack, out = set(), [], []
    def dfs(n):
        seen.add(n)
        stack.append(n)
        for m in sorted(graph.get(n, ())):
            if m in stack:
                out.append(stack[stack.index(m):] + [m])
            elif m not in seen:
                dfs(m)
        stack.pop()
    for n in sorted(graph):
        if n not in seen:
            dfs(n)
    return out


def main():
    if not os.path.isdir(os.path.join(ROOT, *PKG.split("."))):
        print(f"import_graph: no {PKG}/ under {ROOT} — refusing to report 'none' for a tree it did not read", file=sys.stderr)
        return 2
    g = collect()
    print("== EDGES ==")
    for k in sorted(g):
        for v in sorted(g[k]):
            print(f"{k} -> {v}")
    print("\n== CYCLES ==")
    cyc = find_cycles(g)
    if not cyc:
        print("none")
    for c in cyc:
        print(" -> ".join(c))
    print("\n== LAYER VIOLATIONS ==")
    bad = []
    rank = {L: i for i, L in enumerate(LAYERS)}
    for a, deps in g.items():
        la = layer(a)
        if la not in rank:
            continue
        for b in sorted(deps):
            lb = layer(b)
            if lb not in rank:
                continue
            if rank[lb] > rank[la]:
                bad.append(f"{a} ({la}) -> {b} ({lb})")
            elif rank[lb] == rank[la] and a != b and a.split(".")[1] != b.split(".")[1]:
                bad.append(f"{a} ({la}) -> {b} ({lb})  [same-layer cross-slice]")
    print("\n".join(sorted(set(bad))) if bad else "none")
    return 1 if (cyc or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
