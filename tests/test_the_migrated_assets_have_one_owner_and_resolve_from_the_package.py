"""Where the migrated assets live, measured off the resolver the product uses and not off prose.

This file used to read `README.md`, `docs/index-overview.md` and `examples/`, line by line, and
assert that every path a sentence named existed. That is the class of guard the developer guide rules
out -- "the docs agree with the code" is checked once, by reading, when a change lands -- and its cost
was paid on every move: a renamed directory turned seven green tests red without one of them having
said anything about behaviour.

The drift the file was actually protecting against is real, and it is the thing the plan names as a
guard to keep: a move leaves behind a sentence that points at a directory nothing uses. So what is
kept here is the *artefact* half of that claim, which is the half a test can hold: the asset roots
the product resolves exist, they resolve from inside the installed package, and the checkout carries
no second copy of them that the two could disagree with. A sentence that goes stale is a documentation
defect; a package that resolves a path with no files behind it is a product defect, and that is the
only one of the two this suite is for.

What replaced the prose checks, and where the behaviour is asserted instead:

* *the shipped manifest and its defaults are the ones the apply opens* --
  `tests/test_the_manifest_applied_is_the_one_the_package_ships.py` runs the apply and reads the
  manifest the package resolves, so a moved file fails the apply and not a sentence comparison.
* *the product's Tekton manifests are applied from the package* --
  `tests/test_the_manifest_layers_apply_product_then_environment_then_team.py` applies the layer this
  root is and asserts what reaches the cluster.
* *a wheel install has no checkout to point at* -- `tests/test_a_configuration_root_handed_to_a_ci_task_resolves_against_a_real_tree.py`
  and the CI guards read the same resolver, `kubementat.core.assets`.
"""

from pathlib import Path

from asset_paths import ASSET_ROOT, asset_path, tekton_ci_path, values_path

# The checkout-side directories the migration moved into the package. Each is a place a second copy
# of a shipped asset could reappear, and the divergence between the two copies is the defect this
# guard is for: a guard that reads one while the writer opens the other passes by accident.
MOVED_OUT_OF_THE_CHECKOUT = (
    "templates",
    "values",
    "tekton_ci/tasks",
    "tekton_ci/pipelines",
    "tekton_ci/triggers",
)


def test_the_migrated_assets_resolve_from_the_package_and_no_second_copy_ships_in_the_checkout():
    """One owner for each shipped asset root: the package, and nothing that shadows it.

    Both halves are needed. The resolver alone would be a tautology -- it always answers somewhere --
    and the absence check alone would not say whether the product can open the files. Read together
    they are the invariant: what `asset_paths` resolves *is* what ships, because there is nowhere
    else for it to be.
    """
    checkout_root = Path(__file__).resolve().parents[1]

    assert ASSET_ROOT.is_dir(), "the package resolves no asset root at all"
    for name in ("templates", "values", "tekton_ci"):
        root = asset_path(name)
        assert root.is_dir(), f"`{name}` resolves to {root}, which is not a directory"
        assert any(root.rglob("*")), f"`{name}` resolves inside the package and ships nothing"
        assert ASSET_ROOT in root.parents or root == ASSET_ROOT, \
            f"`{name}` resolves to {root}, outside the package root {ASSET_ROOT} -- the product " \
            "would be reading this checkout, which a wheel install does not have"

    for relative in MOVED_OUT_OF_THE_CHECKOUT:
        assert not (checkout_root / relative).exists(), \
            f"the checkout carries `{relative}` again, so the shipped copy and the checkout copy " \
            "have diverged and whichever one a guard reads, one of them is wrong"


def test_the_manifest_and_the_default_values_it_names_are_shipped_data():
    """The files the apply reads are reachable through the resolver, so a move is a failure and not a note.

    The two names here are the ones every release block in the manifest is written against: the
    template itself and one shipped values file per release family. Nothing is counted -- a count in a
    docstring is how the old version of this file died -- only the claim that the artefact the product
    opens is where the product looks for it.
    """
    assert values_path("helmfile.yaml.gotmpl").is_file(), \
        "the manifest every apply reads does not resolve"
    for relative in ("traefik/values.yaml", "monitoring/prometheus_values.yaml"):
        assert (values_path() / relative).is_file(), \
            f"`values/{relative}` is named by the manifest and does not resolve"
    assert sorted(tekton_ci_path(".").rglob("*.yml")), \
        "the package ships no Tekton manifest, so a generated pipeline has nothing to apply"
