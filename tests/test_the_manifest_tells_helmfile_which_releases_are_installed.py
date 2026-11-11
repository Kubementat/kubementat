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
import shutil
import subprocess

import pytest
import yaml

from asset_paths import values_path

HELMFILE = shutil.which("helmfile")
helmfile_only = pytest.mark.skipif(HELMFILE is None,
                                   reason="helmfile is what is under test, and it is not installed")

MANIFEST = values_path("helmfile.yaml.gotmpl")

# The release the assertions are about, spelled as a name and not derived, because the failure this
# guards is a specific release's line. `alloy` is named in the second test's prose rather than asserted
# separately: its build state is checked there, as one of the releases nobody disabled.
SWITCHED_OFF = "grafana"


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
def test_a_manifest_that_nobody_edited_builds_every_release_installed():
    """The default case, and the one a fresh cluster runs on: every release is installed, as booleans.

    `is True` and not `== True`, because the point is the field. helmfile resolves an unquoted
    `true` inside `{{ env ... }}` into its boolean `installed`, and a string that reads `"true"` is
    not that field -- so a template that quoted the answer would pass an equality assert and fail the
    apply.
    """
    releases = built_releases()

    assert releases, "the shipped manifest built nothing at all"
    assert all(release.get("installed") is True for release in releases.values()), \
        {name: release.get("installed") for name, release in releases.items()}


@helmfile_only
def test_the_release_the_environment_switched_off_builds_as_not_installed():
    """The whole claim in one line: an operator's `false` reaches the built spec as `false`.

    A release nobody disabled still reads `true` in the same run, which is the half that stops a
    template from answering everything with the one variable that was set -- and among those others
    is `alloy`, the release that has no namespace key of its own and is therefore only addressable
    through the knob this run set for something else.
    """
    releases = built_releases(**{f"{SWITCHED_OFF.upper()}_ENABLED": "false"})

    assert releases[SWITCHED_OFF]["installed"] is False
    assert all(release["installed"] is True
               for name, release in releases.items() if name != SWITCHED_OFF), \
        "disabling one release decided all of them"


@helmfile_only
def test_the_version_helmfile_builds_is_the_quoted_default_and_not_a_number():
    """The version half, measured the same way: `1.0` must not become the number 1.

    helmfile resolves a release's version against what the chart index serves, so this is the string
    the manifest carried as its literal before the knob landed -- the same value
    `SHIPPED_CHART_VERSIONS` in the manifest guard states.
    """
    releases = built_releases()

    assert releases[SWITCHED_OFF]["version"] == "14.2.0"
    assert isinstance(releases[SWITCHED_OFF]["version"], str)
    assert releases["prometheus-helm-exporter"]["version"] == "1.4.0+e2eee28", \
        "the build metadata in that version is part of the string the index serves"
