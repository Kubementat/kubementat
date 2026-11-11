"""What the stage asks helm to install is what the helm it installs onto accepts.

Measured at helm 4.3.1, on this machine:

    $ XDG_DATA_HOME=$(mktemp -d) helm plugin install https://github.com/databus23/helm-diff
    Error: plugin source does not support verification. Use --verify=false to skip verification

Helm 4 verifies plugin sources by default and helm-diff ships no provenance, so the plain command
exits 1 (databus23/helm-diff#887). The flag that skips the verification is not known to helm 3,
so neither command is safe for both majors: the stage has to reach the accepted form through a
path, plain attempt first and flag as fallback, instead of picking a side.

The old block picked neither and hid the choice: it ran the plain install, sent both streams to
/dev/null, and logged a guess about helmfile's diff step. The lane died at the first command of
stage 02 with `unknown command "diff" for "helm"` and 15 minutes of evidence saying nothing about
a plugin nobody could install.

The guard reads the stage's commands and not its prose, and joins backslash continuations so a
flag cannot hide on the following line.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVISION_SCRIPT = REPO_ROOT / "tests" / "e2e" / "stages" / "01-provision.sh"

PLUGIN_INSTALL = re.compile(r"\bhelm\s+plugin\s+install\b")


def code_lines(script_text):
    """The script's commands without comment lines -- what it does, the only half a guard may read."""
    return [line.strip() for line in script_text.splitlines() if not line.strip().startswith("#")]


def plugin_install_commands(script_text):
    """Every `helm plugin install` command in the stage, continuation lines joined."""
    commands, buffer = [], ""
    for line in code_lines(script_text):
        buffer = (buffer + " " + line) if buffer else line
        if line.endswith("\\"):
            continue
        commands.append(buffer)
        buffer = ""
    if buffer:
        commands.append(buffer)
    return [command for command in commands if PLUGIN_INSTALL.search(command)]


def test_the_provisioner_never_installs_a_plugin_the_pinned_helm_will_refuse():
    """Each plugin install is either in a form the helm on the guest accepts, or is rescued by a retry.

    A command without `--verify=false` is a helm 4 refusal unless the stage says what it does when
    helm 4 says so. And a command whose error output goes to /dev/null is the measured failure
    again: the tool's own words are the evidence, and guessing at them is what the last lane did.
    """
    commands = plugin_install_commands(PROVISION_SCRIPT.read_text())
    assert commands, "no helm plugin install in the provisioning stage: this guard is guarding nothing"

    plain = [c for c in commands if "--verify=false" not in c]
    fallback = [c for c in commands if "--verify=false" in c]

    if plain:
        assert fallback, (
            "the stage installs a plugin with the plain command, which helm 4 refuses with "
            "'plugin source does not support verification' -- retry with --verify=false when the "
            "plain attempt fails, and keep both attempts' stderr: %s" % plain)
    if fallback:
        assert plain, (
            "the stage passes --verify=false to helm unconditionally, and helm 3 has no such flag "
            "-- the flag is the fallback after a plain attempt, not the default: %s" % fallback)

    for command in commands:
        assert "/dev/null" not in command, (
            "the stage discards what helm said about its own plugin install: %s" % command)
