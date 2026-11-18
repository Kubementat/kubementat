"""Recorded Kubernetes client calls, reduced to the operations a test asserts on."""
import base64
import json
import re

# The fake API transport's own record (`tests/apiserver_fake.py`) arrives in the same log as the
# shims' calls, because a run that reads through the client and opens a `kubectl` session is one
# run and the comparison needs both halves. It is not a tool on `PATH` and it is not a mistake:
# `apiserver` is named so a reader can tell the two mechanisms apart in the same list.
API_PROGRAM = "apiserver"
# `--kube-context` is helm's spelling and it took a value, so leaving it out of `VALUE_FLAGS`
# above put its value in the *positional* list instead — `helmfile --kube-context dev apply` read
# as `dev apply`, and the cluster was not a flag the reducer could find. The ratchet that catches
# a flag missing from a table is `test_cluster_flags_are_parsed_as_values_and_not_as_verbs`.


def _flagged(value):
    """A cluster flag's value, or nothing when the flag arrived alone."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "true":
        return None
    return text


def cluster_named(*, context=None, kubeconfig=None):
    """A cluster spelled the way the record spells one, from the parts that name it."""
    named = _flagged(context)
    if named:
        return f"context {named}"
    named = _flagged(kubeconfig)
    if named:
        return f"kubeconfig {named}"
    return None


_PLURALS = {
    "secrets": "secret",
    "namespaces": "namespace",
    # `ns` is kubectl's short name; without this entry it would singularise to `n`.
    "ns": "namespace",
    "serviceaccounts": "serviceaccount",
    "roles": "role",
    "rolebindings": "rolebinding",
    "clusterroles": "clusterrole",
    "clusterrolebindings": "clusterrolebinding",
    "configmaps": "configmap",
    "pods": "pod",
    "nodes": "node",
    "pipelines": "pipeline",
    "tasks": "task",
    "taskruns": "taskrun",
    "pipelineruns": "pipelinerun",
}


class UnaddressedOperationError(Exception):
    """A call this harness refuses to guess about."""


def _singular(kind):
    kind = (kind or "").strip().lower().replace("-", "").replace("_", "")
    if kind in _PLURALS:
        return _PLURALS[kind]
    if kind.endswith("ies"):
        return kind[:-3] + "y"
    if kind.endswith("s") and not kind.endswith(("ss", "us")):
        return kind[:-1]
    return kind


def _selector(text):
    """A label selector, so key order does not count as a difference."""
    return ",".join(sorted(term.strip() for term in str(text).split(",") if term.strip()))


def _model(value):
    """A Kubernetes object, whatever it is, as JSON: unset fields gone, keys sorted."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, (dict, list)):
        return json.dumps(_prune(value), sort_keys=True)
    return json.dumps(str(value))


def _prune(value):
    if isinstance(value, dict):
        return {key: _prune(item) for key, item in value.items() if item not in (None, {}, [])}
    if isinstance(value, list):
        return [_prune(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


def _body_to_dict(body):
    if isinstance(body, dict):
        return body
    if hasattr(body, "to_dict"):
        return body.to_dict()
    return None


def _registry_endpoints(dockerconfigjson):
    """The registry URLs a docker-registry Secret is *for*."""
    if not dockerconfigjson:
        return ()
    try:
        decoded = base64.b64decode(dockerconfigjson).decode("utf-8") if not str(
            dockerconfigjson).strip().startswith("{") else dockerconfigjson
        return tuple(sorted(json.loads(decoded).get("auths", {})))
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise UnaddressedOperationError(f".dockerconfigjson is not decodable as JSON: {error}")


def _rule_triple(rule):
    """One RBAC rule, spelled one way, whatever client wrote it."""
    if not isinstance(rule, dict):
        rule = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule)
    return (tuple(rule.get("apiGroups") or rule.get("api_groups") or ()),
            tuple(rule.get("resources") or ()),
            tuple(rule.get("verbs") or ()))


def payload_facts(kind, body):
    """The fields an operation is about, for each kind it appears for."""
    document = _body_to_dict(body)
    if document is None:
        return () if body is None else (("body", _model(body)),)
    facts = []
    if kind == "secret":
        if document.get("type"):
            facts.append(("type", str(document["type"])))
        data = document.get("data") or {}
        string_data = document.get("string_data") or {}
        keys = tuple(sorted(set(data) | set(string_data)))
        if keys:
            facts.append(("data", keys))
        registries = _registry_endpoints((data or {}).get(".dockerconfigjson"))
        if registries:
            facts.append(("registries", registries))
    elif kind == "serviceaccount":
        # `imagePullSecrets` on the wire, `image_pull_secrets` on a client model's `to_dict()`.
        pull_secrets = document.get("imagePullSecrets") or document.get("image_pull_secrets") or []
        if pull_secrets:
            facts.append(("imagePullSecrets",
                          tuple(sorted(reference.get("name") if isinstance(reference, dict) else
                                       getattr(reference, "name", reference) for reference in pull_secrets))))
    elif kind in ("rolebinding", "clusterrolebinding"):
        # A binding's whole claim is who it binds to what, so those two halves are the payload;
        # the label is how kubementat says it owns the object. `subjects[].namespace` is part of
        # the claim, because a cluster-scoped binding reaches into one namespace by name.
        document = document or {}
        subjects = document.get("subjects") or []
        facts.append(("subjects", tuple(sorted(
            (str(subject.get("kind") or ""), str(subject.get("name") or ""),
             str(subject.get("namespace") or ""))
            for subject in subjects if isinstance(subject, dict)))))
        reference = document.get("roleRef") or document.get("role_ref") or {}
        facts.append(("roleRef", (str(reference.get("kind") or ""),
                                 str(reference.get("name") or ""),
                                 str(reference.get("apiGroup") or reference.get("api_group") or ""))))
        labels = ((document.get("metadata") or {}).get("labels")) or {}
        if labels:
            facts.append(("labels", tuple(sorted(labels))))
    elif kind in ("clusterrole", "role"):
        # The rules are the whole claim of an RBAC object, and the label is how kubementat says it
        # created this one; both appear on either side of the comparison.
        rules = document.get("rules")
        if rules:
            facts.append(("rules", tuple(sorted(_rule_triple(rule) for rule in rules))))
        labels = ((document.get("metadata") or {}).get("labels")) or {}
        if labels:
            facts.append(("labels", tuple(sorted(labels))))
    elif kind == "pipelinerun":
        facts.append(("manifest", _model(_without_generated_name(document))))
    return tuple(facts) or (("body", _model(document)),)


def _without_generated_name(document):
    """Drop the field a run name makes random, keep everything the substitution wrote."""
    clone = json.loads(json.dumps(_prune(document)))
    metadata = clone.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("name", None)
    return clone


class ClusterOperation:
    """One operation on the cluster, reduced to the claim it makes."""

    def __init__(self, verb, kind, namespace=None, name=None, facts=(), cluster=None):
        self.verb = verb
        self.kind = kind
        self.namespace = namespace
        self.name = name
        self.facts = tuple(sorted(facts))
        self.cluster = cluster

    @property
    def is_read(self):
        return self.verb == "read"

    def __eq__(self, other):
        return isinstance(other, ClusterOperation) and self.compare_key().__dict__ == other.compare_key().__dict__

    def __hash__(self):
        key = self.compare_key()
        return hash((key.verb, key.kind, key.namespace, key.name, key.facts, key.cluster))

    def __repr__(self):
        rendered = f"{self.verb} {self.kind}"
        if self.cluster:
            rendered += f" [{self.cluster}]"
        if self.namespace:
            rendered += f" ns/{self.namespace}"
        if self.name:
            rendered += f" {self.name}"
        for key, value in self.facts:
            rendered += f" {key}={value}"
        return rendered

    __str__ = __repr__

    def compare_key(self):
        """The identity a read is compared on: verb, kind, namespace and cluster -- never a name."""
        if self.is_read:
            return ClusterOperation("read", self.kind, self.namespace, None, self.facts,
                                    self.cluster)
        return self


def render(operations):
    """A column of operations, for the failure message. Two of these side by side is the diff."""
    return "\n".join(f"  {index}: {op}" for index, op in enumerate(operations)) or "  (nothing)"


# ---------------------------------------------------------------------------
# recorded client calls
# ---------------------------------------------------------------------------

ENSURE_VERBS = {"apply", "create", "replace", "update", "ensure"}

_API_METHOD = re.compile(r"^(create|replace|patch|delete|read|list|apply|get)_(namespaced_)?(.+)$")

# Client calls that ask and do not change. `create_` is the method prefix the generated client
# gives a `SelfSubjectAccessReview`, and nothing in that name says "this is a question".
PROBE_METHODS = {"create_self_subject_access_review", "create_self_subject_rules_review"}


def operations_from_api_calls(calls):
    """Recorded generated-client calls into operations."""
    operations = []
    for entry in calls:
        method, kwargs, cluster = (entry[0], entry[1],
                                  entry[2] if len(entry) > 2 else None)
        if cluster is None:
            raise UnaddressedOperationError(
                f"{method}: recorded without a cluster. The recorder that logged it was not bound to "
                f"a ClusterTarget, so the call's own target is invisible and this harness will not "
                f"guess it. Bind the recorder: ApiRecorder.for_target(target), or hand the "
                f"test's own ClusterTarget to the client that records.")
        operations.append(_api_operation(method, dict(kwargs), cluster))
    return operations


def _api_operation(method, kwargs, cluster=None):
    if method == "get_api_resources":
        return ClusterOperation("read", "api-resources", None, None,
                                (("group", kwargs.get("group")), ("version", kwargs.get("version"))),
                                cluster)
    if method == "list_cluster_custom_object":
        # A cluster-scoped read of a *custom* resource: there is no method-baked kind to
        # read, so the kind is the plural and the group/version are facts of the operation,
        # which is what separates a metrics-API node read from a core node list.
        return ClusterOperation("read", _singular(str(kwargs.get("plural", ""))), None, None,
                                (("group", kwargs.get("group")), ("version", kwargs.get("version"))),
                                cluster)
    if method == "connect_get_node_proxy_with_path":
        # The client's way into a kubelet: the call renders `/api/v1/nodes/{name}/proxy/{path}`,
        # so it is a read of the node and the path is the fact that tells it apart from a node
        # list.
        return ClusterOperation("read", "node", None, kwargs.get("name"),
                                (("path", kwargs.get("path")),), cluster)
    if method in PROBE_METHODS:
        # A `SelfSubjectAccessReview` is kubectl's `auth can-i` wearing a different hat: it asks
        # the server "may I do this" and the answer arrives as a status, not as an object. The
        # generated client still spells it `create_`, and a reducer that trusts the method name
        # reads a preflight as a write and reports a refusal path as having modified the cluster.
        attributes = review_attributes(kwargs.get("body"))
        return ClusterOperation("read", "access-review", kwargs.get("namespace"), None,
                                (("verb", attributes["verb"]),
                                 ("resource", attributes["resource"])), cluster)
    if method.endswith("_for_all_namespaces"):
        # A cluster-wide read is still a read of the same kind; the scope shows up as the
        # absence of a namespace, which is how compare_key already represents it.
        method = method[:-len("_for_all_namespaces")]
    match = _API_METHOD.match(method)
    if not match:
        raise UnaddressedOperationError(f"kubernetes client: no rule for {method}")
    verb, _, remainder = match.groups()
    body = kwargs.get("body")
    kind = _singular(remainder)
    if kind.startswith("customobject"):
        kind = _singular(str((body or {}).get("kind", kwargs.get("plural", ""))))
    namespace = kwargs.get("namespace")
    if namespace is None:
        namespace = ((_body_to_dict(body) or {}).get("metadata") or {}).get("namespace")
    name = kwargs.get("name") or kwargs.get("resource_name") or _name_of(body)
    if kind == "customobject":
        kind = _singular(str((body or {}).get("kind", kwargs.get("plural", ""))))
    if kind == "namespace":
        name = kwargs.get("name") or namespace or _name_of(body)
    # A list keeps its selector: two lists of one kind with different selectors are different reads.
    selector = kwargs.get("label_selector") or kwargs.get("field_selector")
    facts = ([("selector", _selector(selector))] if method.startswith("list") and selector else []) \
        + list(payload_facts(kind, body))
    # `get_namespaced_custom_object` is the generated client's one-object read, and the CLI side of
    # this harness already calls `get` a read — READ_VERBS says so. It was missing from the reducer's
    # verb list, so a waiter reading one run would have raised `UnaddressedOperationError` for the same act that
    # `kubectl get pod` is allowed to be.
    verb = "ensure" if verb in ENSURE_VERBS else ("read" if verb in ("read", "list", "get") else verb)
    return ClusterOperation(verb, kind, namespace, name, facts, cluster)


def review_attributes(body):
    """The verb and resource a subject access review asked about, model or dict."""
    def field(owner, key):
        if owner is None:
            return None
        if isinstance(owner, dict):
            return owner.get(key)
        return getattr(owner, key, None)

    spec = field(body, "spec")
    attributes = field(spec, "resource_attributes")
    return {"verb": field(attributes, "verb"), "resource": field(attributes, "resource")}


def _name_of(body):
    document = _body_to_dict(body)
    return ((document or {}).get("metadata") or {}).get("name")
