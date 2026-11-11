"""Every release the manifest names carries both of its knobs, and no more than that.

The read path only works if the template asks. `Config` answers `<COMPONENT>_ENABLED` and
`<COMPONENT>_VERSION`, `export_component_release_knobs` writes them into the environment, and
`helmfile.yaml.gotmpl` is the side that has to *use* them -- one `installed:` line and one quoted
`version:` line per release, with the manifest's own pin as the `default`. This file is that guard,
and it is the one that fails when someone adds a release and forgets the two lines next to it, which
is the same failure that would otherwise be caught in a lane twenty minutes later.

Two shapes are asserted and not one, because either alone would let the wrong thing through:

* **The lines exist, one per release, and use the key the component's own name produces.** Read off
  `component_release_index()` and not off a list here, so a release added to the manifest is checked
  the way it becomes installable -- for free, by the file that decides both.
* **The `default` is still the pin kubementat ships.** The templated line is where the literal lives
  now, so a rewrite that "just" reflows a block or bumps a chart without saying so lands here. The
  table below is the re-derivation the pin has to come with; the fix when it moves is to re-read the
  chart index the block names, not to update the row.

The shapes are not incidental: `installed:` is unquoted because helmfile 1.7.0 puts an unquoted
`true`/`false` into its boolean field and a quoted `"false"` into nothing, and `version:` is quoted
because `1.0` otherwise lands as a number. Both were measured on this machine, and the second test
below asserts them as shapes rather than trusting the comment that says so.

Both knobs are answered from configuration, and the manifest is the entry point and so the owner of
the version literals.
"""

import re

import pytest

from asset_paths import values_path
from cluster_fixture import UNREAD_ROOT, configuration_only_context
from kubementat.config import Config, component_config_key
from kubementat.helm_utils import HelmUtils

MANIFEST = values_path("helmfile.yaml.gotmpl")
SUFFIXES = ("ENABLED", "VERSION")

# The chart version kubementat ships, one row per release the manifest names. Each row is the pin the
# release block carried before it was templated, which is the number the block's own comment dates to
# the index re-read of 2026-10-31 -- which is why the rows and the blocks agree and what to do when
# they do not: re-read the chart index named in that block, then change both.
SHIPPED_CHART_VERSIONS = {
    "prometheus": "29.40.1",
    "prometheus-blackbox-exporter": "11.20.2",
    "prometheus-helm-exporter": "1.4.0+e2eee28",
    "grafana": "14.2.0",
    "loki": "20.2.0",
    "alloy": "1.15.0",
    "polaris": "6.0.1",
    "cert-manager": "v1.21.3",
    "traefik": "42.1.3",
    "openbao": "0.30.6",
}

# `installed:` unquoted, so helmfile's boolean field gets a boolean; `version:` quoted, so a version
# that reads like `1.0` survives as the string helm resolves.
INSTALLED_KNOB = re.compile(r'^\s+installed: \{\{ env "(?P<key>[A-Z0-9_]+)" \| default "true" \}\}$')
VERSION_KNOB = re.compile(r'^\s+version: "\{\{ env "(?P<key>[A-Z0-9_]+)" \| default "(?P<version>.*)" \}\}"$')
# The shape either knob loses by being written the other way round: a quoted `installed` is a string
# helmfile does not resolve, and an unquoted `version` is a number.
INSTALLED_LITERAL = re.compile(r"^\s+installed:\s*(?P<value>\S.*?)\s*$")
VERSION_LITERAL = re.compile(r"^\s+version:\s*(?P<value>\S.*?)\s*$")


def live_index():
    """`{component_name label: releases}` for the manifest this suite ships.

    The same read `helmfile_apply` and `kmt component install` make, so the set of releases this
    guard can check is the set of releases that exist.
    """
    return HelmUtils(configuration_only_context(
        "dev", "dev1", Config("dev", "dev1", platform_config_dir=UNREAD_ROOT,
                              preload_configs=False))).component_release_index(MANIFEST)


def release_blocks():
    """`{release name: its lines}` from `releases:` down, commented lines dropped.

    Two things this parser has to get right, both of them the same mistakes `component_release_index`
    makes a point of avoiding. A `repositories:` entry has a `- name:` line too and carries no labels,
    so the read starts at `releases:` and not at the top of the file. And a commented line is not a
    release and not a line the apply reads -- the same rule that index parses with, and the reason it
    is repeated here rather than trusted: a guard that read a commented `version:` as a pin would keep
    the check honest only while nothing ever edited a block.
    """
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
    indexed = {}
    for release, lines in release_blocks().items():
        indexed.setdefault(label_of(lines), []).append((release, lines))
    assert None not in indexed, \
        f"these releases declare no component_name label, so no knob can be addressed to them: " \
        f"{indexed[None]}"
    return indexed


# --- every release carries both lines ------------------------------------------------------


def test_the_releases_the_manifest_names_are_the_rows_this_guard_knows():
    """The labels and the version table are the same set, so a release cannot arrive without a check.

    Add one to the manifest and this fails with the release's name in it, which is the only shape of
    failure that sends whoever added it to the right place: the two lines, and the pin to re-derive.
    """
    assert sorted(live_index()) == sorted(SHIPPED_CHART_VERSIONS)
    assert not [label for label, releases in live_index().items() if len(releases) > 1], \
        "a label on more than one release makes a component's own knob decide twice"


@pytest.mark.parametrize("component", sorted(SHIPPED_CHART_VERSIONS))
def test_every_release_the_manifest_names_carries_both_knobs(component):
    """Both templated lines, exactly one of each, addressed by the key this component's name produces."""
    labelled = [entry for entry in blocks_by_label().get(component, [])]
    assert labelled, f"{component} is in the index but no release block carries its label"

    for release, lines in labelled:
        installed = [INSTALLED_KNOB.match(line) for line in lines if INSTALLED_KNOB.match(line)]
        version = [VERSION_KNOB.match(line) for line in lines if VERSION_KNOB.match(line)]
        assert len(installed) == 1, f"{release} has {len(installed)} templated installed: lines"
        assert len(version) == 1, f"{release} has {len(version)} templated version: lines"
        assert installed[0].group("key") == component_config_key(component, "ENABLED")
        assert version[0].group("key") == component_config_key(component, "VERSION")
        assert version[0].group("version") == SHIPPED_CHART_VERSIONS[component], \
            f"{release} resolves {version[0].group('version')!r} as its default and not " \
            f"{SHIPPED_CHART_VERSIONS[component]!r}: the pin moved, and the row here moves with it"


@pytest.mark.parametrize("component", sorted(SHIPPED_CHART_VERSIONS))
def test_no_release_carries_a_second_knob_line(component):
    """One `installed:` and one `version:` per release, and nothing else that answers either.

    This is the failure the coverage test above cannot see: a block that answers a knob *twice* -- the
    templated line plus the literal someone replaced it from -- still carries a correct templated
    line, so a check that only looks for the right line finds it and passes. helmfile gets a mapping
    with a duplicate key, and which answer it takes is a coin toss.
    """
    for release, lines in blocks_by_label().get(component, []):
        installed = [line for line in lines if INSTALLED_LITERAL.match(line)]
        version = [line for line in lines if VERSION_LITERAL.match(line)]
        assert len(installed) == 1, f"{release} answers installed: {installed}"
        assert len(version) == 1, f"{release} answers version: {version}"


def test_the_only_knob_variables_the_manifest_reads_are_the_exported_ones():
    """No template line invents a knob name, because a name the export never writes is a dead line.

    The other direction of the silently dead key: here it is the file that has the key, and the
    process environment that never will.
    """
    read = set()
    for lines in release_blocks().values():
        for line in lines:
            for pattern in (INSTALLED_KNOB, VERSION_KNOB):
                found = pattern.search(line)
                if found:
                    read.add(found.group("key"))
    assert read, "no release line reads a knob at all, so this guard is reading nothing"
    allowed = {component_config_key(label, suffix)
               for label in live_index() for suffix in SUFFIXES}
    assert read <= allowed, sorted(read - allowed)


def test_the_two_knob_lines_are_written_the_way_their_types_need():
    r"""`installed:` unquoted and `version:` quoted, line by line.

    Not a restatement of the coverage test, which reads one release's lines against a shape: this one
    asks what YAML does with the answer. A quoted `installed:` is worse than nothing, because it
    parses as a string and helmfile's boolean field never sees it. An unquoted `version:` turns a
    version that reads like `1.0` into the number one, which is a chart helm has never been offered.
    Both were measured on helmfile 1.7.0, so they are asserted as shapes and not trusted to the
    comment in the manifest that says them.
    """
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
