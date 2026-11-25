"""Every release the manifest names carries both of its knobs, and no more than that."""

import re

import pytest
from asset_paths import values_path
from cluster_fixture import UNREAD_ROOT, configuration_only_context

from kubementat.core.config.config import Config
from kubementat.infrastructure.helm import HelmUtils

MANIFEST = values_path("helmfile.yaml.gotmpl")
SUFFIXES = ("ENABLED", "VERSION")

# The version kubementat resolves comes from `versions.yaml`, exported by `helmfile_apply`; what
# this file can state from the artefact is the binding: the release block names the knob and keeps
# no number of its own. Writing the set of components and the resolution path down as a table would
# be a second owner — and the only failure worth catching, a block reflowed or a line lost, is a
# failure a copied table cannot see, so the set this guard can check is read off the same artefact.
# A version literal inside this pattern is the shape the census test below refuses.

VERSION_KNOB = re.compile(r'^\s+version: "\{\{ env "(?P<key>[A-Z0-9_]+)_VERSION" \}\}"$')
# The shape either knob loses by being written the other way round: a quoted `installed` is a string
# helmfile does not resolve, and an unquoted `version` is a number.
INSTALLED_LITERAL = re.compile(r"^\s+installed:\s*(?P<value>\S.*?)\s*$")
VERSION_LITERAL = re.compile(r"^\s+version:\s*(?P<value>\S.*?)\s*$")


def live_index():
    """`{component_name label: releases}` for the manifest this suite ships."""
    return HelmUtils(configuration_only_context(
        "dev", "dev1", Config("dev", "dev1", platform_config_dir=UNREAD_ROOT,
                              preload_configs=False))).component_release_index(MANIFEST)


def release_blocks():
    """`{release name: its lines}` from `releases:` down, commented lines dropped."""
    blocks = {}
    current = None
    started = False
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        if line.strip() == "releases:":
            started = True
            continue
        if not started:
            continue
        declared = re.match(r"^- name:\s*(\S+)\s*$", line)
        if declared:
            current = declared.group(1)
            blocks.setdefault(current, [])
            continue
        if current is not None:
            blocks[current].append(line)
    return blocks


def label_of(lines):
    """The `component_name` label a block declares, or `None` for a block that declares none."""
    for line in lines:
        labelled = re.match(r"^\s+component_name:\s*(\S+)\s*$", line)
        if labelled:
            return labelled.group(1)
    return None


def blocks_by_label():
    """`{label: [(release, lines), ...]}` for the manifest, which is what the assertions iterate."""
    indexed = _blocks_by_label()
    assert None not in indexed, \
        f"these releases declare no component_name label, so no knob can be addressed to them: " \
        f"{indexed[None]}"
    return indexed


def _blocks_by_label():
    """The same mapping, without the assertion, so it can be read while the suite is collecting."""
    indexed = {}
    for release, lines in release_blocks().items():
        indexed.setdefault(label_of(lines), []).append((release, lines))
    return indexed


def shipped_chart_versions() -> dict:
    """`{component: the version knob its release block binds}` — the manifest binds, it does not answer.

    A component whose block lost or reflowed its `version:` line drops out of this set, so the census
    test below says the set and the release index still agree; the parametrised rows then have exactly
    the components the manifest names to check against.
    """
    bindings = {}
    for label, entries in _blocks_by_label().items():
        for _release, lines in entries:
            templated = [VERSION_KNOB.match(line) for line in lines if VERSION_KNOB.match(line)]
            if len(templated) == 1:
                bindings[label] = templated[0].group("key")
    return bindings


# Read once, for the parametrised rows below. A component that reaches the manifest arrives here and
# in the index, and the first test says the two answers have to be the same set.
VERSION_BINDINGS_FROM_MANIFEST = shipped_chart_versions()


# --- every release carries both lines ------------------------------------------------------


@pytest.mark.parametrize("component", sorted(VERSION_BINDINGS_FROM_MANIFEST))
def test_no_release_carries_a_second_knob_line(component):
    """One `installed:` and one `version:` per release, and nothing else that answers either."""
    for release, lines in blocks_by_label().get(component, []):
        installed = [line for line in lines if INSTALLED_LITERAL.match(line)]
        version = [line for line in lines if VERSION_LITERAL.match(line)]
        assert len(installed) == 1, f"{release} answers installed: {installed}"
        assert len(version) == 1, f"{release} answers version: {version}"


def test_the_two_knob_lines_are_written_the_way_their_types_need():
    """`installed:` unquoted and `version:` quoted, line by line."""
    seen = 0
    for entries in blocks_by_label().values():
        for release, lines in entries:
            for line in lines:
                installed = INSTALLED_LITERAL.match(line)
                if installed:
                    seen += 1
                    assert not installed.group("value").startswith('"'), \
                        f"{release}'s installed: is quoted, so helmfile reads a string and not a boolean"
                version = VERSION_LITERAL.match(line)
                if version:
                    seen += 1
                    assert version.group("value").startswith('"'), \
                        f"{release}'s version: is not quoted, so a version that reads like 1.0 is a number"
    assert seen == 2 * sum(len(entries) for entries in blocks_by_label().values())


# --- the census that moved into the product as a behaviour guard -----------------------------


def test_every_release_the_manifest_names_carries_a_version_line():
    """The file's name is the claim: no block may lose its line and stay checked by nothing."""
    assert set(VERSION_BINDINGS_FROM_MANIFEST) == set(blocks_by_label()), (
        "the releases this guard can see and the releases the manifest labels disagree: "
        f"{sorted(set(blocks_by_label()) - set(VERSION_BINDINGS_FROM_MANIFEST))}")


def test_no_version_line_keeps_a_default_literal_the_product_no_longer_ships_in_it():
    """Where a version answers is product shape, and every `*_VERSION` line that still hedges with
    a literal is a second owner of that shape.

    This is the census of the move turned into a guard: not "the manifest agrees with the file" — a
    copy that agrees is exactly the bug — but "the manifest binds and never answers".
    """
    offenders = [line for line in MANIFEST.read_text(encoding="utf-8").splitlines()
                 if re.search(r'_VERSION"?\s*\|\s*default\s*"', line)]
    assert offenders == [], \
        "a version literal is back in the manifest; versions.yaml owns it: " + "\n".join(offenders)
