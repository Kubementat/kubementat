"""One process, two clusters, and the check that must be asked twice.

The version preflight is the one read on the Tekton path that answers *about a cluster* rather than
about a call, and that is the only reason it can be cached at all. It used to be cached per object:
the answer lived as a class attribute that the first successful check shadowed onto the instance,
so every `TektonUtils` in a process carried its own copy. The split moves the function out of the
class, and a function has no instance to write to — which is exactly the moment a module-level flag
looks free. It is not free. A flag says "some cluster was verified", and the second cluster a
command addresses then skips a check it never made: it gets the first cluster's answer, and if that
answer is "v1 is served", a cluster that serves nothing goes on applying manifests one 404 at a
time. This file is that case, because nothing else in the suite touches the cache at all.

The shape is `test_two_clusters_can_be_addressed_by_one_process_at_once.py`'s, and for the same
reason: the property is worth more than the assertion's convenience. What that file proves for the
clients, this proves for the one cached answer that sits above them.
"""

from types import SimpleNamespace

import pytest

from cluster_fixture import write_kubeconfig
from kubementat.core.identity.target import ClusterTarget
from kubementat.errors import TektonControllerWithoutConfiguredApiError
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import preflight

# Two servers, named here and not borrowed from `cluster_fixture`. The cache is process-wide by
# design, so any address this file reused is an address some earlier test may already have answered
# for — and that would make this file's verdict depend on the order it ran in, which is the one way
# a cache test can be quietly worthless. These two belong to nothing else.
CLUSTER_A_SERVER = "https://10.9.1.1:6443"
CLUSTER_B_SERVER = "https://10.9.2.2:6443"
# The second test needs a cluster nothing in this file has already had verified, for the same
# reason the two above borrow nothing: a refused answer is only visible as a *re-ask* if that
# address was not already cached by the test that ran before it.
CLUSTER_C_SERVER = "https://10.9.3.3:6443"

# Only what `preflight_tekton_api_version` reads: the group and the version kubementat speaks.
SPOKEN_VERSION = SimpleNamespace(TEKTON_API_GROUP="tekton.dev", TEKTON_API_VERSION="v1")


def a_target_on(kubeconfig, context):
    """A real `ClusterTarget`, read out of a real kubeconfig by its own context.

    `from_kubeconfig` is what the resolution order selects, so the address the cache compares is
    the address a command would have carried, and not a field a test invented for it.
    """
    return ClusterTarget.from_kubeconfig(str(kubeconfig), context, context)


def two_clusters(tmp_path):
    """One file, two contexts, two servers — and nothing contacted to build either of them."""
    return write_kubeconfig(tmp_path / "kubeconfig",
                            {"alpha": CLUSTER_A_SERVER, "beta": CLUSTER_B_SERVER})


def asking_the_served_version(monkeypatch):
    """The discovery question, watched per cluster instead of answered by a socket.

    It is patched on the *class* so that every `KubernetesUtils` answers from the same recorder and
    the recording can name which cluster asked — which is what the assertion below needs, and what a
    shared answer would blur.
    """
    asked = []

    def is_served(self, group, version):
        asked.append(self.target.address)
        return True

    monkeypatch.setattr(KubernetesUtils, "api_version_is_served", is_served)
    return asked


def test_both_clusters_are_asked_and_neither_is_asked_twice(tmp_path, monkeypatch):
    """The case the split could have lost silently: a second cluster in one process.

    The order is the whole test. `alpha` is asked first, and `beta` second — because the way this
    fails is not a crash, it is `beta` quietly inheriting `alpha`'s verdict and never being asked.
    The second half is the cache still working: after both answers are in, nothing is asked again,
    including for a second `KubernetesUtils` built to address the cluster the first one already
    answered for. That is the property of a cache keyed by cluster rather than by object, and it is
    the half that makes the first half worth having.
    """
    kubeconfig = two_clusters(tmp_path)
    asked = asking_the_served_version(monkeypatch)

    alpha = KubernetesUtils(a_target_on(kubeconfig, "alpha"))
    beta = KubernetesUtils(a_target_on(kubeconfig, "beta"))

    preflight._preflight_tekton_api_version(alpha, SPOKEN_VERSION)
    assert asked == [alpha.target.address], (
        "the first cluster was not asked the question it is about to act on")

    preflight._preflight_tekton_api_version(beta, SPOKEN_VERSION)
    assert asked == [alpha.target.address, beta.target.address], (
        f"a cluster that is not the one already asked was not asked itself: {asked}. The answer "
        "this function caches is per cluster, and a shared 'verified' is the failure.")

    # ...and a second object on the cluster that has already answered, which is the dedup a
    # cluster-keyed cache gets and an object-keyed one would not.
    preflight._preflight_tekton_api_version(KubernetesUtils(a_target_on(kubeconfig, "alpha")),
                                            SPOKEN_VERSION)
    preflight._preflight_tekton_api_version(KubernetesUtils(a_target_on(kubeconfig, "beta")),
                                            SPOKEN_VERSION)
    assert asked == [alpha.target.address, beta.target.address], (
        "a cluster already verified was asked again, so the answer is not held per cluster")


def test_a_cluster_that_refused_the_question_is_asked_again(tmp_path, monkeypatch):
    """The cache holds answers, not hopes.

    Nothing is written until the check has passed, so a cluster that answered `no` gets asked the
    same question on the next call instead of being believed verified for the rest of the process —
    which is what the first version of this cache would have done to a cluster that was merely not
    up yet, and the reason the write sits after the call rather than before it.
    """
    kubeconfig = write_kubeconfig(tmp_path / "kubeconfig", {"gamma": CLUSTER_C_SERVER})
    asked = []

    def refuses(self, group, version):
        asked.append(self.target.address)
        return False

    monkeypatch.setattr(KubernetesUtils, "api_version_is_served", refuses)
    cluster = KubernetesUtils(a_target_on(kubeconfig, "gamma"))

    for _ in range(2):
        with pytest.raises(TektonControllerWithoutConfiguredApiError):
            preflight._preflight_tekton_api_version(cluster, SPOKEN_VERSION)

    assert asked == [cluster.target.address, cluster.target.address], (
        "a refused cluster was believed verified: the answer is written before the check is asked")
