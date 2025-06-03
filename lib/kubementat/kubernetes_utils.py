import logging
import base64
import json
import subprocess
import os
from kubernetes import client, config

class KubernetesUtils:
    """Utility class for Kubernetes operations."""

    def __init__(self):
        """Initialize Kubernetes utility with CoreV1 API client."""
        # Load Kubernetes configuration
        config.load_kube_config()

        self.core_api = client.CoreV1Api()
        self.rbac_api = client.RbacAuthorizationV1Api()
        self.custom_objects_api = client.CustomObjectsApi()
        self.apps_api = client.AppsV1Api()

    # getters for the api clients, in case we need to use them outside of this class
    def get_apps_api(self):
        return self.apps_api

    def get_core_api(self):
        return self.core_api

    def get_rbac_api(self):
        return self.rbac_api

    def get_custom_objects_api(self):
        return self.custom_objects_api

    def get_kubernetes_client(self):
        return client

    def cluster_info(self):
        current_context = config.list_kube_config_contexts()[1]
        logging.info("Current context:")
        logging.info(current_context)
        return current_context

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

    def delete_namespace(self, namespace: str) -> None:
        """
        Delete a Kubernetes namespace

        Args:
            namespace (str): Name of the namespace to delete.

        Raises:
            Exception: If deletion fails with a status other than 404
        """
        try:
            logging.info(f"Deleting namespace: '{namespace}'")
            self.core_api.delete_namespace(namespace)
        except client.exceptions.ApiException as e:
            if e.status == 404:  # Already exists
                logging.info(f"Namespace '{namespace}' does not exist, skipping deletion")
            else:
                logging.error(f"Failed to delete namespace '{namespace}': {e}")
                raise

    # secret helpers
    def create_or_replace_secret(self, namespace: str, secret_name: str, key_type: str,
                                 raw_data: dict = None, string_data: dict = None) -> None:
        """
        Create or replace a Kubernetes secret in the specified namespace.

        Args:
            namespace (str): Namespace where the secret will be created/updated.
            secret_name (str): Name of the secret.
            key_type (str): Type of the secret (e.g., 'kubernetes.io/ssh-auth').
            raw_data (dict, optional): Raw data for the secret. Defaults to None.
            string_data (dict, optional): String data for the secret. Defaults to None.

        Raises:
            Exception: If creation/update fails with an error other than 409 (already exists).
        """

        if raw_data is None and string_data is None:
            raise ValueError("Either raw_data or string_data must be provided")
        if raw_data is not None and string_data is not None:
            raise ValueError("Only one of raw_data or string_data can be provided")

        try:
            self.core_api.create_namespaced_secret(
                namespace=namespace,
                body=client.V1Secret(
                    metadata=client.V1ObjectMeta(name=secret_name),
                    type=key_type,
                    data=raw_data,
                    string_data=string_data
                )
            )
        except client.exceptions.ApiException as e:
            if e.status == 409:  # Secret already exists, replace it
                self.core_api.replace_namespaced_secret(
                    namespace=namespace,
                    name=secret_name,
                    body=client.V1Secret(
                        metadata=client.V1ObjectMeta(name=secret_name),
                        type=key_type,
                        data=raw_data,
                        string_data=string_data
                    )
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

        plural_identifier = plural_identifier or f"{kind.lower()}s"

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

    # rbac helpers
    def create_or_replace_role(self, namespace, body):
        try:
            self.rbac_api.create_namespaced_role(
                namespace=namespace,
                body=body
            )
        except client.exceptions.ApiException as e:
            if e.status == 409:  # Resource found, replace it
                logging.info(f"Role '{name}' already exists in namespace '{namespace}', replacing it")
                self.rbac_api.replace_namespaced_role(
                    namespace=namespace,
                    name=name,
                    body=body
                )
            else:
                raise

    def create_or_replace_role_binding(self,namespace, name, body):
      try:
        self.rbac_api.create_namespaced_role_binding(
            namespace=namespace,
            body=body
        )
      except client.exceptions.ApiException as e:
        if e.status == 409:  # Resource found, replace it
          logging.info(f"Role binding '{name}' already exists in namespace '{namespace}', replacing it")
          self.rbac_api.replace_namespaced_role_binding(
              namespace=namespace,
              name=name,
              body=body
          )
        else:
          raise

    def list_role_bindings(self, namespace: str) -> client.V1RoleBindingList:
        """
        List all role bindings in a specific namespace.

        Args:
            namespace (str): Namespace to list role bindings from

        Returns:
            client.V1RoleBindingList: The retrieved list of role bindings
        """
        return self.rbac_api.list_namespaced_role_binding(namespace=namespace)

    def get_role_binding(self, namespace: str, name: str) -> client.V1RoleBinding:
        """
        Get a specific role binding from the cluster.

        Args:
            namespace (str): Namespace of the role binding
            name (str): Name of the role binding

        Returns:
            client.V1RoleBinding: The retrieved role binding object
        """
        return self.rbac_api.read_namespaced_role_binding(
            namespace=namespace,
            name=name
        )

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

    def get_role_binding(self, namespace, name):
        """Get a role binding"""
        try:
            return self.rbac_api.read_namespaced_role_binding(
                namespace=namespace,
                name=name
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:  # Not found is okay
                return None
            else:
                raise

    # ATTENTION: This will stall the current process until the tunnel is closed
    def open_tunnel(self, namespace, pod_name, local_port, remote_port, host='0.0.0.0'):
        try:
            command = f"kubectl --namespace {namespace} port-forward --address {host} pod/{pod_name} {local_port}:{remote_port}"
            logging.info(f"Opening tunnel to pod: {pod_name} in namespace: {namespace} on local port: {local_port} pointing to remote pod port: {remote_port}")
            logging.info(f"Running command: {command}")
            logging.info("###########################")
            logging.info(f"Local address: http://localhost:{local_port}")
            logging.info("###########################")
            logging.info("The tunnel will be closed when you press Ctrl+C")
            subprocess.run([command], check=True, shell=True, capture_output=True)
            return 0
        except Exception as e:
            logging.error(f"An error occured while opening a tunnel to pod: {pod_name} in namespace: {namespace}.")
            logging.error(e.stderr)
            return 1

    def get_pod_name_for_deployment(self, namespace, selector):
        # kubectl -n "$GRAFANA_DEPLOYMENT_NAMESPACE" get pod -l "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana" -o json
        command = f"kubectl --namespace {namespace} get pod -l '{selector}' -o json"
        logging.info(f"Running command: {command}")
        result = subprocess.run([command], check=True, shell=True, capture_output=True)
        result_hash = json.loads(result.stdout)

        pod_name = result_hash["items"][0]["metadata"]["name"]
        return pod_name