"""The argv behind "what releases live in this namespace", checked against the helm that ships.

`helm list --all-namespaces --all` used to be that question. helm 4 deleted `--all`, and this host
runs `helm v4.3.1`, where asking for it exits 1 with `Error: unknown flag: --all` — the read that
`HelmUtils._installed_release_names` makes before it uninstalls anything, so a component someone
wanted to remove was never even looked up. The defect is `src/kubementat/helm_utils.py`; the
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

import re
import shutil
import subprocess

import pytest

from cluster_fixture import TARGET_DEV
from kubementat import helm_utils
from kubementat.automation import Automation
from kubementat.cluster import Context
from kubementat.helm_utils import HelmUtils, helm_list_releases_argv
from kubementat.kubernetes_utils import KubernetesUtils, helm_target_args

# The argv the fix ships. Written out here rather than read from the module so a change to the
# constant has to be agreed with the change to this list, and so a `--all` put back under the
# pressure of a helm 3 cluster fails here too.
EXPECTED_HELM_LIST_RELEASES_ARGV = ["helm", "list", "--all-namespaces",
                                    "--deployed", "--failed", "--pending", "--superseded",
                                    "--uninstalled", "--uninstalling", "--output", "json"]


def expected_read_for(target) -> list:
    """That same read, addressed to the cluster the caller was pointed at.

    The splice is spelled here and not imported, for the same reason the statuses are spelled here:
    the question the read asks is what this file guards, and a call site that addressed the read by
    narrowing it would look exactly like a call site that addressed it correctly.
    """
    return [*EXPECTED_HELM_LIST_RELEASES_ARGV[:2], *helm_target_args(target),
            *EXPECTED_HELM_LIST_RELEASES_ARGV[2:]]


# ---------------------------------------------------------------------------------------
# one answer, read the same way everywhere
# ---------------------------------------------------------------------------------------


def test_nothing_else_in_the_product_spells_a_helm_list_argv():
    """One read of "what releases live here", spelled in exactly one file.

    This is the guard for the case a later change reintroduces: a second `helm list` argv somewhere,
    assembled out of a docstring or a plan, answering the same question with a different set of
    statuses. That is what `e57fed3` was fixing, and the reason row 24 had both `automation.py`
    call sites and the routing probe read the constant instead of writing their own: a sweep, a gate
    and a probe that disagree about which releases exist is a cluster nobody can describe. A new argv
    in a new file is the failure this test exists for, so the message says that rather than quoting a
    line number.

    The check is on the argv and not the prose: `"helm", "list"` inside a list literal is a command,
    and the same words in a comment are an explanation.
    """
    import pathlib

    definition = pathlib.Path(helm_utils.__file__).resolve()
    source_root = definition.parent
    spelling = re.compile(r'\[\s*"helm",\s*"list"')

    found = sorted(str(path.relative_to(source_root))
                   for path in source_root.rglob("*.py")
                   if path.resolve() != definition and spelling.search(path.read_text()))

    assert not found, (
        "%s spells its own `helm list` argv: there is one answer to "
        "`what releases live here`, in helm_utils.HELM_LIST_RELEASES_ARGV, and a second spelling is"
        " a second answer to the same question -- read the constant."
        % ", ".join(found))


def test_the_two_teardown_reads_ask_that_argv_and_not_an_equal_one(monkeypatch):
    """What the two `automation.py` call sites actually issue, and not what a comment claims.

    Row 24's half that is not a constant: the release sweep and the Traefik-definition gate both
    asked `helm list --all-namespaces --output json`, which is the narrower read -- correct on helm
    4, where every status is the default, and blind to `pending`/`superseded`/`uninstalled` on helm
    3, where the mask starts narrower. One answer is only one answer if the command issued is the
    constant, so this runs both methods against a recording `shell.run` and reads the argv back.

    The recording answers with a single Traefik release: enough for the gate to decide it has nothing
    to delete, and not enough for the sweep to uninstall anything it should not, so the commands
    recorded here are the reads under test.

    The instance is no longer state-free. The cluster is on this command's argv, so a teardown
    read needs the address it was handed -- and an instance built with `__new__` and no `kubernetes_utils`
    now fails instead of quietly asking the machine running the test.
    """
    import kubementat.automation

    asked = []
    traefik = ('[{"name": "traefik", "namespace": "kube-system", "chart": "traefik-33.2.1",'
               ' "status": "deployed"}]')

    def recording_run(argv, **kwargs):
        asked.append(list(argv))
        return kubementat.shell.CommandResult(argv, 0, stdout=traefik, stderr="")

    monkeypatch.setattr(kubementat.automation.shell, "run", recording_run)
    gate = Automation.__new__(Automation)
    gate.kubernetes_utils = KubernetesUtils(TARGET_DEV)     # the read needs an address

    Automation._uninstall_helm_releases(gate, ["dev1"])
    Automation._delete_crds_traefik_helm_leaves_behind(gate)

    assert [argv for argv in asked if argv[:2] == ["helm", "list"]] == [
        expected_read_for(TARGET_DEV), expected_read_for(TARGET_DEV)], (
        "a teardown read asked its own question: %s" % asked)


def json_rows():
    """One release, as `helm list --output json` prints it — `failed`, because that is the case
    the statuses are there for."""
    return ('[{"name": "polaris", "namespace": "polaris", "status": "failed",'
            ' "chart": "polaris-1.0.0"}]')


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


def test_the_command_issued_is_that_argv_and_not_a_claim_about_it(fixture_repo, dry_run_env,
                                                                  fake_binaries):
    """What the cluster is actually asked, from the function that has to ask it.

    A constant that nothing runs is a comment with a name, so this calls `_installed_release_names`
    and reads the shim log. The fake answers `helm list ...` whatever the flags are, which is the
    point: the answer is the same either way, and only the argv distinguishes a read that works on
    helm 4 from one that exits 1 -- and tells the operator which cluster they are talking about.
    """
    fake_binaries.respond("helm", json_rows(), subcommand="list")

    context = Context.build(fixture_repo.environment, fixture_repo.team)
    releases = HelmUtils(context)._installed_release_names("polaris")

    assert releases == ["polaris"]
    helms = [command for command in fake_binaries.commands() if command[:2] == ["helm", "list"]]
    assert helms == [expected_read_for(context.target)], helms
    assert "--kube-context" in helms[0], \
        "a release read that does not say which cluster answers whichever one the process last saw"


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
