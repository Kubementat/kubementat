"""The Tekton half of the infrastructure layer: five modules, and no names.

This file is a marker and nothing else. `[tool.setuptools.packages.find]` in `pyproject.toml`
packages by marker, so without it `generation.py` and the four modules that follow it are not a
package, are not installed, and are importable in a checkout and nowhere else. It re-exports
nothing on purpose: every seam on the Tekton path is patched in the tests by *module object*, and a
name re-exported here is a second binding for it that a `monkeypatch.setattr` on either side cannot
tell apart. One spelling per name, and it is always `<the module>.<name>`.
"""
