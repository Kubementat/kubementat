"""Kubernetes operations against one cluster: one `KubernetesUtils`, assembled from its areas.

The class is the union of the areas and not a re-export of a module that already held them. Every
method still answers to the same name, the constructor still takes the `Context` a command resolved
(or the `ClusterTarget` a library caller or a test addresses one with) and still accepts an
injected `api_client`, because those three shapes are how the rest of the tree and the suite reach
a cluster: a command builds the object, hands it across a module boundary to whoever needs a read,
and a test that cannot get past the constructor of the object this class is built into injects
through the target's own client factory, because that constructor is the only part of the seam that
resolves anything. A class that had to be renamed, or a set of module functions a caller had to be told about, would have moved that seam, and an unchanged suite is
not the same thing as an unchanged contract.

The areas are what made the split worth doing: one file per kind of thing the cluster is asked
about, so that "where does this read live" has an answer a reader can find. The composition is
the only place they are joined -- an area class is not usable on its own, because a namespace
method reaches for the client the assembled instance provides and not for one it imported.
Nothing here is re-exported beyond the class itself, and the order the bases are listed in is the
one the dependency rule reads bottom-up: clients at the bottom, then the resources that read
them, then the sessions that read the resources.
"""

from kubementat.infrastructure.kubernetes.clients import KubernetesClients
from kubementat.infrastructure.kubernetes.customresources import KubernetesCustomResources
from kubementat.infrastructure.kubernetes.exec import KubernetesExec
from kubementat.infrastructure.kubernetes.namespaces import KubernetesNamespaces
from kubementat.infrastructure.kubernetes.rbac import KubernetesRbac
from kubementat.infrastructure.kubernetes.secrets import KubernetesSecrets
from kubementat.infrastructure.kubernetes.serviceaccounts import KubernetesServiceAccounts
from kubementat.infrastructure.kubernetes.tunnel import KubernetesTunnel
from kubementat.infrastructure.kubernetes.workloads import KubernetesWorkloads


class KubernetesUtils(KubernetesExec,
                      KubernetesWorkloads,
                      KubernetesCustomResources,
                      KubernetesSecrets,
                      KubernetesServiceAccounts,
                      KubernetesRbac,
                      KubernetesNamespaces,
                      KubernetesTunnel,
                      KubernetesClients):
    """Kubernetes operations against one `ClusterTarget`, and never against "this process".

    The rule in one sentence: this class binds, it does not resolve. It is told which cluster it
    is talking to -- by a `Context` from a command, or by a `ClusterTarget` from the library or a test
    -- and every client it builds comes out of that target alone. The old constructor asked the
    kubernetes library which cluster it meant, which made the answer a
    fact about the machine running the process rather than about the command, and that is what this
    class refuses to keep: a library that holds one process-wide answer cannot hold two clusters.

        Everything is built on first use. That is not a performance preference: the
        configuration-only context (`Context.for_configuration`) builds this object to write JSON
        files and never addresses a cluster, and a
        constructor that resolved a kubeconfig made that path fail on a host with none -- which is
        the same argument `Context.for_configuration` makes about the target itself.

    The methods are not here: they live one area per file, under the names this class is called
    by, and this composition is what lets a session read a pod's logs and a report read the same
    pod's status without either owning the lookup that finds it.
    """
