"""Every values file the shipped manifest names is a file the shipped package has.

The manifest kubementat applies ships inside the package, and a release's `values:` path
resolves against the directory of the manifest helmfile was handed -- which is the packaged
directory. So a reference is only a reference when `<asset root>/values/<reference>` exists, and
this check is what says so. That is a different claim from "the release block parses", and it is
the claim most likely to pass while lying: a file that moved, lost its suffix, or landed one
directory over still reads as a release, and helmfile answers the apply with chart defaults.

The shape of the failure is the reason to care. A stale reference does not fail with a message
about the file that is missing; helmfile names the release, names the `values` key, and stops. So
the only honest version of this check is one that reads the reference list out of the manifest
rather than comparing two lists somebody wrote down.

Nothing under a configuration directory is expected to hold any of these names. `kmt initialize`
copies neither the manifest nor its values: what the configuration contributes is the operator's
own values, and a file that duplicates a shipped default is the second copy this rule deletes.
"""

import re
from pathlib import Path

import yaml

from asset_paths import values_path

# The packaged root. It is also the working directory `helmfile_paths` hands `helmfile`, for the
# one reason that matters: a relative `values:` reference resolves here.
VALUES_ROOT = values_path()
HELMFILE_MANIFEST = VALUES_ROOT / "helmfile.yaml.gotmpl"

VALUES_REFERENCE = re.compile(r"^\s+-\s+([A-Za-z0-9_][A-Za-z0-9_/.-]*\.yaml)\s*$")


def referenced_values_files():
    """The `values:` paths the live manifest names, in declaration order, commented releases excluded.

    A commented release is not a release, so a commented `- path/to/values.yaml` line is a
    note about a chart nobody installs and not a reference. Same rule
    `test_component_namespaces_come_from_configuration.py` applies to namespaces and labels, same
    reason: reading the file is not the same as trusting the file.
    """
    referenced = []
    for line in HELMFILE_MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        found = VALUES_REFERENCE.match(line)
        if found:
            referenced.append(found.group(1))
    return referenced


def shipped_values_files():
    """Every `*.yaml` the package ships under the values root, path-relative.

    Deliberately a glob and not the reference list: comparing a list to itself is how a guard like
    this one passes while telling you nothing, so this half walks the tree.
    """
    return {str(path.relative_to(VALUES_ROOT)).replace("\\", "/")
            for path in VALUES_ROOT.rglob("*.yaml")}


def live_release_blocks():
    """The release blocks the manifest actually applies, each with the references it hands out.

    Both halves are read from the block rather than from a list someone wrote down, for the reason
    this file states about the packaged references: the assertion is about what the file says, and a
    transcription of it stops being an assertion the moment someone edits the file.
    """
    blocks = []
    current = None
    for line in HELMFILE_MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        started = re.match(r"^- name:\s*(\S+)", line)
        if started:
            current = {"name": started.group(1), "component_name": None,
                       "shipped": [], "operator": None}
            blocks.append(current)
            continue
        if current is None:
            continue
        labelled = re.match(r"^\s+component_name:\s*(\S+)", line)
        if labelled:
            current["component_name"] = labelled.group(1)
        found = VALUES_REFERENCE.match(line)
        if found:
            current["shipped"].append(found.group(1))
        layer = re.search(r'template "kubementat-operator-values" "([^"]+)"', line)
        if layer:
            current["operator"] = layer.group(1)
    return [block for block in blocks if block["shipped"] or block["operator"]]


def test_the_shipped_set_does_not_gain_a_member_from_the_operator_layout():
    """The four operator files per release are addressed from outside the package, so the set this
    file guards must be exactly the shipped defaults and nothing else.

    Two ways to get this wrong, and they fail in opposite directions. Dropping a
    `values/<component>.yaml` next to the packaged defaults to make an operator error go away grows
    the shipped set and makes `test_shipped_and_referenced_are_one_set` fail in a way that looks like
    a stray file. Naming an operator layer as though it were a shipped path grows the *reference*
    list, which is the direction that installs nothing while looking satisfied -- the exact failure
    `loki-stack` arrived with. This reads the manifest's own printf format strings, which is where
    the operator half now lives.
    """
    body = HELMFILE_MANIFEST.read_text(encoding="utf-8")
    formats = [fmt for fmt in re.findall(r'printf "([^"]+)"', body) if "values" in fmt]

    assert formats, "the operator layers are no longer addressed through the exports by printf"
    for fmt in formats:
        assert fmt.startswith("%s/") or "%s/" in fmt, \
            f"{fmt} addresses a layer without substituting a coordinate"
        assert "%s" in fmt.split("values")[0], \
            f"{fmt} hard-codes a coordinate instead of substituting the export that names it"

    for block in live_release_blocks():
        assert block["operator"], f"{block['name']} reads no operator layers at all"
        assert all(ref.endswith(".yaml") and ".encrypted" not in ref for ref in block["shipped"]), \
            f"{block['name']} names a shipped reference that is not a packaged default: {block['shipped']}"


def test_every_live_release_block_hands_both_halves_to_helmfile():
    """Each block names the packaged default it reads *and* the one name the operator layers go
    through.

    This is the operator half of the sentence this file already states about the packaged half. It
    matters because the packaged references are the ones whose absence a glob catches, while layers
    that quietly stopped being read are invisible to every other check in the suite: the apply
    succeeds, nothing warns, and the configuration the operator wrote does nothing.
    """
    blocks = live_release_blocks()

    assert len(blocks) >= 9, f"only {len(blocks)} release blocks were found, fewer than the manifest ships"
    for block in blocks:
        assert block["component_name"], f"{block['name']} carries no component_name label"
        assert block["shipped"], f"{block['name']} reads no packaged default: the product half is gone"
        assert block["operator"] == block["component_name"], (
            f"{block['name']} hands the operator layers {block['operator']!r} while its "
            f"component_name is {block['component_name']!r}")


def test_the_manifest_references_values_files_at_all():
    """A parser that finds nothing passes a check that proves nothing.

    Ten releases, ten references. If a later edit moves values into `--set` arguments this list
    shrinks toward empty and the rest of this file has to be rethought rather than left holding an
    assertion about an empty list.
    """
    assert len(referenced_values_files()) >= 9, referenced_values_files()
    assert HELMFILE_MANIFEST.is_file(), \
        f"the manifest helmfile applies is not at {HELMFILE_MANIFEST}, so every reference below reads nothing"


def test_every_values_file_the_manifest_names_resolves_from_the_packaged_root():
    """The reference has to be a file in the directory helmfile runs in. Nothing else is consulted.

    This is the check that stands or falls here. Before it, the reference resolved against a copy
    `initialize` had dropped into `platform_config/{environment}/kubementat_components/`, so the
    file's existence and the manifest's content were both the operator's problem; now both are the
    package's, and a reference that lost its file is answered here rather than by an apply that
    silently installs chart defaults.
    """
    missing = sorted(reference for reference in referenced_values_files()
                     if not (VALUES_ROOT / reference).is_file())

    assert missing == [], "the manifest names these values files and the package does not ship them"


def test_shipped_and_referenced_are_one_set():
    """Nothing ships that no release reads, and nothing is read that does not ship.

    A values file the manifest does not name is not inert. It is a file an operator can find, open,
    and edit, on the strength of a chart nobody installs -- which is exactly the orphan failure
    `loki-stack` arrived with and which the migration of 2026-10-31 deleted rather than archived.
    The other direction is the same failure seen from the release: a reference with no file behind
    it installs defaults while the manifest looks satisfied.
    """
    referenced = set(referenced_values_files())

    assert shipped_values_files() == referenced, (
        "shipped and referenced have stopped being one set (shipped but unapplied, applied but "
        f"unshipped): {sorted(shipped_values_files() - referenced)}, "
        f"{sorted(referenced - shipped_values_files())}")


def test_no_values_file_lingers_under_the_templates_root():
    """The `.template` suffix is gone from these files, so nothing may carry it here.

    A file under `templates/` reads as material `initialize` copies, and there is no longer an
    `initialize` that copies any of them. The two names would be two files, and only one is read.
    """
    from asset_paths import template_path

    stale = sorted(str(path) for path in
                   template_path("environment", "kubementat_components").rglob("*.yaml*")
                   if "values" in path.name)
    assert stale == [], f"values templates survive under the templates root: {stale}"


def test_the_logging_values_files_are_all_both_shipped_and_referenced():
    """Inside the one directory the logging migration rewrote, shipped and referenced are one set.

    The whole-tree check above is the general rule; this is its scoped ancestor, kept because the
    failure it was written against was a migration that rewrote a file under one name and repointed
    the release at another. `loki-stack` -> `loki` arrived exactly that way, and the old
    the loki-stack values file had to be deleted rather than kept "in case": a file nobody
    references is not inert, it is material somebody will edit for a chart the product no longer
    ships.
    """
    shipped = {str(path.relative_to(VALUES_ROOT)).replace("\\", "/")
               for path in (VALUES_ROOT / "logging").rglob("*.yaml")}
    referenced = {name for name in referenced_values_files() if name.startswith("logging/")}

    assert shipped == {"logging/loki_values.yaml",
                       "logging/alloy_values.yaml"}, shipped
    assert shipped == referenced, ("shipped and referenced disagree over logging/ (shipped but "
                                   f"unapplied, applied but unshipped): "
                                   f"{sorted(shipped - referenced)}, {sorted(referenced - shipped)}")


def test_the_loki_and_alloy_values_files_are_the_two_the_logging_releases_read():
    """The migration's own half of the rule, stated where a reader will look for it.

    Loki came off `loki-stack` with its values file rewritten rather than renamed forward, because
    chart 20 will not accept the old one (`additional properties 'promtail' not allowed`, and
    `/loki/config: got object, want string`). Alloy arrived with a values file of its own. Both names
    are therefore load-bearing, and this names the pair so a later tidy-up cannot quietly point one
    release at the other's file.
    """
    assert referenced_values_files().count("logging/loki_values.yaml") == 1
    assert referenced_values_files().count("logging/alloy_values.yaml") == 1
    assert "logging/loki_stack_values.yaml" not in referenced_values_files()


def test_the_logging_values_files_are_yaml_and_the_loki_one_says_what_it_thinks_it_is():
    """Both files parse, and the retention number is under the key the chart reads it from.

    This is the unit-side half of "retention is the point of the migration". It cannot prove that
    `limits_config.retention_period` deletes anything -- only a rendered install and fourteen days can
    do that -- but it does say that the value a reader will look for is there, under the key the chart
    passes through, and that a file which lost its indentation still fails here loudly.
    """
    loki = yaml.safe_load(
        (VALUES_ROOT / "logging/loki_values.yaml").read_text())
    alloy = yaml.safe_load(
        (VALUES_ROOT / "logging/alloy_values.yaml").read_text())

    assert loki["loki"]["limits_config"]["retention_period"] == "336h"
    assert loki["loki"]["compactor"]["retention_enabled"] is True
    # The gateway would answer on a different address than the Grafana datasource uses.
    assert loki["gateway"]["enabled"] is False
    # The chart's Monolithic default is zero replicas, so this line is what makes the release exist.
    assert loki["singleBinary"]["replicas"] == 1

    assert alloy["controller"]["type"] == "daemonset"
    assert "loki.loki.svc.cluster.local:3100" in alloy["alloy"]["configMap"]["content"], \
        "the collector has to write to the address the Grafana datasource reads"


def test_no_logging_values_file_names_a_storage_class_or_an_object_store():
    """Everything here is filesystem-backed on whatever the cluster already has.

    A StorageClass named by literal is the failure
    `test_the_routing_values_file_never_writes_a_service_type_or_a_storage_class.py` states for
    traefik, and it is a failure here for the same reason: kubementat's storage class is a
    configuration key, not a string in a values file. Object storage is out for the other half of the
    same reason -- MinIO or S3 "for the next step" is a second storage layout maintained from the
    first, and this migration was told to leave it alone.
    """
    words = re.compile(r"(local-path\b|host ?path\b|storageclass|minio|bucketNames|\bs3\b)",
                       re.IGNORECASE)

    for name in ("logging/loki_values.yaml", "logging/alloy_values.yaml"):
        text = (VALUES_ROOT / name).read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        hits = sorted(set(m.group(0) for m in words.finditer(body)))
        assert hits == [], f"{name} names something this install does not own: {hits}"
