"""A helm or helmfile call that is not told which cluster is a bug, and the refusal says so.

Cluster operations run over two channels. One is the Python client, bound to a `ClusterTarget`, which
cannot be asked a question about the wrong cluster by construction. The other is the `helm` and
`helmfile` binaries, where the cluster is an argument -- so a call that does not get one is not a
small oversight, it is half a run, answered by whichever `~/.kube/config` the process last looked at.

This file used to hold the rule as a tree guard: walk every module in `src/`, read every argv out of
the AST, and assert each one carries `--kube-context` or `--kubeconfig`. That assertion is about this
repository's own source, which is the one thing the developer guide says a test may not police, and it
cost more than it paid: it failed on relocations, and the two mutation tests that proved it could fail
were tests of the guard's own machinery rather than of the product.

What replaces it is the two decisions the code makes at runtime, which is where the rule actually
live

* `helm_target_args` refuses a target that names no cluster -- the splat every addressing call site
  hands over, tested at the function that enforces it.
* `helm_list_releases_argv` is the one release read four call sites share, and addressing it must not
  change what it asks.

The local-only calls (`helm repo update`, `helm version`, and the rest) stay declared in
`recorded_operations.LOCAL_ONLY_HELM_CALLS`, which the lane's operation reducer also reads, so there is
still one table answering "did this ask a cluster?" -- it is just no longer policed by a scan of the
checkout. And the behaviour that guard was protecting is covered where it runs: the addressing flags
are read off the argv the binaries are handed by
`tests/test_command_execution_policy.py`, the release listing by
`tests/test_the_release_listing_asks_for_the_statuses_helm_actually_has.py`, and the apply path by
`tests/test_the_helmfile_argv_is_built_in_python_and_the_script_stays_deleted.py`.
"""

import pytest


def test_helm_target_args_refuses_a_target_that_cannot_be_addressed():
    """The refusal the splat depends on, named.

    A target built from a connection document has neither flag, so a helm call handed one has
    nothing to carry. Saying "and then we read `~/.kube/config`" would be the report's failure arriving
    from the other side: a release applied to whichever cluster the running user last looked at.
    """
    from kubementat.core.identity.target import ClusterTarget, helm_target_args

    with pytest.raises(Exception) as raised:
        helm_target_args(ClusterTarget(environment="dev", context=None, kubeconfig_path=None,
                                       address="https://example.test:6443",
                                       source="connection"))
    assert "kubeconfig" in str(raised.value).lower() or "kubecontext" in str(raised.value).lower()

    assert helm_target_args(ClusterTarget(environment="dev", context="dev", kubeconfig_path=None,
                                          address="https://x:6443",
                                          source="static.json")) == ["--kube-context", "dev"]
    assert helm_target_args(ClusterTarget(environment="dev", context=None,
                                          kubeconfig_path="/tmp/x", address="https://x:6443",
                                          source="kubeconfig_file")) == ["--kubeconfig", "/tmp/x"]
