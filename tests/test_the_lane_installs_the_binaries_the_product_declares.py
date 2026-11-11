"""What the lane installs is what `constants.PINNED_BINARY_VERSIONS` declares, or the lane is quiet.

A lane that runs binaries the product does not declare is not evidence about the product. Measured:
the full/full lane recorded `tool_versions` of helm **3.22.1** and helmfile **1.8.2** while the
checkout declared 4.3.1 and 1.7.0 -- green, because nothing compared the two. The disagreement lived
in a JSON file nobody reads. The owner decision of 2026-10-30 is both halves: install what the table
declares, and name the drift when the guest ends up elsewhere.

Two mechanisms, and the file says which one is which:

1. **`01-provision.sh` reads the table** rather than carrying a literal. It is a guest script, so it
   cannot import the package: it resolves the checkout it was mounted with and reads
   `constants.PINNED_BINARY_VERSIONS` with plain `python3` (the module imports nothing). A checkout
   that cannot be read is an error and not a licence to guess a version.
2. **Stage 01 says so** when what the guest runs is not what its own checkout declares, rather than
   leaving it as `tool_versions` (see `harness/stages.py`).

The guard below tests the mechanism and not a copy of it: it extracts the script's own `pin` function
and runs it against the real `constants.py`. A test that re-implemented the read in Python would pass
even if the script's read were broken, which is the only kind of parity test that matters here.

`tests/e2e/stages/01-provision.sh` is the file this guards, and `constants.py` is the other side.
"""

import json
import re
import subprocess
from pathlib import Path

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

from kubementat import constants

stages = load_harness()

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVISION_SCRIPT = REPO_ROOT / "tests" / "e2e" / "stages" / "01-provision.sh"

# A version written down where a version must be interpolated. Two shapes and not one, because a
# pinned URL appears both as `helm-v4.3.1-...` and as `helmfile_1.8.2_...`.
VERSION_LITERAL = re.compile(r"\d+\.\d+\.\d+")


def install_lines(script_text: str) -> list:
    """The lines that actually cause a helm/helmfile download, comments and `log` calls aside.

    Prose about the drift is not a version being installed. This returns only the commands.
    """
    lines = []
    for line in script_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if "get.helm.sh/helm-v" in stripped or "helmfile/releases/download/v" in stripped:
            lines.append(stripped)
    return lines


def code_lines(script_text: str) -> str:
    """The script without its comments -- what it does, and the only half a guard may read."""
    return "\n".join(line for line in script_text.splitlines() if not line.strip().startswith("#"))


def shell_function(text: str, name: str) -> str:
    """One shell function's text, read out of the script it is defined in.

    Read and not re-typed: the point of the guard is that the *script's* read resolves.
    """
    start = text.index("\n%s() {\n" % name) + 1
    remainder = text[start:]
    end = remainder.index("\n}\n")
    return remainder[:end + 3]


def ask_the_script_to_pin(name: str) -> str:
    """Run the script's own `pin` for `name`, against the real checkout, exactly as the guest does."""
    function = shell_function(PROVISION_SCRIPT.read_text(), "pin")
    command = '%s\nPIN_SOURCE="%s"\npin %s\n' % (function, REPO_ROOT, name)
    answer = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert answer.returncode == 0, "the stage's own read of constants.py failed: %s" % answer.stderr
    return answer.stdout.strip()


# ---------------------------------------------------------------------------------------
# the two halves of the decision, on the install side
# ---------------------------------------------------------------------------------------


def test_the_pinned_binaries_are_the_ones_the_provisioning_stage_downloads():
    """Every download line interpolates the pin, and no download line carries a version.

    The sentence that fails here is the failure the owner named: a URL with a version in it is a
    version nobody declared, and the lane measures the guest image instead of the product.
    """
    text = PROVISION_SCRIPT.read_text()
    lines = install_lines(text)

    assert lines, "no helm or helmfile download in the provisioning stage: the guard is guarding nothing"
    for line in lines:
        literal = VERSION_LITERAL.search(line)
        assert literal is None, (
            "the provisioning stage installs %s as a literal, and constants.PINNED_BINARY_VERSIONS"
            " says %s -- a lane that runs a version the product does not declare is evidence about"
            " the guest image, not about kubementat. Read the pin: `pin helm` / `pin helmfile`."
            % (literal.group(0), constants.PINNED_BINARY_VERSIONS))
    assert "${HELM_VERSION}" in "".join(lines), "the helm download does not interpolate the pin"
    assert "${HELMFILE_VERSION}" in "".join(lines), "the helmfile download does not interpolate the pin"


def test_the_provisioning_stage_reads_the_table_rather_than_assuming_a_version():
    """The script's own `pin` resolves both pinned binaries out of the real `constants.py`.

    The mechanism has to work and not merely be described. Running the script's function against the
    real table is what makes this a parity test: change `constants.py` and this changes with it,
    which is the whole point of reading instead of writing.
    """
    for binary, pinned in sorted(constants.PINNED_BINARY_VERSIONS.items()):
        assert ask_the_script_to_pin(binary) == pinned, (
            "the stage cannot pin %s; %s declares %s" % (binary, "constants.py", pinned))


def test_a_checkout_that_cannot_be_read_is_an_error_and_not_a_default_version():
    """No table, no install. A fallback version would be the drift this stage is written to stop."""
    code = code_lines(PROVISION_SCRIPT.read_text())
    assert "exit 4" in code, "the stage reads a version from nowhere when constants.py is missing"
    assert 'pin helm' in code and "pin helmfile" in code, \
        "the stage must go through the table for both pinned binaries"


def test_the_provisioning_stage_does_not_hand_helm_over_to_an_installer_that_picks_the_version():
    """`get-helm-3` is helm 3's installer and cannot fetch the helm 4 the table declares.

    It is also the thing that produced the drift: an installer that resolves "latest" for us is a
    version nobody declared, arriving from the network. The tarball URL is the only form that is
    identical on both majors and whose version is the one the table gives. Read from the code and not
    the prose: the script is allowed to explain the mistake while not making it.
    """
    code = code_lines(PROVISION_SCRIPT.read_text())

    assert "get-helm-3" not in code, \
        "get-helm-3 installs whatever helm 3 is current, which is not what is declared"
    assert "helmfile/releases/download/v${HELMFILE_VERSION}" in code, \
        "the helmfile URL is not built from the pin"


# ---------------------------------------------------------------------------------------
# the other half: what the guest ended up running is said, not recorded
# ---------------------------------------------------------------------------------------


def a_probe_answering(declared: dict, observed: dict):
    """A guest that answers the stage's version probe with the two tables, as the real one does."""
    guest = FakeGuest()
    guest.respond("python -c", lambda command: Answer(
        0, json.dumps({"declared": declared, "observed": observed}), ""))
    runner = stages.LaneRunner(stages.config.LANES["smoke"], type("Options", (), {"env": {}})(),
                              FakeArtifacts(), None, {}, Recording(), lambda message: None)
    runner.guest = guest
    runner.stage = "01-provision"
    return runner


def test_the_lane_names_the_versions_the_guest_rather_than_recording_them():
    """Stage 01 fails when the guest's helm is not the declared one.

    `tool_versions` already existed and already proved nothing: a green lane and a wrong helm were
    both in the same file, and the only reader of a JSON blob is whoever thinks to open it. Asking
    the guest's own product code for both tables is what turns the next disagreement into a named
    failure, and it is the product's table rather than a harness copy because two places that decide
    versions is the failure this row is about.
    """
    declared = dict(constants.PINNED_BINARY_VERSIONS)
    running = dict(declared)
    running["helm"] = "3.22.1"  # what the measured lane actually had on the guest

    drift, detail = a_probe_answering(declared, running)._tool_version_drift()

    assert drift, "a guest running an undeclared helm is still green, so the drift is still a log line"
    assert "3.22.1" in detail and declared["helm"] in detail, \
        "the failure names neither side: %s" % detail
    assert "constants.PINNED_BINARY_VERSIONS" in detail, "the failure does not say where the pin lives"


def test_the_lane_says_so_and_not_merely_when_the_probe_itself_cannot_answer():
    """A guest that cannot say what it runs is drift, because "unknown" is not evidence.

    The old behaviour here is the shape of every bad lane: the probe fails, nothing is compared,
    the row is green, and the versions sit unread in `tool_versions`.
    """
    runner = a_probe_answering({}, {})
    runner.guest = FakeGuest(default="not json at all")
    runner.guest.respond("python -c", Answer(1, "", "no module named kubementat"))

    drift, detail = runner._tool_version_drift()

    assert drift, "a version nobody could read is not a pass"
    assert "no module named kubementat" in detail


def test_the_lane_does_not_assert_a_version_the_product_did_not_declare():
    """Both tables agree, and the row passes on the product's declaration and nothing else.

    The assert's own correctness is the boring half, and it is what fails loudest if the harness
    grew a copy of the version table: a copy agrees with itself forever.
    """
    declared = dict(constants.PINNED_BINARY_VERSIONS)

    drift, detail = a_probe_answering(declared, dict(declared))._tool_version_drift()

    assert drift == [], "a lane running the declared versions is not drift: %s" % detail
    for binary, pinned in sorted(declared.items()):
        assert pinned in detail, "the pass does not say what was true for %s: %s" % (binary, detail)
