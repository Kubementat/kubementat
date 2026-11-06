"""Where kubementat's own version comes from, and what it is read from.

`pyproject.toml`'s `project.version` is the source of truth; this module is the only reader and
the distribution metadata is the pipe. Nothing here holds a literal, because a literal is a
second source of truth by construction: before this module there were three
(`pyproject.toml` = `0.4.0`, `VERSION` = `0.3.0`, and the git tags `0.1.0`…`0.3.0`), and the
one every tool could read was the stale one. The `VERSION` file is deleted for that reason and
no fallback constant replaces it.

The lookup is done when it is asked for rather than at import. `importlib.metadata` is cheap,
but doing it at module scope would make every consumer of `import kubementat` depend on the
package having been installed, and the one command that actually wants the answer is
`kmt --version` — which is exactly the command that fails first in a source checkout nobody
has installed yet. Failing there, with the sentence that says how to install the distribution,
beats failing on import.
"""

from importlib import metadata

# The distribution name, not the import name: the metadata is what `pip`/`uv` wrote at install
# time and it is keyed by the project name in `pyproject.toml`.
_DIST = "kubementat"


def get_version() -> str:
    """The version of the installed kubementat distribution.

    Raises:
        RuntimeError: if the distribution is not installed, so no metadata exists to read.
            That is either a checkout that has never been installed or an interpreter that was
            never given the distribution, and the message says which fix applies to which case
            rather than quietly returning a number that may already be wrong.
    """
    try:
        return metadata.version(_DIST)
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            f"{_DIST} is not installed, so its version cannot be read from the distribution "
            "metadata. Install the distribution itself — `pipx install kubementat`, or "
            "`uv tool install kubementat` — and run it again. If this is a source checkout, "
            "sync it first, because an installed checkout answers to the same metadata: "
            "`uv sync --all-extras` at the root of the checkout."
        ) from exc
