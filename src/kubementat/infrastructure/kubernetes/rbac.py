"""The RBAC surface: Roles, RoleBindings, ClusterRoles, ClusterRoleBindings.

The create-or-replace family is the reason this is one module and not a helper per caller: an
install that runs twice has to replace what it wrote instead of dying on the second attempt, and
the answer to "what happens on a 409" cannot be written separately in each place that meets one.
"""

import logging

from kubernetes import client

from kubementat.infrastructure.kubernetes.clients import KubernetesClients




class KubernetesRbac(KubernetesClients):
    """Roles, bindings, cluster roles and cluster role bindings.

    Composed into `KubernetesUtils`, like every area. Each write is create-or-replace because both
    halves of the product can run against a cluster that already has the object -- a reinstall, or
    an install that was interrupted -- and a 409 that ends the command is a refusal the cluster
    never made.
    """
    # ------------------------------------------------------------------
    # the four kinds that moved off kubectl, read and deleted through one seam each
    # ------------------------------------------------------------------
    def list_cluster_roles(self) -> list:
        """Every ClusterRole on the cluster -- the read `kubectl get clusterrole` was.

        The uninstall sweep asks the cluster rather than keeping a name list, because the pinned
        release configuration changes what is in that list.
        """
        return self.rbac_api.list_cluster_role().items

    def list_cluster_role_bindings(self) -> list:
        """Every ClusterRoleBinding on the cluster, for the same reason and by the same rule."""
        return self.rbac_api.list_cluster_role_binding().items

    def list_roles(self, namespace: str) -> list:
        """
        Roles in a namespace.

        Args:
            namespace (str): Namespace to list from.

        Returns:
            list[client.V1Role]: The roles, empty when there are none.
        """
        return self.rbac_api.list_namespaced_role(namespace=namespace).items

    # rbac helpers
    #
    # The create-or-replace family takes the name as its own argument rather than reading
    # it out of *body*, because `body` arrives here both as a client model object and as a
    # plain dict, and reading one path out of the other is exactly the sort of guess that
    # hid D4: the 409 branch used a `name` that was never bound, so re-running any command
    # against a cluster that already had the role died with a NameError instead of
    # replacing it.
    def create_or_replace_role(self, namespace, name, body):
        """Create a Role, or replace it if it already exists.

        Args:
            namespace (str): Namespace the Role belongs to.
            name (str): Name of the Role, matching body's metadata name.
            body: V1Role or a dict with the same shape.
        """
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

    def create_or_replace_cluster_role(self, name, body):
        """Create a ClusterRole, or replace it if it already exists.

        Args:
            name (str): Name of the ClusterRole, matching body's metadata name.
            body: V1ClusterRole or a dict with the same shape.
        """
        try:
            self.rbac_api.create_cluster_role(body=body)
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logging.info(f"ClusterRole '{name}' already exists, replacing it")
                self.rbac_api.replace_cluster_role(name=name, body=body)
            else:
                raise

    def create_or_replace_cluster_role_binding(self, name, body):
        """Create a ClusterRoleBinding, or replace it if it already exists.

        Args:
            name (str): Name of the ClusterRoleBinding, matching body's metadata name.
            body: V1ClusterRoleBinding or a dict with the same shape.
        """
        try:
            self.rbac_api.create_cluster_role_binding(body=body)
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logging.info(f"ClusterRoleBinding '{name}' already exists, replacing it")
                self.rbac_api.replace_cluster_role_binding(name=name, body=body)
            else:
                raise

    def delete_role(self, namespace, name):
        """Delete a Role; a Role that is not there is a success."""
        try:
            logging.info(f"Deleting role '{name}' from namespace '{namespace}'")
            self.rbac_api.delete_namespaced_role(namespace=namespace, name=name)
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logging.info(f"Role '{name}' not found in namespace '{namespace}', doing nothing")
            else:
                raise

    def delete_role_binding(self, namespace, name):
        """Delete a RoleBinding; a RoleBinding that is not there is a success."""
        try:
            logging.info(f"Deleting role binding '{name}' from namespace '{namespace}'")
            self.rbac_api.delete_namespaced_role_binding(namespace=namespace, name=name)
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logging.info(f"Role binding '{name}' not found in namespace '{namespace}', doing nothing")
            else:
                raise

    def delete_cluster_role(self, name):
        """Delete a ClusterRole; a ClusterRole that is not there is a success.

        Cluster roles are cluster scoped, so uninstalling them cannot be done by deleting
        a namespace -- `helm-deployer-cluster-role` is the one kubementat owns here.
        """
        try:
            logging.info(f"Deleting cluster role '{name}'")
            self.rbac_api.delete_cluster_role(name=name)
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logging.info(f"Cluster role '{name}' not found, doing nothing")
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

    def list_role_bindings(self, namespace: str) -> list:
        """
        List all role bindings in a specific namespace.

        Returns just the items, as its siblings `list_service_accounts` and `list_roles` do:
        the callers iterate the result. Handing back the whole `V1RoleBindingList` model is a
        `TypeError` on the first iteration, which is where the team-account flow met it -
        after it had already written every Role and Binding it came to write.

        Args:
            namespace (str): Namespace to list role bindings from

        Returns:
            list[client.V1RoleBinding]: The role bindings, empty when there are none.
        """
        return self.rbac_api.list_namespaced_role_binding(namespace=namespace).items

    def get_role_binding(self, namespace: str, name: str) -> client.V1RoleBinding:
        """
        Get a specific role binding from the cluster.

        There was a second copy of this method further down the file, shadowing this one
        and quietly returning None on a 404; one method means one contract.

        Args:
            namespace (str): Namespace of the role binding
            name (str): Name of the role binding

        Returns:
            client.V1RoleBinding: The retrieved role binding object

        Raises:
            client.exceptions.ApiException: When the role binding does not exist.
        """
        return self.rbac_api.read_namespaced_role_binding(
            namespace=namespace,
            name=name
        )
