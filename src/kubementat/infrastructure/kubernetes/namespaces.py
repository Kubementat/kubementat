"""Namespaces, and the wait that lets an uninstall say what it actually deleted.

Deleting a namespace returns as soon as the request is accepted, so "asked" and "gone" are two
facts and the gap between them is measured here rather than guessed at. The four verbs the
cluster answers about a namespace are here too, because they are the same read the teardown
diffs before and after.
"""

import logging
import time

from kubernetes import client

from kubementat.infrastructure.kubernetes.clients import KubernetesClients


# Namespace deletion is asynchronous, and a measured one rather than a guessed one: a full lane's
# own watch log showed ten kubementat namespaces draining over 42 s on a 4 vCPU guest while
# `kmt uninstall` reported "still present" four seconds after firing the deletes. Nothing was
# blocking them -- the cluster simply was not finished. The ceiling is that observation with room
# for a slower host; the interval is what makes the wait a poll and not a sleep.
NAMESPACE_GONE_WAIT_S = 180

NAMESPACE_POLL_INTERVAL_S = 1.0




class KubernetesNamespaces(KubernetesClients):
    """Namespaces: created, listed, deleted, waited for, and reported by phase.

    Composed into `KubernetesUtils` rather than used on its own, so that a namespace read reaches
    the client built here through the one instance a command already holds, and not through a
    second object handed down beside it.
    """
    # namespace helpers
    def create_namespace(self, namespace: str) -> None:
        """
        Create a Kubernetes namespace if it doesn't exist.

        Args:
            namespace (str): Name of the namespace to create.

        Raises:
            Exception: If creation fails with an error other than 409 (already exists).
        """
        try:
            logging.info(f"Creating namespace: '{namespace}'")
            self.core_api.create_namespace(
                body=client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=namespace)
                )
            )
        except client.exceptions.ApiException as e:
            if e.status == 409:  # Already exists
                logging.info(f"Namespace '{namespace}' already exists, skipping creation")
            else:
                logging.error(f"Failed to create namespace '{namespace}': {e}")
                raise

    def delete_namespace(self, namespace: str, ignore_not_found: bool = True) -> None:
        """
        Delete a Kubernetes namespace

        Args:
            namespace (str): Name of the namespace to delete.
            ignore_not_found (bool): Tolerate a namespace that is already gone. Deleting a
                list of namespaces one of which was never created is a normal uninstall,
                so this defaults to True; a cluster-wide delete that must prove absence
                sets it to False.

        Raises:
            Exception: If deletion fails with a status other than 404
        """
        try:
            logging.info(f"Deleting namespace: '{namespace}'")
            self.core_api.delete_namespace(name=namespace)
        except client.exceptions.ApiException as e:
            if e.status == 404 and ignore_not_found:
                logging.info(f"Namespace '{namespace}' does not exist, skipping deletion")
            else:
                logging.error(f"Failed to delete namespace '{namespace}': {e}")
                raise

    def list_namespaces(self) -> list:
        """
        Names of every namespace on the cluster.

        Returns:
            list[str]: Namespace names, unsorted.

        Used by uninstall to diff the cluster before and after: what is left over is the
        evidence that the uninstall did its job.
        """
        return [item.metadata.name for item in self.core_api.list_namespace().items]

    def wait_namespaces_gone(self, namespaces, timeout=None,
                             interval=NAMESPACE_POLL_INTERVAL_S) -> list:
        '''
        Poll until the cluster reports none of `namespaces`, and return the ones it still reports.

        Deleting a namespace returns as soon as the request is accepted, not when the namespace is
        gone, so an uninstall that fires deletes and then looks has to either wait or say nothing
        about what it has not yet proven. This waits, bounded and polled, and hands back the
        survivors for the caller to name.

        No failure is raised here on purpose: what a survivor means depends on the command, and
        `uninstall` has a residue list to put it in. Silence is the failure mode this avoids.

        Args:
            namespaces (list): Namespace names to wait on, order preserved for the log.
            timeout (float): Ceiling in seconds. Polled, never slept past.
            interval (float): Seconds between reads.

        Returns:
            list[str]: Namespaces the cluster still reports, in the order they were asked for,
                       empty when the cluster finished the deletion inside the ceiling.
        '''
        wanted = list(dict.fromkeys(namespaces))
        if not wanted:
            return []
        ceiling = NAMESPACE_GONE_WAIT_S if timeout is None else timeout
        deadline = time.time() + ceiling
        survivors = self._namespaces_present(wanted)
        while survivors and time.time() < deadline:
            time.sleep(interval)
            survivors = self._namespaces_present(wanted)
        return survivors

    def service_endpoint_addresses(self, namespace: str, name: str) -> list:
        '''
        The addresses the Endpoints object behind a Service currently advertises.

        This is the read that answers "can the API server reach this webhook", which is not the
        same question as "is the Deployment rolled out": a Service that has just been created has
        no Endpoints object at all, and one whose pods are not passing their readiness probes has
        an object with empty `addresses`. Asking the Deployment about `readyReplicas` would answer
        neither, because it describes the pods the controller expects, not the sockets the API
        server will actually dial.

        A missing Endpoints object is not an error: it means nothing is reachable yet, which is
        what a caller waiting for readiness needs to know, and the alternative -- a 404 on the
        first read after an apply, before the service controller has written anything -- would put
        a stack trace where a `not yet` belongs. Any other failure is the API server refusing to
        answer, and it propagates.

        Args:
            namespace: The namespace holding the Service.
            name: The Service name, which is also the Endpoints name.

        Returns:
            list[str]: Pod addresses the Service resolves to, empty when nothing is reachable.
        '''
        try:
            endpoints = self.get_core_api().read_namespaced_endpoints(name=name, namespace=namespace)
        except client.exceptions.ApiException as error:
            if error.status == 404:
                return []
            raise
        return [
            str(address.ip)
            for subset in (endpoints.subsets or [])
            for address in (subset.addresses or [])
        ]

    def namespace_phases(self, namespaces) -> dict:
        '''
        What phase each namespace is in, so a survivor can be reported as `Terminating` and not
        as a bare name. A namespace that vanished between the read and the report is simply absent
        from the answer, which is the honest representation of a cluster mid-deletion.

        Args:
            namespaces (list): Namespace names to look up.

        Returns:
            dict: namespace name -> phase string, for the names that still exist.
        '''
        phases = {}
        for item in self.core_api.list_namespace().items:
            if item.metadata.name in set(namespaces):
                # `status` is there on a real namespace and absent on a hand-built stand-in, and
                # an exception here would land inside a report about why deleting failed.
                status = getattr(item, "status", None)
                phases[item.metadata.name] = (getattr(status, "phase", None) if status else None) or "Unknown"
        return phases

    def _namespaces_present(self, wanted) -> list:
        seen = set(self.list_namespaces())
        return [name for name in wanted if name in seen]
