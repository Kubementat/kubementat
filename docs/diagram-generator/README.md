# diagram-generator

Two commands, from the repo root (both work from any directory; paths resolve from the script):

```
python3 docs/diagram-generator/build_tree_diagram.py   # regenerate docs/diagrams/src-class-diagram.excalidraw
python3 tools/import_graph.py src                      # edges, cycles, layer violations — exit 1 if either exists
python3 docs/diagram-generator/export_png.py           # re-render the PNG from the .excalidraw just written
```

- `tree_survey.py` — measures `src/`: every module's LOC, public-surface counts, and the AST import edges.
  Knows nothing about boxes; the numbers are all it produces.
- `tree_boxes.py` — `BOXES`, the hand-maintained box table: which real file or package each box names,
  which modules it draws, and `BANDS`, the vertical order. `build()` turns the two into box-pair edges.
- `build_tree_diagram.py` — layout and drawing. Reads `tree_survey` through `tree_boxes`, so every label,
  arrow and count on the page is measured, and writes only the `.excalidraw`.
- `tools/import_graph.py` — not a diagram tool; the layer/cycle gate. The generator's caption cites it
  because the drawing's claim that the arrows are the real edges is the same claim this script checks.

`BOXES` = 30 boxes: `band`, `members` (module prefixes it owns), `note`, `blurb`. Band order is the import
direction, not the folder order. The box's display name is its path under `src/`, so a box label is a claim
about the tree, and `stale_boxes()` prints a warning if that path has stopped resolving.

## What goes stale when you move code

- A rename or move inside `src/` that leaves a box drawing a path that no longer exists — the warning above
  is the only thing that says so; nothing else checks the box names.
- A new module no box claims. `build()` raises `module not in any box`, so this one fails loudly.
- A `members` prefix that overreaches, e.g. after a move into a deeper package: the box silently swallows
  modules it no longer owns. Nothing checks this, so re-run and read the box headers.
- The generator itself never goes stale. Only `tree_boxes.py` and the counts the band captions print are
  hand-held, and the diagram is only as good as the last time it was regenerated.
