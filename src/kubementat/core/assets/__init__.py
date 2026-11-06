"""The package's own data: the files `kmt` ships and opens, resolved from inside the package.

The outcome of moving the data is a sentence about installation, not about directories. `templates/`
used to
sit at the repository root, and every reader reached it by walking *up* out of
`src/kubementat/config.py` to a root that only exists in a checkout. A wheel has no root to walk
up to, so the same code answered `KUBEMENTAT_HOME` or failed. Resolving from inside the package
answers all three installs with one line:

* a **wheel** — the data unpacks into `site-packages/kubementat/core/assets/templates/`, and
  `importlib.resources.files()` names it;
* a **checkout** — `src/kubementat/core/assets/templates/` is a real directory there too, so the
  bytes are the ones the developer is editing;
* a **read-only install** — nothing is ever written under the asset root, so a directory the
  interpreter cannot write to is still readable.

One resolver, not one per caller: `asset_path` is the only way in. Callers get a real `Path`
that survives them, which is what `open` and `os.walk` need — a resource path that disappears
when a context manager exits is not a template path.
"""

from __future__ import annotations

import contextlib
import importlib.resources
from pathlib import Path

__all__ = ["asset_path", "assets_root"]

# `as_file` materialises a resource that has no filesystem at all (a zipimported wheel, an
# egg) into a directory that exists only while its context manager is open. A path handed to
# `os.walk` or held on a `Config` object outlives any single call, so the materialisation is
# entered once and parked here, where it stays for the life of the process. Nothing else in
# this module touches it; a filesystem install never takes this branch at all.
_MATERIALIZED = contextlib.ExitStack()


def assets_root() -> Path:
    """Where this package's data lives, as a real directory.

    The test is `is_dir()`, not a type check: `files()` returns a `pathlib.Path` for a wheel, a
    checkout, and a read-only install alike, and a `MultiplexedPath` or a zip member for the
    installs that do not, so a type test would guess wrong about exactly the installs that are
    hard to get right.
    """
    resource = importlib.resources.files(__package__ or __name__)
    candidate = Path(str(resource))
    if candidate.is_dir():
        return candidate.resolve()
    return Path(str(_MATERIALIZED.enter_context(importlib.resources.as_file(resource)))).resolve()


def asset_path(*parts: str) -> Path:
    """`<asset root>/<parts>` — the one accessor for anything the package ships.

    `asset_path("templates")` is the templates root;
    `asset_path("templates", "environment", "static.json.template")` is one file under it.
    Nothing here walks the tree to find the repository: that walk is the bug this ticket
    deletes, and a resolver that quietly fell back to it would make the wheel look broken only
    on a machine that also has a checkout lying around.
    """
    root = assets_root()
    for part in parts:
        root = root / part
    return root
