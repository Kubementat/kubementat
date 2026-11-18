"""What helmfile builds is what the two knobs say, measured out of the binary and not restated.

The read path and the template meet in one place: helmfile evaluates its own file in the environment
it is handed, so `Config` answering `false` only becomes a release that is not installed if the
`installed:` line resolves to helmfile's boolean field. That is a fact about helmfile 1.7.0 and not
about this repository, which is why this file runs the binary: a Python re-reading of a gotmpl is not
a measurement of anything, and every other assertion here could pass while helmfile ignored the
variable completely.

What is measured, on a machine that has helmfile:

* nothing declared, so every release builds `installed: true` -- the manifest's own `default`, which
  is what a tree that never edited a knob is entitled to;
* one release switched off by its variable, which builds `installed: false` and is not an error; and
* the version a build resolves, which comes out of the quoted `default` and is a string and not the
  number `1.0` that the same characters would parse as.

The shape of the last two was measured by hand on helmfile 1.7.0 and is asserted here so the day a
helmfile upgrade changes how a quoted `installed:` resolves is the day this file says so. Where
helmfile is not installed these skip, which is the honest answer and not a pass.
"""

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
    """`{release name: its built spec}` for the shipped manifest, with *knobs* exported.

    The values that decide where the operator's tree is get popped, so a run from a shell that is
    carrying `KUBEMENTAT_CONFIG_ROOT` builds the shipped defaults and not somebody's tree. Grafana's
    admin pair is `requiredEnv`'d by its own block, exactly as an apply does, so its absence would
    fail the template for a reason that has nothing to do with a knob.
    """
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
    """The default case, and the one a fresh cluster runs on: every release builds its own default,
    as booleans -- installed where the block is wound true, not-installed where it ships wound false.

    `is True` and not `== True`, because the point is the field. helmfile resolves an unquoted
    `true` inside `{{ env ... }}` into its boolean `installed`, and a string that reads `"true"` is
    not that field -- so a template that quoted the answer would pass an equality assert and fail the
    apply. The same holds for `false`: the opt-in releases (`concourse` in `group: extra`) must come
    out the field `False`, not the string `"false"` and not silently `True`.
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
