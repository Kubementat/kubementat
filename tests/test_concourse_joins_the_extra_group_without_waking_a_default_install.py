"""Concourse is an opt-in member of `extra`, and everything a default install does is unchanged.

Three sentences, each held once. (1) The `extra` group is `openbao` and `concourse` -- the group
that keeps releases out of `kmt install` and every lane's `install_groups` has exactly those two
members, so a Concourse that is wanted by nobody changes nothing. (2) Concourse is the one release
that ships *wound off*: `installed:` defaults `false`, which is the opt-in the plan's §4 states,
and its counterpart is that every other release's `installed:` still defaults `true` -- the
default install is not merely unpinned, it is unbothered. (3) What the Concourse chart ships as
its own values it is not: demo private keys and the `test:test` local user are the chart's
defaults, and plan R-2 says they must be unreachable from a kubementat-rendered release, so the
shipped values file overwrites every one of them with emptiness -- this file is what says so
without trusting the comment in the manifest.

The binary half of "defaults unchanged" is measured in
`test_the_manifest_tells_helmfile_which_releases_are_installed.py`, which builds the manifest and
reads the booleans out of helmfile itself; this file is the static half -- the labels, the
winding, the values -- and a census that belongs to *derivation* (the group membership is read off
the blocks, not quoted from a table), for the reason that same family of guards states.

The file that seeds these values is the Phase-1 prototype's working `values.yaml`, stripped of key
material: see `reports/t2-phase1-prototype.md` for the install it came from.
"""

import json
import re

import yaml

from asset_paths import values_path
from kubementat.core import constants

MANIFEST = values_path("helmfile.yaml.gotmpl")
CONCOURSE_VALUES = values_path("ci", "concourse", "values.yaml")


def release_blocks():
    """`{release name: its lines}` from `releases:` down, commented lines dropped.

    Commented out of the rules above, one restatement: a commented release is not a release, and
    the read starts at `releases:` because `repositories:` shares the shape of the block header.
    """
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


def installed_default_of(lines):
    """The line's release's own `installed:` default, or None for a block that declares none.

    Either boolean shape is a default; what must not exist is a default that is not a boolean,
    and the regex is exactly that shape.
    """
    for line in lines:
        found = re.match(r'^\s+installed: \{\{ env "([A-Z0-9_]+)" \| default "(true|false)" \}\}$', line)
        if found:
            return found.group(2)
    return None


# --- (1) the group census ---------------------------------------------------------------------


def test_the_extra_group_is_openbao_and_concourse_and_nothing_else():
    """`group: extra` identifies exactly the two opt-in releases, read off the blocks.

    The census moves when a block moves, which is the point: a table here would rot the first day
    an opt-in arrives or leaves, and the sentence worth keeping is the rule, not the count.
    """
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

    `ci` is the plan's mapping (plan §2: the release row labels it so); `concourse` is what
    `kmt component install <environment> concourse` puts into the `-l` filter. A label that
    disagrees with the name the table addresses is a component that installs under nothing.
    """
    block = release_blocks()["concourse"]
    assert label_of(block, "component") == "ci", block
    assert label_of(block, "component_name") == "concourse", block
    assert label_of(block, "group") == "extra", block


# --- (2) the winding ----------------------------------------------------------------------------


def test_concourse_is_the_one_release_wound_off_by_default():
    """`CONCOURSE_ENABLED` defaults false, and it is the only block that ships that way.

    Both directions, because either alone passes a wrong thing: the opt-in exists (one `false`
    default, and it is concourse's), and the default install is untouched (every other block still
    defaults `true`). What helmfile then builds from these defaults is measured with the binary --
    this guard reads the manifest; the build guard reads the field.
    """
    wound_off = {name for name, lines in release_blocks().items()
                 if installed_default_of(lines) == "false"}
    assert wound_off == {"concourse"}, \
        f"opt-in is spelled one way in this manifest: {sorted(wound_off)}"
    for name, lines in release_blocks().items():
        assert installed_default_of(lines) in ("true", "false"), \
            f"{name}'s installed: default is neither boolean"
    assert installed_default_of(release_blocks()["concourse"]) == "false"
    assert installed_default_of(release_blocks()["openbao"]) == "true", \
        "openbao is opt-in by group and wound-on by default; that distinction belongs to openbao"


def test_concourse_namespace_default_and_constants_agree_and_the_pin_is_the_app_version_pair():
    """Two literals, each stated once elsewhere: namespace -> `constants`, version -> `imageTag`.

    The namespace guard's file already checks all releases against the export and `constants` for
    shape; this is the one line that is not shape, it is the value -- `concourse` defaulting to
    `concourse` is what `DEFAULT_NAMESPACES` says, and the plan's §4 says the same. The version
    half belongs here and not there: 20.3.3 is Concourse 8.3.3, and the chart's `appVersion`
    decides the image tag the values pin, so a bump that moves one line and not the other is
    caught nowhere but here.
    """
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
    """Plan R-2, as a static assert: the chart's demo secrets are unreachable, by emptiness.

    Chart 20.3.3 ships private keys and the `test:test` local user as *values defaults*, so
    "stripped" does not mean "the keys are absent from our file" -- absent means the chart's own
    demo material is what renders. Each one is present here and empty, so the release cannot reach
    the demo half and the chart's own `encryptionKey` gate is what answers for the whole. What
    belongs in these files is the layering story: key material arrives from the operator's
    encrypted layer, which this file states and a plain layer must never be able to carry.
    """
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


def test_the_shipped_values_fix_the_halves_the_plan_fixes():
    """containerd, StatefulSet, persistence, HPA off, ingress off: plan §4's fixed list.

    Not a transcription of a config surface -- a guard on the values that implement it. The
    ephemeral-worker mode (R-5) and the queue-depth autoscaling (R-7) are out for those reasons,
    and the comments in the file are what they say; this says what is fixed: a runtime other than
    containerd is a dead end the prototype measured (`no image plugin configured`), a worker that
    is not a StatefulSet loses its volume on replacement, persistence off is the same failure in
    slow motion, HPA is CPU-only garnish, and ingress is the routing layer's decision, not this
    file's.
    """
    values = yaml.safe_load(CONCOURSE_VALUES.read_text(encoding="utf-8"))
    assert values["worker"]["kind"] == "StatefulSet", values["worker"]
    assert values["concourse"]["worker"]["runtime"] == "containerd", values["concourse"]["worker"]
    assert values["worker"]["persistence"]["enabled"] is True
    assert values["worker"]["persistence"]["size"] == "20Gi"
    assert values["concourse"]["worker"]["autoscaling"] == {}, "HPA off is an explicit value, not silence"
    assert values["concourse"]["web"]["encryption"]["enabled"] is True, \
        "encryption off hands the gate (and the credential story) back to luck"
    assert values["web"]["ingress"]["enabled"] is False, "ingress is a routing-layer answer"


def test_the_namespace_label_step_is_documented_where_an_operator_will_read_it():
    """The PSA gate leaves no native place for its labels, so it leaves a step -- and the step ships.

    Helm's `--create-namespace` (the `createNamespace: true` `helmDefaults` speaks) creates a
    namespace it cannot label, and kubementat ships no Namespace-manifest mechanism that could:
    so this release, alone among the platform components, asks the operator for one line before
    the first install -- and this assertion is what keeps that ask from rotting into a comment
    three directories away. On a `restricted` cluster the workers cannot schedule without it; the
    evidence for both halves is t2's report §6.
    """
    text = CONCOURSE_VALUES.read_text(encoding="utf-8")
    assert "pod-security.kubernetes.io/enforce=privileged" in text, text[-300:]
    assert "pod-security.kubernetes.io/warn=privileged" in text, "enforce without warn is half the label"
