"""The identity half of `core`: what cluster a command means, and how that was answered.

This file is a marker and nothing else. `[tool.setuptools.packages.find]` in `pyproject.toml` picks
packages up by *marker* and looks only under `src/`, so without this line the modules beside it are
not a package, are not installed, and are importable in the checkout and nowhere else.

It re-exports nothing on purpose. A module name written into this file's namespace is a second way to
reach the same object, and a second way is exactly what makes a `monkeypatch.setattr` land on the copy
nobody is running: the patch is applied to the attribute named in the call, the code reads the
attribute it reached, and when the two differ the test is green and the product is not.
`kubementat.core.identity.kubeconfig` is therefore reached through its own dotted path, one way
each, and every caller imports the name it actually calls. This first ticket moved only that module;
`target.py`, `comparison.py`, `registry.py` and `context.py` came with the tickets after it, and the
order is not arbitrary: `kubeconfig ← target ← comparison ← registry ← context`, so the leaf moved
first and every edge in every commit pointed down. `context.py` arrived last, and with it
`src/kubementat/cluster.py` was deleted rather than left as a shell, because `Context` is the class
the other four are read by and none of them reads.
"""
