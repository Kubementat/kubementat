import functools
import logging
import base64
import json
import os
import time
from typing import Optional

from kubernetes import client, dynamic

from kubementat.cluster import ClusterNotConfiguredError, ClusterTarget, Context

from kubementat import shell

# A Secret that carries a service account's credentials is recognised by its type plus the
# annotation naming the service account it belongs to. Both halves are needed: the type
# alone cannot say whose token it is, and since 1.24 nothing exists without that
# annotation. Clusters older than 1.24 put the same annotation on the token secret the
# controller created automatically, so one rule finds both.
SERVICE_ACCOUNT_TOKEN_SECRET_TYPE = "kubernetes.io/service-account-token"
SERVICE_ACCOUNT_NAME_ANNOTATION = "kubernetes.io/service-account.name"

# Namespace deletion is asynchronous, and a measured one rather than a guessed one: a full lane's
# own watch log showed ten kubementat namespaces draining over 42 s on a 4 vCPU guest while
# `kmt uninstall` reported "still present" four seconds after firing the deletes. Nothing was
# blocking them -- the cluster simply was not finished. The ceiling is that observation with room
# for a slower host; the interval is what makes the wait a poll and not a sleep.
NAMESPACE_GONE_WAIT_S = 180
NAMESPACE_POLL_INTERVAL_S = 1.0

# The kubelet's own stats endpoint, reached through the API's node-proxy path. The script
# `list_pvc_usage.sh`, deleted when this read came into Python, got here by starting
# `kubectl proxy`, sleeping two seconds, curling `127.0.0.1:8001` and then `pkill -9`ing it; the
# client's `connect_get_node_proxy_with_path` asks for `/api/v1/nodes/{name}/proxy/{path}`, which
# is the same URL with no local server in the middle.
NODE_STATS_PATH = "/stats/summary"


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


def kubectl_target_args(target) -> list:
    """The argv fragment that points a `kubectl` at *target*, and nothing else.

    The rule is stated in the negative: a `kubectl` invocation that is not given its cluster is
    a bug, not a shortcut. This is the only place that sentence is turned into argv, so a call that
    forgets the flags is a call that cannot be written without skipping this function.

    Both flags come from the `ClusterTarget` the caller already resolved -- never from what the
    process's kubeconfig happens to say. `--context` is what a kubeconfig-based target addresses;
    `--kubeconfig` is named only when the target resolved a file, because naming a file kubectl
    would otherwise have found for itself is a claim about provenance this object is allowed to
    make and no one else is.
    """
    args = []
    if target.kubeconfig_path is not None:
        args += ["--kubeconfig", str(target.kubeconfig_path)]
    if target.context:
        args += ["--context", str(target.context)]
    if not args:
        raise ClusterNotConfiguredError(
            f"environment '{target.environment}' addressed a cluster through "
            f"{target.source}, which carries no kubecontext and no kubeconfig file, and "
            f"{target.source} cannot be handed to a kubectl session. `kubectl logs`/`exec`/"
            f"`port-forward` need one of --context or --kubeconfig (\u00a710.2); a target built from a "
            f"connection document has neither, so those three calls have no address to carry and "
            f"refuse rather than guess."
        )
    return args


def helm_target_args(target) -> list:
    """The argv fragment that points a *helm* or *helmfile* at *target*, and nothing else.

    The same sentence as :func:`kubectl_target_args`, in helm's spelling: `--kubeconfig` for the
    file and `--kube-context` for the context (helm does not accept kubectl's `--context`), and
    both binaries take them wherever the subcommand goes. Refusing a target that names neither is
    the point, not a nicety: `helm_utils.py` used to hand the cluster over in the environment
    (`os.environ["KUBECONFIG"]`), which is how a helm run could land on a cluster the operator had
    not named and leave no trace of the choice in the argv it logged.
    """
    args = []
    if target.kubeconfig_path is not None:
        args += ["--kubeconfig", str(target.kubeconfig_path)]
    if target.context:
        args += ["--kube-context", str(target.context)]
    if not args:
        raise ClusterNotConfiguredError(
            f"helm needs the cluster named on the command line, and environment "
            f"'{target.environment}' addressed a cluster through {target.source}, which carries no "
            f"kubecontext and no kubeconfig file. helm and helmfile accept --kube-context and "
            f"--kubeconfig (\u00a711); a target built from a connection document has neither, so the "
            f"read has no address to carry and refuses rather than picking up whatever"
            f" ~/.kube/config the running user happens to have."
        )
    return args


def plural_for_kind(kind: str) -> str:
    """The resource name a kind is addressed by in the API path.

    `f"{kind.lower()}s"` was the whole rule, and it broke on the one kind anything here applies
    that is not a Tekton resource: an `Ingress` lives under `ingresses`, so the naive plural asked
    for `ingresss` and the answer was a 404 that named neither the kind nor the path. Kubernetes
    itself pluralises by rule, and the two rules that cover every kind kubementat sends are the
    ones below -- which is why the answer is derived here and not written out per kind.
    """
    lowered = (kind or "").strip().lower()
    if lowered.endswith(("s", "x", "ch", "sh")):
        return lowered + "es"
    return lowered + "s"


class PodNotFoundError(Exception):
    """No pod matched the query, i.e. the component is not running on this cluster.

    "Component not installed" is an ordinary state, not a programming error, so it is
    reported as a message the operator can act on rather than as an IndexError.
    """


class ServiceAccountTokenNotFoundError(Exception):
    """A service account has no token secret to read.

    Clusters since 1.24 do not mint a token for a service account on its own, so the
    secret has to be requested once. This error names the command that does it.
    """


class NamespaceNotFoundError(Exception):
    """The namespace named on the command line is not on this cluster.

    Reading a namespace that was never created is an operator typo or a misremembered
    install, not a programming error, so it is a message that names the namespace rather
    than an ApiException whose status code means nothing without `kubectl explain`.
    """


# How kubementat addresses a deployment's pods, wherever a selector is needed (the same
# label stream_logs documents): the helm release name. Reading a deployment by anything
# else finds the pods a manual `kubectl label` left behind.
DEPLOYMENT_LABEL = "app.kubernetes.io/instance"

# kubementat's claim on an object, in two parts because they are one fact and reading one of them
# from the air is how a sweep deletes somebody else's work. Every writer in this product sends the
# pair (`tekton_utils.helm_deployer_cluster_role`, `tekton_utils._trigger_labels`,
# `tekton_utils.CLEANUP_JOB_LABELS`, the role bindings in `automation._setup_pipelines_setup_role_bindings`,
# the Secrets in `cli/commands/kubernetes.py`), and `kubernetes_utils` is where the pair is said to
# mean what it means: `create_or_replace_secret` documents that an unlabelled object is
# indistinguishable from one the operator wrote.
#
# The uninstall reads it back in the other direction -- see
# `Automation._delete_cluster_scoped_rbac_kubementat_owns` -- which is the other half of the same
# claim: if a teardown is allowed to identify what it owns by a label, the label has to be written
# by the code that owns the object and not guessed at read time.
MANAGED_BY_LABEL = "managed-by"
MANAGED_BY_KUBEMENTAT = "kubementat"

# What `kmt deploy-ssh` hands the pod when the operator does not name a command: bash, under
# the path every kubementat image has shipped. It is a path and not `sh -c ...` for the same
# reason the script it replaced was written that way -- a one-token argv entry cannot be
# re-parsed by a shell that is not there.
DEPLOYMENT_SHELL_COMMAND = ("/bin/bash",)


def parse_cpu(value) -> float:
    """A Kubernetes quantity of cpu, in cores. "500m" -> 0.5, "2" -> 2.0, nanocores too."""
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        if text.endswith("m"):
            return float(text[:-1]) / 1000.0
        if text.endswith("n"):
            return float(text[:-1]) / 1_000_000_000.0
        return float(text)
    except ValueError as error:
        raise ValueError(f"Not a cpu quantity: {value!r}") from error


def parse_memory(value) -> float:
    """A Kubernetes quantity of memory, in bytes.

    Kubernetes writes the binary suffixes (Ki, Mi, Gi, ...) for most memory fields; the
    decimal ones (K, M, G, ...) are legal and show up from custom quantities, so both are
    read. A bare number is bytes.
    """
    text = str(value).strip()
    if not text:
        return 0.0
    suffixes = (
        ("Ei", 1024 ** 6), ("Pi", 1024 ** 5), ("Ti", 1024 ** 4), ("Gi", 1024 ** 3),
        ("Mi", 1024 ** 2), ("Ki", 1024),
        ("E", 1000 ** 6), ("P", 1000 ** 5), ("T", 1000 ** 4), ("G", 1000 ** 3),
        ("M", 1000 ** 2), ("k", 1000),
    )
    try:
        for suffix, factor in suffixes:
            if text.endswith(suffix):
                return float(text[:-len(suffix)]) * factor
        return float(text)
    except ValueError as error:
        raise ValueError(f"Not a memory quantity: {value!r}") from error


def _percent_of(part: dict, whole: dict) -> dict:
    """Part as a percentage of whole, per resource, zero when the whole is zero."""
    result = {}
    for resource in ("cpu", "memory"):
        total = whole.get(resource) or 0
        result[resource] = round((part.get(resource, 0) / total) * 100, 1) if total else 0.0
    return result


def _node_summary(answer) -> dict:
    """One kubelet's `stats/summary` as a document, whatever shape it arrived in.

    A proxy body is bytes, the client's default is the text of that body, and a test that answers
    the call directly hands over the parsed document. All three are the same answer; only the
    transport differs, and reading the wrong one as a mapping is the kind of `TypeError` a
    diagnostic command should not cause.
    """
    if isinstance(answer, (bytes, bytearray)):
        answer = answer.decode("utf-8")
    if isinstance(answer, str):
        return json.loads(answer)
    return answer or {}


def _byte_quantity(value) -> int:
    """A stats figure as whole bytes, with what is not there read as zero.

    A volume entry that carries a `pvcRef` but no `capacityBytes` is a claim whose numbers the
    kubelet did not take. Zero is what goes in its column, because the percentage next to it says
    `n/a` rather than pretending. A value that is present and not a number raises, the same way
    `parse_memory` raises on a quantity that is not one: a malformed figure is a cluster saying
    something strange, not a half that is absent.
    """
    if value is None:
        return 0
    if isinstance(value, str):
        return int(parse_memory(value))
    return int(value)


def _usage_percentage(used: int, capacity: int):
    """Usage as a percentage of capacity, or `None` when there is no capacity to divide by.

    `capacityBytes: 0` is reachable -- a claim the kubelet has not finished attaching, or a volume
    it has not measured. Since the divisor may be zero, the division is not attempted: the answer
    is `None`, and the CLI prints `n/a` for it. The script this replaced died here instead: its
    `jq` said `number (5) and number (0) cannot be divided because the divisor is zero` and exited
    5 (run against such a document on 2026-10-29), which is the traceback a report is not supposed
    to be.
    """
    return round(used / capacity * 100, 1) if capacity else None


def _pod_summary(pod) -> dict:
    """A pod as one line of a report: name, phase, node."""
    return {
        "name": pod.metadata.name,
        "status": pod.status.phase if pod.status else None,
        "node": pod.spec.node_name if pod.spec else None,
    }


class KubernetesUtils:
    """Kubernetes operations against one `ClusterTarget`, and never against "this process".

    The rule in one sentence: this class binds, it does not resolve. It is told which cluster it
    is talking to -- by a `Context` from a command, or by a `ClusterTarget` from the library or a test
    -- and every client it builds comes out of that target alone. The old constructor asked the
    kubernetes library which cluster it meant, which made the answer a
    fact about the machine running the process rather than about the command, and that is what this
    class refuses to keep: a library that holds one process-wide answer cannot hold two clusters.

        Everything is built on first use. That is not a performance preference: the
        configuration-only context builds this object inside `Automation` to write JSON files, and a
        constructor that resolved a kubeconfig made that path fail on a host with none -- which is
        the same argument `Context.for_configuration` makes about the target itself.
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
        return kubectl_target_args(self.target)

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

    def list_roles(self, namespace: str) -> list:
        """
        Roles in a namespace.

        Args:
            namespace (str): Namespace to list from.

        Returns:
            list[client.V1Role]: The roles, empty when there are none.
        """
        return self.rbac_api.list_namespaced_role(namespace=namespace).items

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

        plural_identifier = plural_identifier or plural_for_kind(kind)

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

    # ATTENTION: This will stall the current process until the tunnel is closed
    def open_tunnel(self, namespace, pod_name, local_port, remote_port, host='0.0.0.0'):
        argv = [
            "kubectl",
            *self.kubectl_args,
            "--namespace",
            namespace,
            "port-forward",
            "--address",
            host,
            f"pod/{pod_name}",
            f"{local_port}:{remote_port}",
        ]
        try:
            logging.info(f"Opening tunnel to pod: {pod_name} in namespace: {namespace} on local port: {local_port} pointing to remote pod port: {remote_port}")
            logging.info("###########################")
            logging.info(f"Local address: http://localhost:{local_port}")
            logging.info("###########################")
            logging.info("The tunnel will be closed when you press Ctrl+C")
            # capture=False: port-forward is interactive. Capturing its output is what used to
            # make the tunnel look frozen — nothing reaches the terminal until it exits.
            shell.run(argv, capture=False, check=True)
            return 0
        except shell.CommandExecutionError as error:
            logging.error(f"An error occured while opening a tunnel to pod: {pod_name} in namespace: {namespace}.")
            logging.error(error)
            return 1

    def get_pod_name_for_deployment(self, namespace, selector):
        """The first pod `selector` finds in `namespace`.

        `get pod -l … -o json`, converted. This is a read that decides something: the pod it
        names is the one a tunnel is opened to or a shell is started in, so parsing kubectl's table
        into that answer put the decision on the wrong side of a text format.

        The two failures stay separate. A namespace that does not exist has no pods *and* no
        anything, and a namespace that exists but runs nothing is a component that was never
        installed; the old code said which by whether kubectl's exit was non-zero, and now says it
        because the client raised or did not.
        """
        try:
            items = self.list_pods(namespace, selector)
        except client.exceptions.ApiException as error:
            # Usually a namespace that does not exist because the component was never installed.
            if error.status == 404:
                raise NamespaceNotFoundError(
                    f"Namespace '{namespace}' is not on the cluster, so nothing can be running in "
                    f"it and no pod for selector '{selector}' could be there.") from error
            raise PodNotFoundError(
                f"No pod found in namespace '{namespace}' for selector '{selector}'.\n"
                f"The cluster said: {error}\n"
                "Is the component installed? There is nothing to tunnel to.") from error
        if not items:
            raise PodNotFoundError(
                f"No running pod found in namespace '{namespace}' for selector '{selector}'.\n"
                "Is the component installed? There is nothing to tunnel to.")
        return items[0].metadata.name

    # pod and node helpers
    def list_pods_for_all_namespaces(self) -> list:
        """Every pod on the cluster, whichever namespace it is in.

        `get pods --all-namespaces`, converted. `CoreV1Api.list_pod_for_all_namespaces` is
        the same read; the namespace is on the object rather than on the command line, which is
        why Traefik discovery can say *where* it found a controller.

        Raises:
            client.exceptions.ApiException: When the cluster will not answer. The caller treats that
                as a finding and not a failure, and says which.
        """
        return self.core_api.list_pod_for_all_namespaces().items

    def list_pods(self, namespace: str, selector: str = None) -> list:
        """
        Pods in a namespace, optionally narrowed by a label selector.

        Args:
            namespace (str): Namespace to list pods from.
            selector (str, optional): Label selector, e.g. 'app.kubernetes.io/instance=nginx'.

        Returns:
            list[client.V1Pod]: The pods, empty when nothing matches.
        """
        return self.core_api.list_namespaced_pod(namespace=namespace, label_selector=selector).items

    def list_services(self, namespace: str, selector: str = None) -> list:
        """
        Services in a namespace, optionally narrowed by a label selector.

        Args:
            namespace (str): Namespace to list services from.
            selector (str, optional): Label selector, e.g. 'app.kubernetes.io/name=grafana'.

        Returns:
            list[client.V1Service]: The services, empty when nothing matches.
        """
        return self.core_api.list_namespaced_service(namespace=namespace, label_selector=selector).items

    def get_service_endpoint(self, namespace: str, selector: str) -> str:
        """
        The URL this node can open for the service `selector` names, or `''` when there is none.

        Why not `<name>.<namespace>.svc.cluster.local`: that name only answers inside the
        cluster. A probe that runs on the node asks it and gets nothing at all, which reads as a
        credential failure when it is a routing one (measured on the full lane, where the assert
        reported `grafana api replied 000` with the service found and the namespace right). A
        ClusterIP is routable from the node, so it is what an out-of-cluster caller gets.

        A headless service (`clusterIP: None`) is skipped for the same reason, and so is a
        service with no ports: neither has an address to open. "Nothing routable" is an empty
        string rather than an exception, because the caller has a better answer than `''` -- it
        names the selector and the flag that overrides it -- and a stack trace about a service
        lookup is not what an operator needs when the fix is a flag.

        Args:
            namespace (str): Namespace the service lives in.
            selector (str): Label selector, usually `Config.component_service_selector`.

        Returns:
            str: `http://<clusterIP>:<port>` for the first match, or `''`.
        """
        try:
            services = self.list_services(namespace, selector)
        except Exception as error:  # noqa: BLE001 - the caller's message is the answer here
            logging.debug(f"Could not list services in namespace '{namespace}' for selector "
                          f"'{selector}': {error}")
            return ''

        for service in services:
            spec = service.spec or None
            if spec is None:
                continue
            cluster_ip = getattr(spec, 'cluster_ip', None)
            if not cluster_ip or cluster_ip == 'None':
                continue
            ports = getattr(spec, 'ports', None) or []
            port = getattr(ports[0], 'port', None) if ports else None
            if not port:
                continue
            return f"http://{cluster_ip}:{port}"
        return ''

    def get_pod_logs(self, namespace: str, pod_name: str, container: str = None,
                     tail_lines: int = None, timestamps: bool = False) -> str:
        """
        Read a pod's log once, returning the text.

        This is the captured sibling of :meth:`stream_logs`: one asks for the text and
        keeps going, the other stays attached to the terminal.

        Args:
            namespace (str): Namespace of the pod.
            pod_name (str): Pod to read logs from.
            container (str, optional): Container within the pod.
            tail_lines (int, optional): Only this many lines from the end.
            timestamps (bool): Prefix every line with its timestamp.

        Returns:
            str: The log output.
        """
        return self.core_api.read_namespaced_pod_log(
            namespace=namespace,
            name=pod_name,
            container=container,
            tail_lines=tail_lines,
            timestamps=timestamps
        )

    def stream_logs(self, namespace: str, selector: str, container: str = None,
                    tail_lines: int = None) -> "shell.CommandResult":
        """
        Follow the logs of everything matching a selector, straight to the terminal.

        Blocking, like :meth:`open_tunnel`: `kubectl logs -f` ends when the operator
        interrupts it, so capturing it would show them nothing until it exits. A deployment
        is addressed by its `app.kubernetes.io/instance` label, which is what `selector`
        is for.

        Args:
            namespace (str): Namespace to read logs from.
            selector (str): Label selector selecting the deployment's pods.
            container (str, optional): Container within the selected pods.
            tail_lines (int, optional): Lines from the end to start with.

        Returns:
            shell.CommandResult: Whatever `kubectl logs` reported once it stopped.
        """
        argv = [
            "kubectl",
            *self.kubectl_args,
            "--namespace", namespace,
            "logs",
            "--selector", selector,
            "--timestamps=true",
            "--follow",
        ]
        if container:
            argv += ["--container", container]
        if tail_lines:
            argv += ["--tail", str(tail_lines)]
        logging.info(f"Streaming logs for '{selector}' in namespace '{namespace}' (Ctrl+C to stop)")
        return shell.run(argv, capture=False, check=True)

    def exec_in_pod(self, namespace: str, pod_name: str, command: list,
                    container: str = None, interactive: bool = True,
                    capture: bool = False) -> "shell.CommandResult":
        """
        Run a command inside a container of a pod.

        Args:
            namespace (str): Namespace of the pod.
            pod_name (str): Pod to run in, usually from :meth:`get_pod_name_for_deployment`.
            command (list): The command and its arguments, one entry each. Never a string.
            container (str, optional): Container to run in, required for a multi-container pod.
            interactive (bool): Give the child stdin and a tty, which is what a shell needs.
                A caller running non-interactively should pass False; the CLI decides that
                from the terminal, not this method.
            capture (bool): Collect the output instead of letting it stream.

        Returns:
            shell.CommandResult: The command's verdict and, when captured, its output.
        """
        argv = ["kubectl", *self.kubectl_args, "--namespace", namespace, "exec"]
        if interactive:
            argv += ["--stdin", "--tty"]
        argv.append(pod_name)
        if container:
            argv += ["--container", container]
        argv.append("--")
        argv += list(command)
        return shell.run(argv, capture=capture, check=True)

    def open_deployment_shell(self, namespace: str, deployment: str, container: str = None,
                              command: list = None) -> "shell.CommandResult":
        '''
        Put the operator in a shell inside one pod of a deployment, until they leave it.

        This is `ssh_into_helm_deployment.sh`: find the pod, run a shell in it. Both halves are
        reused rather than rewritten, because the pod lookup already knows the answer that is
        easy to get wrong -- a deployment is addressed by its helm release name in
        `app.kubernetes.io/instance` (`DEPLOYMENT_LABEL`), and a second selector written here
        would find the pods a manual `kubectl label` left behind. `command` exists so a command
        other than a shell can be run through the same lookup; nothing today needs it, and the
        default is what the script always ran.

        The call is not captured. `kubectl exec --stdin --tty` is the terminal, and capturing it
        would show the operator nothing until the session ended -- which is when they press
        Ctrl+D, not when the command inside the pod finishes.

        Args:
            namespace (str): Namespace the deployment lives in.
            deployment (str): Deployment to attach to, addressed by its release name.
            container (str, optional): Container to enter, required for a multi-container pod.
            command (list, optional): What to run, one entry per argument.
                Defaults to :data:`DEPLOYMENT_SHELL_COMMAND`.

        Returns:
            shell.CommandResult: The verdict of the session, once the operator has left.

        Raises:
            NamespaceNotFoundError: When the namespace was never created. Checked first, so a
                typo does not arrive as "no pod found" for a namespace that has no pods because
                it has no anything.
            PodNotFoundError: When the namespace is there and the deployment is not.
        '''
        self._require_namespace(namespace)
        pod_name = self.get_pod_name_for_deployment(
            namespace, f"{DEPLOYMENT_LABEL}={deployment}")
        logging.info(f"Attaching a shell to '{pod_name}' in namespace '{namespace}'"
                     f"{f' (container {container})' if container else ''}. It ends when you exit.")
        return self.exec_in_pod(namespace, pod_name, list(command or DEPLOYMENT_SHELL_COMMAND),
                                container=container, interactive=True, capture=False)

    def stream_deployment_logs(self, namespace: str, deployment: str, container: str = None,
                              tail_lines: int = None) -> "shell.CommandResult":
        '''
        Follow the logs of a deployment, to the terminal, until the operator interrupts it.

        The other half of what the two helm scripts did: `tail_logs_for_helm_deployment.sh` was
        one `kubectl logs -l 'app.kubernetes.io/instance'=<name> --timestamps=true -f`, which is
        :meth:`stream_logs` with the selector that names a deployment filled in. Like the shell,
        this blocks and is not captured -- Ctrl+C is how it ends.

        Args:
            namespace (str): Namespace the deployment lives in.
            deployment (str): Deployment to read logs from, addressed by its release name.
            container (str, optional): Container within the selected pods.
            tail_lines (int, optional): Where to start. Absent means from the beginning, which
                is what the script did and what someone debugging a failed rollout wants.

        Returns:
            shell.CommandResult: The verdict of `kubectl logs`, once it stopped.

        Raises:
            NamespaceNotFoundError: When the namespace was never created, rather than as a
                `kubectl logs` failure that names only the namespace in passing.
        '''
        self._require_namespace(namespace)
        return self.stream_logs(namespace, f"{DEPLOYMENT_LABEL}={deployment}",
                                container=container, tail_lines=tail_lines)

    def _require_namespace(self, namespace: str) -> None:
        '''
        Stop a namespace that is not there before anything reaches for its pods.

        Both halves of these two commands read through `kubectl` (`get pod -l`, `logs -l`), and
        kubectl answers a namespace that does not exist with an error that could mean anything.
        The client can answer it exactly, so it is asked first and the answer goes out as
        :class:`NamespaceNotFoundError` -- the same sentence every other namespace-typing
        command gives.

        This is a read the scripts did not make. It is also the only read here that says "the
        namespace is missing" in those words, which is why it is worth the one call.

        Args:
            namespace (str): Namespace to confirm.

        Raises:
            NamespaceNotFoundError: When the cluster says it has no such namespace.
        '''
        try:
            self.core_api.read_namespace(name=namespace)
        except client.exceptions.ApiException as error:
            if error.status == 404:
                raise NamespaceNotFoundError(
                    f"Namespace '{namespace}' does not exist on this cluster.") from error
            raise

    def get_nodes(self) -> list:
        """
        Every node on the cluster.

        Returns:
            list[client.V1Node]: The nodes; `status.addresses`, `status.capacity` and
            `status.allocatable` are what the reporting commands read.
        """
        return self.core_api.list_node().items

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

    # diagnostics: what is running, what images it runs, how full the cluster is
    def analyze_deployment(self, namespace: str, deployment: str) -> dict:
        """
        Everything to look at when a deployment is not behaving: its pods and their logs.

        Args:
            namespace (str): Namespace the deployment lives in.
            deployment (str): Deployment to analyze, addressed the way every other
                kubementat command addresses a deployment: the `app.kubernetes.io/instance`
                label, which is the helm release name.

        Returns:
            dict: The pods in the namespace, the pods the deployment selects, their logs,
            and a `message` when the deployment answers nothing. A deployment that is not
            there is reported, not raised: the whole point of this command is looking at
            what is missing, and the pod list is useful evidence even then.

        Raises:
            NamespaceNotFoundError: When the namespace does not exist.
        """
        selector = f"{DEPLOYMENT_LABEL}={deployment}"
        try:
            pods = self.list_pods(namespace)
            matched = self.list_pods(namespace, selector=selector)
            try:
                self.apps_api.read_namespaced_deployment(name=deployment, namespace=namespace)
            except client.exceptions.ApiException as error:
                if error.status != 404:
                    raise
                message = (f"Deployment '{deployment}' does not exist in namespace "
                           f"'{namespace}'.")
            else:
                message = None
        except client.exceptions.ApiException as error:
            if error.status == 404:
                raise NamespaceNotFoundError(
                    f"Namespace '{namespace}' does not exist on this cluster.") from error
            raise

        logs = {}
        for pod in matched:
            name = pod.metadata.name
            try:
                logs[name] = self.get_pod_logs(namespace=namespace, pod_name=name)
            except client.exceptions.ApiException as error:
                if error.status == 404:
                    logs[name] = "<the pod is gone; there are no logs to read>"
                else:
                    raise
        if not logs:
            message = message or (f"No pods carry the selector '{selector}' in namespace "
                                  f"'{namespace}'.")
        return {
            "namespace": namespace,
            "deployment": deployment,
            "selector": selector,
            "pods": [_pod_summary(p) for p in pods],
            "matched_pods": [_pod_summary(p) for p in matched],
            "logs": logs,
            "message": message,
        }

    def list_pod_images(self, namespace: str) -> dict:
        """
        The pods in a namespace and every image they run.

        Args:
            namespace (str): Namespace to read.

        Returns:
            dict: `pods` (sorted pod names) and `images` (sorted unique images, counting
            init containers too — a debug image pulling from a private registry is still
            an image the registry has to serve).

        Raises:
            NamespaceNotFoundError: When the namespace does not exist.
        """
        try:
            pods = self.list_pods(namespace)
        except client.exceptions.ApiException as error:
            if error.status == 404:
                raise NamespaceNotFoundError(
                    f"Namespace '{namespace}' does not exist on this cluster.") from error
            raise
        images = set()
        for pod in pods:
            containers = (pod.spec.containers or []) + (pod.spec.init_containers or [])
            images.update(container.image for container in containers)
        return {
            "pods": sorted(pod.metadata.name for pod in pods),
            "images": sorted(images),
        }

    def cluster_capacity(self) -> dict:
        """
        Requested versus allocatable per node, and real usage when metrics-server serves it.

        Requests come from the pods scheduled onto each node (the same numbers
        `kubectl describe node` prints in its Requests section) and are measured against
        `status.allocatable`, not the node's raw capacity, because that is what actually
        schedulable capacity means. Usage comes from the metrics API, which is a separate
        question to the cluster: a cluster without metrics-server answers the requests
        half and says so about the usage half. It is a message in the result, never a
        traceback.

        Returns:
            dict: `nodes` (name, allocatable, requested, requested_percent, usage_percent
            or None), `average_requested_percent`, `pod_distribution`, and `metrics`
            (`available` plus a `message` when it is not installed).
        """
        nodes = self.get_nodes()
        pods = self.core_api.list_pod_for_all_namespaces().items

        metrics_available = True
        metrics_message = None
        usage = {}
        try:
            metrics = self.get_custom_objects_api().list_cluster_custom_object(
                group="metrics.k8s.io", version="v1beta1", plural="nodes")
            for item in metrics.get("items", []):
                node_usage = {"cpu": 0.0, "memory": 0.0}
                for container in item.get("containers", []):
                    node_usage["cpu"] += parse_cpu(container.get("usage", {}).get("cpu", 0))
                    node_usage["memory"] += parse_memory(
                        container.get("usage", {}).get("memory", 0))
                usage[item.get("metadata", {}).get("name")] = node_usage
        except client.exceptions.ApiException as error:
            # 404 says no service is mapped for metrics.k8s.io, i.e. metrics-server is not
            # installed; a 503 says it is installed but not answering. Either way the
            # capacity answer stays valid, so the absence goes into the result.
            if error.status in (404, 503):
                metrics_available = False
                metrics_message = ("metrics-server is not installed or not serving "
                                   "metrics.k8s.io/v1beta1, so actual usage cannot be "
                                   "reported. Requested-versus-allocatable below comes "
                                   "from the pods, not the metrics API.")
            else:
                raise

        requested = {}
        pod_distribution = []
        for pod in pods:
            node_name = pod.spec.node_name if pod.spec else None
            phase = pod.status.phase if pod.status else None
            if phase in ("Succeeded", "Failed"):
                # A finished pod stopped asking for cpu and memory; counting it would say
                # the node is fuller than it is.
                continue
            if node_name:
                totals = requested.setdefault(node_name, {"cpu": 0.0, "memory": 0.0})
                for container in (pod.spec.containers or []) + (pod.spec.init_containers or []):
                    resources = container.resources
                    if resources and resources.requests:
                        totals["cpu"] += parse_cpu(resources.requests.get("cpu", 0))
                        totals["memory"] += parse_memory(resources.requests.get("memory", 0))
            pod_distribution.append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": phase,
                "node": node_name,
            })

        node_rows = []
        for node in sorted(nodes, key=lambda n: n.metadata.name):
            name = node.metadata.name
            allocatable = {
                "cpu": parse_cpu((node.status.allocatable or {}).get("cpu", 0)),
                "memory": parse_memory((node.status.allocatable or {}).get("memory", 0)),
            }
            node_requested = requested.get(name, {"cpu": 0.0, "memory": 0.0})
            percent = _percent_of(node_requested, allocatable)
            node_usage = usage.get(name)
            node_rows.append({
                "name": name,
                "allocatable": allocatable,
                "requested": node_requested,
                "requested_percent": percent,
                "usage_percent": _percent_of(node_usage, allocatable) if node_usage else None,
            })

        average = None
        if node_rows:
            average = {
                "cpu": round(sum(row["requested_percent"]["cpu"] for row in node_rows)
                             / len(node_rows), 1),
                "memory": round(sum(row["requested_percent"]["memory"] for row in node_rows)
                                / len(node_rows), 1),
            }
        return {
            "nodes": node_rows,
            "average_requested_percent": average,
            "pod_distribution": pod_distribution,
            "metrics": {"available": metrics_available, "message": metrics_message},
        }

    def list_pvc_usage(self) -> dict:
        """
        Every claim the kubelets report, and how full each of them is.

        The numbers come from each node's own `/stats/summary`, asked for through the client's
        node-proxy call (`CoreV1Api.connect_get_node_proxy_with_path`, which renders
        `/api/v1/nodes/<node>/proxy/stats/summary`). The script this replaces, `list_pvc_usage.sh`,
        is gone and reached the same URL by starting `kubectl proxy` in the background,
        `sleep 2`, curling `127.0.0.1:8001`, then `pkill -9`ing the proxy. Two seconds was the
        whole synchronisation, and it was a guess about how long a local HTTP server needs; the
        client asks for the same path and answers when the cluster answers.

        Only a volume entry carrying a `pvcRef` is a claim. That is the script's
        `select(has("pvcRef"))` and for its reason: the node's stats list `emptyDir`s, config
        maps and secrets in the same column, and an entry without a `pvcRef` is a directory on the
        node rather than a claim somebody asked the cluster for.

        A kubelet that answers nothing, or one that answers without a `pods` half, is a message in
        `messages` and not a traceback. It is the rule `cluster_capacity` states about
        metrics-server, here pointed the other way: there a whole API service may be absent, here
        one node of N may be, and in both cases what is missing is named in the answer instead of
        guessed from the rest of it. `capacityBytes: 0` is the same class of case and is answered
        with no percentage rather than with a `ZeroDivisionError` (see
        `_usage_percentage`).

        Returns:
            dict: `pvcs` - one row per claim, sorted by name, each row `name`, `node`,
            `capacity_bytes`, `used_bytes`, `available_bytes`, `percentage_used` (`None` when the
            capacity is 0); `nodes` - the names read, sorted; `messages` - one entry per node that
            did not answer, naming it and saying what that leaves out.

            A claim bound onto more than one node is two rows, because two kubelets answered about
            it. The script merged every node's list into one column and said nothing about which
            node said so; `node` is that missing half, and it is also what makes the sort
            deterministic when two nodes report the same claim.
        """
        rows = []
        messages = []
        nodes_read = []

        for node in sorted(self.get_nodes(), key=lambda n: n.metadata.name):
            name = node.metadata.name
            nodes_read.append(name)
            try:
                summary = self.core_api.connect_get_node_proxy_with_path(
                    name=name, path=NODE_STATS_PATH)
                payload = _node_summary(summary)
            except client.exceptions.ApiException as error:
                messages.append(
                    f"Node '{name}' answered nothing (status {error.status}), so the claims "
                    "scheduled onto it are missing from the rows below.")
                continue
            except ValueError as error:
                messages.append(
                    f"Node '{name}' answered, but not with a stats summary that parses as JSON "
                    f"({error}), so the claims scheduled onto it are missing from the rows below.")
                continue
            if "pods" not in payload:
                # Not a silence and not a failure: a kubelet that is serving something else. The
                # volumes it would have listed are simply not in what it chose to report.
                messages.append(
                    f"Node '{name}' answered without a pods list, so there are no volume entries "
                    f"to read claims out of.")
                continue
            for pod in payload.get("pods") or []:
                for volume in (pod.get("volume") or []):
                    reference = volume.get("pvcRef")
                    if not reference:
                        continue
                    capacity = _byte_quantity(volume.get("capacityBytes"))
                    used = _byte_quantity(volume.get("usedBytes"))
                    rows.append({
                        "name": reference.get("name"),
                        "node": name,
                        "capacity_bytes": capacity,
                        "used_bytes": used,
                        "available_bytes": _byte_quantity(volume.get("availableBytes")),
                        "percentage_used": _usage_percentage(used, capacity),
                    })

        rows.sort(key=lambda row: (row["name"] or "", row["node"]))
        return {"pvcs": rows, "nodes": nodes_read, "messages": messages}

