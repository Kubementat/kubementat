"""Service accounts, and the bearer token a kubeconfig is built out of.

A service account does not come with credentials: since 1.24 a token exists only once something
has asked the controller for one. The request and the read are therefore asked of the same
object, and the read that guesses a name from a convention is the one that finds nothing on a
cluster that has never run the request.
"""

import base64
import json
import logging

from kubernetes import client

from kubementat.errors import NamespaceNotFoundError, ServiceAccountTokenNotFoundError
from kubementat.infrastructure.kubernetes.clients import KubernetesClients
from kubementat.infrastructure.kubernetes.constants import (SERVICE_ACCOUNT_NAME_ANNOTATION,
                                                            SERVICE_ACCOUNT_TOKEN_SECRET_TYPE)




class KubernetesServiceAccounts(KubernetesClients):
    """Service accounts, and the bearer token a kubeconfig is built from.

    Composed into `KubernetesUtils`. A service account does not come with credentials: since 1.24
    a token exists only once something has asked the controller for one, so the request and the
    read sit together, and the read that guessed at a secret name instead of reading the
    annotation is the one that finds nothing on a cluster that has never run the request.
    """
    # service account helpers
    def get_service_account(self, namespace: str, name: str) -> client.V1ServiceAccount:
        """
        Get a specific service account from the cluster.

        Args:
            namespace (str): Namespace of the service account
            name (str): Name of the service account

        Returns:
            client.V1ServiceAccount: The retrieved service account object

        Raises:
            Exception: If the service account does not exist or retrieval fails
        """
        return self.core_api.read_namespaced_service_account(
            namespace=namespace,
            name=name
        )

    def create_service_account(self, namespace, sa_name, secrets):

      try:
        # Create a ServiceAccount object
        sa = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": sa_name,
                "namespace": namespace
            },
            "secrets": secrets
        }

        # Attempt to create the service account
        self.core_api.create_namespaced_service_account(
            namespace=namespace,
            body=sa
        )
        logging.info(f"Service account '{sa_name}' created or updated in namespace '{namespace}'.")
      except client.exceptions.ApiException as e:
          if e.status == 409:
              logging.info(f"Service account '{sa_name}' already exists in namespace '{namespace}', skipping creation")
          else:
              raise e

    def create_or_replace_service_account(self, namespace: str, name: str,
                                          labels: dict = None) -> None:
        """Create a ServiceAccount, or replace it if it already exists.

        This exists because `create_service_account(namespace, sa_name, secrets)` carries the
        `secrets` field the flow must not write. `secrets` is how a service account was told
        which token Secrets belong to it before 1.24; a kubementat team account has no
        Secrets to attach at creation time (the token Secret is written separately, by
        `create_or_replace_service_account_token_secret`), so a flow that reused
        `create_service_account` would either have to send an empty list against a field the
        API is documented to ignore, or patch around it. Neither is what the bash `kubectl
        apply -f -` of a ServiceAccount manifest did.

        The deprecated field itself is not fixed here - `create_service_account` still has
        it, and it still has its own caller - because that belongs to the deferred
        platform_config work, not to this flow.

        Args:
            namespace (str): Namespace to create the ServiceAccount in.
            name (str): Name of the ServiceAccount.
            labels (dict, optional): Labels for the object, which is how the accounts the trigger
                flow creates say which team's they are -- the same labels `kubectl apply` of the
                script's manifest put there. None, what every existing caller passes, sends none.
        """
        body = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            # No namespace inside the object: the request already carries it, and a manifest
            # that repeats it is the only place a rename can disagree with itself.
            "metadata": {"name": name, "labels": labels},
        }
        try:
            logging.info(f"Creating service account '{name}' in namespace '{namespace}'")
            self.core_api.create_namespaced_service_account(namespace=namespace, body=body)
        except client.exceptions.ApiException as e:
            if e.status == 409:  # Already there; the flow is to make it exist, not to keep it
                logging.info(f"Service account '{name}' already exists in namespace '{namespace}', replacing it")
                self.core_api.replace_namespaced_service_account(namespace=namespace, name=name, body=body)
            else:
                raise

    def list_service_accounts(self, namespace: str) -> list:
        """
        Service accounts in a namespace.

        Args:
            namespace (str): Namespace to list from.

        Returns:
            list[client.V1ServiceAccount]: The service accounts, empty when there are none.
        """
        return self.core_api.list_namespaced_service_account(namespace=namespace).items

    def patch_service_account_with_pull_secrets(self, namespace, service_account_name, image_pull_secrets: list[str]):
        """
        Patch a service account with image pull secrets
        """
        logging.info(f"Patching service account {service_account_name} in namespace {namespace} for docker registry secrets usage")
        logging.info(f"Image pull secrets: {image_pull_secrets}")

        # Create the patch body
        patch_body = {"imagePullSecrets": image_pull_secrets}

        # Display what we're patching with
        logging.debug("Patching Service Account with:")
        logging.debug(json.dumps(patch_body))

        try:
            # Get the current service account
            service_account = self.get_service_account(namespace, service_account_name)

            # Update with the new image pull secrets
            service_account.image_pull_secrets = [
                self.get_kubernetes_client().V1LocalObjectReference(name=secret["name"])
                for secret in image_pull_secrets
            ]

            # Apply the update
            self.get_core_api().patch_namespaced_service_account(
                name=service_account_name,
                namespace=namespace,
                body=service_account
            )

            logging.info(f"Successfully patched service account {service_account_name} in namespace {namespace}")
        except Exception as e:
            logging.error(f"Error patching service account: {e}")
            raise

    def get_service_account_token(self, namespace, service_account_name) -> str:
        """Read the bearer token a service account authenticates with.

        Replaces the shell one-liner that grepped `kubectl get secret` for
        `<service-account>-token`: the name is a convention, the annotation is the fact.

        Args:
            namespace (str): Namespace holding the service account's token Secret.
            service_account_name (str): Service account to read the token of.

        Returns:
            str: The decoded token.

        Raises:
            NamespaceNotFoundError: When the cluster says it has no such namespace.
            ServiceAccountTokenNotFoundError: When no Secret declares this service account
                (nothing minted yet) or when the Secret exists but is still empty.
        """
        try:
            secrets = self.core_api.list_namespaced_secret(namespace=namespace).items
        except client.exceptions.ApiException as error:
            if error.status == 404:
                raise NamespaceNotFoundError(
                    f"Namespace '{namespace}' does not exist on this cluster.") from error
            raise
        candidates = [
            secret for secret in secrets
            if secret.type == SERVICE_ACCOUNT_TOKEN_SECRET_TYPE
            and (secret.metadata.annotations or {}).get(SERVICE_ACCOUNT_NAME_ANNOTATION) == service_account_name
        ]
        if not candidates:
            raise ServiceAccountTokenNotFoundError(
                f"No token secret for service account '{service_account_name}' in namespace '{namespace}'.\n"
                "A cluster does not mint one by itself: request it with "
                "create_or_replace_service_account_token_secret(), after checking the service account exists."
            )
        if len(candidates) > 1:
            logging.warning(
                f"{len(candidates)} token secrets for service account '{service_account_name}' "
                f"in namespace '{namespace}', using '{sorted(s.metadata.name for s in candidates)[0]}'"
            )
        secret = sorted(candidates, key=lambda item: item.metadata.name)[0]
        token = (secret.data or {}).get("token")
        if not token:
            raise ServiceAccountTokenNotFoundError(
                f"Token secret '{secret.metadata.name}' for service account '{service_account_name}' "
                f"in namespace '{namespace}' carries no token yet.\n"
                "The token controller has not filled it in - request it again with "
                "create_or_replace_service_account_token_secret()."
            )
        return base64.b64decode(token).decode()

    def delete_service_account(self, namespace, sa_name):
        """Delete a service account"""
        try:
            self.core_api.delete_namespaced_service_account(
                namespace=namespace,
                name=sa_name
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:  # SA not found is okay
                pass
            else:
                raise
