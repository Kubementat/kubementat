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

What sits *beside* this package, and not inside it, is the verb half: `kubementat.config.init`,
`kubementat.config.bootstrap`, `kubementat.config.answers`, `kubementat.config.keys` and
`kubementat.config.validate`. Item 8 moved those five writers under `src/kubementat/config/` out of
the flat `src/kubementat/config_*.py` spellings they had carried since 1.x, so the concept now greps
from one place. The split the names describe is unchanged: they orchestrate, this package answers,
the writers import the readers and never the other way round, and nothing above the line needs to
know the readers exist. What moved is where the writers' names live, not what they do — and the
import block that proved it (`config_validate.py:62-64`, which reached a flat sibling writer
beside this package's readers in one block) now reaches both halves as `kubementat.config.*` and
`kubementat.core.config.*`, with no flat spelling left in it.
"""
