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
* **The `default` is still a version, and is the only one the release block offers.** The templated
  line is where the literal lives, so it is read back off the block rather than quoted from a table
  here: a rewrite that "just" reflows a block, or a pin that lands as a boolean or a number,
  fails on the shape rather than on a row that was written down and left to rot.

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
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key
from kubementat.infrastructure.helm import HelmUtils

MANIFEST = values_path("helmfile.yaml.gotmpl")
SUFFIXES = ("ENABLED", "VERSION")

# The version kubementat resolves for a release when configuration says nothing, read off the
# manifest's own templated `default` for that component rather than written down here. A table in this
# file would be a second owner of the pin, and the only failure worth catching -- a block reflowed or
# bumped without the check moving with it -- is a failure a copied table cannot see, because the copy
# stays green while the file it claims to quote moves. So the set of components this guard can check,
# and the version each one resolves to, come from the same read of the shipped artefact that
# `helmfile_apply` makes.

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
    """`{component: the version its own release block resolves}` -- the pins, read off the artefact.

    The `default` inside the templated `version:` line is the only place a shipped chart version is
    written, so it is read there and nowhere else: a bumped or reflowed block moves this answer with
    it, which is the whole point of not writing the table out a second time.
    """
    pins = {}
    for label, entries in _blocks_by_label().items():
        for _release, lines in entries:
            templated = [VERSION_KNOB.match(line) for line in lines if VERSION_KNOB.match(line)]
            if len(templated) == 1:
                pins[label] = templated[0].group("version")
    return pins


# Read once, for the parametrised rows below. A component that reaches the manifest arrives here and
# in the index, and the first test says the two answers have to be the same set.
CHART_VERSIONS_FROM_MANIFEST = shipped_chart_versions()


# --- every release carries both lines ------------------------------------------------------


def test_the_releases_the_manifest_names_are_the_rows_this_guard_knows():
    """The labels the manifest declares and the index the apply reads are the same set, once each.

    Add one to the manifest and this fails with the release's name in it, which is the only shape of
    failure that sends whoever added it to the right place: the two lines. Nothing is quoted from a
    table here, so nothing can be green while the row it claimed to check has moved.
    """
    labels = {label for label in _blocks_by_label() if label is not None}
    assert sorted(labels) == sorted(live_index()), \
        (f"the release blocks and the index the apply reads disagree: "
         f"only in the blocks {sorted(labels - set(live_index()))}, "
         f"only in the index {sorted(set(live_index()) - labels)}")
    assert not [label for label, releases in live_index().items() if len(releases) > 1], \
        "a label on more than one release makes a component's own knob decide twice"


@pytest.mark.parametrize("component", sorted(CHART_VERSIONS_FROM_MANIFEST))
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
        # The pin itself is read off this same line, so what is asserted is that the line answers:
        # a version, quoted, and not the empty string a block loses when its default is dropped.
        default = version[0].group("version")
        assert default.strip() == default and default, \
            f"{release} resolves {default!r} as its version default, which is not a version"
        assert "{{" not in default, f"{release} nests a second substitution inside its version default"
        assert default not in ("true", "false"), \
            f"{release} resolves {default!r}, which helmfile reads as a boolean and not as a chart"


@pytest.mark.parametrize("component", sorted(CHART_VERSIONS_FROM_MANIFEST))
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
