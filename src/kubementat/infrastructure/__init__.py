"""The layer that touches a cluster, a tool, or a terminal, and everything below it that is pure.

This file is a marker and nothing else: `[tool.setuptools.packages.find]` in `pyproject.toml`
picks packages up by *marker* and looks only under `src/`, so without it the areas beside it are
not a package, are not installed, and are importable in a checkout and nowhere else.

It re-exports nothing on purpose. A name written into a package marker is a second route to the
object it names, and the second route is what makes a patch land on the copy nobody runs: the
caller reads the attribute it reached, the test replaces the attribute it named, and when those
two differ the suite is green while the product is not.
"""
