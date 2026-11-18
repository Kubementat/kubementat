"""A `kubectl` call that was not told which cluster is a bug, and the refusal is named.

The design's negative claim is short: *a `kubectl` invocation that is not given its cluster is a bug,
not a shortcut*. Three things in this checkout had been that bug -- `kubectl logs` and `port-forward`
inheriting whatever the process's kubeconfig called current, and a read that resolved a cluster only
to hand the answer to a binary that would ignore it.

This file used to hold the rule as a tree guard over `src/`, plus a table of "the calls that are still
allowed to be unaddressed, and which module's seam they fall through". Both were assertions about this
repository's own source, which is what the developer guide tells a test not to police, and the table
worst of all: it was a second answer to one question, and it needed editing on every move of a file.

What is kept is the runtime refusal -- the function the surviving call sites splat, tested at the seam
where the decision is made. The behaviour that guard protected is asserted where it runs, at the argv
the binaries are handed:

* `tests/test_uninstall_sweeps_everything_kubementat_owns.py` strips the cluster flags off every
  recorded call and asserts what is left is the operation, which only works if the flags are there.
* `tests/test_a_helm_component_is_uninstalled_from_the_namespace_the_config_names.py` reads them off
  the same argv for the same reason.
* `tests/test_a_team_account_kubeconfig_is_a_document_and_not_a_string.py` asserts the `kubectl` calls
  the team-account flow makes carry the context the environment declared.

The table of reads the conversion still owes is gone with the guard, and the debt it counted is not
denied by this file: it was a count of unfinished work, not a claim about behaviour.
"""

import pytest


def test_kubectl_target_args_refuses_a_target_that_cannot_be_addressed():
    """The refusal, named — because a rule enforced only at the call site has as many owners as the call site.

    A target built from a connection document has no kubeconfig and no context, so the session
    calls that survive have nothing to carry. Saying "and then we guess" would be the failure the
    deleted read used to make, arriving from the other side: a `port-forward` against a cluster the
    command was never pointed at.
    """
    from kubementat.core.identity.target import ClusterTarget, kubectl_target_args

    with pytest.raises(Exception) as raised:
        kubectl_target_args(ClusterTarget(environment="dev", context=None, kubeconfig_path=None,
                                          address="https://example.test:6443",
                                          source="connection"))
    assert "kubecontext" in str(raised.value) or "kubeconfig" in str(raised.value).lower()

    assert kubectl_target_args(ClusterTarget(environment="dev", context="dev",
                                             kubeconfig_path=None, address="https://x:6443",
                                             source="static.json")) == ["--context", "dev"]
    assert kubectl_target_args(ClusterTarget(environment="dev", context=None,
                                             kubeconfig_path="/tmp/x", address="https://x:6443",
                                             source="kubeconfig_file")) == ["--kubeconfig", "/tmp/x"]
