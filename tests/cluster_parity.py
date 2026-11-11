"""The comparison half of the bash/Python parity harness.

`tests/conftest.py` puts fake cluster tools on `PATH`; this module reduces what they recorded
to something two different implementations can be *equal* to.

The problem it solves: the bash scripts speak to the cluster with `kubectl`, and the Python
replacement mostly does not — it calls the generated Kubernetes client. `create_or_replace_secret`
and `kubectl apply -f -` are the same act, and no textual comparison of the two will ever say
so. So both sides are reduced to one shape:

    ClusterOperation(verb, kind, namespace, name, facts, cluster)

Five rules make that shape comparable instead of merely convenient. They are what someone
reading a parity failure needs to know:

* **create / replace / update / apply are one verb: `ensure`.** They answer one question — is
  this object present with this content — and kubectl's `apply` means create-or-update, so
  telling them apart compares an implementation detail.
* **a read is a read.** `get`, `describe` and `list` reduce to `read`, and the *name* goes
  with them: reading one secret and listing a namespace's secrets is the same read of the same
  kind in the same namespace. Selectors survive, because those do state what was guaranteed.
* **writes stay in order, reads do not.** A delete before a create is a different operation. A
  `kubectl get` that moved is not. That is the plan's tolerance for read reordering,
  made precise rather than fuzzy.
* **the cluster is part of the record.** one `create secret` on `dev` and one on `prod`
  reduce identically in every other field, so a record without a cluster is a parity test that
  can pass while the wrong cluster is edited. Both channels carry it — the binaries in their argv
  (`--context`/`--kubeconfig`, helm's `--kube-context`) and the client through the `ClusterTarget`
  its recorder is bound to. An operation whose target the reducer cannot see raises, and that is
  the same rule as the paragraph below, not a new one.

Anything the reducer cannot recognise raises `ParityError` instead of being skipped, and an
operation that appears on one side only has to be *declared* by the test that compares them.
A parity test that quietly drops a call it did not understand is the failure mode this harness
exists to avoid.
"""

import base64
import json
import re

import yaml

# Programs that talk to the cluster (or to a registry) rather than to a human.
CLUSTER_TOOLS = {"kubectl", "tkn", "helm", "helmfile", "mysql", "git-crypt", "ssh-keygen"}

# The fake API transport's own record (`tests/apiserver_fake.py`) arrives in the same log as the
# shims' calls, because a run that reads through the client and opens a `kubectl` session is one
# run and the comparison needs both halves. It is not a tool on `PATH` and it is not a mistake:
# `apiserver` is named so a reader can tell the two mechanisms apart in the same list.
API_PROGRAM = "apiserver"

# What is allowed to go unaddressed: the helm calls that read this machine and never ask a cluster
# anything, so a `run` operation for them carries no cluster *and must not* be asked for one.
# This is the guard test's table (`test_no_helm_call_is_addressed_by_the_process_any_more.py`
# imports it from here) because the harness and the guard are asking one question — "did this ask
# a cluster?" — and two answers to it is the disease this tree keeps naming.
LOCAL_ONLY_HELM_CALLS = {
    ("helm", "repo", "update"): "helm's own repository index on this machine; no cluster is asked",
    ("helm", "repo", "list"): "the repositories this helm has been told about, which is local config",
    ("helm", "show", "crds"): "the chart's crds/ directory, unpacked from the chart cache",
    ("helm", "search", "repo"): "helm's repository index, a local database of charts",
    ("helm", "version"): "the binary's version, which is what the dependency check reads",
    ("helm", "diff", "version"): "the diff plugin's version, same reason",
    ("helmfile", "--version"): "the binary's version, which is what the dependency check reads",
}

# The flags that name a cluster on the command line, in the spellings the binaries accept. helm
# rejects kubectl's `--context`, so both spellings belong here; a harness that recognised only one
# would report a cluster call that was addressed, in one spelling, and unaddressed in the other.
CLUSTER_FLAGS = ("context", "kube-context", "kubeconfig")

# `cluster-info` is a read of the cluster's own identity: bash printed it so the operator knew
# which cluster they were about to edit, and `set -e` meant it had to succeed. It was in
# `KINDLESS_READS` but not in `READ_VERBS`, so the reducer raised `ParityError` on a team-account
# run and a parity test had to drop that call - the quiet dropping this harness exists to
# prevent.
READ_VERBS = {"get", "describe", "list", "version", "config", "can-i", "auth", "logs", "top",
              "api-resources", "cluster-info"}
ENSURE_VERBS = {"apply", "create", "replace", "update", "ensure"}
KINDLESS_READS = {"version", "api-resources", "cluster-info", "can-i", "kubeconfig"}

# Flags that consume the argument after them. What follows one is a value, never a verb.
VALUE_FLAGS = {
    "-n", "-o", "-l", "-f", "-p", "-c", "-v", "-i", "-t",
    "--namespace", "--output", "--selector", "--filename", "--patch", "--type",
    "--context", "--kube-context", "--cluster", "--user", "--kubeconfig",
    "--field-selector", "--sortby",
    "--template", "--docker-server", "--docker-username", "--docker-password", "--docker-email",
    "--dry-run", "--address", "--image", "--port", "--command", "--container", "--tail",
    "--local-port", "--remote-port", "--key", "--format", "--timeout", "-b",
}
# `--kube-context` is helm's spelling and it took a value, so leaving it out of `VALUE_FLAGS`
# above put its value in the *positional* list instead — `helmfile --kube-context dev apply` read
# as `dev apply`, and the cluster was not a flag the reducer could find. The ratchet that catches
# a flag missing from a table is `test_cluster_flags_are_parsed_as_values_and_not_as_verbs`.


def _flagged(value):
    """A cluster flag's value, or nothing when the flag arrived alone.

    `--context` with nothing after it says "someone meant to name one" and nothing else, and that
    is not a target. The shims record a bare flag as the literal `true`.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "true":
        return None
    return text


def cluster_named(*, context=None, kubeconfig=None):
    """A cluster spelled the way the record spells one, from the parts that name it.

    This is the spelling every *declared* cluster has to use, so a test cannot invent a private one
    that quietly matches nothing. Prefer it, or `cluster_fixture.target_cluster`, to typing the
    string by hand: the format belongs to the harness and not to the test that uses it.
    """
    named = _flagged(context)
    if named:
        return f"context {named}"
    named = _flagged(kubeconfig)
    if named:
        return f"kubeconfig {named}"
    return None


def cluster_identity(flags):
    """The one string two sides compare clusters on, or `None` when neither flag names one.

    The context is the identity when it is there, because it is what names the cluster; the file is
    the identity only when no context names it, because a path is the part of an address that is a
    local artefact — comparing it everywhere would make a fixture's `tmp_path` part of every
    expected set, and a run that chose the wrong *context* inside the right file is the failure
    this comparison is about.

    A flag that arrived with no value is not a target: see `cluster_named`.
    """
    return cluster_named(context=flags.get("context") or flags.get("kube-context"),
                         kubeconfig=flags.get("kubeconfig"))


def helm_is_local_only(program, args):
    """Whether a helm/helmfile argv is one of the exempt local reads.

    Same rule as the guard's (`..._addressed_by_the_process_any_more.py`, whose table this is),
    written the same way: the words that name the operation, with the cluster flags and their values
    taken out, matched against the declared prefixes. Prefix and not equality, so
    `helm repo update --kubeconfig /x` is still the read of a local index that it is.

    `args` is what the shim recorded, which excludes the program's own name -- that name is the
    first word of every declaration in `LOCAL_ONLY_HELM_CALLS`, so it is put back here.
    """
    asked, skipping = [program], False
    for word in args:
        if skipping:
            skipping = False
            continue
        if word in ("--context", "--kube-context", "--kubeconfig"):
            skipping = True
            continue
        asked.append(word)
    return any(asked[:len(declared)] == list(declared) for declared in LOCAL_ONLY_HELM_CALLS)


def touches_cluster(program, args):
    """Whether this call asked something of a *cluster*, as opposed of this machine.

    The reducer has to know the difference, because the rule cuts both ways: a cluster call the
    harness cannot address raises, and demanding a cluster for `helm repo update` -- a read of a
    local index -- would raise on something that is not the failure this is for.
    """
    if program == API_PROGRAM:
        return True
    if program in ("helm", "helmfile"):
        return not helm_is_local_only(program, args)
    if program == "kubectl":
        # `kubectl config ...` reads the local file. Everything else, including a session like
        # `exec` or `port-forward`, is an ask of whichever cluster the flags name.
        return not (len(args) > 0 and args[0] == "config")
    return program not in {"mysql", "git-crypt", "ssh-keygen"}


_PLURALS = {
    "secrets": "secret",
    "namespaces": "namespace",
    # `kubectl get ns` is how bash asks, and `list_namespace` is how the client asks. Reduced
    # without this entry, `ns` becomes `n` and a read that both sides performed looks like a
    # difference. Same class of bug as the binding rule: the harness had no name for it.
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


class ParityError(Exception):
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
    """A label selector, so key order does not count as a difference.

    The scripts and `constants.py` have listed `app.kubernetes.io/name=…` and
    `app.kubernetes.io/instance=…` in the opposite order for at least one component — `vault` did,
    for as long as the scripts and the code disagreed about which of the two came first. That is not
    two selectors, so it is not a difference. `openbao` is written in the order its chart writes,
    so there is no disagreement to fold today, but the fold stays: the rule is that a selector is a
    set, and a rename that reorders one must not read as a product change.
    """
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
    """The registry URLs a docker-registry Secret is *for*.

    In bash they come from `--docker-server=…`; in Python they are written into
    `.dockerconfigjson`. Comparing the flag against nothing would be worse than useless: the
    URL is the only fact the whole operation exists to carry.
    """
    if not dockerconfigjson:
        return ()
    try:
        decoded = base64.b64decode(dockerconfigjson).decode("utf-8") if not str(
            dockerconfigjson).strip().startswith("{") else dockerconfigjson
        return tuple(sorted(json.loads(decoded).get("auths", {})))
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise ParityError(f".dockerconfigjson is not decodable as JSON: {error}")


def _rule_triple(rule):
    """One RBAC rule, spelled one way, whatever client wrote it.

    A manifest says `apiGroups`; a client model's `to_dict()` says `api_groups`. They are the same
    rule, and a ClusterRole whose rules are compared as free-form JSON would fail for that reason
    alone — which is how a parity test ends up declaring a difference that is not a difference.
    """
    if not isinstance(rule, dict):
        rule = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule)
    return (tuple(rule.get("apiGroups") or rule.get("api_groups") or ()),
            tuple(rule.get("resources") or ()),
            tuple(rule.get("verbs") or ()))


def payload_facts(kind, body):
    """The fields an operation is about, for each kind it appears for.

    One function, so the two sides cannot disagree about what matters: the bash side hands it
    the manifest it applied, the Python side hands it the body it sent.
    """
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
    """Drop the field a run name makes random, keep everything the substitution wrote.

    `run_pipeline` rewrites `metadata.name` with a random postfix so two runs do not collide.
    Everything else in the manifest is what the script decided, so that is what gets compared
    in full.
    """
    clone = json.loads(json.dumps(_prune(document)))
    metadata = clone.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("name", None)
    return clone


class ClusterOperation:
    """One operation on the cluster, reduced to the claim it makes.

    `cluster` is the sixth field and the one added last: which cluster was asked, in the
    harness's own spelling (see `cluster_identity`). It is part of the identity for reads as well
    as writes -- `compare_key` keeps it -- because "the same read, the same namespace, a different
    cluster" is the sentence this record exists to be able to tell apart.
    """

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


def parse_argv(args):
    """`['-n', 'dev', 'get', 'pod']` into `(namespace, flags, positional)`."""
    namespace = None
    flags = {}
    positional = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument.startswith("--") and "=" in argument:
            key, value = argument.split("=", 1)
            flags[key.lstrip("-")] = value
            if key == "--namespace":
                namespace = value
            index += 1
            continue
        if argument.startswith("-"):
            if argument in VALUE_FLAGS and index + 1 < len(args):
                flags[argument.lstrip("-")] = args[index + 1]
                if argument in ("-n", "--namespace"):
                    namespace = args[index + 1]
                index += 2
                continue
            flags[argument.lstrip("-")] = "true"
            index += 1
            continue
        positional.append(argument)
        index += 1
    return namespace, flags, positional


def operations_from_calls(calls, *, cluster=None):
    """Everything the shims recorded, as operations.

    `calls` is the `RecordedCall` list from `ShimEnvironment.calls()`.

    `cluster` is a **declaration** and it fills exactly one gap: a call whose argv names no cluster
    at all. That is what the *deleted* bash scripts looked like — they addressed the cluster
    through the process environment, so a comparison against one of them has to say what that was,
    the same way it has to say which operations one side does not do. A call that does name one is
    read from the call, never from here: a declaration that could overrule argv would be the
    harness quietly deciding the answer, which is the failure mode this module's rule is written
    against.
    """
    operations = []
    for call in calls:
        if call.program == API_PROGRAM:
            operations.extend(_apiserver_operations(call, cluster))
            continue
        if call.program not in CLUSTER_TOOLS:
            raise ParityError(f"{call.program}: not a cluster tool this harness knows (PATH leaked?)")
        if call.program == "kubectl":
            operations.extend(_kubectl_operations(call, cluster))
        else:
            operations.extend(_other_tool_operations(call, cluster))
    return operations


def _resolved_cluster(program, args, flags, declared, description):
    """What this call addressed, or the loud no that refusing to guess a cluster requires.

    Flags first, always: the argv is what the call did. A declared cluster is only what fills the
    gap for a call that carried none — and when the gap is there and the call *did* ask something
    of a cluster, this raises rather than recording an operation whose other half cannot be found.
    """
    seen = cluster_identity(flags)
    if seen is None:
        seen = declared
    if seen is None and touches_cluster(program, args):
        raise ParityError(
            f"{description}: this asked a cluster and the harness cannot tell which one. "
            f"No --{'/--'.join(CLUSTER_FLAGS)} in its argv, and no cluster was declared for the "
            f"side it came from. An operation with no cluster "
            f"is the one thing a parity test must not guess at: the same write on two "
            f"clusters reduces identically in every other field, so comparing it would prove "
            f"nothing about which one was edited.")
    return seen


def _apiserver_operations(call, declared=None):
    """A recorded API request, reduced to one operation.

    `apiserver GET /api/v1/namespaces/grafana/pods labelSelector=app=grafana` is the same act as
    `kubectl get pods -n grafana -l app=grafana`, and this is where that is said: the path carries
    the scope, the kind and the name and the query carries the selector. An operation that arrived
    as a path and comes out as a `ClusterOperation` is what lets a parity test compare a client
    read against the `kubectl` the bash side made.

    The cluster arrives the same way the shims carry theirs: the fake transport records the
    `--context`/`--kubeconfig` of the client that made the request, which is what lets this half be
    compared against a `kubectl` line and not merely beside it. A request the fake cannot attribute
    to a target it was told about raises, because "asked through the client" is not a way of
    avoiding the question of which cluster.
    """
    # `call.args` excludes the program's own name, which is what `call.program` is.
    argv = list(call.args)
    _namespace, flags, _positional = parse_argv(argv)
    cluster = _resolved_cluster(API_PROGRAM, argv, flags, declared, "apiserver request")
    method = (argv[0] if argv else "GET").upper()
    path, _query = (argv[1] if len(argv) > 1 else ""), ""
    if "?" in path:
        path, _query = path.split("?", 1)
    selectors = [item for item in argv[2:] if "=" in item and not item.startswith("--")]
    query = _query + ("&" if _query and selectors else "") + "&".join(selectors)

    verb = {"GET": "read", "POST": "ensure", "PUT": "ensure", "PATCH": "ensure",
            "DELETE": "delete", "HEAD": "read", "OPTIONS": "read"}.get(method)
    if verb is None:
        raise ParityError(f"apiserver {method}: no verb for this HTTP method")

    parts = [part for part in path.strip("/").split("/") if part]
    namespace = None
    if "namespaces" in parts:
        index = parts.index("namespaces")
        if index + 1 < len(parts):
            namespace = parts[index + 1]
    if parts and parts[-1] == "namespaces" and verb in ("ensure", "read"):
        return [ClusterOperation("read", "namespace", None, namespace, (), cluster)]
    tail = [part for part in parts[index + 2:] if index + 2 <= len(parts) - 1] if namespace else []
    if not tail and parts and parts[-1] != "namespaces":
        tail = parts[-1:]
    kind = _singular(tail[0]) if tail else "cluster"
    name = tail[1] if len(tail) > 1 else None
    facts = []
    for item in _query_pairs(query):
        key, value = item.split("=", 1)
        if key in ("labelSelector", "fieldSelector"):
            facts.append(("selector", _selector(value)))
    return [ClusterOperation(verb, kind, namespace, name, tuple(facts), cluster)]


def _query_pairs(query):
    return [pair for pair in query.split("&") if "=" in pair]


def _kubectl_operations(call, declared=None):
    namespace, flags, positional = parse_argv(call.args)
    if not positional:
        raise ParityError(f"kubectl {call.args}: no verb")
    cluster = _resolved_cluster("kubectl", call.args, flags, declared, "kubectl")
    verb = positional[0]
    rest = positional[1:]
    if verb == "proxy":
        # `kubectl proxy` is a session with the API server and not a request about an object: it
        # opens a local port and forwards whatever arrives. It reads nothing, so it sits in the
        # read bucket with the port it served as its only fact. What it opened the way to is not
        # in this log: a script that read through it read with `curl`, which is not a cluster tool,
        # so a parity test may not let the proxy stand in for the reads that followed it.
        return [ClusterOperation("read", "proxy", namespace, None,
                                 (("port", flags.get("port", "8001")),), cluster)]

    if verb in READ_VERBS:
        return _kubectl_reads(verb, namespace, flags, rest, cluster)
    if verb == "port-forward":
        target = rest[0] if rest else ""
        return [ClusterOperation("port-forward", "pod", namespace, target.removeprefix("pod/"),
                                 (("address", flags.get("address", "0.0.0.0")),
                                  ("ports", ",".join(rest[1:]))), cluster)]
    if verb == "exec":
        # `kubectl exec` is a session and not a mutation, so what it claims is which container
        # it enters and what runs there. The pod is named -- the session is with one pod, not
        # with a selector -- because unlike a read, nothing here is reorderable.
        #
        # `--` is where kubectl's own arguments end and the command begins. The shims record a
        # flat argv, so the boundary is restored here: `--stdin --tty` are flags, the first
        # positional is the pod, and everything after it is the command.
        target = rest[0] if rest else None
        command = [token for token in rest[1:] if token != "--"]
        facts = [("command", " ".join(command))]
        container = flags.get("c") or flags.get("container")
        if container:
            facts.append(("container", container))
        return [ClusterOperation("exec", "pod", namespace, target, tuple(facts), cluster)]
    if verb == "apply":
        if not (call.stdin or "").strip():
            # Nothing arrived, so this is the tail of a pipeline — `kubectl create --dry-run
            # -o yaml | kubectl apply -f -`, where the shims recorded both halves but the
            # manifest went from one stdout into the other's stdin without ever touching a log.
            # The half that *decided* something is the `create`; this one is delivery.
            return []
        documents = [document for document in yaml.safe_load_all(call.stdin) if document]
        return [_manifest_operation(document, namespace, cluster) for document in documents]
    if verb in ENSURE_VERBS:
        kind = _singular(rest[0]) if rest else None
        name = rest[1] if len(rest) > 1 else None
        facts = []
        if kind == "secret" and len(rest) > 2:
            # `create secret docker-registry NAME` is sugar for a Secret of a known type, so
            # the subtype and the name come out of the positional words, and the flags are
            # translated into the object they describe.
            facts = _docker_secret_facts(rest[1], rest[2], flags)
            name = rest[2]
        else:
            facts = payload_facts(kind, None)
        if kind == "namespace":
            facts = ()
        return [ClusterOperation("ensure", kind, namespace, name, facts, cluster)]
    if verb in ("delete", "patch"):
        kind = _singular(rest[0]) if rest else None
        name = rest[1] if len(rest) > 1 else None
        facts = ()
        if verb == "patch":
            payload = flags.get("p") or flags.get("patch")
            try:
                facts = payload_facts(kind, json.loads(payload)) if payload else ()
            except (TypeError, ValueError):
                raise ParityError(f"kubectl patch {kind} {name}: a payload this harness cannot read")
        return [ClusterOperation(verb, kind, namespace, name, facts, cluster)]
    raise ParityError(f"kubectl {verb}: no rule for this verb")


def _docker_secret_facts(subtype, name, flags):
    """What `kubectl create secret <subtype> NAME --docker-…` is asking the cluster for."""
    if subtype != "docker-registry":
        raise ParityError(f"kubectl create secret {subtype}: no rule for this kind of secret")
    payload = {
        "kind": "Secret",
        "type": "kubernetes.io/dockerconfigjson",
        "metadata": {"name": name},
        "data": {".dockerconfigjson": json.dumps({"auths": {flags.get("docker-server", ""): {}}})},
    }
    return payload_facts("secret", payload)


def _manifest_operation(document, default_namespace, cluster=None):
    """What applying a manifest actually asks for: this object, here, with this content."""
    metadata = document.get("metadata", {}) if isinstance(document, dict) else {}
    namespace = metadata.get("namespace") or default_namespace
    kind = _singular(str(document.get("kind", "")).lower())
    return ClusterOperation("ensure", kind, namespace, metadata.get("name"),
                            payload_facts(kind, document), cluster)


def _kubectl_reads(verb, namespace, flags, rest, cluster=None):
    facts = []
    selector = flags.get("l") or flags.get("selector")
    if selector:
        facts.append(("selector", _selector(selector)))
    kind = rest[0] if rest and not rest[0].startswith("-") else verb
    if kind == "can-i" or verb == "can-i":
        return [ClusterOperation("read", "can-i", namespace, None, (("request", " ".join(rest)),),
                                 cluster)]
    if verb == "version" or kind == "version":
        return [ClusterOperation("read", "version", namespace, None, facts, cluster)]
    if verb == "config":
        # `kubectl config ...` reads the file on this machine and asks the cluster nothing, so it
        # is the one read here that carries no cluster. It is not a hole in the record: an
        # operation with no cluster is a claim about the local file, and comparing it against a
        # cluster operation is what the tests that declare `bash_only=` have always had to say.
        return [ClusterOperation("read", "kubeconfig", namespace, None,
                                 (("subcommand", " ".join(rest)),), None)]
    if verb == "api-resources" or kind == "api-resources":
        return [ClusterOperation("read", "api-resources", namespace, None, (), cluster)]
    return [ClusterOperation("read", _singular(kind), namespace, None, facts, cluster)]


def _other_tool_operations(call, declared=None):
    """`tkn`, `helm`, `helmfile`, `git-crypt`, `mysql`, `ssh-keygen`."""
    namespace, flags, positional = parse_argv(call.args)
    if call.program == "tkn":
        if not positional:
            raise ParityError("tkn: no resource given")
        cluster = _resolved_cluster("tkn", call.args, flags, declared, "tkn")
        if positional[0] == "version":
            return [ClusterOperation("read", "version", None, None, (("tool", "tkn"),), cluster)]
        resource = _singular(positional[0])
        verb = "list" if "list" in positional else None
        facts = []
        if flags.get("l") or flags.get("selector"):
            facts.append(("selector", _selector(flags.get("l") or flags.get("selector"))))
        if verb != "list" and len(positional) > 1:
            raise ParityError(f"tkn {positional}: only `list` is a read here")
        return [ClusterOperation("read", resource, namespace, None, facts, cluster)]
    facts = [("argv", tuple(call.args))]
    if call.stdin:
        facts.append(("stdin", call.stdin.strip()))
    return [ClusterOperation("run", call.program, None, None, facts,
                             _resolved_cluster(call.program, call.args, flags, declared,
                                               call.program))]


# ---------------------------------------------------------------------------
# the Python side: recorded client calls, same shape
# ---------------------------------------------------------------------------

_API_METHOD = re.compile(r"^(create|replace|patch|delete|read|list|apply|get)_(namespaced_)?(.+)$")

# Client calls that ask and do not change. `create_` is the method prefix the generated client
# gives a `SelfSubjectAccessReview`, and nothing in that name says "this is a question".
PROBE_METHODS = {"create_self_subject_access_review", "create_self_subject_rules_review"}


def operations_from_api_calls(calls):
    """Recorded generated-client calls into operations.

    `calls` is a list of `(method_name, kwargs)` — what a recording fake client leaves — or of
    `(method_name, kwargs, cluster)`, which is what `ApiRecorder` leaves now that the recorder knows
    the `ClusterTarget` its client was built for. The order is the order they were called.

    An entry with no third element is an operation whose target the harness cannot see, and it
    raises. That is the rule of this module turned on the Python half: the recorder that cannot say
    which cluster it addressed is the same recorder that used to answer for "this process", and a
    comparison built from it would pass no matter which cluster the code had picked.
    """
    operations = []
    for entry in calls:
        method, kwargs, cluster = (entry[0], entry[1],
                                  entry[2] if len(entry) > 2 else None)
        if cluster is None:
            raise ParityError(
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
        # list. It is asked once per node, which is why a parity test against a script that looped
        # the nodes declares one of these per node it read.
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
        raise ParityError(f"kubernetes client: no rule for {method}")
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
    # A list carries its selector, and the script side states it (`-l`), so the client side has to
    # as well or a read both sides performed looks like one Python did alone. It is asked of the
    # method and not of the reduced verb, which is decided below.
    selector = kwargs.get("label_selector") or kwargs.get("field_selector")
    facts = ([("selector", _selector(selector))] if method.startswith("list") and selector else []) \
        + list(payload_facts(kind, body))
    # `get_namespaced_custom_object` is the generated client's one-object read, and the CLI side of
    # this harness already calls `get` a read — READ_VERBS says so. It was missing from the reducer's
    # verb list, so a waiter reading one run would have raised `ParityError` for the same act that
    # `kubectl get pod` is allowed to be.
    verb = "ensure" if verb in ENSURE_VERBS else ("read" if verb in ("read", "list", "get") else verb)
    return ClusterOperation(verb, kind, namespace, name, facts, cluster)


def review_attributes(body):
    """The verb and resource a subject access review asked about, model or dict.

    The body is built by `KubernetesUtils.self_can` as a real `V1SelfSubjectAccessReview`, so the
    reducer reads attributes; a test that hands a recorder a plain dictionary is equally legal, so
    both shapes are read. A `None` in is not an error — the review asked about the verb it was
    given, and an empty one is reported as such rather than raised over.
    """
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


# ---------------------------------------------------------------------------
# the comparison
# ---------------------------------------------------------------------------

def _only_the_cluster_differs(bash, python):
    """Whether the two sides would agree if the cluster were ignored.

    When they do, the cluster is the whole of the difference, and the failure message should say so
    instead of leaving the reader to notice it between two nearly identical columns. That case is
    named exactly — the namespace created on one cluster and the release applied on
    another, with every other field of both halves agreeing. So the harness diagnoses it rather than
    describing it.
    """
    def unaddressed(operations):
        return [ClusterOperation(op.verb, op.kind, op.namespace, op.name, op.facts, None)
                for op in operations]

    return unaddressed(bash) == unaddressed(python)


def compare(bash_operations, python_operations, *, bash_only=(), python_only=(), context=""):
    """Assert both sides do the same things to the cluster.

    `bash_only` / `python_only` are the operations one side legitimately does not do. They are
    not an escape hatch: a declared difference still has to be one the test can explain in a
    comment, because an undeclared one is exactly what this harness is for.
    """
    bash = [operation.compare_key() for operation in bash_operations]
    python = [operation.compare_key() for operation in python_operations]

    bash_writes = [op for op in bash if not op.is_read]
    python_writes = [op for op in python if not op.is_read]
    bash_reads = sorted((op for op in bash if op.is_read), key=repr)
    python_reads = sorted((op for op in python if op.is_read), key=repr)

    def without_declared(operations, declared, other_side):
        remaining = list(operations)
        for operation in declared:
            if operation in remaining:
                remaining.remove(operation)
        return [operation for operation in remaining if operation not in other_side]

    declared_bash = set(map(repr, bash_only))
    declared_python = set(map(repr, python_only))

    problems = []
    if bash_writes != python_writes:
        problems.append("write operations differ, order included:\n"
                        f"bash:\n{render(bash_writes)}\npython:\n{render(python_writes)}")
    only_bash = [op for op in without_declared(bash_reads, bash_only, python_reads) if repr(op) not in declared_bash]
    only_python = [op for op in without_declared(python_reads, python_only, bash_reads)
                   if repr(op) not in declared_python]
    if only_bash or only_python:
        problems.append("reads differ, and not the way the test declared:\n"
                        f"only bash does: {sorted(map(repr, only_bash))}\n"
                        f"only Python does: {sorted(map(repr, only_python))}\n"
                        f"bash:\n{render(bash_reads)}\npython:\n{render(python_reads)}")

    if problems and _only_the_cluster_differs(bash, python):
        problems.append(
            "the only difference is *which cluster was addressed*: with the cluster taken out, the "
            "two sides do the same things in the same order. That is the failure this record exists to "
            "catch — the "
            "namespace created on one cluster and the release applied on another, both halves of it "
            "looking correct — and it is the reason the cluster is part of the record and not a "
            "detail of the run.")

    if problems:
        raise AssertionError(
            f"bash and Python do not do the same thing to the cluster"
            f"{': ' + context if context else ''}\n\n" + "\n\n".join(problems)
            + "\n\nall bash operations:\n" + render(bash)
            + "\n\nall Python operations:\n" + render(python))
    return bash, python


def without_generated_run_name(text, base_name):
    """Strip the random run-name suffix, on both sides.

    `run_pipeline` appends a random postfix (`openssl rand -hex 5` in bash, six characters in
    Python) so repeat runs do not collide. Nothing else about the two paths depends on it, so
    the harness compares `hello-run-<postfix>` and refuses to treat the random part as a
    difference — while still proving both sides started from the same base name.
    """
    if not text:
        return text
    return re.sub(rf"^{re.escape(base_name)}-[a-z0-9]+$", f"{base_name}-<postfix>", text)


def with_generated_run_name_normalized(operations, base_name):
    """The same operations, with a run name's random suffix folded to `<postfix>`.

    Only the *name* is folded, so a run that landed in the wrong namespace or with the wrong
    spec -- or on the wrong cluster, which is the field this record grew -- still fails the
    comparison. So the cluster comes along.
    """
    return [ClusterOperation(op.verb, op.kind, op.namespace,
                             without_generated_run_name(op.name, base_name), op.facts,
                             op.cluster)
            for op in operations]
