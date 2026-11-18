"""What helmfile builds is what the two knobs say, measured out of the binary and not restated."""

import os
import re
import shutil
import subprocess

import pytest
import yaml
from asset_paths import values_path

HELMFILE = shutil.which("helmfile")
# `helmfile build` asks the installed helm for its version before it renders anything, so a host with
# helmfile and no helm does not skip this: it fails inside helmfile with `error determining helm
# version`. Both binaries are preconditions, and the guard says so.
helmfile_only = pytest.mark.skipif(HELMFILE is None or shutil.which("helm") is None,
                                   reason="helmfile builds its state through the installed helm, and "
                                           "this host is missing one of the two")

MANIFEST = values_path("helmfile.yaml.gotmpl")


# `{release: its own `installed:` default}` as the shipped manifest declares it. The read starts at
# the `releases:` line and skips comments for the same reasons the manifest guard gives:
# `repositories:` shares the shape of the release header, and a commented release is not a release.
# This is the file's census of opt-in: everything ships `default "true"` except what `extra` keeps
# out of the default install (concourse ships "false" on purpose), and a hard-coded *all build
# installed* here would be a statement about yesterday's manifest. The measurement this file makes
# is knob-to-boolean resolution, and the truth it measures against is the manifest's own default.
INSTALLED_LINE = re.compile(r'^\s+installed: \{\{ env "[A-Z0-9_]+" \| default "(true|false)" \}\}$')
NAME_LINE = re.compile(r"^- name:\s*(\S+)\s*$")


def installed_defaults():
    """`{release name: the built `installed` a run that declares nothing must produce}`."""
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    started = False
    defaults, current = {}, None
    for line in lines:
        if line.strip() == "releases:":
            started = True
            continue
        if not started or line.lstrip().startswith("#"):
            continue
        declared = NAME_LINE.match(line)
        if declared:
            current = declared.group(1)
            continue
        found = INSTALLED_LINE.match(line)
        if found and current is not None:
            defaults[current] = found.group(1) == "true"
    assert defaults, "the shipped manifest declares no templated `installed:` line, so this guard reads nothing"
    return defaults


def built_releases(**knobs):
    """`{release name: its built spec}` for the shipped manifest, with *knobs* exported."""
    environment = dict(os.environ)
    for name in ("KUBEMENTAT_CONFIG_ROOT", "KUBEMENTAT_ENVIRONMENT", "KUBEMENTAT_TEAM"):
        environment.pop(name, None)
    # A knob left in the process environment by a test that runs before this one is a knob this
    # build would answer to, so every knob this file could set starts from unset.
    for name in [name for name in environment if name.endswith(("_ENABLED", "_VERSION"))]:
        environment.pop(name)
    environment["GRAFANA_ADMIN_USER"] = "admin"
    environment["GRAFANA_ADMIN_PASSWORD"] = "not-a-password"
    for key, value in knobs.items():
        environment[key] = str(value)

    result = subprocess.run([HELMFILE, "-f", str(MANIFEST), "build"],
                            cwd=str(values_path()), env=environment,
                            capture_output=True, text=True)
    assert result.returncode == 0, f"`helmfile build` said: {result.stderr.strip()}"
    state = yaml.safe_load(result.stdout)
    assert state and state.get("releases"), f"helmfile returned no releases:\n{result.stdout[:400]}"
    return {release["name"]: release for release in state["releases"]}


@helmfile_only
def test_a_manifest_that_nobody_edited_builds_every_release_at_its_own_default():
    """The default case, and the one a fresh cluster runs on: every release builds its own default, as
    booleans -- installed where the block is wound true, not-installed where it ships wound false.
    """
    releases = built_releases()
    expected = installed_defaults()

    assert releases, "the shipped manifest built nothing at all"
    assert set(expected) == set(releases), \
        f"the blocks the manifest declares and the releases the build returned disagree: " \
        f"{sorted(set(expected) ^ set(releases))}"
    assert {name: release.get("installed") for name, release in releases.items()} == expected, \
        {name: (release.get("installed"), expected[name])
         for name, release in releases.items() if release.get("installed") is not expected[name]}
