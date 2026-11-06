"""The configuration that is read from below the line: the layers, the reads, the derived answers.

This file is a marker and nothing else. `[tool.setuptools.packages.find]` in `pyproject.toml` picks
packages up by *marker* and looks only under `src/`, so without this line the four modules beside it
are not a package, are not installed, and are importable in the checkout and nowhere else.

It re-exports nothing on purpose. A module name written into this file's namespace is a second way to
reach the same object, and a second way is exactly what makes a `monkeypatch.setattr` land on the
copy nobody is running: the patch is applied to the attribute named in the string, the call goes
through the attribute the code reached, and when the two differ the test is green and the product is
not. `kubementat.core.config.config`, `kubementat.core.config.derived`,
`kubementat.core.config.reads` and `kubementat.core.config.release_index` are therefore reached
through their own dotted paths, one way each, and every caller imports the name it actually calls.

What is deliberately *not* here is the verb half — `config_answers`, `config_bootstrap`,
`config_init`, `config_keys`, `config_validate` stay flat under `src/kubementat/`, because they
orchestrate and this package answers. Nothing in this package imports them, and nothing above the
line needs to know they exist.
"""
