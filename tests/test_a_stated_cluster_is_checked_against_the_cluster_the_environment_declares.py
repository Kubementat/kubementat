"""A run that states a cluster and an environment that declares one
have to agree, and when they do not, kubementat refuses instead of choosing.

The defect is a Tekton pod's, and it is worth spelling out because it is what makes this a rule
about *claims* and not about files. A pipeline pod clones the configuration repository and runs
`kmt` inside the cluster it was scheduled onto. The clone declares `CLUSTER_KUBE_CONTEXT`, and —
an absolute `CLUSTER_KUBECONFIG_PATH` in a shared tree being a mistake — that context
names a file that exists on somebody's laptop and not in the pod. Meanwhile the pod's own mounted
service account would answer for the cluster it is standing on. v2's ordering puts the clone's
declaration (layer 3) **ahead** of that service account (layer 4), so a pod that says nothing
errors out on a dead reference instead of quietly installing into the cluster it is running on.

The fix is not "teach layer 3 to fall through when its file is unreadable" — the spec refuses that
in as many words, on the ground that a configuration which cannot find the cluster it declares does
not get to go and discover a different one. The fix is a comparison, asked before anything resolves:

* the pipeline **states** its cluster with the dials above the file (`--kubeconfig`, `--context`,
  `CLUSTER_KUBECONFIG_PATH`), so the file that gets opened is the pipeline's and the answer that
  wins is the one the pipeline declared, and
* the clone's declaration is then checked against that statement, and a disagreement is a refusal
  that names both claims and the flag, variable, or file that wrote each of them.

The reasoning for the refusal is the sentence that matters, and it is in the message: the
configuration's claim is about **which environment** the work is for, the run's claim is about
**where the work runs**, and when those two disagree kubementat is not the thing that decides.

The resolution order is untouched by all of this, and these tests are written so that stays true:
nothing here reorders the layers, nothing here makes a failed read fall through, and a run that
states a cluster for an environment that declared nothing resolves exactly the way it did before.
What changed is only that a statement above the file may *answer for* an environment and may not
contradict it.
"""

import pytest

from kubementat.cluster import (
    Context,
    SOURCE_FLAG,
)
from kubementat.errors import (
    ClusterFileUnreadableError,
    StatedClusterMismatchError,
)

from cluster_fixture import (
    DEV_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

# The cluster the configuration clone declares, and the cluster the pod is standing on. Two
# addresses, because a comparison that only ever sees one of them is a comparison nobody read.
DECLARED_SERVER = "https://10.0.4.2:6443"
IN_CLUSTER_SERVER = "https://10.43.0.1:6443"

CI_CONTEXT = "ci-cluster"


def test_a_stated_cluster_the_environment_declares_by_the_same_name_proceeds(tmp_path, monkeypatch):
    """The other half of agreeing: naming the same context from a flag resolves, and changes nothing.

    `--context` is layer 1, so this is also the test that the check did not quietly demote the flag:
    the target's source is still the flag, and the address is still read from the layer-1 file.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DECLARED_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=kubeconfig)

    ctx = Context.build("ci", "the-team", context=CI_CONTEXT, kubeconfig=kubeconfig)

    assert ctx.target.address == DECLARED_SERVER
    assert ctx.target.source == SOURCE_FLAG


def test_a_stated_cluster_the_environment_does_not_declare_refuses_and_names_both_claims(
        tmp_path, monkeypatch):
    """The refusal, and what it is an answer to.

    The run states one context and the clone declares another. v2's ordering would take the
    statement and be done with it — which is how `ci`'s platform ends up in a cluster nothing
    declared it for — so the refusal names both claims *and* both sources, the flag the run used and
    the file that holds the declaration. It is the reader who has to be able to tell which of the
    two is the mistake.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DECLARED_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=kubeconfig)
    static_path = root / "platform_config" / "ci" / "static.json"

    with pytest.raises(StatedClusterMismatchError) as refused:
        Context.build("ci", "the-team", context="somewhere-else")

    message = str(refused.value)
    assert "somewhere-else" in message
    assert "flag --context" in message
    assert CI_CONTEXT in message
    assert str(static_path) in message
    assert "CLUSTER_KUBE_CONTEXT" in message
    assert refused.value.stated and refused.value.declared


def test_a_stated_cluster_and_a_declaration_whose_file_is_not_here_refuse_on_the_claims(
        tmp_path, monkeypatch):
    """The failure the ticket exists for, told in the right order.

    The clone declares a context whose kubeconfig is a path that does not exist in this process —
    which is exactly what a shared tree should never have written. If the check ran after
    the target was built, the operator would get a file-not-found stack about a file the *run* never
    chose, and the actual disagreement would never be mentioned. It is asked first, so the refusal
    is the comparison.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    dead_path = root / "kubeconfig-on-the-operators-laptop"
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=dead_path)

    with pytest.raises(StatedClusterMismatchError) as refused:
        Context.build("ci", "the-team", context="the-pods-context")

    message = str(refused.value)
    assert "does not exist" not in message, \
        "the dead declared path answered this run; the disagreement was supposed to be the answer"
    assert "the-pods-context" in message and CI_CONTEXT in message


def test_a_stated_kubeconfig_that_does_not_have_the_declared_context_is_refused(
        tmp_path, monkeypatch):
    """A file is a claim about the cluster too, and here it is the *run* making it.

    The stated kubeconfig has one context; the environment declares another. The ordering would open
    the run's file and read the declared name out of it, fail, and report a context that is not in
    one file while the other file's own claim goes unmentioned. So the comparison is made against
    the contexts the stated file actually has.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    clone_kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DECLARED_SERVER})
    mounted = write_kubeconfig(root / "mounted-kubeconfig", {"ci-worker": IN_CLUSTER_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=clone_kubeconfig)

    with pytest.raises(StatedClusterMismatchError) as refused:
        Context.build("ci", "the-team", kubeconfig=mounted)

    message = str(refused.value)
    assert "flag --kubeconfig" in message
    assert str(mounted) in message
    assert "ci-worker" in message, "the stated file's own claim is what the reader needs to see"
    assert CI_CONTEXT in message


def test_the_process_variable_stating_a_cluster_is_compared_like_a_flag(tmp_path, monkeypatch):
    """`CLUSTER_KUBECONFIG_PATH` is a dial the pipeline states with, not a hint.

    The list of layer-1 dials names the variable beside the flags, and the CI step that exports
    it is the same claim a `--kubeconfig` makes. This states it through the variable and gets the
    same refusal, with the variable — and not a file — named as its source.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    clone_kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DECLARED_SERVER})
    mounted = write_kubeconfig(root / "mounted-kubeconfig", {CI_CONTEXT: IN_CLUSTER_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=clone_kubeconfig)
    monkeypatch.setenv("CLUSTER_KUBECONFIG_PATH", str(mounted))

    with pytest.raises(StatedClusterMismatchError):
        Context.build("ci", "the-team", context="not-the-declared-one")


def test_a_run_that_states_nothing_leaves_the_answer_to_the_environment(tmp_path, monkeypatch):
    """Nothing stated, nothing compared: the ordinary laptop case is untouched.

    The check is a guard and not a mode. A run that carries no dials resolves the way it always did
    and the report says which file answered.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DEV_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=kubeconfig)

    ctx = Context.build("ci", "the-team")

    assert ctx.target.address == DEV_SERVER
    assert "static.json" in ctx.resolution_report[0]


def test_an_environment_that_declares_nothing_can_be_stated_by_a_run_without_argument(
        tmp_path, monkeypatch):
    """Layer 1 keeps its one real job: answering for an environment that has no claim yet.

    The comparison is only between two claims, and here there is one. This is also the bootstrap
    case — a freshly prepared tree that has not named its cluster yet is still installable from the
    command line — so a refusal that ignored the declaration's emptiness would be the strictest
    command in the product for no reason.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DEV_SERVER})
    write_environment(root, "ci", context=None)

    ctx = Context.build("ci", "the-team", context=CI_CONTEXT, kubeconfig=kubeconfig)

    assert ctx.target.address == DEV_SERVER


def test_a_stated_cluster_that_matches_the_declaration_still_reports_the_layer_that_answered(
        tmp_path, monkeypatch):
    """The resolution report is what tells a reader which claim won, so a check must not lose it.

    The report is the only thing a CI log gives an operator, and the resolution it describes has to
    say whether the answer came from the flag or the file. Nothing in the comparison edits it.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DEV_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=kubeconfig)

    ctx = Context.build("ci", "the-team", context=CI_CONTEXT, kubeconfig=kubeconfig)

    assert "flag --context" in ctx.resolution_report[0]


def test_the_refusal_is_a_resolution_failure_and_the_cli_prints_it_as_one(tmp_path, monkeypatch):
    """A refused run is the command's answer, not a traceback, so it travels in the type the CLI
    already renders.

    `cli.context._resolve` catches `ClusterResolutionError` and prints one `Error:` line; a sibling
    exception that fell outside that catch would reach the operator as a stack trace about a
    comparison this file's whole subject says is normal.
    """
    from click.testing import CliRunner

    from cli.commands import kubementat as kubementat_cli

    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {CI_CONTEXT: DEV_SERVER})
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=kubeconfig)

    result = CliRunner().invoke(
        kubementat_cli.install, ["ci", "the-team", "--context", "somewhere-else"])

    assert result.exit_code != 0
    assert "somewhere-else" in result.output
    assert "CLUSTER_KUBE_CONTEXT" in result.output


def test_the_check_does_not_rescue_a_dead_declaration_by_finding_a_different_cluster(
        tmp_path, monkeypatch):
    """The rule this replaces, measured so it stays replaced.

    Agreement does not make a dead path readable, and the spec is explicit that a configuration
    which cannot find the cluster it declares does not get to discover a different one. So a run
    that states the declared context and says nothing about the file still dies on the file — with
    the file's name, and with the ordering intact.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    dead_path = root / "kubeconfig-on-the-operators-laptop"
    write_environment(root, "ci", context=CI_CONTEXT, kubeconfig_path=dead_path)

    with pytest.raises(ClusterFileUnreadableError) as refused:
        Context.build("ci", "the-team", context=CI_CONTEXT)

    assert str(dead_path) in str(refused.value)
