"""The clients one instance is built out of, and the address they are built from.

The rest of the package is a client read before it is anything else, so the question "which
cluster" is answered once, here, and lazily: a command that reads nothing never resolves a
kubeconfig, and a client built somewhere else would be a second answer to the same question.
That is also why the constructor takes the object the command resolved instead of asking the
library which cluster it means -- a library that holds one process-wide answer cannot hold two
clusters, and this product is asked about two in one process on purpose.

The API objects are built from `api_client` and memoised on the attribute that was asked for, so
a caller -- or a test -- that replaces one of them finds the swap remembered.
"""

import functools
import logging
from typing import Optional

from kubernetes import client, dynamic

from kubementat.core.identity import target as _target
from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget


# The two helpers this module reaches for are bound as modules and not as names, the way
# every module in the tree that needs a moved helper does it: a name import leaves
# `kubementat.core.identity.target.kubectl_target_args` and the function it points at reachable
# as two things, and a patch aimed at the binding nobody runs stays green while the code reads
# the other one.


class KubernetesClients:
    """The clients one instance is built out of, and the two questions only the server answers.

    This is where the package decides which cluster a call goes to, and it decides it lazily and
    from the target alone: a command that reads nothing never resolves a kubeconfig, and a client
    built anywhere else is a second answer to that question. `api_client` stays a constructor
    parameter for the reason it always was -- the recorder a suite injects replaces everything the
    target would otherwise have built, and a module-level target would put that seam back where
    two clusters in one process could overwrite each other.

    The API objects are memoised by the attribute that is asked for, so that replacing one of them
    -- which is how a test lets one recorder answer for four API classes -- makes the swap stick.
    """
    def __init__(self, ctx_or_target, *, api_client=None):
        """Bind to the cluster `ctx_or_target` addresses, building nothing yet.

        A `Context` is the normal argument -- it is what every command has -- and a bare
        `ClusterTarget` is accepted because that is the shape the library caller holding its own
        credentials has, and the shape a test addresses a cluster with.

        `api_client` is a seam and stays one: an injected recorder replaces everything this
        class would otherwise build from the target, and it is a parameter rather than module state
        because a module-level target is the process-wide global under another name.
        """
        if isinstance(ctx_or_target, Context):
            self._ctx = ctx_or_target
            self._target = ctx_or_target.target
        elif isinstance(ctx_or_target, ClusterTarget):
            self._ctx = None
            self._target = ctx_or_target
        else:
            raise TypeError(
                "KubernetesUtils takes the Context a command resolved or the ClusterTarget it was "
                f"given, and got {type(ctx_or_target).__name__} instead. A KubernetesUtils that "
                "resolved 'which cluster' for itself is the second answer that must not exist.")
        self._api_client = api_client

    @property
    def target(self) -> ClusterTarget:
        """The cluster this instance addresses, asked of the `Context` only when it is needed.

        `require_target` is called here and nowhere else. That placement is deliberate, not a dodge:
        a constructor that refused a `target=None` Context would fail the
        configuration-only build -- the object a verb gets when it writes a tree and never addresses
        a cluster -- exactly
        the eager kubeconfig load this replaced. Refusing *when a client is built* says the same
        sentence to the call site that needs to hear it, and leaves the command that never asks
        alone.
        """
        if self._target is None:
            self._target = self._ctx.require_target(
                "the Kubernetes client this command would otherwise build")
        return self._target

    @property
    def api_client(self):
        """The client the API objects are built from, lazily, and only ever the target's own.

        `ClusterTarget.client()` hands back an `ApiClient` with a private `Configuration`, so nothing
        here reads or writes the library's default one. That is what lets two instances address two
        clusters in one process without either overwriting the other, and it is asserted, not
        assumed, in `test_two_targets_in_one_process_never_share_a_client`.

        An injected `api_client` short-circuits all of it: no target is asked, no kubeconfig is
        opened, no socket is dialed.
        """
        if self._api_client is None:
            self._api_client = self.target.client()
        return self._api_client

    def _api(self, factory):
        """One API object, built from the client this instance has been given.

        A real `ApiClient` gets the generated client wrapped around it, which is the composition
        (`ApiClient(target.client()) -> client.CoreV1Api(api_client)`). Anything else is taken to be
        the API object itself -- that is what a recorder is, and it is the only way one
        injected object can answer for four API classes without patching four globals.

        The memo is not here: it belongs to the attribute that is being asked for, so that a test (or
        a caller) can replace one API object and have the swap stick.
        """
        seam = self.api_client
        return factory(seam) if isinstance(seam, client.ApiClient) else seam

    # getters for the api clients, in case we need to use them outside of this class
    @functools.cached_property
    def apps_api(self):
        return self._api(client.AppsV1Api)

    @functools.cached_property
    def custom_objects_api(self):
        return self._api(client.CustomObjectsApi)

    @functools.cached_property
    def core_api(self):
        return self._api(client.CoreV1Api)

    @functools.cached_property
    def rbac_api(self):
        return self._api(client.RbacAuthorizationV1Api)

    @functools.cached_property
    def networking_api(self):
        return self._api(client.NetworkingV1Api)

    @functools.cached_property
    def authorization_api(self):
        """The API `auth can-i` asks, replacing the seven kubectl probes.

        A `SelfSubjectAccessReview` is the same question kubectl's `auth can-i` sends, minus the
        text parsing: it is answered by the server, it carries the verb and the resource, and it
        has a boolean. kubectl's exit code and its `yes`/`no` string were one and the same answer
        read two ways, which is why the two spellings could disagree.
        """
        return self._api(client.AuthorizationV1Api)

    @functools.cached_property
    def apiextensions_api(self):
        return self._api(client.ApiextensionsV1Api)

    @functools.cached_property
    def version_api(self):
        """The API that answers "is there a cluster here, and which one".

        This replaces `kubectl cluster-info`. `cluster-info` printed four namespaces
        that mean nothing to kubementat and exit-coded on nothing; the version endpoint is the
        request that succeeds only when the API server answers, which is the whole fact the
        install prompt was reaching for.
        """
        return self._api(client.VersionApi)

    @functools.cached_property
    def dynamic_client(self):
        """The discovery client `api-resources` is answered with.

        There is no single generated call for "what does this cluster serve", which is why the old
        code asked kubectl. `DynamicClient.resources` is the same discovery walk, without a
        serialised table to re-parse.
        """
        return dynamic.DynamicClient(self.api_client)

    # ------------------------------------------------------------------
    # the kubectl that stays kubectl is addressed here
    # ------------------------------------------------------------------
    @property
    def kubectl_args(self) -> list:
        """`--context`/`--kubeconfig` for this instance's target, for the session calls.

        Three kubectl calls stay kubectl and they still carry the cluster. This is how
        they get it: from the target, asked lazily like everything else, so a command that streams
        no logs and opens no tunnel never resolves one.
        """
        return _target.kubectl_target_args(self.target)

    # ------------------------------------------------------------------
    # reads that decide something — the kubectl side of these is gone
    # ------------------------------------------------------------------
    def self_can(self, verb: str, resource: str, namespace: Optional[str] = None) -> bool:
        """Whether *this* credential may `verb` `resource`, answered by the server.

        The client's version of `kubectl auth can-i <verb> <resource>`, and the one the seven
        permission probes and the team-account preflight now ask. `namespace` is left out of the
        review when it is None, which is what keeps a cluster-scoped probe cluster-scoped.
        """
        review = self.authorization_api.create_self_subject_access_review(
            body=client.V1SelfSubjectAccessReview(
                spec=client.V1SelfSubjectAccessReviewSpec(
                    resource_attributes=client.V1ResourceAttributes(
                        verb=verb, resource=resource, namespace=namespace))))
        allowed = getattr(getattr(review, "status", None), "allowed", None)
        return bool(allowed)

    def cluster_is_reachable(self) -> bool:
        """Whether the API server answers, which is the fact `cluster-info` was standing in for.

        Nothing here raises: the callers have a residue list or a refusal of their own to say, and
        "could not ask" is the answer they need rather than a traceback. A 401/403 still means a
        server is there, so it counts as reachable; that is what `version_api.get_code` returning
        at all says.
        """
        try:
            self.version_api.get_code()
            return True
        except Exception as error:                                        # noqa: BLE001
            logging.debug(f"The cluster did not answer a version request: {error}")
            return False

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

    def api_version_is_served(self, group: str, version: str) -> bool:
        """Ask the API server whether it serves `group/version`.

        This is the discovery request that answers "does the controller behind this group
        exist here, and does it speak this version", which is the one thing a
        create/patch-by-custom-object cannot tell you: it fails with a 404 per resource and
        leaves the reader guessing whether the controller is missing, old, or merely
        unreachable.

        Args:
            group: API group, for example "tekton.dev".
            version: API version, for example "v1".

        Returns:
            True if the API server serves that group/version, False if it serves nothing
            under that group/version.

        Raises:
            Exception: If discovery fails for any reason other than the group/version not
                being served, so that a missing kubeconfig, an expired token, or an
                unreachable cluster is reported as what it is rather than looked over here.
        """
        try:
            self.custom_objects_api.get_api_resources(group=group, version=version)
            return True
        except client.exceptions.ApiException as error:
            if error.status == 404:
                return False
            raise

    def get_api_resources(self) -> list:
        """
        The resource types the cluster serves, read from the cluster's own discovery data.

        `kubectl api-resources --output=json` converts to this: the discovery documents are what that
        command assembles, and `DynamicClient.resources` is the same walk without a serialised table
        to re-parse. The shape kubectl printed is kept -- `name`, `singularName`, `namespaced`,
        `kind`, `verbs` per entry -- because the primitive's contract is that table and not the
        discovery document, and a caller that reads a name should not have to know which client
        walked.

        Returns:
            list[dict]: One entry per resource type, ordered by name so two reads of one cluster
            answer in one order.
        """
        table = []
        for (group, version), resources in self.dynamic_client.resources.items():
            for name, detail in resources.items():
                table.append({
                    "name": f"{name}.{group}" if group else name,
                    "singularName": detail.get("singularName") or name,
                    "namespaced": bool(detail.get("namespaced")),
                    "kind": detail.get("kind") or "",
                    "verbs": list(detail.get("verbs") or []),
                })
        return sorted(table, key=lambda entry: (entry["name"], entry["kind"]))
