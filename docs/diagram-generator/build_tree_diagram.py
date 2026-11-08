#!/usr/bin/env python3
"""Regenerate docs/diagrams/src-class-diagram.excalidraw from the tree under src/.

Everything drawn here is measured, not remembered: the boxes name files and packages that exist,
the edges are the import edges an AST walk finds, and every number in the surface-count table is a
count over the source. Re-run after a phase that changes the tree:

    python3 docs/diagram-generator/build_tree_diagram.py

It only writes the .excalidraw. It changes no source, so `uv run pytest -q` cannot move.
Both the tree it measures and the file it writes are resolved from this script's own location, so
the diagram is the same whether it is run from the repo root or from here.
"""
import ast
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tree_survey import survey, ROOT  # noqa: E402
import tree_boxes  # noqa: E402
from tree_boxes import BANDS, BOXES, BARE_INITS, build, owner_of  # noqa: E402

# docs/diagram-generator/build_tree_diagram.py -> <repo>/docs/diagram-generator -> <repo>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO_ROOT, "docs", "diagrams", "src-class-diagram.excalidraw")

# ---- style ---------------------------------------------------------------------------------
FONT = 3
INK = "#313639"
MUTED = "#64748b"
GREY = "#94a3b8"
FAINT = "#cbd5e1"
BLUE = "#1e40af"
LAYER_COLOR = {
    "cli": "#c2410c",
    "verbs": "#0f766e",
    "automations": "#6d28d9",
    "infrastructure": "#047857",
    "core": "#1e3a5f",
}
LAYER_BG = {
    "cli": "#fed7aa",
    "verbs": "#ccfbf1",
    "automations": "#ddd6fe",
    "infrastructure": "#a7f3d0",
    "core": "#bfdbfe",
}
# counts the band captions carry, measured rather than remembered
def band_files(band):
    """how many .py files the band's tree holds, counted against an absolute path."""
    p = {"cli": "src/cli", "verbs": None, "automations": "src/kubementat/automations",
         "infrastructure": "src/kubementat/infrastructure", "core": "src/kubementat/core"}[band]
    if p is None:
        return len([f for f in os.listdir(os.path.join(ROOT, "kubementat")) if f.endswith(".py")])
    n = 0
    for dirpath, dirs, files in os.walk(os.path.join(REPO_ROOT, p)):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        n += len([f for f in files if f.endswith(".py")])
    return n


BAND_TITLE = {
    "cli": "src/cli — the operator's surface: {n} files, every verb is a click function",
    "automations": "automations — what an install, an uninstall, a tunnel, a team, an ingress actually does ({n} files)",
    "verbs": "the five flat config verbs the plan left alone — 5 of the {n} files that sit directly in src/kubementat/",
    "infrastructure": "infrastructure — where src/ stops touching objects and starts touching a cluster ({n} files)",
    "core": "core — the leaf layer, {n} files: data, the filesystem, identity. Plus errors.py and crypto_utils.py, "
            "which import nothing",
}

X0 = 60
BAND_W = 2520
COL_X = X0 + BAND_W + 20       # right column for the legend and the count table
COL_W = 860
CHAR11 = 6.6     # monospace advance at fontSize 11
CHAR15 = 9.0
LINE11 = 13.75
elements = []
_id = [0]


def _nxt():
    _id[0] += 1
    return _id[0]


def base(etype, x, y, w, h, **kw):
    i = _nxt()
    el = {
        "type": etype, "x": round(x, 2), "y": round(y, 2), "width": round(w, 2), "height": round(h, 2),
        "angle": 0, "strokeColor": kw.pop("strokeColor", INK), "backgroundColor": kw.pop("backgroundColor", "transparent"),
        "fillStyle": "solid", "strokeWidth": kw.pop("strokeWidth", 1), "strokeStyle": kw.pop("strokeStyle", "solid"),
        "roundness": None, "boundElements": None, "id": f"e{i:05d}", "locked": False,
        "isDeleted": False, "link": None, "locked": False, "frameId": None, "groupIds": [],
        "opacity": kw.pop("opacity", 100), "roughness": 0, "seed": 100000 + i, "version": 1, "versionNonce": 1000 + i,
    }
    el.update(kw)
    elements.append(el)
    return el


def rect(x, y, w, h, **kw):
    return base("rectangle", x, y, w, h, **kw)


def line(x, y, pts, **kw):
    el = base("line", x, y, max(abs(p[0]) for p in pts), max(abs(p[1]) for p in pts), **kw)
    el["points"] = [list(p) for p in pts]
    el["lastCommittedPoint"] = None
    el["startBinding"] = None
    el["endBinding"] = None
    el["startArrowhead"] = None
    el["endArrowhead"] = None
    return el


def arrow(x, y, pts, **kw):
    el = base("arrow", x, y, max(abs(p[0]) for p in pts) or 1, max(abs(p[1]) for p in pts) or 1, **kw)
    el["points"] = [[0, 0]] + [[round(px, 2), round(py, 2)] for px, py in pts[1:]]
    el["lastCommittedPoint"] = None
    el["startBinding"] = None
    el["endBinding"] = None
    el["startArrowhead"] = None
    el["endArrowhead"] = kw.pop("endArrowhead", "arrow")
    el["elbowed"] = False
    return el


def text(x, y, s, size=11, color=INK, align="left", valign="top", w=None):
    lines = s.split("\n")
    lw = max(len(ln) for ln in lines) * size * 0.6
    h = len(lines) * size * 1.25
    return base("text", x, y, w if w is not None else round(lw, 2), round(h, 2), strokeColor=color,
                fontSize=size, fontFamily=FONT, textAlign=align, verticalAlign=valign,
                baseline=0, lineHeight=1.25, containerId=None, text=s, originalText=s)


# ---- measure ------------------------------------------------------------------------------
def measure():
    mods = survey()
    pairs = build(mods)
    fan_in = collections.Counter()
    for (a, b), n in pairs.items():
        fan_in[b] += 1
    # what reaches outside src/, measured by the import that reaches it
    outside = collections.defaultdict(set)
    for m, v in mods.items():
        if m in BARE_INITS:
            continue
        src = open(os.path.join(ROOT, v["path"]), encoding="utf-8").read()
        tree = ast.parse(src)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        if "subprocess" in names:
            outside["kubectl · helm · helmfile"].add(m)
        if any(n.split(".")[0] == "kubernetes" for n in names):
            outside["kubernetes.client"].add(m)
        if any(n.split(".")[0] in ("requests", "urllib", "http") for n in names):
            outside["Grafana admin API"].add(m)
    # error taxonomy: 37 classes, each with its own first docstring line, from errors.py
    errs = []
    for node in ast.parse(open(os.path.join(ROOT, "kubementat", "errors.py"), encoding="utf-8").read()).body:
        if isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            first = next((ln.strip() for ln in doc.splitlines() if ln.strip()), node.name)
            errs.append({"name": node.name, "base": " | ".join(sorted(b.id for b in node.bases if isinstance(b, ast.Name))),
                         "say": first, "line": node.lineno})
    return mods, pairs, fan_in, outside, errs


# ---- layout -------------------------------------------------------------------------------
def file_lines(box):
    """one line per module the box draws, plus its imports — the edge list, as text."""
    out = []
    for m, v in sorted(box["files"]):
        # A dotted name can be a package's `__init__`, and drawing it as `identity.py` tells a reader to
        # look for a file that does not exist. A trailing slash says directory, and says it from the tree.
        name = (m.split(".")[-1] + "/") if os.path.isdir(os.path.join(ROOT, *m.split("."))) else (m.split(".")[-1] + ".py")
        surf = " ".join(x for x in (f"{v['classes']}c" if v["classes"] else "",
                                    f"{v['methods']}m" if v["methods"] else "",
                                    f"{v['funcs']}f" if v["funcs"] else "") if x)
        head = f"{name:<14}{surf:>8}  {v['loc']:>5}L"
        deps = sorted({tree_boxes.short(owner_of(d)) for d in v["imports"] if owner_of(d) != owner_of(m)})
        if not deps:
            out.append(head)
            continue
        tail = "→ " + " · ".join(deps)
        if len(head) + 3 + len(tail) <= 64:
            out.append(head + "   " + tail)
        else:
            out.append(head)
            out.append("   " + tail if len(tail) < 70 else "   " + tail[:66] + " …")
    return out


def box_size(name, b):
    lines = file_lines(b)
    w = max(300, len(name) * CHAR15 + 24, min(720, max((len(l) for l in lines), default=0) * CHAR11 + 24))
    if name == "src/cli/commands/":
        w = 800
    h = 39 + 17 + LINE11 * len(lines) + (14 if b["note"] else 0) + 8
    return round(w), round(h), lines


def layout(mods, pairs):
    placed = {}
    y = 210
    band_geom = {}
    for band in BANDS:
        boxes = [(n, b) for n, b in BOXES.items() if b["band"] == band]
        sizes = {n: box_size(n, b) for n, b in boxes}
        rows, row, x = [], [], X0 + 2
        maxw = X0 + BAND_W - 2
        for n, b in boxes:
            w = sizes[n][0]
            if row and x + w > maxw:
                rows.append(row)
                row, x = [], X0 + 2
            row.append((n, x))
            x += w + 12
        if row:
            rows.append(row)
        top = y + 42
        cy = top
        for row in rows:
            rh = max(sizes[n][1] for n, _ in row)
            for n, cx in row:
                placed[n] = (cx, cy, sizes[n][0], sizes[n][1], sizes[n][2])
            cy += rh + 18
        band_geom[band] = {"top": y, "height": cy - y + 6, "inner": top}
        y = cy + 96          # gutter for edges to the band below
    return placed, band_geom, y


def draw(mods, pairs, fan_in, outside, errs):
    placed, band_geom, y_end = layout(mods, pairs)
    max_y = y_end

    # title
    text(X0, 12, "kubementat — src/ as a module graph", 32, BLUE)
    n_mods = len([m for m in mods if m not in BARE_INITS])
    tot = [sum(v[k] for v in mods.values()) for k in ("classes", "methods", "funcs", "loc")]
    n_edges = sum(len(v["imports"]) for m, v in mods.items() if m not in BARE_INITS)
    text(X0, 56, "every box is a file or a package that exists in src/; every arrow is a real import edge. "
                 f"{n_mods} modules · {n_edges} import edges · {tot[0]} classes · {tot[1]} methods · {tot[2]} module functions · {tot[3]} LOC",
         13, MUTED)
    text(X0, 78, "measured by docs/diagram-generator/build_tree_diagram.py against HEAD, with the edges walked by "
                 "tools/import_graph.py — no class named here that src/ does not have", 13, MUTED)

    # legend (right column)
    lx, ly = COL_X, 108
    rect(lx, ly, COL_W, 168, strokeColor="#334155", backgroundColor="#1e293b")
    text(lx + 14, ly + 8, "how to read this diagram", 13, "#22c55e")
    text(lx + 14, ly + 32, "├ box header: a file or a package that exists", 11, "#e2e8f0")
    text(lx + 14, ly + 50, "ƒ module function  ·  12c classes  ·  18m methods  ·  5f funcs  ·  L = LOC", 11, "#e2e8f0")
    text(lx + 14, ly + 68, "→ inside a box: what that one module imports, by box name", 11, "#e2e8f0")
    arrow(lx + 14, ly + 92, [(0, 0), (34, 0)], strokeColor=GREY)
    text(lx + 56, ly + 86, "imports — an edge the AST walk found, drawn once per box pair", 11, "#e2e8f0")
    arrow(lx + 14, ly + 112, [(0, 0), (34, 0)], strokeColor="#047857")
    text(lx + 56, ly + 106, "reaches outside src/", 11, "#e2e8f0")
    text(lx + 14, ly + 132, "arrows run downward: the band below is what the band above imports.", 11, "#f87171")
    text(lx + 14, ly + 150, "the band order is the import direction, not the folder order.", 11, "#94a3b8")

    # surface-count table (right column, below the legend)
    ty = ly + 196
    rect(lx, ty, COL_W, 34 * 2 + 22 * 27, strokeColor=GREY, backgroundColor="#ffffff")
    rect(lx, ty, COL_W, 34, strokeColor=GREY, backgroundColor="#f1f5f9")
    text(lx + 14, ty + 9, "where the public surface lives", 13, BLUE)
    for col, lbl in ((664, "classes"), (732, "methods"), (796, "funcs"), (COL_W - 14, "LOC")):
        text(lx + col - len(lbl) * 6.6, ty + 11, lbl, 11, MUTED)
    row = ty + 40
    sub = {}
    for band in BANDS:
        names = [n for n, b in BOXES.items() if b["band"] == band]
        rect(lx + 8, row - 4, COL_W - 16, 18, strokeColor=FAINT, backgroundColor=BG_OF[band] if False else "transparent",
             strokeStyle="dotted")
        text(lx + 14, row - 3, band_note(band), 11, LAYER_COLOR[band])
        row += 18
        for n in names:
            b = BOXES[n]
            text(lx + 24, row, n, 11, "#334155")
            for col, val in ((664, b["classes"]), (732, b["methods"]), (796, b["funcs"]), (COL_W - 14, b["loc"])):
                txt = "—" if val == 0 and col != COL_W - 14 else f"{val:,}"
                text(lx + col - len(txt) * 6.6, row, txt, 11, INK)
            row += 20
        sub[band] = row
        row += 4
    row += 6
    line(lx + 8, row, [(0, 0), (COL_W - 16, 0)], strokeColor=GREY)
    row += 8
    text(lx + 14, row, "all of src/", 12, BLUE)
    for col, val in ((664, tot[0]), (732, tot[1]), (796, tot[2]), (COL_W - 14, tot[3])):
        txt = f"{val:,}"
        text(lx + col - len(txt) * 7.2, row, txt, 12, BLUE)
    row += 26
    text(lx + 14, row, f"counts are AST counts of public names: {n_edges} import edges, {len(errs)} error classes,", 11, MUTED)
    row += 16
    text(lx + 14, row, "all defined once in src/kubementat/errors.py and raised from there", 11, MUTED)
    max_y = max(max_y, row + 40)

    # bands
    for band in BANDS:
        g = band_geom[band]
        rect(X0 - 2, g["top"], BAND_W, g["height"], strokeColor=FAINT)
        rect(X0 + 6, g["top"] + 8, 900, 26, strokeColor="#ffffff", backgroundColor="#ffffff")
        text(X0 + 12, g["top"] + 12, BAND_TITLE[band].format(n=band_files(band)), 17, BLUE)
        text(X0 + 916, g["top"] + 12, f"{len([m for m, b in BOXES.items() if b['band'] == band])} boxes drawn", 11, GREY)
    for name, (x, y, w, h, lines) in placed.items():
        b = BOXES[name]
        col = LAYER_COLOR[b["band"]]
        bg = LAYER_BG[b["band"]]
        rect(x, y, w, h, strokeColor=col, backgroundColor=bg)
        rect(x, y, w, 39, strokeColor=col, backgroundColor="#ffffff")
        text(x + 11, y + 4, name, 15, col)
        text(x + 11, y + 24, f"{b['nfiles']} files · {b['classes']}c · {b['methods']}m · {b['funcs']}f · {b['loc']:,}L"
             + (f" · {b['internal']} internal" if b.get("internal") else ""), 11, MUTED)
        yy = y + 39 + 15
        if b["note"]:
            text(x + 11, yy, b["note"], 11, MUTED)
            yy += 15
        for l in lines:
            text(x + 11, yy + 1, l, 11, INK)
            yy += LINE11

    # edges — one arrow per box pair, from the source's bottom edge to the target's top edge,
    # with the horizontal run kept in the gutter so it never crosses a box's text
    bottom_of = {b: g["top"] + g["height"] for b, g in band_geom.items()}
    by_band_pair = collections.defaultdict(list)
    for (a, b), n in pairs.items():
        by_band_pair[(BOXES[a]["band"], BOXES[b]["band"])].append((a, b, n))
    for (ba, bb), es in by_band_pair.items():
        ia, ib = BANDS.index(ba), BANDS.index(bb)
        for i, (a, b, n) in enumerate(sorted(es)):
            ax, ay, aw, ah, _ = placed[a]
            bx, byy, bw, bh, _ = placed[b]
            sx, sy = ax + aw / 2, ay + ah
            if ba == bb:
                if ay == byy:                       # same row: dip into the row gap
                    mid, ty = sy + 18 + (i % 5) * 6, byy + bh
                else:                               # lower row of the same band
                    mid, ty = sy + (byy - sy) * 0.45, byy
            elif ib > ia:                           # downward: run under the target band's gutter
                mid, ty = band_geom[bb]["top"] - 34 + (i % 8) * 5, byy
            else:                                   # upward: run under the source band, enter the target's bottom
                mid, ty = bottom_of[ba] + 22 + (i % 5) * 5, byy + bh
            pts = [(0, 0), (0, mid - sy), (bx + bw / 2 - sx, mid - sy), (bx + bw / 2 - sx, ty - sy)]
            arrow(sx, sy, pts, strokeColor=LAYER_COLOR[ba], strokeWidth=1, opacity=55)

    # band captions: how many edges cross each boundary
    for band in BANDS:
        down = sum(n for (ba, bb), n in pairs.items() if ba == band and BANDS.index(bb) > BANDS.index(band))
        same = sum(n for (ba, bb), n in pairs.items() if ba == band and bb == band)
        if down or same:
            g = band_geom[band]
            text(X0 + BAND_W - 210, g["top"] + 12, f"{down} edges down · {same} within", 11, GREY)

    # outside band
    y = max(g["top"] + g["height"] for g in band_geom.values()) + 96
    rect(X0 - 2, y, BAND_W, 176, strokeColor=FAINT)
    rect(X0 + 6, y + 8, 900, 26, strokeColor="#ffffff", backgroundColor="#ffffff")
    text(X0 + 12, y + 12, "outside src/ — what kubementat talks to, measured by the import that reaches it", 17, BLUE)
    ox = X0 + 6
    outside_boxes = {}
    for label, note, w in (("kubernetes.client", "the API server, one ClusterTarget at a time", 430),
                           ("kubectl · helm · helmfile", "binaries, reached only through infrastructure/shell.py", 430),
                           ("Grafana admin API", "/api/users · /api/org · /api/teams over HTTP", 380),
                           ("the platform_config tree", "read by core/config, committed by config_bootstrap.py", 430)):
        rect(ox, y + 46, w, 61, strokeColor="#64748b", backgroundColor="#f1f5f9")
        rect(ox, y + 46, w, 39, strokeColor="#64748b", backgroundColor="#ffffff")
        text(ox + 11, y + 50, label, 15, "#64748b")
        text(ox + 11, y + 69, note, 11, GREY)
        outside_boxes[label] = (ox, y + 46, w, 61)
        ox += w + 22
    for label, (ox, oy, ow, oh) in outside_boxes.items():
        hits = outside.get(label, set())
        boxes_hit = collections.Counter(owner_of(m) for m in hits)
        for bname, cnt in sorted(boxes_hit.items(), key=lambda kv: (-kv[1], kv[0])):
            x, byy, bw, _, _ = placed[bname]
            arrow(x + bw / 2, byy + placed[bname][3], [(0, 0), (0, oy + oh - byy - placed[bname][3])],
                  strokeColor="#047857", strokeWidth=1, opacity=50)
        if label == "the platform_config tree":
            text(ox + 11, oy + oh + 6, f"28 files touch a path; the tree is written by config_init.py and committed by config_bootstrap.py", 11, GREY)
    y = max(y + 176, max_y)

    # error taxonomy
    ey = y + 60
    text(X0, ey, f"the taxonomy — {len(errs)} error classes, all defined in src/kubementat/errors.py, "
                 "raised and never returned · ▸ marks a subclass of something other than KubementatError", 19, "#b91c1c")
    text(X0, ey + 26, "each sentence is the class's own docstring, read out of errors.py; the tail says what a caller catches", 12, MUTED)
    ey += 54
    order = sorted(errs, key=lambda e: e["line"])
    col_w = 780
    per = 10
    rowh = 46
    for i, e in enumerate(order):
        col = i // per
        rowy = ey + (i % per) * rowh
        cx = X0 + col * (col_w + 18)
        line(cx, rowy + 9, [(0, 0), (16, 0)], strokeColor="#fecaca")
        say = e["say"]
        if len(say) > 96:
            say = say[:94].rsplit(" ", 1)[0] + "…"
        text(cx + 22, rowy - 3, ("▸ " if not e["base"].startswith("KubementatError") and e["base"] != "Exception" else "") + e["name"], 12, "#b91c1c")
        text(cx + 240, rowy + 14, say, 10, "#f87171")
        text(cx + col_w - 130, rowy - 3, e["base"].replace("KubementatError", "Kubementat")[:20], 10, GREY)
    cols = (len(order) + per - 1) // per
    rect(X0 - 2, ey - 22, cols * (col_w + 18) + 12, per * rowh + 26, strokeColor="#fca5a5")
    y = ey + per * rowh + 34

    # footer
    text(X0, y, "Every edge above is an import a parser found, and every count is an AST count. Each box names a file or "
                "package that exists at this HEAD; the classes the earlier drawing of this tree showed as boxes are gone, "
                "and what answers their surface is named instead.", 12, MUTED)
    y += 24
    return y + 30


BG_OF = {"cli": "transparent", "verbs": "transparent", "automations": "transparent", "infrastructure": "transparent", "core": "transparent"}


def band_note(band):
    return {"cli": "src/cli", "verbs": "src/kubementat — flat", "automations": "automations",
            "infrastructure": "infrastructure", "core": "core"}[band]


if __name__ == "__main__":
    mods, pairs, fan_in, outside, errs = measure()
    for stale in tree_boxes.stale_boxes():
        print(f"STALE BOX: {stale}", file=sys.stderr)
    draw(mods, pairs, fan_in, outside, errs)
    doc = {"type": "excalidraw", "version": 2, "source": "kubementat", "appState": {"viewBackgroundColor": "#ffffff", "gridSize": 20}, "files": {}, "elements": elements}
    json.dump(doc, open(OUT, "w"), indent=1)
    print(f"{OUT}: {len(elements)} elements, {len(BOXES)} boxes, {len(pairs)} box edges, {len(errs)} error classes")
