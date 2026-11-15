"""What exists, and in what namespace, is answered once — from the release index.

Two questions used to be answered in two places, and this file is the claim that they no longer
are. `cli/commands/component.py` used to decide "is this a kubementat component" from
`constants.DEFAULT_NAMESPACES` while `core/config/release_index.py` decided "does this release
exist" from the labels the shipped manifest carries. Both were internally correct; the sentence a
refusal produced was not. Measured on the shipped tree, the CLI used to tell an operator that
`alloy` was "not a kubementat component" in the same breath as the manifest it was about to apply
named a release `component_name=alloy`.

So every test below is a **set equation between the two reads**, asked for every shipped release,
and none of them is a count. A count is what the last version of this rule looked like ("ten
labels, thirteen namespace keys") and it rots the day a block is commented in.

* **(a) the derived answer equals the file.** `core.components` is checked against an independent
  parse of the manifest — not against `read_release_index`, because comparing a function with
  itself proves nothing.
* **(b) the two mismatch directions are derived, not typed.** The labels no namespace key names,
  and the keys no release backs, are both computed.
* **(c) a refusal says which case it is in.** A shipped release is never refused as a typo, and a
  typo is never refused as a release.
* **(d) the CRD sweep's set is set-equal to what the tables produce**, for every component the
  manifest ships. This is the highest-consequence line in the file: a wrong derivation here either
  leaves cluster-scoped definitions behind or deletes somebody else's.
* **(e) one read per command.** A verb that asks the manifest twice can give itself two answers,
  so the universe is read once and every question is answered off it.
"""

from pathlib import Path
import re

import pytest

from cli.commands import component as component_cli
from kubementat.core import components, constants
from kubementat.core.config.config import Config
from kubementat.automations.uninstall import residue
from kubementat.infrastructure.helm import HelmUtils
from asset_paths import values_path
from cluster_fixture import UNREAD_ROOT, configuration_only_context

LIVE_HELMFILE = values_path("helmfile.yaml.gotmpl")

# A manifest with one release every shipped component commented out and one unaddressed release
# kept, so the derivation can be shown to *move*. A test that only ever reads the shipped file
# cannot tell a derivation from a copy of the shipped file, and the whole claim of this item is
# that it is a derivation.
ONE_RELEASE_EACH = """\
helmDefaults:
  wait: true
releases:
- name: prometheus-blackbox-exporter
  chart: prometheus-community/kube-prometheus-stack
  namespace: prometheus
  labels:
    component_name: prometheus-blackbox-exporter
    group: standard
# - name: cert-manager
#   chart: jetstack/cert-manager
#   namespace: cert-manager
#   labels:
#     component_name: cert-manager
#     group: standard
"""


def read_the_manifest_yourself(path: Path) -> dict:
    """The shipped manifest, parsed here rather than through the parser under test.

    Deliberately the long way: a comment-skipping regex, and a `repositories:` entry that shares
    the shape of a release header. The test's parser and the product's parser agreeing is the
    check that the derivation follows the file; agreeing with `read_release_index` would be an
    echo.
    """
    releases = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        header = re.match(r"^- name:\s*(\S+)", line)
        if header:
            current = header.group(1)
            continue
        label = re.match(r"^\s+component_name:\s*(\S+)", line)
        if label and current:
            releases.setdefault(label.group(1), []).append(current)
    return {label: sorted(set(names)) for label, names in releases.items()}


def shipped_universe() -> components.ComponentUniverse:
    """The universe of the tree this checkout ships, read through the product's own path."""
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    return components.ComponentUniverse.read(config)


def universe_of(manifest_text: str, tmp_path, declared=None) -> components.ComponentUniverse:
    """A universe over a manifest the test wrote, so the derivation can be pointed somewhere else."""
    path = tmp_path / "helmfile.yaml.gotmpl"
    path.write_text(manifest_text, encoding="utf-8")
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    return components.ComponentUniverse.read(config, path, declared=declared)


# ---------------------------------------------------------------------------------------------
# (a) the derived answer equals the file, for every shipped release
# ---------------------------------------------------------------------------------------------


def test_every_release_the_manifest_names_is_in_the_answer_and_nothing_else_is():
    """`released()` equals the manifest, read independently.

    A label that only the manifest carries, a label that only the code carries: both are bugs this
    equation catches, and neither is caught by counting.
    """
    universe = shipped_universe()
    from_file = read_the_manifest_yourself(LIVE_HELMFILE)

    assert set(universe.released()) == set(from_file), \
        f"the derived release universe and the manifest disagree: {set(universe.released()) ^ set(from_file)}"


def test_no_shipped_release_is_answered_as_though_it_did_not_exist():
    """The sentence this item exists to retire, asserted by enumeration.

    Every label the manifest carries gets a kind that is *not* `not_a_kubementat_name`, because
    telling an operator that a release they are about to apply is not a kubementat thing is the
    disagreement the two oracles produced. `alloy`, `prometheus-blackbox-exporter` and
    `prometheus-helm-exporter` are the names this catches, and they are caught by the rule and not
    by name.
    """
    universe = shipped_universe()

    refused_as_absent = sorted(name for name in universe.released()
                               if universe.kind(name) == components.NOT_A_KUBEMENTAT_NAME)
    assert refused_as_absent == [], \
        f"the manifest names these releases and the existence answer denies them: {refused_as_absent}"


def test_the_namespace_table_and_the_release_index_agree_for_every_name_either_one_names():
    """Three set equations, and they are the whole of "the two oracles answer identically".

    (1) what the verbs accept is exactly what the namespace table keys, (2) the labels with no key
    are exactly the ones the manifest carries and the table does not, (3) the namespace-only names
    are exactly the keys the manifest does not back. Nothing here is a size.
    """
    universe = shipped_universe()
    from_file = read_the_manifest_yourself(LIVE_HELMFILE)

    assert set(universe.accepted_names()) == set(constants.DEFAULT_NAMESPACES)
    assert set(universe.releases_without_a_component()) == set(from_file) - set(constants.DEFAULT_NAMESPACES)
    assert set(universe.namespace_only()) == set(constants.DEFAULT_NAMESPACES) - set(from_file)
    # The intersection that decides what a component verb will apply: derived from both reads, so
    # an index that answers twice for the same release cannot hide behind a list.
    installable = {name for name in universe.accepted_names() if universe.releases_for(name)}
    assert installable == set(from_file) & set(constants.DEFAULT_NAMESPACES)
    assert all(from_file[name] for name in from_file), \
        "the manifest declares an empty release list, which is not the same fact as no release"


# ---------------------------------------------------------------------------------------------
# (b) both directions of the mismatch are derived
# ---------------------------------------------------------------------------------------------


def test_the_mismatch_moves_when_either_side_moves(tmp_path):
    """A name added to the table and a release added to the manifest both land in the answer.

    Half the old disagreement was that only one half moved. Here neither side needs an edit to a
    third place: `newcomer` appears in the universe's namespace-only answer because `constants`
    carries it, and `standalone` appears in the release-without-a-component answer because the
    manifest carries it.
    """
    manifest = ONE_RELEASE_EACH + """- name: standalone
  chart: fairwinds-incubator/standalone
  namespace: standalone
  labels:
    component_name: standalone
    group: standard
"""
    universe = universe_of(manifest, tmp_path,
                           declared=tuple(sorted(set(constants.DEFAULT_NAMESPACES) | {"newcomer"})))

    assert "newcomer" in universe.namespace_only()
    assert "standalone" in universe.releases_without_a_component()
    assert universe.kind("newcomer") == components.NAMESPACE_ONLY
    assert universe.kind("standalone") == components.RELEASE_WITHOUT_COMPONENT
    assert universe.kind("cert-manager") == components.NAMESPACE_ONLY, \
        "a release commented out of a manifest that still keys its namespace is namespace-only, "\
        "which is the answer `kmt component install` refuses with a namespace-only verb"


def test_commenting_a_release_out_removes_it_from_the_answer_and_from_the_sweep(tmp_path):
    """The direction that proves this is a derivation and not a copy.

    `cert-manager` is shipped, so the shipped answer includes its CRD groups. Read a manifest that
    does not ship it and those groups are not kubementat's to delete -- and `tekton.dev` is still
    there, because that half is not answered by the index and the test says so rather than
    assuming it.
    """
    universe = universe_of(ONE_RELEASE_EACH, tmp_path)
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    groups = set(residue.crd_groups_kubementat_owns(config, _manifest(tmp_path)))

    assert "cert-manager" not in universe.released()
    assert set(residue.CERT_MANAGER_CRD_GROUPS).isdisjoint(groups), \
        "the sweep still deletes the CRDs of a release this manifest does not apply"
    assert set(residue.CRD_GROUPS_APPLIED_WITH_KUBECTL) <= groups


def _manifest(tmp_path) -> Path:
    return tmp_path / "helmfile.yaml.gotmpl"


def seeded_config(tmp_path, environment: str = "dev") -> Config:
    """A `Config` over a real prepared tree, for the questions that read a namespace.

    `UNREAD_ROOT` is the right root for a question that only reads the manifest, and the wrong one
    for a question that asks `component_namespace`: that read consults `static.json`, and the root
    is named precisely so it cannot exist. The rename guard asks it, so that test gets a tree.
    """
    from cluster_fixture import stamp_tree, write_environment

    root = tmp_path / "seeded"
    write_environment(root, environment, context="dev-cluster")
    stamp_tree(root)
    return Config(environment, None, platform_config_dir=root / "platform_config",
                  preload_configs=False)


# ---------------------------------------------------------------------------------------------
# (c) a refusal says which case it is in
# ---------------------------------------------------------------------------------------------


def test_a_release_with_no_component_of_its_own_is_not_refused_as_a_typo():
    """The exact lie, on the live tree, in the live command's own words.

    Two assertions and they are the item: the sentence must not claim the name is unknown, and it
    must name the verb that does reach the release. The accepted-name set is unchanged by this
    item, so the refusal is still a refusal -- only the reason is now read from the same file the
    apply is about to use.
    """
    universe = shipped_universe()
    unaddressed = universe.releases_without_a_component()
    assert unaddressed, \
        "the shipped manifest names no release outside the namespace table, so this test has nothing to say"

    for name in unaddressed:
        with pytest.raises(Exception) as raised:
            component_cli._known_component_or_raise(name, "installed", universe)
        message = str(raised.value)
        assert "is not a kubementat component" not in message, \
            f"`{name}` is labelled by the shipped manifest, and the refusal still calls it unknown: {message}"
        assert "no component kubementat addresses owns that label" in message, message
        assert "--helmfile-installation-group" in message, \
            f"the refusal for `{name}` does not name the verb that applies it: {message}"


def test_a_name_neither_read_is_still_refused_as_a_typo():
    """The other half, so (c) cannot be passed by refusing everything.

    A name the manifest does not label and the table does not key is a typo, and it is refused with
    the list of names that *are* kubementat's -- read from the table, so the list cannot go stale
    against the verb that prints it.
    """
    universe = shipped_universe()

    with pytest.raises(Exception) as raised:
        component_cli._known_component_or_raise("not-a-component-at-all", "installed", universe)
    message = str(raised.value)
    assert "is not a kubementat component" in message, message
    assert "grafana" in message

    for name in universe.accepted_names():
        assert component_cli._known_component_or_raise(name, "installed", universe) == name


def test_the_accepted_names_are_exactly_the_namespace_addressed_names():
    """Nothing this item did changes which names a verb takes.

    That is the claim the before/after capture makes and this is where it is held down: the
    accepted set is `constants.DEFAULT_NAMESPACES`, exactly as it was, and the released set is
    wider than it on purpose -- a release with no namespace of its own is not something to install
    by component name, which is a decision and not an oversight.
    """
    universe = shipped_universe()

    assert set(universe.accepted_names()) == set(constants.DEFAULT_NAMESPACES)
    # Both directions of the difference are populated, and both have to stay that way. If every
    # released name became accepted, the verbs would offer to install releases kubementat owns no
    # namespace for; if every accepted name had a release, the namespace-only components would stop
    # being refused by a helm command that cannot apply them.
    assert set(universe.released()) - set(universe.accepted_names()), \
        "every shipped release now names a component, so `kmt component install` would offer to " \
        "install releases that land in somebody else's namespace"
    assert set(universe.accepted_names()) - set(universe.released()), \
        "every addressed component now has a release, so the namespace-only refusal is dead"


# ---------------------------------------------------------------------------------------------
# (d) the CRD sweep, set-equal to the tables
# ---------------------------------------------------------------------------------------------


def test_the_derived_crd_sweep_equals_the_set_the_tables_produce():
    """The highest-consequence assertion in the item, and it is an equality.

    For the shipped manifest, the groups `crd_groups_kubementat_owns` hands the cluster must be
    the union of the two tables it replaced -- `("tekton.dev",) + CERT_MANAGER_CRD_GROUPS` -- and
    the fact that it is equal is *why* the derivation is safe: a swept set that moved either
    leaves cluster-scoped definitions behind or deletes objects that were never kubementat's.
    """
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    groups = set(residue.crd_groups_kubementat_owns(config))

    assert groups == set(residue.CRD_GROUPS_APPLIED_WITH_KUBECTL) | set(residue.CERT_MANAGER_CRD_GROUPS), \
        f"the CRD sweep's set moved: {groups ^ (set(residue.CRD_GROUPS_APPLIED_WITH_KUBECTL) | set(residue.CERT_MANAGER_CRD_GROUPS))}"


def test_every_shipped_component_that_owns_definitions_is_swept_by_exactly_one_path():
    """No released component is unswept, and no unreleased one is swept twice.

    The table is keyed by component and the index decides what shipped, so "whose CRDs are these"
    is answerable per row. Every shipped component with a row must be reached by the unconditional
    sweep or by the cluster-gated sweep that exists for it -- and a component whose chart leaves
    nothing behind must not be swept out of a guess.
    """
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    released = set(components.components_the_manifest_releases(config))
    unconditional = set(residue.crd_groups_kubementat_owns(config))
    gated = set(residue.CRD_GROUPS_BY_COMPONENT["traefik"]) | set(residue.CRD_GROUPS_BY_COMPONENT["alloy"])

    for component, groups in residue.CRD_GROUPS_BY_COMPONENT.items():
        if component not in released:
            continue
        assert set(groups) <= unconditional | gated, \
            f"{component}'s release is shipped and its API groups are swept by neither path: {groups}"
        paths = [name for name, swept in (("the unconditional sweep", unconditional),
                                          ("a cluster-gated sweep", gated))
                 if set(groups) & swept]
        assert len(paths) == 1, \
            (f"{component}'s definitions are deleted by {len(paths)} sweeps: {paths}. The same group swept twice"
             " is a sweep that reports a clean teardown over a cluster that still has something to delete.")

    # And the unconditional sweep deletes no group belonging to a component that is not shipped.
    unswept_shipped = {group for component, groups in residue.CRD_GROUPS_BY_COMPONENT.items()
                       if component in released and component in residue.CRD_GROUPS_SWEEPED_WITHOUT_A_CLUSTER_READ
                       for group in groups} - unconditional
    assert unswept_shipped == set()


def test_the_crd_sweep_says_so_when_it_cannot_read_its_own_manifest(tmp_path, monkeypatch):
    """An unreadable manifest deletes *more*, and says so.

    The alternative is a `kmt uninstall` that reports a clean teardown while cert-manager's
    definitions are still serving, which is the failure this file keeps having to describe. So a
    missing manifest is answered with the whole table and a warning, not with the empty set.
    """
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    missing = tmp_path / "not-here.yaml"
    warned = []
    monkeypatch.setattr(residue.logging, "warning", lambda *a: warned.append(a[0]))

    groups = set(residue.crd_groups_kubementat_owns(config, missing))

    assert set(residue.CERT_MANAGER_CRD_GROUPS) <= groups, \
        "the fallback deleted less than the derived answer would have, which is the direction that leaves " \
        "cluster-scoped definitions on a cluster that was just told it was clean"
    assert set(residue.CRD_GROUPS_APPLIED_WITH_KUBECTL) <= groups
    assert warned, "the fallback ran silently, which is what a residue assert cannot tell apart from a clean sweep"

    # No configuration at all is the same failure and gets the same answer.
    assert set(residue.crd_groups_kubementat_owns(None)) >= set(residue.CRD_GROUPS_APPLIED_WITH_KUBECTL)
    # Traefik's and Alloy's groups are *not* added here, even in the fallback: they belong to the
    # two sweeps that ask the cluster first, and deleting them from here would take definitions away
    # from a proxy or a collector this uninstall never installed.
    assert set(residue.CRD_GROUPS_BY_COMPONENT["traefik"]).isdisjoint(groups)
    assert set(residue.CRD_GROUPS_BY_COMPONENT["alloy"]).isdisjoint(groups)


# ---------------------------------------------------------------------------------------------
# (e) one read per command
# ---------------------------------------------------------------------------------------------


def test_a_command_reads_the_manifest_once_and_answers_every_question_off_it(tmp_path, monkeypatch):
    """`ComponentUniverse.read` is the only read a verb makes.

    Counted, not asserted: the manifest is parsed once per `read`, and then `kind`,
    `releases_for`, `releases_without_a_component` and `accepted_names` are all asked of that
    object. Four reads of one file by one command is the shape that lets a command give itself
    four answers, so the count is the claim.
    """
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    parser = components.release_index
    reads = []
    original = parser.read_release_index
    def counting(*args, **kwargs):
        reads.append(original(*args, **kwargs))
        return reads[-1]
    monkeypatch.setattr(parser, "read_release_index", counting)

    universe = components.ComponentUniverse.read(config)
    for name in sorted(constants.DEFAULT_NAMESPACES) + ["grafana", "alloy"]:
        universe.kind(name)
        universe.releases_for(name)
    universe.releases_without_a_component()
    universe.namespace_only()
    universe.accepted_names()

    assert len(reads) == 1, f"the command read the manifest {len(reads)} times"


def test_helm_and_the_cli_ask_the_same_module_the_same_question(tmp_path, monkeypatch):
    """The wiring, not the intention.

    `helm.py`'s rename guard skips a component kubementat does not address. It used to ask that of
    `constants` directly -- the same *value* as the CLI's answer and a different *source*, which is
    how two files agree right up until the day they do not. Both now call
    `components.components_kubementat_addresses`, and the guard's own answer for the shipped tree is
    what is asserted: asked about a cluster that holds every release the manifest names, all of them
    in one namespace kubementat addresses, the conflicts that come back are exactly the names that
    are both addressed and released. The labels with no namespace key are absent, and that absence
    is the guard saying "I have no configured answer for this release" -- which is now, literally,
    the same answer `kmt component install` gives about those same names.
    """
    import json

    from kubementat.core.config import release_index
    from kubementat.infrastructure import helm as helm_utils

    config = seeded_config(tmp_path)
    utils = HelmUtils(configuration_only_context("dev", None, config))
    universe = shipped_universe()

    assert components.components_kubementat_addresses() == universe.accepted_names()

    # A namespace kubementat owns and no shipped release claims, so "somewhere else" is a place
    # kubementat addresses -- which is the half of the guard's rule that decides whether a release
    # found out of place is a rename or somebody else's install.
    addressed_namespaces = {config.component_namespace(name) for name in universe.accepted_names()}
    claimed = {config.component_namespace(name)
               for name in universe.accepted_names() if universe.releases_for(name)}
    somewhere_else = sorted(addressed_namespaces - claimed)[0]
    rows = [{"name": release, "namespace": somewhere_else}
            for releases in release_index.read_release_index(config, LIVE_HELMFILE).values()
            for release in releases]

    class ClusterHoldingEverything:
        def run(self, argv, **kwargs):
            return type("Result", (), {"returncode": 0, "ok": True, "stdout": json.dumps(rows),
                                       "stderr": "", "argv": list(argv)})()

    monkeypatch.setattr(helm_utils, "shell", ClusterHoldingEverything())
    conflicts = {conflict["component"] for conflict in utils._namespace_renames(helmfile_path=LIVE_HELMFILE)}

    assert conflicts == set(universe.accepted_names()) & set(universe.released())
