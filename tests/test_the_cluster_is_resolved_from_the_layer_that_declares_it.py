"""Which layer gets to say where a command is going — and the answer is the highest one that speaks.

One order for every command, and this is the only place it is implemented. The
layers are:

```
1. --context / --kubeconfig           the command's own flags
2. CLUSTER_KUBE_CONTEXT / CLUSTER_KUBECONFIG_PATH   the process environment
3. env_static's two keys              platform_config/{environment}/static.json
4. otherwise: error                   the refusal, which is the interface now
```

The reason the order matters is not tidiness: layer 2 exists so CI can point one checkout at a
cluster without editing a tracked file, and layer 3 exists so the environment itself declares its
cluster. A command that read the static file first would ignore what the operator typed; a
command that read the process environment first would ignore the environment it was pointed at.

Each test below makes one layer say something the layer below it also answers, so an inverted order
shows up as a wrong address rather than as a passing test.

**What the contradiction check changed about this file, and what it did not.** A *name* stated above
the file may now
only answer for an environment that declared none, and a *file* stated above the file is read for
the name the environment declares — a run that contradicts a declaration is refused instead of
resolved. That half is tested in
`test_a_stated_cluster_is_checked_against_the_cluster_the_environment_declares.py`. So the order of
the layers is checked here where it is still observable, in the two places a precedence can be
demonstrated without an operator's environment disagreeing with itself: **which file gets opened**
(the tests that give the same context name to two files) and **which name answers** when the file
has none. Nothing about the ordering moved: layer 1 still beats layer 2, layer 2 still beats
layer 3, and what changed is only that a statement above the file is not allowed to contradict what
is written in it.

"""

import pytest

from kubementat.cluster import Context
from kubementat.core.constants import SOURCE_FLAG, STATIC_SOURCE_PREFIX
from kubementat.errors import ClusterNotConfiguredError

from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    STAGING_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)


def fixture_root(tmp_path, monkeypatch, kubeconfig_name="kubeconfig"):
    """One checkout with three environments on three clusters, and one cluster nobody claims.

    The spare context is there because the 1:1 check is real: pointing a command at a cluster that
    another environment already declared is a refusal, not a resolution, so a precedence test needs
    a cluster that is only reachable from the layer under test.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(
        root / kubeconfig_name,
        {
            "dev-static": DEV_SERVER,
            "prod-static": PROD_SERVER,
            "staging-static": STAGING_SERVER,
            "unclaimed": "https://10.0.9.9:6443",
        },
        current_context="dev-static",
    )
    for environment, context in (
        ("dev", "dev-static"),
        ("prod", "prod-static"),
        ("staging", "staging-static"),
    ):
        write_environment(root, environment, context=context, kubeconfig_path=kubeconfig)
    return root


def test_the_kubeconfig_a_command_tells_outranks_the_variable_and_the_tree(tmp_path, monkeypatch):
    """Layer 1's path, over layer 2's path, over layer 3's path, in one run.

    The name is not in dispute here — `dev-static` is what the environment declares and it is what
    all three files are asked about (the comparison is a match, not a contradiction) — so the
    address is the only answer that says which file was opened. Three files, three addresses, and
    the flag's file wins.
    """
    root = fixture_root(tmp_path, monkeypatch)
    process_kubeconfig = write_kubeconfig(root / "process", {"dev-static": "https://10.0.9.1:6443"})
    flag_kubeconfig = write_kubeconfig(root / "flagged", {"dev-static": "https://10.0.9.2:6443"})
    monkeypatch.setenv("CLUSTER_KUBECONFIG_PATH", str(process_kubeconfig))

    ctx = Context.build("dev", "the-team", kubeconfig=flag_kubeconfig)

    assert ctx.target.address == "https://10.0.9.2:6443"
    assert ctx.target.kubeconfig_path == flag_kubeconfig


def test_the_kubeconfig_a_process_variable_names_is_read_when_no_flag_names_one(tmp_path,
                                                                                monkeypatch):
    """Layer 2's path. This is the layer a pipeline uses to aim a checkout somewhere without
    committing the change, so the tree's own `static.json` must not outvote it — and the name still
    comes from the tree, which is what keeps this a *where* dial and not a *which environment* one.
    """
    root = fixture_root(tmp_path, monkeypatch)
    ci_kubeconfig = write_kubeconfig(root / "ci-kubeconfig", {"dev-static": "https://10.0.9.1:6443"})
    monkeypatch.setenv("CLUSTER_KUBECONFIG_PATH", str(ci_kubeconfig))

    ctx = Context.build("dev", "the-team")

    assert ctx.target.address == "https://10.0.9.1:6443"
    assert ctx.target.kubeconfig_path == ci_kubeconfig


def test_the_environment_configuration_answers_when_nothing_above_it_speaks(tmp_path, monkeypatch):
    """Layer 3, which is the normal case: the environment declares its own cluster.

    The report says which file the claim came from, so an operator reading it can go look at the
    file the answer was read out of without being told to run anything.
    """
    root = fixture_root(tmp_path, monkeypatch)

    ctx = Context.build("prod", "the-team")
    static_path = root / "platform_config" / "prod" / "static.json"

    assert ctx.target.address == PROD_SERVER
    assert ctx.target.source == f"{STATIC_SOURCE_PREFIX}{static_path}"
    assert str(static_path) in ctx.resolution_report[0]


def test_the_context_a_command_tells_answers_for_an_environment_that_declares_none(
        tmp_path, monkeypatch):
    """Layer 1's name, over layer 2's name, where the file made no claim to contradict.

    This is the ordering layer 1 still has: `dev` names no cluster, so whatever the run states is
    the answer and not a disagreement. Both layers above the file speak, and the flag's name wins,
    which is the same precedence the path tests read — the order of the layers is one rule and it
    does not depend on which half of the claim is being made.
    """
    root = fixture_root(tmp_path, monkeypatch)
    write_environment(root, "dev", context=None, kubeconfig_path=root / "kubeconfig")
    monkeypatch.setenv("CLUSTER_KUBE_CONTEXT", "prod-static")

    ctx = Context.build("dev", "the-team", context="unclaimed")

    assert ctx.target.address == "https://10.0.9.9:6443"
    assert ctx.target.source == SOURCE_FLAG
    assert "flag --context" in ctx.resolution_report[0]


def test_a_process_variable_that_is_set_but_empty_is_refused_rather_than_read_as_unset(tmp_path, monkeypatch):
    """An empty `CLUSTER_KUBE_CONTEXT` is a mistake, not a way to say "skip this layer".

    The same position `paths.py:_directory_from_environment` takes for `KUBEMENTAT_HOME`:
    silently falling back on an empty variable is how a pipeline that forgot to export a value
    ends up installing into the cluster that happens to be in the tree.
    """
    root = fixture_root(tmp_path, monkeypatch)
    monkeypatch.setenv("CLUSTER_KUBE_CONTEXT", "   ")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert "set but empty" in str(refused.value)
    assert "CLUSTER_KUBE_CONTEXT" in str(refused.value)


def test_the_context_a_service_account_or_kubectl_would_pick_is_not_an_answer(tmp_path, monkeypatch):
    """Nothing above layer 3 means an error, and never a `current-context`.

    This is the load-bearing part of the behaviour change. The fixture kubeconfig has a perfectly
    usable `current-context`, the environment's own declaration is removed, and the answer is not
    the obvious cluster — it is a refusal that names the file.
    """
    root = fixture_root(tmp_path, monkeypatch)
    write_environment(root, "dev", context=None)

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert str(root / "kubeconfig") not in str(refused.value)
    assert "current-context" in str(refused.value)
    assert "fallback" in str(refused.value)
    assert refused.value.reason
