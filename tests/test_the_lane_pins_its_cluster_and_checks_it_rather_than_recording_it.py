"""The lane pins its cluster, and says so before it installs anything into it.

D8 used to read "k3s version is recorded, not pinned", and the record was the whole implementation:
`01-provision.sh` installed whatever `get.k3s.io` served that day, the version landed in
`result.json`, and nothing read it. On 2026-11-05 a stable-channel promotion landed on this host as a
guest running `v1.37.2+k3s1` whose `helmcharts.helm.cattle.io` never finished establishing, k3s fataled
every sixty seconds, and the suite died in `01-provision` without ever reaching `02-install`. The
evidence said the version had changed. Nobody had said so.

So the lane now pins k3s, the same way `constants.PINNED_BINARY_VERSIONS` pins helm and helmfile. This
file is the half of that which is not a shell variable: it checks that the pin exists, that it is a
specific release, and that there is an assert that compares it against the guest's own answer. Without
that assert, a promotion still arrives — it just arrives as a mysterious crash instead of a named
failure, which is the difference between a finding and a lost afternoon.

The version itself deliberately lives in the provisioning script and not in a Python constant. That
script is the thing that execs the installer, and a copy of the number in a language that cannot
install k3s is a second place that has to agree with the first.
"""

import re
from pathlib import Path

import pytest

PROVISION_SCRIPT = Path(__file__).resolve().parent / "e2e" / "stages" / "01-provision.sh"

STAGES = Path(__file__).resolve().parent / "e2e" / "harness" / "stages.py"


def test_the_provisioning_script_pins_one_k3s_release():
    """One release, spelled once, in the one file that can install it."""
    assert PROVISION_SCRIPT.is_file(), f"the provisioning script is not where this test reads it: {PROVISION_SCRIPT}"
    script = PROVISION_SCRIPT.read_text()

    declared = re.findall(r'^K3S_VERSION="([^"]+)"', script, re.MULTILINE)
    assert len(declared) == 1, \
        f"exactly one K3S_VERSION belongs in the script, found {declared}"
    pin = declared[0]

    # A tag and not a channel: `v1.36.5+k3s1` is one cluster, while `stable` is a rumour about one.
    assert re.fullmatch(r"v\d+\.\d+\.\d+\+k3s\d+", pin), \
        f"the pin {pin!r} is not a specific k3s release; a channel is not a pin"


def test_the_version_is_read_out_of_the_line_and_not_out_of_a_field():
    """`k3s --version` ends with a commit hash, and that hash is not a version.

    The first implementation took `split()[-1]`, which returns `f6973cb8` for a correctly pinned
    `v1.36.5+k3s1` guest — a comparison that looks exact and is comparing the wrong field, and one that
    fails for *every* guest including the right one.

    The check is a source read and not an import because the unit suite does not put `tests/e2e` on its
    path, and bending it to reach one helper would be the wrong trade: what this guards is that the
    release is *matched* rather than split, and that is visible in the body where the comparison lives.
    """
    body = STAGES.read_text().split("def _k3s_version_drift")[1].split("def _missing_tools")[0]

    assert re.search(r"v\\d\+", body), \
        "the release is not matched out of the line, which leaves a split in place"
    assert "split()[-1]" not in body, \
        "the last field of k3s --version is the commit hash; comparing to it is a green-looking bug"
    assert "is None" in body, \
        "an unparseable line must have a named answer, because 'we do not know' cannot be evidence"


def test_the_provisioning_script_hands_the_pin_past_sudo():
    """`INSTALL_K3S_VERSION=x sudo sh` does not install x, and the first pin did exactly that.

    sudo resets the environment by default, so a variable assigned before `sudo` is dropped before the
    installer's own shell sees it. The guest silently gets the channel default, which reads as a pinned
    lane that is not pinned — and here it read that way in front of a green `node-ready`, because
    `v1.37.2+k3s1` starts cleanly for its first minute before its CRD controller wedges. `sudo env` is
    the spelling that makes the claim true.
    """
    script = PROVISION_SCRIPT.read_text()
    assert "sudo env INSTALL_K3S_VERSION=" in script, \
        "the pin is set before sudo instead of under it, so sudo drops it and the channel decides"
    assert 'INSTALL_K3S_VERSION="$K3S_VERSION" sudo' not in script, \
        "the assignment still sits left of sudo, which is the bug and not the fix"


def test_the_pin_is_a_certified_release_and_not_the_newest_one():
    """The pinned release is the version a full run last went green against.

    This is not a check that can be satisfied by editing. The version is a claim about history, so the
    assert names the release that history recorded, and bumping it is a deliberate act with a green run
    behind it rather than a rebase onto whatever the channel served this morning.
    """
    assert PROVISION_SCRIPT.read_text().count('K3S_VERSION="v1.36.5+k3s1"') == 1, \
        ("the pin moved off v1.36.5+k3s1, the version the last green full run recorded; if it moved "
         "because a full run went green against the new one, that run is the reason this assert can "
         "be updated and not a reason to update it silently")


def test_the_install_stage_checks_the_guest_against_the_pin():
    """Recording is not checking. D8's failure was that the number was written down and read by nobody.

    A mutation that drops the `expect` — keeping the pin and losing the comparison — is exactly the
    regression that let a promoted cluster look like an infrastructure problem instead of a lane that
    had stopped measuring what it thought it was measuring.
    """
    text = STAGES.read_text()
    assert "guest-k3s-matches-the-pinned-release" in text, \
        "the lane records the guest's k3s version but no longer asserts it against the pin"

    # The pin is read out of the script, not duplicated: a Python copy would agree until the day the
    # script moved and this file did not.
    assert "K3S_VERSION=" in text or "_k3s_pinned_version" in text, \
        "the check hardcodes a version instead of reading the one place the number is written"


def test_a_guest_running_an_unpinned_k3s_is_a_failure_and_not_a_warning():
    """The comparison is exact, so a cluster nobody certified cannot pass.

    The message is part of the claim: an operator reading a red `guest-tool-versions-...` line needs to
    know whether the channel promoted or a stale guest survived from an earlier run, and those two
    answers lead to different fixes.
    """
    text = STAGES.read_text()
    body = text.split("def _k3s_version_drift")[1].split("def _missing_tools")[0]
    assert "01-provision.sh pins" in body, \
        "the drift line does not name both sides of the comparison, which is the only usable form"
    assert "self.guest.run" in body, \
        "the check asks the guest rather than trusting what it was handed"
