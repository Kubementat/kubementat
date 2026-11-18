"""The asset root, handed to a test by the same resolver the product uses."""

from pathlib import Path

from kubementat.core.assets import asset_path as _resolver
from kubementat.core.assets import assets_root

__all__ = ["REPO_ROOT", "ASSET_ROOT", "asset_path", "template_path", "values_path", "tekton_ci_path",
           "relocate_asset_root"]

# Taken once, not per call: the resolver is a function of where the package is installed, and
# that does not move inside a run. Named so a guard that wants the whole root does not build the
# same path four times.
ASSET_ROOT: Path = assets_root()

# The checkout this suite runs from: the shipped Tekton tasks, the examples and the pyproject live here.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def asset_path(*parts: str) -> Path:
    """`<asset root>/<parts>` — what `Config.templates_dir` is built from."""
    return _resolver(*parts)


def template_path(*parts: str) -> Path:
    """A configuration-layer witness, read from the fixtures and not from the package."""
    return Path(__file__).resolve().parent / "fixtures" / "configuration_shape" / Path(*parts)


def values_path(*parts: str) -> Path:
    """The packaged helmfile entry point and the default values it names."""
    return _resolver("values", *parts)


def tekton_ci_path(*parts: str) -> Path:
    """The product's Tekton manifests and generator templates, as one accessor."""
    return _resolver("tekton_ci", *parts)


def relocate_asset_root(root, monkeypatch) -> Path:
    """Make every shipped asset resolve under `<root>` for the rest of this test."""
    from kubementat.core.config import config as config_module

    target = Path(root)
    monkeypatch.setattr(config_module, "asset_path", lambda *parts: target.joinpath(*parts))
    return target
