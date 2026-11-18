"""The argv behind "what releases live in this namespace", checked against the helm that ships.

`helm list --all-namespaces --all` used to be that question. helm 4 deleted `--all`, and this host
runs `helm v4.3.1`, where asking for it exits 1 with `Error: unknown flag: --all` — the read that
`HelmUtils._installed_release_names` makes before it uninstalls anything, so a component someone
wanted to remove was never even looked up. The defect is `src/kubementat/infrastructure/helm.py`; the
harness's `Cluster.helm_list` and the routing lane's `HELM_LIST_ARGV` had already dropped the flag
and left this call site behind.

Deleting the flag is not the whole fix, because what it was for is true per major: helm 4 lists
every status by default, helm 3 starts from deployed-or-failed and narrows from there. So the read
spells the statuses, and this file is what keeps it honest — three ways:

1. the argv is the one the fix chose, with the statuses present and `--all` absent;
2. the command actually issued is that argv, and not merely a docstring that claims it is;
3. the helm installed here accepts every flag in it, so the claim is about a binary that exists
   rather than about a version nobody on this host can run.

That third check is what makes the guard real instead of decorative: a flag list asserted against
a helm that is not here would pass anywhere, and it is exactly the kind of claim this file is not
supposed to trust.
"""

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
    """No `--all`, and the six statuses are present by name.

    The other half of the claim matters as much as the deletion: a "fix" that dropped `--all` and
    added nothing is correct on helm 4 and quietly drops `pending`/`superseded`/`uninstalled`
    releases on helm 3, which is the failure this function was written to avoid. `--hidden` is
    excluded on purpose — helm 4.3.1 rejects it, and helm 3.0.0's and 3.14.0's `list.go` never had
    it, so including it would fail the way the flag it replaces fails.
    """
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
    """Run the argv for real. No cluster is expected: a flag error and a connection error differ.

    `helm list` needs an API server, and this host has none, so the honest expectation is that the
    command fails — with `kubernetes cluster unreachable` / `connection refused` and not with
    `unknown flag`. That is what the original bug looked like from the outside: exit 1, and a
    message about the command line instead of the cluster.
    """
    result = subprocess.run(helm_utils.HELM_LIST_RELEASES_ARGV, capture_output=True, text=True)

    stderr = (result.stderr or "")
    assert "unknown flag" not in stderr, f"helm refuses the argv: {stderr.strip()}"
    assert "unknown shorthand" not in stderr, f"helm refuses the argv: {stderr.strip()}"
