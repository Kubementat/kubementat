"""One process, two clusters, and the check that must be asked twice."""

from types import SimpleNamespace

from cluster_fixture import write_kubeconfig

from kubementat.core.identity.target import ClusterTarget
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import preflight

# Two servers, named here and not borrowed from `cluster_fixture`. The cache is process-wide by
# design, so any address this file reused is an address some earlier test may already have answered
# for — and that would make this file's verdict depend on the order it ran in, which is the one way
# a cache test can be quietly worthless. These two belong to nothing else.
CLUSTER_A_SERVER = "https://10.9.1.1:6443"
CLUSTER_B_SERVER = "https://10.9.2.2:6443"

# Only what `preflight_tekton_api_version` reads: the group and the version kubementat speaks.
SPOKEN_VERSION = SimpleNamespace(TEKTON_API_GROUP="tekton.dev", TEKTON_API_VERSION="v1")


def a_target_on(kubeconfig, context):
    """A real `ClusterTarget`, read out of a real kubeconfig by its own context."""
    return ClusterTarget.from_kubeconfig(str(kubeconfig), context, context)


def two_clusters(tmp_path):
    """One file, two contexts, two servers — and nothing contacted to build either of them."""
    return write_kubeconfig(tmp_path / "kubeconfig",
                            {"alpha": CLUSTER_A_SERVER, "beta": CLUSTER_B_SERVER})


def asking_the_served_version(monkeypatch):
    """The discovery question, watched per cluster instead of answered by a socket."""
    asked = []

    def is_served(self, group, version):
        asked.append(self.target.address)
        return True

    monkeypatch.setattr(KubernetesUtils, "api_version_is_served", is_served)
    return asked


def test_both_clusters_are_asked_and_neither_is_asked_twice(tmp_path, monkeypatch):
    """The case the split could have lost silently: a second cluster in one process."""
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
