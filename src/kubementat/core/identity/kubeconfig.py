"""The files a cluster address is read out of, and the material a pod is mounted with.

This is the leaf of `core/identity/`: `target.py`, `comparison.py`, `registry.py` and `context.py`
read it and it reads none of them, so it imports stdlib, `yaml`, `kubementat.errors` and
`core/config/reads.py` and no sibling module. That is not tidiness. Two of the three places that
normalise an address are in this file, so the address rules belong here rather than beside
`ClusterTarget`: the other placement would put `kubeconfig → target` next to the
`target → kubeconfig` the 1:1 check already needs, and the split would stop being a DAG. What this
module never answers is *which layer* said a cluster — that question belongs above it.

Two rules here look trivial and are not:

* **`$KUBECONFIG` is read when it is asked, not when the module was imported.** The library captures
  `KUBE_CONFIG_DEFAULT_LOCATION` at its own import, so a default resolved through it silently
  disagrees with the environment the caller is standing in. `default_kubeconfig_path` applies the
  documented rule — the variable, then `~/.kube/config` — at call time, and `_kubeconfig_files`
  splits it into the files kubectl would read.
* **the address is read out of the YAML rather than loaded into a client.** `_read_kubeconfig` merges
  the files with kubectl's own rule (the first file wins) and keeps contexts and clusters apart, so
  asking which cluster an environment addresses runs no exec plugin and writes no process-wide
  `Configuration`. That is what lets the 1:1 check ask the question of every environment in a root.

The service-account half sits here for the same reason its callers do: the mount is opened by
`_service_account_files`, and the address that proves a process is *on* a cluster is the string
`_service_account_address` builds from it. The two concrete paths the loader needs are here for the
same reason: `_library_config_file` is what a `client()` hands the library when a target carries no
kubeconfig of its own, and `_CONNECTION_DOCUMENT_CONTEXT` is the name the document `client()` builds
is read under, and the document `client()` builds from the material is the function
`_connection_document` below. None of it is a `ClusterTarget` decision — they are the file, the key,
and the shape the loader asks for — which is why `target` and `comparison` read down to them and
nothing reads up to them.

Four tests patch `SERVICE_ACCOUNT_ROOT` to fake a pod, so a reader in another module reaches it
through this module rather than through a binding of its own — otherwise the patch lands on a name
nobody reads and the test is green about a real `/var/run` mount.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import yaml

from kubementat.core.config.reads import read_decrypted
from kubementat.errors import LockedConfigFileError
from kubementat.errors import (
    ClusterContextNotFoundError,
    ClusterFileUnreadableError,
    ServiceAccountNotMountedError,
)

# The names this module owns. `KUBECONFIG_PATH_SOURCE_LABEL` is the key an operator would edit and
# is spelled the same as `constants.CLUSTER_KUBECONFIG_PATH_KEY` because it names the same key from a
# refusal sentence, not because one is defined in terms of the other.
KUBECONFIG_ENV_VAR = "KUBECONFIG"

# Where an environment's own kubeconfig lives, and the one name that answers ahead of `$KUBECONFIG`.
# Spelled once because it is read by the resolution order and written into the advice in three
# places, and a filename a reader has to guess is a filename that stops matching the documentation.
IN_TREE_KUBECONFIG_FILENAME = "kubeconfig.encrypted.yaml"

# The mounted material that proves a process is running *on* a cluster rather than merely
# interested in one. The path is not a configuration option: it is what the kubelet is
# documented to write, and the only reason it is spelled as a constant here is so a test can
# fabricate the three files under a temporary directory instead of pretending to be a pod.
SERVICE_ACCOUNT_ROOT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SERVICE_ACCOUNT_TOKEN_FILE = "token"
SERVICE_ACCOUNT_CA_FILE = "ca.crt"
KUBERNETES_SERVICE_HOST_ENV = "KUBERNETES_SERVICE_HOST"
KUBERNETES_SERVICE_PORT_ENV = "KUBERNETES_SERVICE_PORT"
# The port is not guessed at when the kubelet did not say: 443 is what the Service is created on.
DEFAULT_KUBERNETES_SERVICE_PORT = "443"

# The key an operator would edit, spelled once so a refusal about a missing kubeconfig names it
# instead of paraphrasing it.
KUBECONFIG_PATH_SOURCE_LABEL = "CLUSTER_KUBECONFIG_PATH"

# Where the connection material a `from_connection` target was built from is kept. It is not a
# dataclass field: the `ClusterTarget` fields are the whole of the object's public shape, and
# credential material is not part of what gets compared or printed.
_CONNECTION_FIELD = "_connection_material"


_CONNECTION_DOCUMENT_CONTEXT = "connection"


def default_kubeconfig_path() -> str:
    """The Kubernetes library's own rule, applied now rather than at import time.

    `$KUBECONFIG` if it is set — which may be a path-separated list, which the library's own
    merger understands — and `~/.kube/config` otherwise. This is the *last* answer: an environment
    that carries its own kubeconfig answers first, and this rule is what runs when it does not.
    """
    declared = os.environ.get(KUBECONFIG_ENV_VAR)
    if declared and declared.strip():
        return declared.strip()
    return str(Path.home() / ".kube" / "config")


def in_tree_kubeconfig(platform_config_dir: Path,
                       environment: str) -> Optional[Path]:
    """The environment's own kubeconfig, when the tree has one.

    `platform_config/<environment>/kubeconfig.encrypted.yaml`, and only when the file is there. It
    answers ahead of `$KUBECONFIG` for one reason: a shell variable is something the machine happens
    to have, and a command run against a named environment should read that environment. A stray
    `export KUBECONFIG` in an operator's session silently re-pointing a deployment that named a
different cluster is the defect this answers, and it is the same defect the configuration layers
    were built to close everywhere else.

    The name is not decoration. git-crypt's filter in a prepared tree is `*.encrypted.*`, so this
    file is encrypted by the rule kubementat already installs there, and a kubeconfig that follows it
    is protected where it lives instead of only in the `CLUSTER_KUBECONFIG_PATH` that names it. The
    read itself is `read_decrypted`, so an operator working a locked checkout gets "this holds
    ciphertext" rather than a YAML error from a file git-crypt never unlocked.

    Returns the path, or None when the environment carries no kubeconfig of its own — which is the
    ordinary case today, and the only case in which the library's rule is reached.
    """
    candidate = Path(platform_config_dir) / environment / IN_TREE_KUBECONFIG_FILENAME
    return candidate if candidate.is_file() else None


def _normalise_address(address: str) -> str:
    """`address` with whitespace and one trailing slash removed.

    The check compares the server string, so `https://host:6443/` and `https://host:6443` have to be
    one address and not two. Only one slash is dropped, because a server that really does carry
    a path (`https://host/api`) is not the same endpoint as one that does not.
    """
    trimmed = address.strip()
    return trimmed[:-1] if trimmed.endswith("/") else trimmed


def _kubeconfig_files(path: Optional[str]) -> Tuple[Path, ...]:
    """The files `path` names, as a tuple, defaulting to the library's own rule.

    A `None` path is not a guess about where the kubeconfig is; it is the statement "the
    library's rule", and the rule is `$KUBECONFIG`, then `~/.kube/config`.
    """
    declared = path if path not in (None, "") else default_kubeconfig_path()
    declared = str(declared)
    files = tuple(Path(entry).expanduser() for entry in declared.split(os.pathsep) if entry)
    if not files:
        raise ClusterFileUnreadableError(
            f"{KUBECONFIG_ENV_VAR} names no file.",
            reason=f"{KUBECONFIG_ENV_VAR} is set but empty",
        )
    missing = [str(entry) for entry in files if not entry.is_file()]
    if missing:
        label = ", ".join(missing)
        raise ClusterFileUnreadableError(
            f"the kubeconfig {label} does not exist. Point {KUBECONFIG_PATH_SOURCE_LABEL} at the "
            f"file the context lives in, or pass --kubeconfig.",
            reason=f"no kubeconfig at {label}",
        )
    return files


def _files_label(files: Iterable[Path]) -> str:
    return ", ".join(str(entry) for entry in files)


def _read_kubeconfig(files: Tuple[Path, ...]) -> Dict[str, Dict[str, Any]]:
    """The contexts and clusters `files` declares, merged, with the first file winning.

    Kubectl's own merge rule, which is what a `$KUBECONFIG` list means to every other tool an
    operator has. Contexts are keyed by name and mapped to the cluster they name; clusters are
    keyed by name and mapped to their dictionary, so the server is one lookup away and no
    credential is ever touched.
    """
    contexts: Dict[str, Optional[str]] = {}
    clusters: Dict[str, Dict[str, Any]] = {}
    for entry in files:
        try:
            document = yaml.safe_load(read_decrypted(entry, what="kubeconfig"))
        except (OSError, LockedConfigFileError) as exc:
            # The seam's sentence goes inside this module's own error type and is not rewritten
            # there: the file the operator has to look at is named by the read that failed,
            # which used to describe a locked file as a codec failure and by an assumption in the
            # comment that used to sit here.
            raise ClusterFileUnreadableError(
                f"{entry} could not be read: {exc}", reason=f"{entry} could not be read"
            ) from exc
        except yaml.YAMLError as exc:
            raise ClusterFileUnreadableError(
                f"{entry} is not a kubeconfig: {exc}", reason=f"{entry} cannot be parsed"
            ) from exc
        if document is None:
            document = {}
        if not isinstance(document, dict):
            raise ClusterFileUnreadableError(
                f"{entry} is not a kubeconfig: it is not a mapping.",
                reason=f"{entry} is not a kubeconfig",
            )
        for context_entry in document.get("contexts") or []:
            name = context_entry.get("name") if isinstance(context_entry, dict) else None
            if name and name not in contexts:
                body = context_entry.get("context") or {}
                contexts[name] = body.get("cluster") if isinstance(body, dict) else None
        for cluster_entry in document.get("clusters") or []:
            name = cluster_entry.get("name") if isinstance(cluster_entry, dict) else None
            if name and name not in clusters:
                body = cluster_entry.get("cluster") or {}
                clusters[name] = body if isinstance(body, dict) else {}
    return {"contexts": contexts, "clusters": clusters}


def _address_in_kubeconfig(files: Tuple[Path, ...], context: str) -> str:
    """The server `context` reaches, read out of `files`.

    Raises
    ------
    ClusterContextNotFoundError
        If `context` is not one of the contexts these files declare. The message lists the ones
        that are, which is the answer to a typo rather than a description of the symptom.
    ClusterFileUnreadableError
        If the context is there but the cluster it names, or that cluster's server, is not.
    """
    document = _read_kubeconfig(files)
    contexts = document["contexts"]
    if context not in contexts:
        available = sorted(contexts)
        listing = ", ".join(available) if available else "it declares none"
        raise ClusterContextNotFoundError(
            f"context '{context}' is not in {_files_label(files)}. Contexts it does have: {listing}.",
            reason=f"no such context '{context}' in {_files_label(files)}",
            available=available,
        )
    cluster_name = contexts[context]
    cluster = document["clusters"].get(cluster_name) if cluster_name else None
    if cluster is None:
        raise ClusterFileUnreadableError(
            f"context '{context}' in {_files_label(files)} names cluster '{cluster_name}', "
            f"which the file does not declare.",
            reason=f"context '{context}' has no cluster '{cluster_name}' in {_files_label(files)}",
        )
    server = cluster.get("server")
    if not server or not str(server).strip():
        raise ClusterFileUnreadableError(
            f"cluster '{cluster_name}' in {_files_label(files)} declares no server.",
            reason=f"cluster '{cluster_name}' in {_files_label(files)} declares no server",
        )
    return _normalise_address(str(server))


def _service_account_files(mount_root=None) -> Tuple[Path, Path]:
    """`(token, ca.crt)` under the mounted root, which a test may point somewhere else.

    `mount_root` is an argument and not a monkeypatch: a test that fabricates the
    kubelet's own files under a temporary directory should be able to hand their location to the
    constructor instead of editing a module out from under itself. Nothing about the object changes
    — it is still the same three files in the same order.
    """
    root = Path(mount_root) if mount_root is not None else SERVICE_ACCOUNT_ROOT
    return root / SERVICE_ACCOUNT_TOKEN_FILE, root / SERVICE_ACCOUNT_CA_FILE

def _service_account_address(mount_root=None) -> str:
    """The address of the cluster this process is running *on*, or the named reason it is not.

    The probe is deliberately cheap and deliberately uninformative: it stats the two mounted files
    and reads the two variables the kubelet injects, and it does not open the token, build a client,
    or ask the API server anything. That is what lets the 1:1 check consult it on every command —
    the same arithmetic the check does for kubeconfig reads — and it is why a machine that is not a pod
    answers `ServiceAccountNotMountedError` in microseconds rather than hanging on a dial.

    The scheme is `https` and nothing else. The in-cluster Service is served on TLS in every
    supported cluster, and a target that guessed `http` would send a bearer token in the clear
    before anything could notice.
    """
    token_path, ca_path = _service_account_files(mount_root)
    host = (os.environ.get(KUBERNETES_SERVICE_HOST_ENV) or "").strip()
    port = (os.environ.get(KUBERNETES_SERVICE_PORT_ENV) or "").strip() or DEFAULT_KUBERNETES_SERVICE_PORT

    problems = []
    if not token_path.is_file():
        problems.append(f"no service-account token at {token_path}")
    if not ca_path.is_file():
        problems.append(f"no certificate authority at {ca_path}")
    if not host:
        problems.append(f"{KUBERNETES_SERVICE_HOST_ENV} is not set, so there is no API server to name")
    if problems:
        raise ServiceAccountNotMountedError(
            f"this process is not running inside a cluster: {'; '.join(problems)}.",
            reason=problems[0],
        )
    # An IPv6 API server has to be bracketed or the port reads as part of the address; an IPv4 one
    # is untouched. `KUBERNETES_SERVICE_HOST` is whatever the kubelet wrote, which is one or the other.
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return _normalise_address(f"https://{host}:{port}")

def _library_config_file(kubeconfig_path: Optional[Path]) -> str:
    """The concrete file to hand the Kubernetes loader for `kubeconfig_path`.

    Explicit, always: the library's own default is a constant it captured from the environment at
    import, so leaving it out would let a process that changed `$KUBECONFIG` after import bind to
    one file while its address came from another.
    """
    if kubeconfig_path is not None:
        return str(kubeconfig_path)
    return default_kubeconfig_path()


# The document a connection target is loaded from. It sits here, beside the field its material
# is stashed under, rather than beside `ClusterTarget`: the two names and the key they are read
# under are one decision, and splitting them across the two files puts an edge in each direction
# (census §6). `ClusterTarget.client()` reaches this across that edge.
def _connection_document(address: str, material: Dict[str, Any]) -> Dict[str, Any]:
    """The kubeconfig document `client()` hands the library for a connection target.

    Built as a document rather than by poking `Configuration` fields because the library already
    knows how to turn inline CA data and inline client certificates into the files its transport
    needs, and knows how to clean them up. The shapes it accepts here are deliberately narrow:
    token, or client certificate and key. There is no exec, no auth-provider, no `cmd-path` —
    that is from_connection's one honest limit, stated before it is discovered.
    """
    cluster: Dict[str, Any] = {"server": address}
    ca = material.get("ca")
    if ca:
        ca_path = Path(ca)
        if ca_path.is_file():
            cluster["certificate-authority"] = str(ca_path)
        else:
            cluster["certificate-authority-data"] = _as_pem_base64(ca)
    user: Dict[str, Any] = {}
    if material.get("token"):
        user["token"] = material["token"]
    if material.get("client_certificate"):
        user["client-certificate-data"] = _as_pem_base64(material["client_certificate"])
        user["client-key-data"] = _as_pem_base64(material["client_key"])
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": _CONNECTION_DOCUMENT_CONTEXT,
        "clusters": [{"name": "connection", "cluster": cluster}],
        "contexts": [
            {"name": _CONNECTION_DOCUMENT_CONTEXT, "context": {"cluster": "connection", "user": "connection"}}
        ],
        "users": [{"name": "connection", "user": user}],
    }


def _as_pem_base64(value: str) -> str:
    """`value` as base64, whether it is already encoded or is the PEM itself.

    `from_connection` is a public library door, so a caller may hand over either shape and both
    arrive at the same document. Anything that is neither is left alone rather than mangled —
    the library will refuse it in its own words, which is a better sentence than this one could
    write about someone else's credential format.
    """
    if str(value).lstrip().startswith("-----BEGIN"):
        return base64.b64encode(str(value).encode("utf-8")).decode("ascii")
    return value
