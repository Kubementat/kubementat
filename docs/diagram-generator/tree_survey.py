#!/usr/bin/env python3
"""Measure the tree under src/ for the diagram: modules, internal edges, public surface.

Usage: python3 docs/diagram-generator/tree_survey.py [--edges] [--counts] [--json out.json]

The edge rule is the same one tools/import_graph.py uses (an import of a submodule is an edge to
that submodule; third-party imports are dropped), extended to cover `src/cli` as well as
`src/kubementat` so that the diagram can draw the CLI's edges too. Surface counts are AST-derived:
  classes  = class definitions in the file
  methods  = non-underscore method defs inside those classes (properties included)
  funcs    = non-underscore module-level function defs

The tree measured is always the repo this script is checked out in: `ROOT` is resolved from this
file's own location, so the counts do not depend on the directory the script was invoked from.
"""
import ast
import json
import os
import sys

# docs/diagram-generator/tree_survey.py -> <repo>/docs/diagram-generator -> <repo>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO_ROOT, "src")


def is_module(dotted):
    p = os.path.join(ROOT, *dotted.split("."))
    return os.path.exists(p + ".py") or os.path.isdir(p)


def resolve(dotted):
    while dotted and not is_module(dotted):
        parent = dotted.rsplit(".", 1)[0]
        if parent == dotted:
            return None
        dotted = parent
    return dotted or None


def module_of(path):
    rel = os.path.relpath(path, ROOT)
    if rel.endswith(".py"):
        rel = rel[:-3]
    rel = rel.replace(os.sep, ".")
    return rel[: -len(".__init__")] if rel.endswith(".__init__") else rel


def edges_for(tree, me):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                r = resolve(a.name)
                if r and r != me:
                    out.add(r)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = me.rsplit(".", 1)[0]
                for _ in range(node.level - 1):
                    base = base.rsplit(".", 1)[0]
                mod = f"{base}.{node.module}" if node.module else base
            else:
                mod = node.module or ""
            mod = mod.strip(".")
            if not mod:
                continue
            if is_module(mod) and os.path.isdir(os.path.join(ROOT, *mod.split("."))):
                hit = False
                for n in node.names:
                    sub = f"{mod}.{n.name}"
                    if is_module(sub):
                        if sub != me:
                            out.add(sub)
                        hit = True
                if not hit:
                    r = resolve(mod)
                    if r and r != me:
                        out.add(r)
            else:
                r = resolve(mod)
                if r and r != me:
                    out.add(r)
    return out


def surface(source):
    tree = ast.parse(source)
    classes = 0
    methods = 0
    funcs = 0
    class_names = []
    func_names = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef,)):
            classes += 1
            class_names.append(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                    methods += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                funcs += 1
                func_names.append(node.name)
        elif isinstance(node, ast.ClassDef):
            pass
    return classes, methods, funcs, class_names, func_names


def error_classes(source):
    """names of classes defined in errors.py (the whole taxonomy)."""
    tree = ast.parse(source)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            base = [b.id for b in node.bases if isinstance(b, ast.Name)]
            out[node.name] = base
    return out


def survey():
    modules = {}
    for dirpath, dirs, files in os.walk(ROOT):
        # dirs sorted so the walk order — and therefore the diagram's element ids — do not depend on
        # the order the filesystem happened to put the directories in
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            src = open(path, encoding="utf-8").read()
            me = module_of(path)
            c, m, fn, cn, fns = surface(src)
            modules[me] = {
                "path": os.path.relpath(path, ROOT),
                "classes": c,
                "methods": m,
                "funcs": fn,
                "class_names": cn,
                "func_names": fns,
                "loc": len(src.splitlines()),
                "imports": sorted(edges_for(ast.parse(src), me)),
            }
    return modules


def main():
    mods = survey()
    errs = error_classes(open(os.path.join(ROOT, "kubementat", "errors.py"), encoding="utf-8").read())
    out = sys.argv.index("--json") + 1 if "--json" in sys.argv else 0
    if out:
        json.dump({"modules": mods, "errors": errs}, open(sys.argv[out], "w"), indent=1, sort_keys=True)
        print(f"wrote {sys.argv[out]}: {len(mods)} modules, {len(errs)} error classes")
        return
    tot = [0, 0, 0]
    for me in sorted(mods):
        m = mods[me]
        tot = [tot[0] + m["classes"], tot[1] + m["methods"], tot[2] + m["funcs"]]
        print(f"{me:<58} {m['loc']:>5} LOC  {m['classes']:>3} classes  {m['methods']:>3} methods  {m['funcs']:>3} funcs")
    print("TOTAL", tot)
    n = sum(len(m["imports"]) for m in mods.values())
    print("internal edges:", n)
    print("error classes:", len(errs))


if __name__ == "__main__":
    main()
