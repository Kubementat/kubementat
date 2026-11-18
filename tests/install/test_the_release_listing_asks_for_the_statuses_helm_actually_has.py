"""The argv behind "what releases live in this namespace", checked against the helm that ships."""

import shutil
import subprocess

import pytest

from kubementat.infrastructure import helm as helm_utils

# The argv the fix ships. Written out here rather than read from the module so a change to the
# constant has to be agreed with the change to this list, and so a `--all` put back under the
# pressure of a helm 3 cluster fails here too.
EXPECTED_HELM_LIST_RELEASES_ARGV = ["helm", "list", "--all-namespaces",
                                    "--deployed", "--failed", "--pending", "--superseded",
                                    "--uninstalled", "--uninstalling", "--output", "json"]


# ---------------------------------------------------------------------------------------
# one answer, read the same way everywhere
# ---------------------------------------------------------------------------------------


def test_the_read_names_the_statuses_instead_of_a_flag_helm_4_deleted():
    """No `--all`, and the six statuses are present by name."""
    argv = helm_utils.HELM_LIST_RELEASES_ARGV

    assert argv == EXPECTED_HELM_LIST_RELEASES_ARGV, argv
    assert "--all" not in argv, "helm 4 rejects --all, so the read dies before it asks"
    assert "--hidden" not in argv, "neither major accepts it; it would be a second version trap"
    for status in ("--deployed", "--failed", "--pending", "--superseded",
                   "--uninstalled", "--uninstalling"):
        assert status in argv, f"{status} missing, so the read is narrower than the default"


@pytest.mark.skipif(shutil.which("helm") is None,
                    reason="the guard is about the helm this host ships, and there is none here")
def test_the_helm_on_this_host_accepts_every_flag_in_the_read():
    """Run the argv for real. No cluster is expected: a flag error and a connection error differ."""
    result = subprocess.run(helm_utils.HELM_LIST_RELEASES_ARGV, capture_output=True, text=True)

    stderr = (result.stderr or "")
    assert "unknown flag" not in stderr, f"helm refuses the argv: {stderr.strip()}"
    assert "unknown shorthand" not in stderr, f"helm refuses the argv: {stderr.strip()}"
