"""Concourse is an opt-in member of `extra`, and everything a default install does is unchanged."""

import json
import re

import yaml
from asset_paths import values_path

from kubementat.core import constants

MANIFEST = values_path("helmfile.yaml.gotmpl")
CONCOURSE_VALUES = values_path("ci", "concourse", "values.yaml")


def release_blocks():
    """`{release name: its lines}` from `releases:` down, commented lines dropped."""
    blocks, current, started = {}, None, False
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


def label_of(lines, key):
    for line in lines:
        found = re.match(r"^\s+%s:\s*(\S+)\s*$" % key, line)
        if found:
            return found.group(1)
    return None


# --- (1) the group census ---------------------------------------------------------------------


def test_the_extra_group_is_openbao_and_concourse_and_nothing_else():
    """`group: extra` identifies exactly the two opt-in releases, read off the blocks."""
    extra = {name for name, lines in release_blocks().items()
             if label_of(lines, "group") == "extra"}
    assert extra == {"openbao", "concourse"}, \
        f"`extra` is supposed to be the opt-in group, and it now says: {sorted(extra)}"
    for name in extra:
        block = release_blocks()[name]
        assert label_of(block, "component_name") == name, \
            f"{name}: a member of `extra` whose component_name is its release name under another key"


def test_concourse_is_labelled_like_every_other_release():
    """`component` and `component_name` are the addresses verbs resolve, so they are asserted here.
    """
    block = release_blocks()["concourse"]
    assert label_of(block, "component") == "ci", block
    assert label_of(block, "component_name") == "concourse", block
    assert label_of(block, "group") == "extra", block


# --- (2) the winding ----------------------------------------------------------------------------


def test_concourse_namespace_default_and_constants_agree_and_the_pin_is_the_app_version_pair():
    """Two literals, each stated once elsewhere: namespace -> `constants`, version -> `imageTag`."""
    block = release_blocks()["concourse"]
    namespace = [line for line in block if re.match(r"^\s*namespace:", line)]
    assert len(namespace) == 1, block
    assert 'env "CONCOURSE_DEPLOYMENT_NAMESPACE" | default "concourse"' in namespace[0], namespace
    assert constants.DEFAULT_NAMESPACES["concourse"] == "concourse"
    version = [line for line in block if re.match(r"^\s*version:", line)]
    assert len(version) == 1, block
    assert 'env "CONCOURSE_VERSION" | default "20.3.3"' in version[0], version
    shipped = yaml.safe_load(CONCOURSE_VALUES.read_text(encoding="utf-8"))
    assert shipped["imageTag"] == "8.3.3", "the chart pin and the image pin must move together"


# --- (3) the values are seeded, not seeded-with-keys ------------------------------------------


def test_the_shipped_values_carry_no_key_material_and_no_test_user():
    """The chart's demo secrets are emptied in the shipped values, so none can be reached."""
    values = yaml.safe_load(CONCOURSE_VALUES.read_text(encoding="utf-8"))
    secrets = values["secrets"]
    assert secrets["create"] is True, "the release manages its own Secret object; the keys come later"
    for key in ("hostKey", "hostKeyPub", "sessionSigningKey", "workerKey", "workerKeyPub",
                "encryptionKey", "localUsers"):
        assert key in secrets, f"the chart's demo default for secrets.{key} is what an absent key renders"
        assert not secrets[key], f"secrets.{key} is not empty: it would reach a rendered release"
    assert not values["concourse"]["web"]["auth"]["mainTeam"]["localUser"], \
        "the chart's main-team default names `test`; the shipped floor is emptiness"
    # The literal `test:test` may sit in this file's *prose* explaining why it is gone; what the
    # guard forbids is the credential, so it reads the parsed data and not the comments.
    assert "test:test" not in json.dumps(values), \
        "the `test:test` default survived as a value somewhere in the shipped values"
