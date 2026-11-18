"""The out-of-process half of the cluster fake."""

import json
import os
import re
from urllib.parse import urlparse

# One recorded call per request, in the shim log's shape: `apiserver` is the program, and its
# arguments are the method and the path, so a test reads binaries and client calls from one log.
PROGRAM = "apiserver"

# The clients this fake has seen, as `(configuration, context, config_file)`. Every recorded
# operation has to say which cluster it asked, and a request carries no such thing: the address
# is in the URL and the context is not. So the client factory notes what it was handed, and a
# request is attributed by the `Configuration` object it was made through — which is the one thing
# that is per-target and never shared.
_CLIENTS = []


def _note_clients():
    """Wrap the kubeconfig client factory so each client's target is known at request time."""
    from kubernetes import config as kubernetes_config

    if getattr(kubernetes_config.new_client_from_config, "_kmt_noting", False):
        return

    original = kubernetes_config.new_client_from_config

    def traced(config_file=None, context=None, **kwargs):
        built = original(config_file=config_file, context=context, **kwargs)
        _CLIENTS.append((_configuration_of(built), context, config_file))
        return built

    traced._kmt_noting = True
    kubernetes_config.new_client_from_config = traced


def _configuration_of(client):
    """The `Configuration` a client will make its requests with, wherever the SDK keeps it."""
    for attribute in ("configuration", "_configuration"):
        value = getattr(client, attribute, None)
        if value is not None:
            return value
    return None


def _addressing_flags(configuration):
    """The flags this request's client would have needed, as argv the reducer already reads."""
    for noted, context, config_file in _CLIENTS:
        if configuration is not None and noted is configuration:
            flags = []
            if config_file:
                flags += ["--kubeconfig", str(config_file)]
            if context:
                flags += ["--context", str(context)]
            return flags
    return []


# Responses live next to the shims' own answers, keyed by verb and path. A path is normalised the
# same way every time so a test can write `apiserver.GET.api.v1.namespaces` and not guess about
# the query string, which is not part of any identity.
_SUFFIX = re.compile(r"[^A-Za-z0-9.]+")


def _key(method, path):
    return f"{PROGRAM}.{method.upper()}.{_SUFFIX.sub('.', path.strip('/')).strip('.')}"


class Response:
    """What the transport hands the client: a status, a body, and enough headers to parse it."""

    def __init__(self, status, data, headers=None):
        self.status = status
        self.data = data
        self.headers = headers or {"content-type": "application/json"}

    def getheaders(self):
        return dict(self.headers)


def response_directory():
    return os.environ.get("SHIM_RESPONSE_DIR", "")


def _log(argv, configuration=None):
    log = os.environ.get("SHIM_LOG")
    if not log:
        return
    argv = list(argv) + _addressing_flags(configuration)
    block = f"CALL\t{PROGRAM}\t{os.getcwd()}\n" + "".join(f"ARG\t{a}\n" for a in argv)
    with open(log, "a") as handle:
        handle.write(block)


# Which shim answer file replies to which request, keyed by verb and by the shape of the path.
#
# A test that declares "the cluster has these pods" has always done it with `respond("kubectl", …,
# subcommand="get.pods")`, because that is what kubectl asked. Who asks changed and what is being
# declared did not, and a table is how the declaration keeps working: the alternative was every
# tunnel and session test rewriting its cluster state into API paths, which would make the test
# say something about HTTP instead of about the cluster.
#
# Order matters: the first match answers.
_ANSWERS = [
    ("get", re.compile(r"^/api/v1/namespaces/[^/]+/pods$"), "kubectl.get.pods"),
    ("get", re.compile(r"^/api/v1/namespaces/[^/]+/pods/[^/]+$"), "kubectl.get.pod"),
    ("get", re.compile(r"^/api/v1/namespaces/[^/]+/persistentvolumeclaims$"),
     "kubectl.get.persistentvolumeclaims"),
    ("get", re.compile(r"^/api/v1/namespaces/[^/]+/services$"), "kubectl.get.svc"),
    ("get", re.compile(r"^/api/v1/namespaces/[^/]+/endpoints/[^/]+$"), "kubectl.get.endpoints"),
    ("get", re.compile(r"^/api/v1/nodes$"), "kubectl.get.nodes"),
]


def _mapped(method, path):
    """The shim answer files that could reply, most specific first."""
    for verb, pattern, key in _ANSWERS:
        if method.upper() == verb.upper() and pattern.match(path):
            head, _sep, _tail = key.rpartition(".")
            return [key, head] if head else [key]
    return []


def _debug_trace(method, path, outcome):
    marker = os.environ.get("APISERVER_FAKE_TRACE")
    if marker:
        with open(marker, "a") as handle:
            handle.write(f"{method.upper()} {path} -> {outcome}\n")


def _answer(method, path):
    """The file that answers this request, or 404 when nothing claims it."""
    directory = response_directory()
    mapped = _mapped(method, path)
    for _candidate in mapped:
        body = os.path.join(directory, _candidate)
        if os.path.exists(body):
            text = open(body).read()
            status = 0
            if os.path.exists(f"{body}.exit"):
                status = int(open(f"{body}.exit").read().strip() or 0)
            if status:
                return Response(status, json.dumps({"kind": "Status", "code": status,
                                                    "message": text.strip() or "refused"}))
            if not text.strip():
                return Response(200, json.dumps({"kind": "PodList", "apiVersion": "v1",
                                                 "items": []}) if "pod" in _candidate.lower()
                                else json.dumps({"kind": "List", "apiVersion": "v1",
                                                 "items": []}))
            _debug_trace(method, path, f"mapped:{_candidate}")
            return Response(200, text)
    if not directory:
        return None
    parts = [_key(method, path)]
    head, _sep, tail = path.rstrip("/").rpartition("/")
    if tail:
        parts.append(_key(method, head))
    for key in parts:
        body = os.path.join(directory, key)
        if not os.path.exists(body):
            continue
        status = 0
        if os.path.exists(f"{body}.status"):
            status = int(open(f"{body}.status").read().strip() or 0)
        text = open(body).read()
        _debug_trace(method, path, f"path:{key}")
        return Response(status or 200, text)
    _debug_trace(method, path, "none")
    return None


class FakeApiServer:
    """The transport `ApiClient` talks to, answering from files and recording every request."""

    def __init__(self, configuration=None, **kwargs):
        self.configuration = configuration

    def request(self, method, url, query_params=None, headers=None, body=None,
                post_params=None, _preload_content=True, _request_timeout=None, **kwargs):
        path = urlparse(url).path
        _log([method.upper(), path] + [f"{key}={value}" for key, value in (query_params or [])],
             self.configuration)
        answer = _answer(method, path)
        if answer is not None:
            return answer
        # Nothing claimed this request. An empty list is the honest answer for a read of a
        # collection nobody declared, and saying so in the body is what keeps a test failure at
        # the level it happened: the fake's silence, rather than a half-populated SDK model that
        # raises three frames later on a field the test never touched.
        if method.upper() == "GET":
            return Response(200, json.dumps({"apiVersion": "v1", "kind": "List", "items": [],
                                             "metadata": {"resourceVersion": "0"}}))
        return Response(404, json.dumps({"kind": "Status", "api_version": "v1", "metadata": {},
                                         "status": "Failure", "reason": "NotFound",
                                         "code": 404,
                                         "message": f"nothing in the test answered {method.upper()}"
                                                    f" {path}"}))

    def GET(self, *args, **kwargs):
        return self.request("GET", *args, **kwargs)

    def POST(self, *args, **kwargs):
        return self.request("POST", *args, **kwargs)

    def PUT(self, *args, **kwargs):
        return self.request("PUT", *args, **kwargs)

    def DELETE(self, *args, **kwargs):
        return self.request("DELETE", *args, **kwargs)

    def PATCH(self, *args, **kwargs):
        return self.request("PATCH", *args, **kwargs)

    def HEAD(self, *args, **kwargs):
        return self.request("HEAD", *args, **kwargs)

    def OPTIONS(self, *args, **kwargs):
        return self.request("OPTIONS", *args, **kwargs)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        return None


def install():
    """Point the generated client's transport at the fake. Called once, at interpreter startup."""
    from kubernetes.client import rest

    rest.RESTClientObject = FakeApiServer
    _note_clients()
    return FakeApiServer
