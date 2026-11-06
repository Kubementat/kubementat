"""Measured: two clusters in one process, and the constructor that never asks.

The design buys this property here rather than paying for it later, because the future library API
is the thing that needs two clusters alive at once and the alternative — sweeping
`kubernetes_utils.py` later — is a worse ticket. So these tests are not a survey of the constructor;
they are the two claims the design rests on, each asserted once and each with its opposite shown to
be true of the shape it replaces.

**The property.** Nothing in the construction path reads or writes process state, so two
`KubernetesUtils` objects built from two `ClusterTarget`s answer for their own cluster, and building
the second does not change the first. The suite's two guard tests keep the ambient loader out of
`src/`;
this file proves what their absence buys, because "no global is touched" is only worth something if
"and the clients therefore disagree, correctly" is also true.

**The laziness.** No client is built until one is asked for, and the reason is a command, not a
benchmark: `kmt initialize` builds `Automation`, `Automation` builds this object, and `initialize`
writes JSON files and contacts nothing. A constructor that resolved a kubeconfig made that command
fail on a host that has none — which is why the refusal of a `target=None` `Context` lives where the
client is built and not in the constructor: refusing early is not safety, it is the same
failure with a shorter traceback.

The negative controls at the end exist so the two assertions above are not decorative: each shows the
shape the design replaces, in the same fixture, giving the wrong answer.
"""

import pytest
from kubernetes import client, config

from cluster_fixture import UNREAD_ROOT, DEV_SERVER, PROD_SERVER, write_kubeconfig
from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget
from kubementat.errors import ClusterNotConfiguredError
from kubementat.kubernetes_utils import KubernetesUtils


@pytest.fixture
def two_clusters(tmp_path):
    """One kubeconfig file holding two clusters, in a `tmp_path` no other test can see.

    Both contexts live in one file because that is how the property reads from the caller's side: the
    file is not the scarce thing, the answer is. Nothing here is contacted — resolving a context to an
    address is a YAML read and building a client is a `Configuration`, so this fixture is the
    whole of what the cluster means.
    """
    return write_kubeconfig(tmp_path / "kubeconfig", {"dev": DEV_SERVER, "prod": PROD_SERVER})


def target_on(kubeconfig, context):
    """The same construction a resolved `Context` would carry, minus the resolution.

    `from_kubeconfig` is the only constructor the resolution order selects, so this is a real target
    and not a
    stub: it carries the address the 1:1 check compares and the path a client will be read from.
    """
    return ClusterTarget.from_kubeconfig(str(kubeconfig), context, context)


def test_two_targets_in_one_process_never_share_a_client(two_clusters, monkeypatch):
    """Two `KubernetesUtils`, two clusters, neither one overwriting the other.

    The order is the test. `dev` is asked first and `prod` second, and then `dev` is asked again —
    because the way this fails is not a crash, it is `dev` quietly answering for `prod` after the
    second object is built. That is the exact interference — namespace on cluster A, release on
    cluster B — this design prevents, and it is why the second read matters more than the first.
    """
    # Starting clean, so that the assertion at the end means what it says and not what some earlier
    # test left behind. No global `Configuration` may be installed to build either client, and
    # `load_kube_config` is one of the things that does exactly that (shown below), so a return to it
    # would land here and not only in the guard test that scans `src/`.
    monkeypatch.setattr(client.Configuration, "_default", None)
    installed = []
    monkeypatch.setattr(client.Configuration, "set_default",
                        classmethod(lambda cls, default: installed.append(default)))

    dev = KubernetesUtils(target_on(two_clusters, "dev"))
    prod = KubernetesUtils(target_on(two_clusters, "prod"))

    dev_core = dev.core_api
    prod_core = prod.core_api

    assert dev_core is not prod_core, "the two utilities share one API object, so one is not on dev"
    assert dev_core.api_client is not prod_core.api_client

    assert dev.core_api.api_client.configuration.host == DEV_SERVER
    assert prod.core_api.api_client.configuration.host == PROD_SERVER

    # ...and asked a second time, after the other cluster was built, `dev` still means `dev`.
    assert dev.core_api.api_client.configuration.host == DEV_SERVER
    assert dev.api_client is not prod.api_client

    assert installed == [], "building a target's client installed a process-wide Configuration"
    assert client.Configuration._default is None


def test_building_the_utils_resolves_nothing_so_initialize_can_run_without_a_kubeconfig(
        tmp_path, monkeypatch):
    """As a failure mode: the constructor may not touch a kubeconfig, in any of its forms.

    The kubeconfig this target points at does not exist, so *anything* that reads it here would raise
    on the spot — a constructor that loaded eagerly fails this test the way it fails the command. The
    loader is also watched directly, so the first half is not inferred from the absence of an error.

    The second half is the other half of laziness: the client *is* built the moment an API object is
    asked for. A lazy constructor that stayed lazy forever would be a bug of a different kind, and
    both directions are asserted here so the seam cannot drift either way.
    """
    loads = []

    def watching_loader(*args, config_file=None, context=None, **kwargs):
        loads.append((config_file, context))
        raise AssertionError("a kubeconfig was read")

    monkeypatch.setattr("kubernetes.config.new_client_from_config", watching_loader)

    nowhere = tmp_path / "there-is-no-kubeconfig-here"
    target = ClusterTarget(environment="dev", context="dev", kubeconfig_path=nowhere,
                           address="https://dev.example.test:6443", source="flag")

    utils = KubernetesUtils(target)
    assert loads == [], "the constructor went looking for a kubeconfig"

    with pytest.raises(AssertionError, match="a kubeconfig was read"):
        utils.core_api
    assert loads == [(str(nowhere), "dev")], "the client was not built when an API object was asked for"


def test_a_client_cannot_be_built_against_an_unresolved_target(monkeypatch):
    """`Context.require_target`'s first call site — the guard that must not be decorative.

    Ticket 4 left the guard with no caller and said so, which is the right thing to do to a guard and
    the wrong thing to leave it doing. It has one now, and it is placed where the client is built
    rather than in the constructor, for the reason the laziness test above gives: `kmt initialize`
    builds this object through `Context.for_configuration`, and an early refusal would have failed the
    very command whose `target` is `None` on purpose.

    So the sequence is: constructing is legal, touching nothing is legal, and asking for a client is
    what says the sentence — naming the key that would fix it.
    """
    asked = []
    monkeypatch.setattr(ClusterTarget, "client",
                        lambda self: asked.append(self) or client.ApiClient())

    utils = KubernetesUtils(Context.for_configuration("dev", "ops",
                                                     platform_config_dir=UNREAD_ROOT))
    assert asked == [], "a command that configures a tree also addressed a cluster"

    with pytest.raises(ClusterNotConfiguredError) as raised:
        utils.core_api

    assert "CLUSTER_KUBE_CONTEXT" in str(raised.value)
    assert asked == [], "the client was built against a target that was never resolved"

    # The same refusal from every API object, not just the one the constructor used to build. The
    # accessors are passed around rather than called, because calling one inside the collection
    # raises outside the `raises` block that is meant to be measuring it.
    for ask in (lambda: utils.apps_api,
                utils.get_rbac_api,
                utils.get_custom_objects_api,
                lambda: utils.get_core_api()):
        with pytest.raises(ClusterNotConfiguredError):
            ask()


def test_an_api_object_built_from_the_process_answers_one_cluster_for_both(two_clusters, monkeypatch):
    """The opposite shape, shown once so the assertion above is not decoration.

    Nothing in `src/` does this any more, and the first guard test exists to keep it that way. The
    deleted constructor called `config.load_kube_config()` with no context, which answers from the
    file's `current-context` and installs the result as the process-wide default `Configuration`; a
    zero-argument API object then inherits whatever that default says. That is the whole of the
    interference, and it is worth watching once:

    two objects are asked to address two different clusters, both of them answer for the one the file
    calls current — here `dev` — and `prod`'s address never enters the picture at all. Not a crash, and
    not a wrong answer to a question nobody asked: the same answer to two different questions, with the
    second cluster silently absent.

    `monkeypatch` is not incidental: this test *does* install a process-wide default, and leaving it set
    would make the first test in this file lie in the other direction.
    """
    monkeypatch.setattr(client.Configuration, "_default", None)
    dev = KubernetesUtils(target_on(two_clusters, "dev"))
    prod = KubernetesUtils(target_on(two_clusters, "prod"))
    assert dev.core_api.api_client.configuration.host != prod.core_api.api_client.configuration.host

    # The same two questions, put to the shape this design replaces: no target, only the loader.
    config.load_kube_config(config_file=str(two_clusters))
    first = client.CoreV1Api().api_client.configuration.host
    config.load_kube_config(config_file=str(two_clusters))
    second = client.CoreV1Api().api_client.configuration.host

    assert client.Configuration._default is not None, "the ambient loader did not set the default"
    assert first == second == DEV_SERVER, (
        "the ambient loader did not answer both objects with the current-context it installed")
    assert second != PROD_SERVER, (
        "the address the second object was meant to answer for never entered the answer at all")
