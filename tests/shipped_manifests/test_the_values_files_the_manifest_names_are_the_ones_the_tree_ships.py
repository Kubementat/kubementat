"""Every values file the shipped manifest names is a file the shipped package has."""

import re

import yaml
from asset_paths import values_path

# The packaged root. It is also the working directory `helmfile_paths` hands `helmfile`, for the
# one reason that matters: a relative `values:` reference resolves here.
VALUES_ROOT = values_path()
HELMFILE_MANIFEST = VALUES_ROOT / "helmfile.yaml.gotmpl"

VALUES_REFERENCE = re.compile(r"^\s+-\s+([A-Za-z0-9_][A-Za-z0-9_/.-]*\.yaml)\s*$")


def referenced_values_files():
    """The `values:` paths the live manifest names, in declaration order, commented releases excluded.
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
    """Every `*.yaml` the package ships under the values root, path-relative."""
    return {str(path.relative_to(VALUES_ROOT)).replace("\\", "/")
            for path in VALUES_ROOT.rglob("*.yaml")}


def live_release_blocks():
    """The release blocks the manifest actually applies, each with the references it hands out."""
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
    """The four operator files per release are addressed from outside the package, so the set this file
    guards must be exactly the shipped defaults and nothing else.
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
    """A parser that finds nothing passes a check that proves nothing."""
    assert len(referenced_values_files()) >= 9, referenced_values_files()
    assert HELMFILE_MANIFEST.is_file(), \
        f"the manifest helmfile applies is not at {HELMFILE_MANIFEST}, so every reference below reads nothing"


def test_shipped_and_referenced_are_one_set():
    """Nothing ships that no release reads, and nothing is read that does not ship."""
    referenced = set(referenced_values_files())

    assert shipped_values_files() == referenced, (
        "shipped and referenced have stopped being one set (shipped but unapplied, applied but "
        f"unshipped): {sorted(shipped_values_files() - referenced)}, "
        f"{sorted(referenced - shipped_values_files())}")


def test_the_logging_values_files_are_all_both_shipped_and_referenced():
    """Inside the one directory the logging migration rewrote, shipped and referenced are one set.
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
    """The migration's own half of the rule, stated where a reader will look for it."""
    assert referenced_values_files().count("logging/loki_values.yaml") == 1
    assert referenced_values_files().count("logging/alloy_values.yaml") == 1
    assert "logging/loki_stack_values.yaml" not in referenced_values_files()


def test_the_logging_values_files_are_yaml_and_the_loki_one_says_what_it_thinks_it_is():
    """Both files parse, and the retention number is under the key the chart reads it from."""
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
    """Everything here is filesystem-backed on whatever the cluster already has."""
    words = re.compile(r"(local-path\b|host ?path\b|storageclass|minio|bucketNames|\bs3\b)",
                       re.IGNORECASE)

    for name in ("logging/loki_values.yaml", "logging/alloy_values.yaml"):
        text = (VALUES_ROOT / name).read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        hits = sorted(set(m.group(0) for m in words.finditer(body)))
        assert hits == [], f"{name} names something this install does not own: {hits}"
