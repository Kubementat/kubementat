"""What is running, how full the cluster is, and what it costs.

These reads were scripts: a pod table, a metrics request, each kubelet's own stats document.
What they have in common, and what the reporting commands depend on, is that an absent source is
reported in the answer instead of raised -- metrics-server may not be installed, one kubelet of N
may be silent, and a diagnostic that dies on either tells the operator about the tool instead of
about the cluster.
"""

import json
import logging

from kubernetes import client

from kubementat.core import quantities
from kubementat.errors import NamespaceNotFoundError, PodNotFoundError
from kubementat.infrastructure.kubernetes.clients import KubernetesClients
from kubementat.infrastructure.kubernetes.constants import DEPLOYMENT_LABEL


# The kubelet's own stats endpoint, reached through the API's node-proxy path. The script
# `list_pvc_usage.sh`, deleted when this read came into Python, got here by starting
# `kubectl proxy`, sleeping two seconds, curling `127.0.0.1:8001` and then `pkill -9`ing it; the
# client's `connect_get_node_proxy_with_path` asks for `/api/v1/nodes/{name}/proxy/{path}`, which
# is the same URL with no local server in the middle.
NODE_STATS_PATH = "/stats/summary"


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
        return int(quantities.parse_memory(value))
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




class KubernetesWorkloads(KubernetesClients):
    """The workloads: pods, services, deployments, nodes, and the reports read out of them.

    Composed into `KubernetesUtils`, and read by the session area, which is why that class takes
    this one as its base and not as a sibling: the pod a session attaches to and the pod whose
    logs a report reads are found by one deployment selector, stated once, and a second lookup
    beside it would find the pods a manual `kubectl label` left behind.
    """
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

    def get_nodes(self) -> list:
        """
        Every node on the cluster.

        Returns:
            list[client.V1Node]: The nodes; `status.addresses`, `status.capacity` and
            `status.allocatable` are what the reporting commands read.
        """
        return self.core_api.list_node().items

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
                    node_usage["cpu"] += quantities.parse_cpu(container.get("usage", {}).get("cpu", 0))
                    node_usage["memory"] += quantities.parse_memory(
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
                        totals["cpu"] += quantities.parse_cpu(resources.requests.get("cpu", 0))
                        totals["memory"] += quantities.parse_memory(resources.requests.get("memory", 0))
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
                "cpu": quantities.parse_cpu((node.status.allocatable or {}).get("cpu", 0)),
                "memory": quantities.parse_memory((node.status.allocatable or {}).get("memory", 0)),
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
