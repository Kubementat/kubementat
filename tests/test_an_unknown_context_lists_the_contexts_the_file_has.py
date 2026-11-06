"""A context that is not in the resolved kubeconfig is answered with the list of contexts that are.

The design calls this out by name: an operator who typed `prod-eu` for `prod-europe` gets the
answer,
not a mystery. It is also the cheapest part of the design — the contexts are in the file that was
just read, so refusing is free, and there is no cluster to ask.

The cases below are the ones that otherwise turn into an hour of `kubectl config get-contexts`:
a typo, a context that exists but whose cluster entry does not, a cluster with no server, and a
file that is not a kubeconfig at all.

"""

import pytest

from kubementat.cluster import (
    Context,
    ClusterTarget,
)
from kubementat.errors import (
    ClusterContextNotFoundError,
    ClusterFileUnreadableError,
)

from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
    write_kubeconfig_with_cluster,
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A checkout whose kubeconfig has exactly three contexts, with a typo waiting to be made."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_kubeconfig(
        root / "kubeconfig",
        {"dev": DEV_SERVER, "prod-europe": PROD_SERVER, "staging-eu": "https://10.0.7.2:6443"},
        current_context="dev",
    )
    return root


def declare(root, environment, context, kubeconfig_path=None):
    return write_environment(root, environment, context=context, kubeconfig_path=kubeconfig_path)


def test_the_typo_gets_the_list_of_contexts_the_file_actually_has(root):
    """"`prod-eu` instead of `prod-europe`" is the failure this sentence was written for."""
    declare(root, "prod", "prod-eu", root / "kubeconfig")

    with pytest.raises(ClusterContextNotFoundError) as refused:
        Context.build("prod", "the-team")

    assert "prod-eu" in str(refused.value)
    assert "prod-europe" in str(refused.value)
    assert "dev" in str(refused.value)
    assert "staging-eu" in str(refused.value)


def test_the_alternatives_are_offered_even_when_the_target_was_built_directly(root):
    """Same refusal at both doors, so the library path is not the one that says less.

    A caller that only has the target — no `Context`, no command — still gets the list, and gets
    it in a typed attribute it can show a human rather than parsing a sentence.
    """
    with pytest.raises(ClusterContextNotFoundError) as refused:
        ClusterTarget.from_kubeconfig(str(root / "kubeconfig"), "prod-eu", "prod")

    assert refused.value.available == ("dev", "prod-europe", "staging-eu")
    assert "prod-europe" in str(refused.value)


def test_the_name_of_the_file_that_was_opened_is_in_the_refusal(root):
    """The context is absent from *this* file, so the file has to be in the sentence.

    When `$KUBECONFIG` holds more than one file, "which one did you look in" is the first question,
    and the refusal answers it before it is asked.
    """
    declare(root, "prod", "prod-eu", root / "kubeconfig")

    with pytest.raises(ClusterContextNotFoundError) as refused:
        Context.build("prod", "the-team")

    assert str(root / "kubeconfig") in str(refused.value)


def test_a_context_that_names_a_cluster_the_file_does_not_carry_names_both(root):
    """The context is fine, the cluster it points at is not, and the gap is the whole message.

    A dangling reference is the shape a hand-merged kubeconfig gets, and a refusal that said only
    "no such context" would be describing a problem this file does not have.
    """
    kubeconfig = write_kubeconfig_with_cluster(
        root / "dangling",
        {"prod-europe": "cluster-that-is-not-there"},
        {"some-other-cluster": {"server": PROD_SERVER}},
    )

    with pytest.raises(ClusterFileUnreadableError) as refused:
        ClusterTarget.from_kubeconfig(str(kubeconfig), "prod-europe", "prod")

    assert "prod-europe" in str(refused.value)
    assert "cluster-that-is-not-there" in str(refused.value)


def test_a_cluster_entry_that_declares_no_server_is_not_an_empty_address(root):
    """An address that is absent and an address that is empty both refuse.

    The collision check compares addresses; an environment whose cluster has no server would compare
    `""` against every other misconfigured environment, which is worse than refusing.
    """
    kubeconfig = write_kubeconfig_with_cluster(
        root / "no-server", {"prod-europe": "empty-cluster"}, {"empty-cluster": {}}
    )

    with pytest.raises(ClusterFileUnreadableError) as refused:
        ClusterTarget.from_kubeconfig(str(kubeconfig), "prod-europe", "prod")

    assert "server" in str(refused.value)
    assert "empty-cluster" in str(refused.value)


def test_a_file_that_is_not_a_kubeconfig_says_it_is_not_a_kubeconfig(root):
    """Someone pointed `CLUSTER_KUBECONFIG_PATH` at something that is not one. The answer is not
    a stack trace, and the two shapes of "not one" are told apart.

    A YAML document that is not a mapping is refused as what it is, and so is a file that cannot
    be decoded — a locked `static.encrypted.json` is the same mistake wearing different bytes.
    """
    not_a_mapping = root / "manifest-list.yaml"
    not_a_mapping.write_text("- one\n- two\n", encoding="utf-8")
    locked = root / "static.encrypted.json"
    locked.write_bytes(b"\x00\x01git-crypt\xc3\x28\xc3\xb1")

    with pytest.raises(ClusterFileUnreadableError) as refused:
        ClusterTarget.from_kubeconfig(str(not_a_mapping), "dev", "dev")
    assert str(not_a_mapping) in str(refused.value)

    with pytest.raises(ClusterFileUnreadableError) as refused:
        ClusterTarget.from_kubeconfig(str(locked), "dev", "dev")
    assert str(locked) in str(refused.value)


def test_an_environment_with_no_kubeconfig_file_names_the_file_and_the_key(root):
    """The other direction: the context is fine, the file it lives in is gone.

    The message names the path and the key that pointed there, because the operator has to know
    whether the file moved or the declaration did.
    """
    declare(root, "prod", "prod-europe", root / "moved-away")

    with pytest.raises(ClusterFileUnreadableError) as refused:
        Context.build("prod", "the-team")

    assert "moved-away" in str(refused.value)
    assert "CLUSTER_KUBECONFIG_PATH" in str(refused.value)
