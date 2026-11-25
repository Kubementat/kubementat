"""Reads `versions.yaml` — the single owner of every default version kubementat runs with.

The file lives in the package's data (`core/assets/versions.yaml`) and is resolved through
`core/assets`, so a wheel, a checkout and a read-only install all answer from one place. Every
consumer that needs a default version asks *this* module and never the file directly: a default
that five readers each re-parse is five owners of one number, and the fifth is what a stale reader
runs.

**The reader refuses, it never defaults.** A key that is not there raises
`errors.VersionNotDeclaredError`, naming the file and the key's path, because the only alternative
to "this file did not answer" is an inline fallback at the call site — and a fallback is a second
owner that agrees by luck. A module that returned `"1.0.0"` for an unknown chart would be the bug,
not the convenience.

The module is a leaf: it imports `errors` and the asset resolver and nothing from `constants.py`
or the configuration machinery, so any layer of the tree may import it without bending the
layering. It reads no terminal and no stdin.

`python -m kubementat.core.versions --print-build-args` prints the `ci_image:` block as
`--build-arg NAME=VALUE` lines for the image build scripts, which need the file's answers but may
not assume `yq` on the machine. `--get <dotted.path>` answers one value, the same way a Python
consumer does.
"""

from __future__ import annotations

import argparse
from typing import Any

import yaml

from kubementat import errors
from kubementat.core.assets import asset_path

VERSIONS_ASSET_NAME = "versions.yaml"

# Parsed once per process. Nothing in a running kubementat rewrites the file — it is package data
# — so a per-call `safe_load` would be work without a reason, and a per-instance cache would be a
# second answer where the file is the one.
_DOCUMENT: dict | None = None


def _document() -> dict:
    """The whole file as one mapping, refusing a file that is not there or not a mapping.

    A missing file is not a different fact from a missing key that runs that deep: the file is
    shipped, so its absence says this installation lost its own data. The refusal names it.
    """
    global _DOCUMENT
    if _DOCUMENT is None:
        path = asset_path(VERSIONS_ASSET_NAME)
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as missing:
            raise errors.VersionNotDeclaredError(
                path=str(path), key="<the whole document>",
                reason=f"the file was not readable ({missing}), so no default version answers",
            ) from missing
        if not isinstance(loaded, dict):
            raise errors.VersionNotDeclaredError(
                path=str(path), key="<the whole document>",
                reason="it parses as something other than the mapping every consumer reads",
            )
        _DOCUMENT = loaded
    return _DOCUMENT


def _follow(*parts: str) -> Any:
    """`_document`'s value at `<parts>`, refusing where the walk finds nothing.

    The refusal carries the path walked so far, including the part that failed: an operator
    reading "no version for charts.grafana.version" can open the file and see the line that is
    missing, while "missing key" with no path cannot say which of the six charts moved.
    """
    node = _document()
    walked: list[str] = []
    for part in parts:
        walked.append(part)
        if not isinstance(node, dict) or part not in node or node[part] is None:
            raise errors.VersionNotDeclaredError(
                path=str(asset_path(VERSIONS_ASSET_NAME)), key=".".join(walked),
                reason="the file answers nothing there, and this reader never defaults",
            )
        node = node[part]
    return node


def chart_version(release: str) -> str:
    """The chart version of `release` — `charts.<release>.version`."""
    return str(_follow("charts", release, "version"))


def chart_image_tag(release: str) -> str | None:
    """The image tag shipped beside `release`'s chart, or `None` when it owns no image of its own.

    `None` answers a shape and not a gap: only Concourse pairs a chart version with the appVersion
    tag the chart renders, so a release that is not that one has no tag to export. A release the
    file does not name at all still refuses (`VersionNotDeclaredError`), because that is a typo or
    a deleted block, not a shape.
    """
    entry = _follow("charts", release)
    tag = entry.get("image_tag") if isinstance(entry, dict) else None
    return str(tag) if tag is not None else None


def chart_names() -> tuple[str, ...]:
    """Every release the `charts:` block declares, sorted.

    Membership rather than lookup: the configuration walk asks "does the file own this release's
    version" before it falls back, and a fallback that guessed `False` would silently stop
    answering a release the file does own.
    """
    charts = _follow("charts")
    return tuple(sorted(charts))


def binary_version(binary: str) -> str:
    """The pinned version of a binary kubementat execs — `binaries.<binary>`."""
    return str(_follow("binaries", binary))


def tekton_version(component: str) -> str:
    """The release applied for a Tekton component (`pipeline`, `dashboard`, `triggers`)."""
    return str(_follow("tekton", component))


def image_ref(image: str) -> str:
    """The whole reference (`name:tag`) of a product-shipped container image."""
    return str(_follow("images", image))


def e2e_version(tool: str) -> str:
    """The pinned version of an end-to-end harness fixture (so far: `k3s`)."""
    return str(_follow("e2e", tool))


def ci_image_args() -> dict[str, str]:
    """The `ci_image:` block as `{ARG name: value}` — the build args of the CI image.

    Returned as strings and nothing else: a Docker `--build-arg` has no type to carry, and a YAML
    value that parsed into a number here (`NODE_VERSION: 24.2` reads as a float) would print a
    different string than the file says.
    """
    block = _follow("ci_image")
    return {name: str(value) for name, value in block.items()}


def get(dotted_path: str) -> str:
    """One value, addressed the way `--get` addresses it: `e2e.k3s`, `charts.grafana.version`."""
    return str(_follow(*dotted_path.split(".")))


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kubementat.core.versions",
        description="Read a default version from " + VERSIONS_ASSET_NAME + " without a Python import.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--print-build-args", action="store_true",
                        help="print one --build-arg NAME=VALUE line per ci_image: entry")
    source.add_argument("--get", metavar="PATH", help="the value at a dotted path, e.g. e2e.k3s")
    args = parser.parse_args(argv)

    if args.print_build_args:
        for name, value in sorted(ci_image_args().items()):
            print(f"--build-arg {name}={value}")
    else:
        print(get(args.get))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
