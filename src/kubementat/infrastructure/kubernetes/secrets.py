"""Secrets, including the one that carries a service account's credentials.

A Secret is created or replaced and never patched: what a caller asks for is that this object
exist with this content, and a replace says that without reading the old one first.
"""

import base64
import json
import logging

from kubernetes import client

from kubementat.infrastructure.kubernetes.clients import KubernetesClients
from kubementat.infrastructure.kubernetes.constants import (SERVICE_ACCOUNT_NAME_ANNOTATION,
                                                            SERVICE_ACCOUNT_TOKEN_SECRET_TYPE)




class KubernetesSecrets(KubernetesClients):
    """Secrets, including the one that carries a service account's credentials.

    Composed into `KubernetesUtils`. A Secret here is created or replaced and never patched: what
    the caller is asking for is that this object exist with this content. The token secret belongs
    here and not beside the service-account reads because it is a Secret; the two names that make
    one are shared, in `constants.py`, so that neither half can be updated on its own.
    """
    # secret helpers
    def create_or_replace_secret(self, namespace: str, secret_name: str, key_type: str,
                                 raw_data: dict = None, string_data: dict = None,
                                 labels: dict = None) -> None:
        """
        Create or replace a Kubernetes secret in the specified namespace.

        Args:
            namespace (str): Namespace where the secret will be created/updated.
            secret_name (str): Name of the secret.
            key_type (str): Type of the secret (e.g., 'kubernetes.io/ssh-auth').
            raw_data (dict, optional): Raw data for the secret. Defaults to None.
            string_data (dict, optional): String data for the secret. Defaults to None.
            labels (dict, optional): Labels to put on the Secret, sent on both the create and the
                replace. kubementat labels what it owns with `managed-by: kubementat`, and the
                trigger webhook Secrets carry `team` as well; an unlabelled Secret is
                indistinguishable from one the operator wrote, which is what these were.
                None — every existing caller — sends no labels at all.

        Raises:
            Exception: If creation/update fails with an error other than 409 (already exists).
        """

        if raw_data is None and string_data is None:
            raise ValueError("Either raw_data or string_data must be provided")
        if raw_data is not None and string_data is not None:
            raise ValueError("Only one of raw_data or string_data can be provided")

        def a_secret():
            return client.V1Secret(
                metadata=client.V1ObjectMeta(name=secret_name, labels=labels),
                type=key_type,
                data=raw_data,
                string_data=string_data
            )

        try:
            self.core_api.create_namespaced_secret(namespace=namespace, body=a_secret())
        except client.exceptions.ApiException as e:
            if e.status == 409:  # Secret already exists, replace it
                self.core_api.replace_namespaced_secret(
                    namespace=namespace,
                    name=secret_name,
                    body=a_secret()
                )
            else:
                logging.error(f"Failed to create/update secret {secret_name}: {e}")
                raise

    def create_or_replace_docker_secret(self, namespace: str, name: str, auth_url: str, username: str, password: str, email: str):
        """
        Create or replace a docker registry secret in the specified namespace using Kubernetes Python client
        """
        logging.info(f"Creating docker secret: {name} in namespace: {namespace} for docker auth url: {auth_url} ...")

        # Create auth data and encode as required by Docker
        auth_data = {
            "auths": {
                auth_url: {
                    "username": username,
                    "password": password,
                    "email": email,
                    "auth": base64.b64encode(f"{username}:{password}".encode()).decode()
                }
            }
        }

        # Convert auth data to JSON and encode in base64
        dockerconfigjson = base64.b64encode(json.dumps(auth_data).encode()).decode()

        self.create_or_replace_secret(namespace, name, "kubernetes.io/dockerconfigjson", {".dockerconfigjson": dockerconfigjson})

    def list_secrets(self, namespace: str) -> client.V1SecretList:
        """
        List all secrets in a specific namespace.

        Args:
            namespace (str): Namespace to list secrets from

        Returns:
            client.V1SecretList: The retrieved list of secrets
        """
        return self.core_api.list_namespaced_secret(namespace=namespace)

    def create_or_replace_service_account_token_secret(self, namespace, secret_name, service_account_name):
        """Ask for a token secret belonging to a service account, creating or refreshing it.

        A service account does not come with credentials: the token controller only fills
        in a Secret that declares the service account through
        `kubernetes.io/service-account.name`. A kubeconfig or a login command needs that
        Secret first, which is why both directions live next to each other here.

        Args:
            namespace (str): Namespace to create the Secret in.
            secret_name (str): Name of the Secret.
            service_account_name (str): Service account the token is minted for.
        """
        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": namespace,
                "annotations": {SERVICE_ACCOUNT_NAME_ANNOTATION: service_account_name},
            },
            "type": SERVICE_ACCOUNT_TOKEN_SECRET_TYPE,
        }
        try:
            logging.info(f"Creating token secret '{secret_name}' for service account '{service_account_name}' in namespace '{namespace}'")
            self.core_api.create_namespaced_secret(namespace=namespace, body=body)
        except client.exceptions.ApiException as e:
            if e.status == 409:  # Already requested once, ask the controller to refresh it
                logging.info(f"Token secret '{secret_name}' already exists, replacing it")
                self.core_api.replace_namespaced_secret(namespace=namespace, name=secret_name, body=body)
            else:
                raise

    def delete_secret(self, namespace, secret_name):
        """Delete a Kubernetes secret"""
        try:
            self.core_api.delete_namespaced_secret(
                namespace=namespace,
                name=secret_name
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:  # Secret not found is okay
                pass
            else:
                raise
