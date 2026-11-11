"""Assertions, plus the small kubectl vocabulary the stages are written in.

Every wait is a bounded poll with a stated predicate and deadline; there is no
`sleep` used for synchronisation anywhere in the suite. Assertions are classified
`infra` or `product`: only infra predicates retry (once per run), and a lane that
passes only after a retry is flagged flaky (NFR 6.6).
"""

import hashlib
import json
import re

CAPACITY_MESSAGE = (
    "cluster cannot schedule what the lane needs: increase --vm-ram/--vm-disk "
    "(node reported insufficient resources)"
)

# The node's own words, measured against a real node.
CAPACITY_PATTERNS = [
    r"Insufficient (memory|cpu|pods|ephemeral-storage)",
    r"too many pods",
    r"insufficient free ip addresses",
]


class KubectlError(Exception):
    pass


class CapacityAborted(Exception):
    """Fail-fast abort: the shape is wrong, not the code."""


def looks_like_capacity_failure(text):
    return any(re.search(pattern, text or "", re.IGNORECASE) for pattern in CAPACITY_PATTERNS)


class Assertion:
    def __init__(self, stage, name, cls, passed, detail="", attempts=1, artifacts=None):
        self.stage = stage
        self.name = name
        self.cls = cls
        self.passed = passed
        self.detail = detail
        self.attempts = attempts
        self.artifacts = artifacts or []

    def to_dict(self):
        return {
            "stage": self.stage,
            "name": self.name,
            "class": self.cls,
            "passed": self.passed,
            "detail": self.detail,
            "attempts": self.attempts,
        }


class Cluster:
    """kubectl/helm vocabulary, always executed in the guest through the primitive."""

    def __init__(self, guest, log=None):
        self.guest = guest
        self.log = log or (lambda message: None)

    # ------------------------------------------------------------------ verbs

    def kubectl(self, *args, timeout=90, stdin=None):
        return self.guest.run_argv(["kubectl"] + list(args), timeout=timeout, stdin=stdin)

    def json(self, *args, timeout=90):
        import json

        result = self.kubectl(*args, timeout=timeout)
        if not result.ok:
            raise KubectlError("kubectl %s failed: %s" % (" ".join(args), result.excerpt(300)))
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise KubectlError("kubectl %s did not return JSON: %s" % (" ".join(args), exc))

    def get_names(self, kind, namespace=None):
        args = ["get", kind, "-o", "custom-columns=NS:.metadata.namespace,N:.metadata.name", "--no-headers"]
        if namespace is None:
            args.insert(1, "-A")
        else:
            args += ["-n", namespace]
        result = self.kubectl(*args)
        if not result.ok:
            return []
        names = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] != "<none>":
                names.append("%s/%s" % (parts[0], parts[1]))
            elif parts and parts[0] != "<none>":
                names.append(parts[0])
        return sorted(names)

    # ------------------------------------------------------------------ facts

    def nodes(self):
        return self.json("get", "nodes", "-o", "json")

    def ready_node_count(self):
        try:
            return self.node_count_ready()
        except KubectlError:
            return 0

    def node_count_ready(self):
        nodes = self.nodes()["items"]
        return len([n for n in nodes if _conditions(n).get("Ready") == "True"]), nodes

    def default_storage_class(self):
        """The default SC read from the cluster annotation, never from a string."""
        classes = self.json("get", "storageclass", "-o", "json")["items"]
        for item in classes:
            annotations = ((item.get("metadata") or {}).get("annotations") or {})
            if str(annotations.get("storageclass.kubernetes.io/is-default-class")).lower() == "true":
                return item["metadata"]["name"], item
        if classes:
            return classes[0]["metadata"]["name"], classes[0]
        raise KubectlError("no StorageClass in the cluster")

    def pod_list(self, namespace=None):
        args = ["get", "pods"]
        if namespace is None:
            args.append("-A")
        else:
            args += ["-n", namespace]
        return self.json(*(args + ["-o", "json"]))["items"]

    def pending_pod_diagnosis(self):
        """Return (bad_pods, capacity_message). A capacity message means abort."""
        bad, message = [], None
        for pod in self.pod_list():
            phase = ((pod.get("status") or {}).get("phase") or "").lower()
            name = "%s/%s" % ((pod.get("metadata") or {}).get("namespace"), (pod.get("metadata") or {}).get("name"))
            if phase in ("pending", "failed"):
                reasons = _pod_messages(pod)
                if looks_like_capacity_failure(" ".join(reasons)):
                    message = "node says: %s -> %s" % ("; ".join(reasons)[:200], CAPACITY_MESSAGE)
                bad.append((name, phase, "; ".join(reasons)[:200]))
        return bad, message

    def pvc_status(self):
        """All claims, classified. WaitForFirstConsumer claims are reported, not failed."""
        out = {"bound": [], "pending": [], "waiting_for_consumer": []}
        try:
            items = self.json("get", "pvc", "-A", "-o", "json")["items"]
        except KubectlError:
            return out
        for item in items:
            name = "%s/%s" % (item["metadata"]["namespace"], item["metadata"]["name"])
            status = item.get("status") or {}
            phase = status.get("phase")
            if phase == "Bound":
                out["bound"].append(name)
                continue
            conditions = " ".join(str(c.get("message", "")) for c in status.get("conditions") or [])
            if "first consumer" in conditions or "WaitForFirstConsumer" in conditions:
                out["waiting_for_consumer"].append(name)
            else:
                out["pending"].append((name, phase, (status.get("message") or conditions)[:160]))
        return out

    def namespaces(self):
        return sorted(n["metadata"]["name"] for n in self.json("get", "ns", "-o", "json")["items"])

    def crds(self):
        try:
            return sorted(c["metadata"]["name"] for c in self.json("get", "crd", "-o", "json")["items"])
        except KubectlError:
            return []

    def clusterroles(self):
        try:
            return sorted(c["metadata"]["name"] for c in self.json("get", "clusterrole", "-o", "json")["items"])
        except KubectlError:
            return []

    def cluster_scoped_names(self, kind):
        """Names of one cluster-scoped kind; empty when the cluster does not serve it."""
        try:
            return sorted(item["metadata"]["name"] for item in self.json("get", kind, "-o", "json")["items"])
        except KubectlError:
            return []

    def helm_list(self):
        """Every release on the cluster, as the row `helm list -A -o json` reports it.

        `namespace`, `release` and `status` are what the callers here have always read, and keep
        working unchanged. `chart` and `revision` come along for free from the same row, and two
        things need them: a release *name* is not an identity -- kubementat's `traefik` lives in the
        namespace `traefik`, k3s's `traefik` in `kube-system`, so they are two objects with one name
        -- and "nothing was overwritten" is a claim about the chart version and the revision, not
        about a name that is still there and still says `deployed`.
        """
        result = self.guest.run_argv(["helm", "list", "-A", "-o", "json"], timeout=120)
        if not result.ok:
            return []
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError:
            return []
        return [{"namespace": row.get("namespace", ""), "release": row.get("name", ""),
                 "status": row.get("status", ""), "chart": row.get("chart", ""),
                 "revision": row.get("revision", "")} for row in rows]

    def helm_list_rows(self):
        """The same read as `helm_list`, but it says when the cluster could not answer.

        `(rows, error)`, and the error is the half that matters to a guard. `helm_list()` answers
        `[]` for "nothing is installed" and for "helm would not say" alike, and a row that asks
        "did anything get installed" would read a failure as a no and call it green. Most callers
        want the list and keep using `helm_list()` unchanged; the ones that decide from an absent
        release read this instead.
        """
        result = self.guest.run_argv(["helm", "list", "-A", "-o", "json"], timeout=120)
        if not result.ok:
            return [], "helm list -A exited %s: %s" % (result.exit_code, result.excerpt(200))
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError as exc:
            return [], "helm list -A did not answer JSON: %s" % exc
        return [{"namespace": row.get("namespace", ""), "release": row.get("name", ""),
                 "status": row.get("status", ""), "chart": row.get("chart", ""),
                 "revision": row.get("revision", "")} for row in rows], ""

    def ingress_classes(self):
        """Every IngressClass on the cluster and the controller it binds, as `{name: controller}`.

        Raises `KubectlError` rather than answering `{}` when the cluster will not say, because the
        two answers mean opposite things and every caller here decides between "one controller" and
        "two of them". A cluster old enough to lack the API is a finding the caller reports, not an
        empty cluster.
        """
        document = self.json("get", "ingressclass", "-o", "json", timeout=90)
        return ingress_class_bindings(document)

    def endpoints(self, namespace, name):
        """What one Service's endpoints resolve to, read out of the Endpoints object.

        Not out of a selector: `kubectl get endpoints -o json` is the list the data plane is being
        told to route to, and an address with a `targetRef` is a *pod*, not a guess about one. That
        is the difference between "the Service selects Traefik pods" and "the traffic goes to
        Traefik", which is the whole content of the routing lane's endpoint assert.

        Raises `KubectlError` when the read failed. An object with no ready addresses is a real
        answer and comes back with empty lists; not being able to ask is not.
        """
        document = self.json("get", "endpoints", name, "-n", namespace, "-o", "json", timeout=90)
        ready, not_ready = [], []
        for subset in document.get("subsets") or []:
            for address in subset.get("addresses") or []:
                ready.append(address)
            for address in subset.get("notReadyAddresses") or []:
                not_ready.append(address)
        return {"ready": ready, "not_ready": not_ready}


    # -------------------------------------------------------------- mutations

    def apply(self, text, namespace=None, timeout=120):
        args = ["apply", "-f", "-"]
        if namespace:
            args += ["-n", namespace]
        return self.kubectl(*args, timeout=timeout, stdin=text)

    def delete(self, what, namespace=None, timeout=120):
        args = ["delete", what, "--ignore-not-found", "--wait=false"]
        if namespace:
            args += ["-n", namespace]
        return self.kubectl(*args, timeout=timeout)

    def auth_can_i(self, verb, resource, namespace, identity, timeout=60):
        args = ["auth", "can-i", verb, resource, "-n", namespace]
        for flag, value in identity.items():
            args += ["--%s" % flag, value]
        result = self.kubectl(*args, timeout=timeout)
        return result.stdout.strip() if result.stdout.strip() else "error: %s" % result.excerpt(160)

    # ------------------------------------------------------------------- waits

    def wait(self, description, predicate, timeout, interval=5, cls="product", deadline_probe=None):
        """Bounded poll. Returns (passed, detail, attempts)."""
        import time

        deadline = time.time() + timeout
        attempts = 0
        detail = "condition never met: %s" % description
        while time.time() < deadline:
            attempts += 1
            passed, detail = predicate()
            if passed:
                return True, detail, attempts
            bad, capacity = self.pending_pod_diagnosis()
            if capacity:
                raise CapacityAborted("%s | unschedulable/failed pods: %s" % (capacity, bad[:3]))
            if deadline_probe:
                deadline_probe()
            time.sleep(interval)
        if cls == "infra":
            # A second window as long as half the first: re-polling for two seconds after the
            # deadline gave the environment no time to recover, so it was a retry in name only.
            passed, detail, more = self._retry_once(description, predicate, max(30, timeout / 2))
            if passed:
                return True, detail + " (passed after one infra retry)", attempts + more
        return False, detail, attempts

    def _retry_once(self, description, predicate, timeout):
        passed, detail, attempts = self.wait(description, predicate, timeout, interval=1, cls="product")
        return passed, detail, attempts


def _conditions(pod_or_node):
    return {c["type"]: c["status"] for c in (pod_or_node.get("status") or {}).get("conditions") or []}


def _pod_messages(pod):
    status = pod.get("status") or {}
    messages = []
    for container in (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or []):
        waiting = (container.get("state") or {}).get("waiting") or {}
        if waiting.get("message"):
            messages.append(waiting["message"])
    for condition in status.get("conditions") or []:
        if condition.get("message"):
            messages.append(condition["message"])
    for event_container in status.get("containerStatuses") or []:
        last = (event_container.get("lastState") or {}).get("terminated") or {}
        if last.get("message"):
            messages.append(last["message"])
    return messages


def resource_sets(cluster, kinds, exclude=()):
    """{kind: sorted namespace/name list} for the idempotency check.

    Names, not digests: diffing a digest would compare hex characters, which tells you
    that *something* changed and nothing about what. `exclude` holds namespaces the suite
    does not manage, so that e.g. k3s installing its own addons cannot read as a delta.
    """
    out = {}
    for kind in kinds:
        out[kind] = sorted(set(name for name in cluster.get_names(kind)
                               if name.split("/", 1)[0] not in exclude))
    return out


def diff_sets(before, after):
    """What appeared/disappeared between two resource-set snapshots."""
    out = {}
    for kind in sorted(set(before) | set(after)):
        added = sorted(set(after.get(kind, [])) - set(before.get(kind, [])))
        removed = sorted(set(before.get(kind, [])) - set(after.get(kind, [])))
        if added or removed:
            out[kind] = {"added": added, "removed": removed}
    return out


def content_digests(cluster, kinds, exclude=()):
    """{kind: {namespace/name: digest of its payload}} for the content half of the rerun check.

    Only the payload is hashed (`type`, `data`, `stringData`, `binaryData`): metadata carries a
    resourceVersion that moves on every write, including a write of identical bytes, and that is
    not the change an operator would notice.
    """
    out = {}
    for kind in kinds:
        try:
            items = cluster.json("get", kind, "-A", "-o", "json", timeout=120)["items"]
        except KubectlError:
            out[kind] = {}
            continue
        digests = {}
        for item in items:
            meta = item.get("metadata") or {}
            if meta.get("namespace", "") in exclude:
                continue
            payload = {key: item.get(key) for key in ("type", "data", "stringData", "binaryData")}
            digests["%s/%s" % (meta.get("namespace", ""), meta.get("name"))] = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        out[kind] = digests
    return out


def changed_content(before, after):
    """{kind: [names present in both snapshots whose payload differs]}; names only, never values."""
    out = {}
    for kind in sorted(set(before) | set(after)):
        old, new = before.get(kind, {}), after.get(kind, {})
        changed = sorted(name for name in set(old) & set(new) if old[name] != new[name])
        if changed:
            out[kind] = changed
    return out


def signature_matches(body, tokens):
    """Which of `tokens` the body does not contain. Case-insensitive, order-preserving.

    A token only belongs in this table if the document being read actually carries it: a token
    that lives in a JS bundle, a redirect body, or a rendered DOM is not evidence about a
    response body, however true the underlying claim is.
    """
    lowered = (body or "").lower()
    missing = [token for token in tokens if token.lower() not in lowered]
    return (not missing), missing


# ---------------------------------------------------------------- routing reads
#
# Two shape decisions rather than two assertions, and they live here because they are the shape of
# a claim rather than the claim itself. Both exist so that the routing lane decides from what the
# cluster answers and not from a name the harness remembers.

# A helm release runs a Traefik *proxy* when its chart is `traefik-<version>`. The same rule the
# product's own uninstall sweep runs on, for the reason a name list cannot work: the bundled controller and
# kubementat's own are both `traefik-<something>`, a chart named `traefik-crds` ships no proxy at
# all, and a controller that arrived as a plain manifest has no release to name. A release-name list
# gets every one of those cases wrong.
TRAEFIK_PROXY_CHART = re.compile(r"^traefik-\d")


def traefik_proxy_releases(rows):
    """The `helm_list` rows that describe a running Traefik proxy, filtered by chart."""
    return [row for row in rows if TRAEFIK_PROXY_CHART.match(str(row.get("chart") or ""))]


def ingress_class_bindings(document):
    """`{class name: spec.controller}` for one Kubernetes list of IngressClasses.

    The controller is the only part of an IngressClass that says who serves it, which is why the
    routing lane's "exactly one class claims Traefik" assert counts bindings rather than names: a
    second chart that names its class `traefik` too is a conflict a name set cannot see, and a class
    that binds someone else's controller is not Traefik's however it is called.
    """
    bindings = {}
    for item in (document or {}).get("items") or []:
        name = ((item.get("metadata") or {}).get("name")) or ""
        controller = ((item.get("spec") or {}).get("controller")) or ""
        if name:
            bindings[name] = controller
    return bindings

