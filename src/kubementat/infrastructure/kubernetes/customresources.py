"""Custom objects, the definitions that declare them, and the deletes a sweep finishes with.

The custom-object client addresses a resource by group, version and plural, so the plural is
derived once, here: an install that spells it differently per call is one typo away from writing
into an API group that answers with a 404.

A cluster-scoped object cannot be deleted by name alone -- the generated clients are per kind --
so the generic delete dispatches the kind to the API object that owns it, which is the whole
content of that method and the reason it is stated once rather than in the one caller that needs
it.
"""

import logging

from kubernetes import client

from kubementat.core import quantities
from kubementat.infrastructure.kubernetes.clients import KubernetesClients


class DeleteOutcome(tuple):
    """Whether an object is gone, and the server's own sentence when it is not.

    A tuple rather than a bool because the residue list names the reason and not just the verdict
    — the same fact `kubectl` used to put on stderr, now arriving where the code can actually read
    it. It is truthy exactly when the object is gone, so `if utils.delete_...(...)` reads the way
    it did before and `outcome.reason` reads the way the error deserves.
    """

    def __new__(cls, deleted: bool, reason: str = ""):
        return super().__new__(cls, (deleted, reason))

    @property
    def deleted(self) -> bool:
        return self[0]

    @property
    def reason(self) -> str:
        return self[1]

    def __bool__(self) -> bool:
        return bool(self[0])


def _reason(error) -> str:
    """The server's own sentence about a refusal, without the wrapper the SDK adds.

    `ApiException.body` is the JSON the API server actually answered with and `reason` is the HTTP
    phrase; which one carries something useful depends on the failure, so both are tried and the
    first that says something is used. A refusal quoted from the wrong field reads as `Forbidden`
    and tells an operator nothing about their own volume.
    """
    body = getattr(error, "body", None)
    if body:
        text = body if isinstance(body, str) else str(body)
        try:
            import json as _json

            decoded = _json.loads(text)
            message = decoded.get("message") if isinstance(decoded, dict) else None
            if message:
                return str(message)
        except ValueError:
            return text
    return str(getattr(error, "reason", "") or error)




class KubernetesCustomResources(KubernetesClients):
    """Custom objects, the definitions that declare them, and the deletes a sweep finishes with.

    Composed into `KubernetesUtils`. The plural of a kind is derived here and nowhere else, which
    is what keeps an install from writing the same resource to two different paths; the
    cluster-scoped delete dispatches a kind to the API object that owns it rather than guessing
    from the name.
    """
    def list_ingress_classes(self) -> list:
        """Every IngressClass on the cluster.

        `NetworkingV1Api.list_ingress_class`, which is `kubectl get ingressclass --output json` and
        the same object shape: `spec.controller` is what both the nginx sweep and the Traefik
        discovery read.

        Raises:
            client.exceptions.ApiException: When the cluster will not answer. A caller that has to
                treat "could not ask" differently from "there is nothing here" catches this and
                says which; the two answers must not be merged, and the uninstall sweeps are the
                place that says so.
        """
        return self.networking_api.list_ingress_class().items

    def delete_ingress_class(self, name: str) -> bool:
        """One IngressClass, gone. `False` when the cluster would not delete it.

        The 404 tolerance every delete in this class already has, kept for the same reason
: a class somebody else already removed is a class that is gone, and an uninstall
        that reports it as residue is claiming a failure the cluster never reported.
        """
        try:
            self.networking_api.delete_ingress_class(name=name)
            return DeleteOutcome(True)
        except client.exceptions.ApiException as error:
            if error.status == 404:
                logging.info(f"IngressClass '{name}' is already gone, so there was nothing to "
                             "delete.")
                return DeleteOutcome(True)
            logging.warning(f"The cluster would not delete the IngressClass '{name}'"
                            f" (status {error.status}): {error}")
            return DeleteOutcome(False, _reason(error))

    def list_persistent_volume_claims(self, namespace: str, selector: str = None) -> list:
        """The claims in `namespace`, optionally narrowed by `selector`.

        One read for both halves of the product (`automation.py`'s uninstall sweep and
        `helm_utils`'s release-claim delete, which asked kubectl the same question in its own
        words until here). That divergence is the reason this bucket exists.

        Raises:
            client.exceptions.ApiException: When the namespace cannot be read. An unreadable
                namespace is not an empty one, and both callers need that difference.
        """
        return self.core_api.list_namespaced_persistent_volume_claim(
            namespace=namespace, label_selector=selector).items

    def delete_persistent_volume_claim(self, namespace: str, name: str) -> "DeleteOutcome":
        """One claim, gone, and what the cluster said when it refused.

        A `bool` cannot carry the server's sentence, and the sentence is what makes a residue list
        actionable: the old code got it off stderr, and a bare `False` would have put it back.
        Truthiness is preserved deliberately, so a caller that only asks "did it go" still reads
        the same answer.

        Same 404 tolerance as `delete_ingress_class`, and for the same reason: while a namespace is
        draining the API is still releasing volumes, so a name that is already away is the answer
        the teardown wanted.

        It is said out loud when it happens. Silence would be indistinguishable from a sweep that
        never looked, and the sentence the caller prints after a refusal — "not there anymore, for
        all that the delete refused it" — is not the sentence this one needs.
        """
        try:
            self.core_api.delete_namespaced_persistent_volume_claim(name=name,
                                                                    namespace=namespace)
            return DeleteOutcome(True, "")
        except client.exceptions.ApiException as error:
            if error.status == 404:
                logging.info(f"Claim '{name}' in namespace '{namespace}' is already gone, so the "
                             "teardown has nothing left to do about it.")
                return DeleteOutcome(True, "")
            logging.warning(f"The cluster would not delete claim '{name}' in namespace "
                            f"'{namespace}' (status {error.status}): {error}")
            return DeleteOutcome(False, _reason(error))

    def list_custom_resource_definitions(self) -> list:
        """Every CustomResourceDefinition the cluster serves.

        `ApiextensionsV1Api.list_custom_resource_definition` is `kubectl get crd`; the name of a
        definition is its fully-qualified `group/plural`, which is what the group suffix sweeps
        compare against.

        The shape is `kubectl get crd`'s, and the name of a definition is its
        fully-qualified `group/plural`, which is what the group-suffix sweeps compare against.

        Raises:
            client.exceptions.ApiException: When the cluster will not answer, which is said apart
                from "there are none" for the reason `_delete_crds_in_groups` gives.
        """
        return self.apiextensions_api.list_custom_resource_definition().items

    def delete_cluster_scoped(self, kind: str, name: str) -> "DeleteOutcome":
        """One cluster-scoped object, gone, with the reason it did not go.

        The generic delete, spelled once here. `kubectl delete <kind> <name>` could address a kind by
        name alone; the generated client cannot, so the kind is dispatched to the API object that
        owns it. That mapping is the whole content of the function, which is why it lives here and
        not in the one caller that needs it: an uninstall that spelled it in place would be one
        more place that answers "how do you delete a ClusterRoleBinding" differently.

        `True` covers the object that was already absent, because a teardown that reports an
        absence as residue is claiming a failure the cluster never reported.
        """
        try:
            if kind == "clusterrolebinding":
                self.rbac_api.delete_cluster_role_binding(name=name)
            elif kind == "clusterrole":
                self.rbac_api.delete_cluster_role(name=name)
            elif kind == "crd":
                self.apiextensions_api.delete_custom_resource_definition(name=name)
            else:
                raise ValueError(f"delete_cluster_scoped cannot address {kind!r}; no API object "
                                 "here owns that kind, and guessing which one would be a second "
                                 "answer to where the object lives.")
            return DeleteOutcome(True)
        except client.exceptions.ApiException as error:
            if error.status == 404:
                logging.info(f"{kind} '{name}' is not there, so there was nothing to delete.")
                return DeleteOutcome(True)
            logging.warning(f"The cluster would not delete the {kind} '{name}'"
                            f" (status {error.status}): {error}")
            return DeleteOutcome(False, _reason(error))

    # custom resource helpers
    def get_custom_resource(self, group: str, version: str, namespace: str, name: str, plural_identifier: str):
        """
        Get a custom resource from Kubernetes.

        Args:
            group (str): API group of the resource
            version (str): API version of the resource
            namespace (str): Namespace of the resource
            name (str): Name of the resource
            plural_identifier (str): Plural form of the resource name (e.g. 'tasks', 'pipelines')

        Returns:
            client.CustomObject: The retrieved Tekton resource object
        """
        return self.get_custom_objects_api().get_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural_identifier,
            name=name
        )

    def list_custom_resources(self, group: str, version: str, namespace: str, plural_identifier: str):
        """
        List custom resources from Kubernetes.

        Args:
            group (str): API group of the resource
            version (str): API version of the resource
            namespace (str): Namespace of the resource
            plural_identifier (str): Plural form of the resource name (e.g. 'tasks', 'pipelines')

        Returns:
            client.CustomObject: The retrieved Tekton resource object
        """

        return self.custom_objects_api.list_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural_identifier
        )

    def create_or_replace_custom_resource(self, group, version, namespace, kind, body, plural_identifier=None):
        """
        Creates or replaces a custom resource in Kubernetes cluster.

        Args:
            group (str): The API group of the resource (e.g. tekton.dev)
            version (str): The API version of the resource (e.g. v1beta1, v1)
            namespace (str): The target namespace (e.g. default, tekton-pipelines)
            kind (str): The kind of the resource (e.g. Task, Pipeline, etc.)
            body (dict): The resource definition to apply
            plural_identifier (str): The plural form of the resource kind (e.g. tasks, pipelines)
                                  (optional, defaults to f"{kind.lower()}s")

        Returns:
            None

        Raises:
            Exception: If there's an error creating or updating the resource
        """

        resource_name = body['metadata']['name']
        if not resource_name:
            raise ValueError("Resource name is required in body['metadata']['name']")

        plural_identifier = plural_identifier or quantities.plural_for_kind(kind)

        try:
            # First try to create the resource
            try:
                self.custom_objects_api.create_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural_identifier,
                    body=body
                )
            except client.exceptions.ApiException as e:
                if e.status == 409:  # Resource already exists
                    # Replace the existing resource
                    self.custom_objects_api.patch_namespaced_custom_object(
                        group=group,
                        version=version,
                        namespace=namespace,
                        plural=plural_identifier,
                        name=resource_name,
                        body=body
                    )
                else:
                    raise
        except Exception as e:
            logging.error(f"Failed to apply resource: {str(e)}")
            raise

    def delete_custom_resource(self, group, version, namespace, resource_name, plural_identifier):
        try:
                # First try to create the resource
            self.custom_objects_api.delete_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural_identifier,
                name=resource_name
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:  # Resource not found, so it's safe to delete it
                logging.info(f"Resource {resource_name} not found in namespace {namespace}. Doing nothing.")
                pass
            else:
                raise
        return None
