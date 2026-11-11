"""Kubementat's automations, as a package rather than as a directory that happens to exist.

`[tool.setuptools.packages.find]` in `pyproject.toml` picks packages up by *marker*, and it only
looks under `src/`: a subdirectory without an `__init__.py` is not a package, so it is not
installed. That failure does not look like anything here — this checkout is importable whatever
the marker says — it looks like an `ImportError` on the next machine, which is why the marker is
the thing that has to be committed rather than the directory.
"""
