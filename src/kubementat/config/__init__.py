"""The configuration that is written from above the line: the tree's five verbs.

This file is a marker and nothing else. `[tool.setuptools.packages.find]` in `pyproject.toml` picks
packages up by *marker* and looks only under `src/`, so without this line the five modules beside it
are not a package, are not installed, and are importable in the checkout and nowhere else.

It re-exports nothing on purpose, for the same reason `kubementat.core.config.__init__` does not. A
name written into this file's namespace is a second way to reach the same object, and a second way is
exactly what makes a `monkeypatch.setattr` land on the copy nobody is running. `kubementat.config.init`,
`.validate`, `.bootstrap`, `.keys` and `.answers` are therefore reached through their own dotted paths,
one way each, and every caller imports the name it actually calls.

What lives beside this package is the other half of the concept: `kubementat.core.config` answers the
tree (the layers, the reads, the derived values) and this package changes it. The two are one
namespaces with two spellings, and the seam between them was visible at `kubementat/config/validate.py:62-64`,
which imported a sibling writer next to the reader's names in one import block — a reader who grepped
either spelling found half the concept and concluded the other half was elsewhere. Item 8 (the merge)
moved the writers here so one grep finds the whole concept, and the move changed no behaviour: the
five modules read, write, and refuse exactly as they did flat under `src/kubementat/`.
"""
