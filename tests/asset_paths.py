"""The asset root, handed to a test by the same resolver the product uses.

What the product ships is what a guard should read, so there is one way in and it is the product's
way: `kubementat.core.assets`, the module every reader calls. The asset root is whatever the installed
`kubementat` package says it is — here, `src/kubementat/core/assets`, which is this checkout's own
tree, so the bytes a guard reads are the bytes the writer opens.

`template_path(...)` is the exception, and the exception is the item that deleted the files. It used to
resolve into the package, because `Config.templates_dir` resolved there and a guard and a writer had to
land on one path. The shipped templates are gone — 1.x's seed copies, which no verb opened — and the two
that survived did so as *witnesses* a test reads, so they live beside the test that reads them. A data
file whose only reader is a test is a fixture, and this is where the sentence that was true about them
lives now: `tests/fixtures/configuration_shape/`.

The shape of what is exposed:

* `asset_path(...)` — anything the package ships, rooted at the asset root;
* `template_path(...)` — one of the two witnesses, which is *not* package data any more;
* `values_path(...)` — the packaged helmfile and the default values it names, which are
  data and not templates and therefore have no copy anywhere;
* `tekton_ci_path(...)` — the product's Tekton manifests and generator templates, spelled the way
  `Config` spells them under `src/kubementat/core/assets/tekton_ci/`;
* `ASSET_ROOT` — the same thing as a name, for the module-level constants the guards were already
  writing.

Note what is *not* here: the directories a test builds for itself (`tmp_path / "templates"`,
`fixture_repo.root / "templates"`, `fixture_repo.root / "tekton_ci" / "pipeline-runs"`) stay as
they are. Those are a checkout the test owns, not the product's data, and pointing them at the
package would replace a fixture with a fact. `tekton_ci/pipeline-runs` in particular is output and
never ships, so there is no asset path that names it and a guard that asked for one would be
asserting that a half-written manifest lives in site-packages.
"""

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
    """A configuration-layer witness, read from the fixtures and not from the package.

    `template_path("environment", "static.json.template")` is the environment layer's shape as
    1.x's `initialize` handed it over: a table of keys whose answers `constants.py` owns now. The
    package ships no templates, so nothing in a run opens this file; it exists so a guard can ask
    `constants.py` whether it still says what the layer says, which is the only claim left that a
    drift can be caught by.
    """
    return Path(__file__).resolve().parent / "fixtures" / "configuration_shape" / Path(*parts)


def values_path(*parts: str) -> Path:
    """The packaged helmfile entry point and the default values it names.

    `values_path("helmfile.yaml.gotmpl")` is what `Config.helmfile_asset_dir` resolves to, and
    `values_path("monitoring", "prometheus_values.yaml")` is one of the defaults the
    releases in it read. Both moved out of `templates/` and out of `platform_config/`: the
    manifest is what is applied and the values are shipped data, so neither is a template anybody
    copies any more, and a guard that still reads them from `templates/` is reading a file nothing
    opens.
    """
    return _resolver("values", *parts)


def tekton_ci_path(*parts: str) -> Path:
    """The product's Tekton manifests and generator templates, as one accessor.

    `tekton_ci_path("tasks")` is what `Config.tekton_task_asset_dir` is, and
    `tekton_ci_path("pipelines", "templates")` is what `Config.tekton_pipeline_template_dir` is —
    which is the whole point of spelling them here. A guard that reads
    `REPO_ROOT / "tekton_ci" / "tasks"` while `setup_pipelines` applies
    `Config.tekton_task_asset_dir` is a guard that passes only while the checkout happens to
    carry the directory; the sentence the ticket is about is "applied from the package, not from
    a directory that may not exist", and a test that measures the old location cannot see that.

    `pipeline-runs` is deliberately not a possible argument in spirit: it is output, it stays
    checkout-relative, and `Config.tekton_global_pipeline_run_dir` is where a test goes for it.
    """
    return _resolver("tekton_ci", *parts)


def relocate_asset_root(root, monkeypatch) -> Path:
    """Make every shipped asset resolve under `<root>` for the rest of this test.

    This is the one thing here that looks like the fallback the product says no. It is not one:
    there is no discovery, no candidate search, and no second root the product would consult on
    its own. It is `monkeypatch`, applied by a test that needs it, undone by pytest when the test
    ends, and it lives on the test side of a line the product never crosses.

    A test needs it for exactly one reason. Some guards have as their subject *two files that
    disagree*, and one of those files is product data — shipped, not writable, and
    therefore not something a test can arrange a divergence against. Moving the resolver is how
    that half stays addressable without teaching the product a root it was told to stop using.
    """
    from kubementat.core.config import config as config_module

    target = Path(root)
    monkeypatch.setattr(config_module, "asset_path", lambda *parts: target.joinpath(*parts))
    return target
