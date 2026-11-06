"""The layer everything else may read from: the declared values, the version, the shipped data.

`[tool.setuptools.packages.find]` in `pyproject.toml` picks packages up by *marker*, and it only
looks under `src/`: a subdirectory without an `__init__.py` is not a package, so it is not
installed. Here the marker is load-bearing twice over, because `assets/` travels with it —
`include-package-data` ships the data a package holds, and a directory that is not a package ships
nothing. That failure does not look like anything in this checkout, where everything is importable
whatever the marker says; it looks like a wheel with code and no templates.
"""
