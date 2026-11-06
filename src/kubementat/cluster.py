"""An environment addresses one cluster, and the address is the claim the 1:1 check compares.

Three objects live here, and each of them exists to stop "which cluster" being answered six
times by six different classes. Nothing in this module runs a command, opens a
socket, or asks a cluster anything: every address is read out of a file before a service exists
— or, last of the layers that can answer, out of the material the kubelet mounted in the pod that
is asking.

* `ClusterTarget` — a frozen value object that says *which* cluster, *where* that was declared,
  and carries its own server address so a comparison cannot forget to happen.
* `EnvironmentRegistry` — enumerates environments (directories at depth 1 of `platform_config`
  that contain a `static.json`) and resolves each one's own claim.
* `Context` — the pair of `(config, target)` plus the team and environment they belong to,
  built in one place so a command either has a cluster it can name or does not run.

Two decisions that are load-bearing and not obvious from the shape of the code:

* **The address is resolved by reading the YAML, not by loading the client.**
  `kubernetes.config.load_kube_config` would run any exec plugin the kubeconfig names and would
  mutate the process-wide default `Configuration` — the side effect `ClusterTarget.client()` now
  avoids by loading into its own. Reading the file directly means the address is available for every
  environment in the root, cheaply enough for the check to run on reads, and it means an
  `environments list` cannot trigger a credential refresh.
* **`$KUBECONFIG` is read when we are asked, not when the module was imported.**
  `kubernetes.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION` is a module-level constant the
  library captured at import (the fixtures in `tests/conftest.py:547` exist because of this), so
  a default resolved through it would silently disagree with the environment the caller is in.
  This module applies the same documented rule — `$KUBECONFIG`, then `~/.kube/config` — itself,
  then hands the concrete path to the library so the address and the client cannot disagree.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import yaml

from kubementat.core.config.config import Config
from kubementat.core.config.reads import LockedConfigFileError, read_decrypted
from kubementat.errors import (
    ClusterContextNotFoundError,
    ClusterFileUnreadableError,
    ClusterIdentityCollisionError,
    ClusterNotConfiguredError,
    ClusterResolutionError,
    ServiceAccountNotMountedError,
    StatedClusterMismatchError,
    TeamNotSuppliedError,
)

# The two environment-level keys that decide which cluster a command means. They are here rather
# than in `constants` because
# they mean something only in this module's resolution order, and a constant nobody reads from
# the resolution order is a constant that goes stale.
CLUSTER_KUBE_CONTEXT_KEY = "CLUSTER_KUBE_CONTEXT"
CLUSTER_KUBECONFIG_PATH_KEY = "CLUSTER_KUBECONFIG_PATH"

# The `source` values a target can carry. `static.json:<path>` is a prefix plus the path rather
# than a
# bare label, because "which file said this" is the answer a debugging session is looking for.
SOURCE_FLAG = "flag"
SOURCE_ENV = "env"
SOURCE_CONNECTION = "connection"
SOURCE_SERVICE_ACCOUNT = "service-account"
STATIC_SOURCE_PREFIX = "static.json:"

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
# dataclass field: the fields above are the whole of the object's public shape, and credential
# material is not part of what gets compared or printed.
_CONNECTION_FIELD = "_connection_material"


# `TeamNotSuppliedError` is imported above rather than defined here, and it comes from
# `kubementat.errors` rather than from this module's neighbourhood: the same refusal is raised by
# `Context.require_team` and by `Config`'s team-scoped reads, and defining a second one here would
# make the guard raise one type and the services catch another. Not a resolution failure: the cluster
# resolved and `Context` carries it. What is missing is the other half of the pair —
# `platform_config/{environment}/<team>/` — and kmt does not guess what a command means, so the object
# says so rather than letting a service read a path built out of a name nobody gave it. That both
# call sites raise it is the reason the refusal has one home instead of one per service.


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


@dataclass(frozen=True)
class ClusterTarget:
    """One cluster, described once, with its own address and the path that led here.

    `address` is a field rather than a property: a check that has to ask for the address is a check
    that can be skipped by a call site, and a field is compared or
    not compared by the same code that compares everything else. Two consequences worth stating:
    the address is only as current as the file it came from, and building a target cannot make
    a network call.
    """

    environment: str
    context: Optional[str]
    kubeconfig_path: Optional[Path]
    address: str
    source: str

    @classmethod
    def from_kubeconfig(
        cls,
        path: Optional[str],
        context: Optional[str],
        environment: str,
        *,
        source: str = SOURCE_FLAG,
    ) -> "ClusterTarget":
        """A target read from a kubeconfig — the CLI path, and the only one the resolution order
        selects.

        `path=None` is the library's own rule (`$KUBECONFIG`, then `~/.kube/config`) and is
        recorded as `None` rather than as the file that rule happened to resolve to, so the
        object says what was declared and not what the machine had.

        `source` is a keyword-only extra because the constructor cannot know which layer the
        arguments came from, while the object's `source` has to: the same construction answers
        `--context`, a process variable, and a `static.json`, and "which one" is the whole
        difference when the wrong cluster is being blamed.
        """
        if not context or not str(context).strip():
            raise ClusterNotConfiguredError(
                f"no context was declared for environment '{environment}'. "
                f"There is no current-context fallback: name the context in "
                f"platform_config/{environment}/static.json, pass --context, or set "
                f"{CLUSTER_KUBE_CONTEXT_KEY}.",
                reason=f"{CLUSTER_KUBE_CONTEXT_KEY} is not set for '{environment}'",
            )
        files = _kubeconfig_files(None if path is None else str(path))
        target = cls(
            environment=environment,
            context=str(context).strip(),
            kubeconfig_path=None if path is None else Path(path).expanduser(),
            address=_address_in_kubeconfig(files, str(context).strip()),
            source=source,
        )
        return target

    @classmethod
    def from_connection(
        cls,
        host: str,
        ca: Optional[str],
        token_or_cert,
        environment: str,
        *,
        source: str = SOURCE_CONNECTION,
    ) -> "ClusterTarget":
        """A target built from credentials someone is already holding — the library path.

        Nothing in the resolution order ever selects this, and that is the boundary that keeps
        kubementat out of the credential business: the kubeconfig, and nothing kubementat writes,
        is where a cluster's credentials live. Its limits belong to it: no exec
        plugins, `kubeconfig_path` is `None` by definition, and `source` is `"connection"`.

        `ca` is either the path of a CA file or its PEM contents. `token_or_cert` is a bearer
        token, or a `(client certificate, client key)` pair — anything else is a programming
        error rather than a configuration one, so it is a `ValueError`.

        `source` is the keyword-only extra `from_kubeconfig` already carries, and `from_service_account`
        needs the same argument for the same reason: the object's shape is identical in both cases
        and only
        provenance says which door the credentials came in through.
        """
        if not host or not str(host).strip():
            raise ValueError("from_connection needs a host: an addressless target addresses nothing.")
        material: Dict[str, Any] = {}
        if isinstance(token_or_cert, (tuple, list)):
            if len(token_or_cert) != 2:
                raise ValueError("client-certificate authentication needs both the certificate and the key.")
            material["client_certificate"], material["client_key"] = (
                str(token_or_cert[0]),
                str(token_or_cert[1]),
            )
        elif isinstance(token_or_cert, dict):
            material.update(token_or_cert)
        elif isinstance(token_or_cert, str) and token_or_cert.strip():
            material["token"] = token_or_cert.strip()
        else:
            raise ValueError("from_connection needs a token or a client certificate and key.")
        if "client_certificate" in material and not material.get("client_key"):
            raise ValueError("a client certificate needs its key: nothing was passed for it.")
        if not material.get("token") and not material.get("client_certificate"):
            raise ValueError(
                "from_connection needs a bearer token or a client certificate and key; "
                "a certificate authority alone authenticates nothing."
            )
        if ca:
            material["ca"] = str(ca)

        target = cls(
            environment=environment,
            context=None,
            kubeconfig_path=None,
            address=_normalise_address(str(host)),
            source=source,
        )
        object.__setattr__(target, _CONNECTION_FIELD, material)
        return target

    @classmethod
    def from_service_account(
        cls, environment: str, *, mount_root=None
    ) -> "ClusterTarget":
        """A target built from the material the kubelet mounts — the resolution order's last layer.

        The token and the authority come from the mounted paths, the host from
        `KUBERNETES_SERVICE_HOST` (plus `KUBERNETES_SERVICE_PORT`, defaulting to 443), and no
        kubeconfig is consulted, read, or looked up: `context` and `kubeconfig_path` are both `None`
        for the reason `from_connection` gives the same fields, and `source` is `"service-account"`
        rather than
        `"connection"` because "the pod's own credentials" and "credentials someone handed a library
        caller" are two different claims to make in a debugging session.

        It belongs **last** in the resolution order and this constructor makes no promise about
        where it is
        called from: nothing here looks at the layers above it, and an environment that declares
        `CLUSTER_KUBE_CONTEXT` uses what it declares. That is what keeps in-cluster from being a
        silent winner — this object is only ever reached when everything above it stayed quiet.

        Raises
        ------
        ServiceAccountNotMountedError
            If the token or the authority is not mounted, or the API server was never named. The
            sentence names the path that is not there, because the answer to that failure is "you
            are not in a pod" and not "the file is broken".
        """
        address = _service_account_address(mount_root)
        token_path, ca_path = _service_account_files(mount_root)
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ServiceAccountNotMountedError(
                f"this process is not running inside a cluster: the service-account token at "
                f"{token_path} could not be read ({exc}).",
                reason=f"the service-account token at {token_path} could not be read",
            ) from exc
        if not token:
            raise ServiceAccountNotMountedError(
                f"this process is not running inside a cluster: the service-account token at "
                f"{token_path} is empty, which authenticates nothing.",
                reason=f"the service-account token at {token_path} is empty",
            )
        # The authority is handed over as a path rather than as its contents: the
        # object must not be the thing that carries a cluster's private material further than it
        # needs to, and `client()` reads it from disk when it builds the transport.
        return cls.from_connection(
            address, ca_path, token, environment, source=SOURCE_SERVICE_ACCOUNT
        )

    def client(self):
        """An `ApiClient` bound to this target, and nothing else about this object does.

        Building it is lazy on purpose: enumerating an environment root should not parse
        credentials, write temporary files for inline certificate data, or run an exec plugin
        that no command asked to run. The credentials reach the client here, from the file or the
        material this target was built from, and the returned client is bound to a private
        `Configuration` — the library's default one is never touched, so two targets in one
        process cannot overwrite each other.
        """
        # Imported here rather than at module level so that importing this module — all a listing
        # needs to do — does not pay for the generated client, and so that no resolution path can
        # reach a credential loader by accident.
        from kubernetes import client
        from kubernetes import config as kubernetes_config

        material = getattr(self, _CONNECTION_FIELD, None)
        if material is not None:
            configuration = client.Configuration()
            kubernetes_config.load_kube_config_from_dict(
                _connection_document(self.address, material),
                context=_CONNECTION_DOCUMENT_CONTEXT,
                client_configuration=configuration,
                persist_config=False,
            )
            return client.ApiClient(configuration)
        # `new_client_from_config` is the call that satisfies the rule that no client is built from
        # the process-wide default, in the only sense that matters:
        # it loads into a `Configuration` of its own, so the process-wide default is never written,
        # and it still runs the exec plugins a managed-cluster kubeconfig depends on. Naming
        # `load_kube_config` here instead would be the same line the guard test exists to forbid,
        # for no behaviour this object needs.
        return kubernetes_config.new_client_from_config(
            config_file=_library_config_file(self.kubeconfig_path),
            context=self.context,
            # persist_config=True is the library's default, and writing back would edit the
            # operator's kubeconfig while a command was only trying to read it.
            persist_config=False,
        )

    def __repr__(self) -> str:
        """Every credential this target may carry, left out.

        A `ClusterTarget` ends up in log lines and in failure messages, and one of those places
        is a machine someone is debugging on. The kubeconfig path is a path, not a secret, and
        the address is what the 1:1 check compares, so both stay; what a connection target was built
        from does not.
        """
        credentials = getattr(self, _CONNECTION_FIELD, None)
        redacted = "" if credentials is None else ", credentials='<redacted>'"
        kubeconfig = "None" if self.kubeconfig_path is None else f"'{self.kubeconfig_path}'"
        return (
            f"ClusterTarget(environment={self.environment!r}, context={self.context!r}, "
            f"kubeconfig_path={kubeconfig}, address={self.address!r}, source={self.source!r}"
            f"{redacted})"
        )


_CONNECTION_DOCUMENT_CONTEXT = "connection"


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


def running_cluster_address() -> Optional[str]:
    """This process's own cluster address, or `None` when it is not inside one.

    The question is asked for one reason: in-cluster is the only resolution path
    where the target is the cluster the code is running on, and a 1:1 check that compares against
    *itself* would refuse kubementat's own install. The answer is used only to *subtract*, never to
    choose — no resolution path here consults it as a candidate, and a command that addresses no
    cluster still gets the error that names the file and the key rather than this one.

    `None` is the normal answer everywhere except inside a pod, and the probe is the two file stats
    and two variable reads that keep the check cheap on reads.
    """
    try:
        return _service_account_address()
    except ServiceAccountNotMountedError:
        return None


def _addresses_the_running_cluster(record, self_address: Optional[str]) -> bool:
    """Whether `record` is the cluster this command is already standing on.

    Spelled once because both halves of the answer have to agree: the comparison that may refuse,
    and the report line that has to say it was held back. An address equality is the only test —
    the identity being compared is the server string, and the running cluster is identified that
    way.
    """
    return bool(self_address) and record.target is not None and record.target.address == self_address


def _library_config_file(kubeconfig_path: Optional[Path]) -> str:
    """The concrete file to hand the Kubernetes loader for `kubeconfig_path`.

    Explicit, always: the library's own default is a constant it captured from the environment at
    import, so leaving it out would let a process that changed `$KUBECONFIG` after import bind to
    one file while its address came from another.
    """
    if kubeconfig_path is not None:
        return str(kubeconfig_path)
    return default_kubeconfig_path()


@dataclass(frozen=True)
class EnvironmentResolution:
    """One environment's own claim on a cluster, or the reason it has none.

    The unresolved half is not a `None` in a list: the 1:1 check wants the environment named with
    the reason it could not be read, because a check that silently omits environments is a check
    that lies about its coverage.

    `declared_context` is what the environment's `static.json` said, whether or not it reached a
    cluster. `kmt environments list` prints that column, and it has to come out of the same
    read the check ran: an answer the command derived for itself is a second read of one key, and
    two reads of one key are how a listing and a check start to disagree about what is configured.
    """

    environment: str
    target: Optional[ClusterTarget]
    reason: Optional[str] = None
    declared_context: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.target is not None


@dataclass(frozen=True)
class ClusterComparison:
    """One 1:1 check, with everything the answer was built out of still attached to it.

    It is a comparison and not a boolean because the same question feeds two sentences: the refusal,
    which only needs the environments that overlapped, and the report line, which needs the whole
    set that was compared and the half of it held back because it is the cluster the command is
    running on. Returning only
    the overlaps would leave the second sentence to go and work the answer out again — and that
    second working is exactly the second implementation this object exists to delete.

    * `others` is every *other* environment in the root, unresolved ones included, because a line
      that says what was verified has to be able to name what could not be.
    * `self_address` is the running cluster's exclusion made visible: the same value the check
      subtracted with, so
      the report can say *which* environments were held back and why.
    * `collisions` is the only field that may refuse a command, and it is empty exactly when the
      command may run.

    `target` is what was asked about, kept so a reader of a log or a debugger can see the question
    beside the answer instead of inferring it from the address.
    """

    target: ClusterTarget
    others: Tuple[EnvironmentResolution, ...]
    collisions: Tuple[EnvironmentResolution, ...]
    self_address: Optional[str] = None


class EnvironmentRegistry:
    """Every environment under one `platform_config` root, and the cluster each one declares.

    Enumeration is directories at depth 1 of the root that contain a `static.json`, and nothing
    more: that falls out of the existing shape — a team's `static.json` is depth 2, and
    `kubementat_components/` carries no environment-level one — so there is no registry file and
    no rule to keep in sync with the tree.
    """

    def __init__(self, platform_config_dir):
        self.platform_config_dir = Path(platform_config_dir)

    def environments(self) -> Tuple[str, ...]:
        """Environment names, sorted, by what is on disk.

        Reading a list out of the directory rather than out of code is the same decision that put
        `Config.teams()` on a directory listing: a name someone remembered is a name someone
        forgot.
        """
        if not self.platform_config_dir.is_dir():
            return ()
        return tuple(
            sorted(
                entry.name
                for entry in self.platform_config_dir.iterdir()
                if entry.is_dir() and (entry / "static.json").is_file()
            )
        )

    def all(self) -> Tuple[EnvironmentResolution, ...]:
        """Every environment's own claim, resolved or refused with a reason.

        Nothing here consults the process environment or anyone's command line: a sibling is
        asked what *it* declares, which is the only question that can be answered about a directory
        the current command is not running.
        """
        return tuple(self.resolve(name) for name in self.environments())

    def resolve(self, environment: str) -> EnvironmentResolution:
        """One environment's target, read out of its own `static.json`."""
        static_path = self.platform_config_dir / environment / "static.json"
        if not static_path.is_file():
            return EnvironmentResolution(
                environment,
                None,
                reason=f"{static_path} does not exist",
            )
        try:
            declared = _static_declaration(static_path)
        except ClusterResolutionError as exc:
            return EnvironmentResolution(environment, None, reason=exc.reason)
        context = declared["context_value"]
        if context is None:
            return EnvironmentResolution(
                environment,
                None,
                reason=f"{CLUSTER_KUBE_CONTEXT_KEY} {declared['context_state']} in {static_path}",
                declared_context=context,
            )
        try:
            target = ClusterTarget.from_kubeconfig(
                declared["path_value"] or in_tree_kubeconfig(self.platform_config_dir, environment),
                context,
                environment,
                source=f"{STATIC_SOURCE_PREFIX}{static_path}",
            )
        except ClusterResolutionError as exc:
            return EnvironmentResolution(
                environment, None, reason=exc.reason, declared_context=context)
        return EnvironmentResolution(environment, target, declared_context=context)

    def collision_check(self, target: ClusterTarget) -> ClusterComparison:
        """The *other* environments that address the same cluster `target` does, and the comparison
        that answer came out of.

        An empty `collisions` is the only answer that lets a command run. The target's own
        environment is excluded by name, not by address: it is allowed to be the environment that
        declares this cluster, which is the whole point of 1:1.

        So is the running cluster, by a rule that has to be stated rather than inferred: when this
        process is inside one, an environment
        that resolves to that same address is *here*, not a second cluster, and reporting it as a
        collision would refuse kubementat's own install. The exclusion is applied once, here, and
        returned inside the comparison instead of being recomputed by whoever reports the answer —
        which is the reason this method exists rather than an inline `any()` somewhere else. Once
        `Context.build` spelled this filter out beside the method and then probed the running
        cluster a second time for its own report line: two spellings of one rule are how a refusal
        and the sentence about it start to disagree, and one probe is what makes them share.
        """
        # One pass over the root, because the cost of local reads is acceptable but the cost of
        # doing it twice for the sake of a report line is not.
        self_address = running_cluster_address()
        others = tuple(record for record in self.all() if record.environment != target.environment)
        collisions = tuple(
            record
            for record in others
            if record.target is not None
            and record.target.address == target.address
            and not _addresses_the_running_cluster(record, self_address)
        )
        return ClusterComparison(target=target, others=others, collisions=collisions,
                                 self_address=self_address)


def _static_declaration(static_path: Path) -> Dict[str, Any]:
    """The two cluster keys as they were declared in `static_path`.

    Returns the value of each key (or `None` when it is absent, null, or blank — all three are
    treated as not declared) plus a `context_state` phrase that says *which* of the three it was,
    because the error has to name the value found and not just the key missing.
    """
    try:
        document = json.loads(read_decrypted(static_path, "environment's static.json"))
    except OSError as exc:
        raise ClusterFileUnreadableError(
            f"{static_path} could not be read: {exc}", reason=f"{static_path} could not be read"
        ) from exc
    except LockedConfigFileError as exc:
        # The seam's sentence, this module's type. It used to be a `UnicodeDecodeError`
        # that walked past the two handlers below and printed a codec traceback about a file whose
        # only crime was being locked.
        raise ClusterFileUnreadableError(
            f"{static_path} could not be read: {exc}", reason=f"{static_path} could not be read"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ClusterFileUnreadableError(
            f"{static_path} is not valid JSON: {exc}", reason=f"{static_path} is not valid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise ClusterFileUnreadableError(
            f"{static_path} is not an environment static.json: it is not an object.",
            reason=f"{static_path} is not an object",
        )

    raw_context = document.get(CLUSTER_KUBE_CONTEXT_KEY)
    raw_path = document.get(CLUSTER_KUBECONFIG_PATH_KEY)
    return {
        "context_value": _declared_value(raw_context),
        "context_state": _declaration_state(raw_context, CLUSTER_KUBE_CONTEXT_KEY in document),
        "path_value": _declared_value(raw_path),
    }


def _declared_value(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _declaration_state(value, present: bool) -> str:
    """How the key reads in the file, for an error that has to name the value found.

    Absent, null, and empty are the three ways a key reads as not declared, and
    an operator reading the file sees three different files, so the sentence says which one it
    found — `null` is what a freshly prepared environment ships, and "not set" is what a
    hand-edited one that never had the key looks like.
    """
    if not present:
        return "is not set"
    if value is None:
        return "is null"
    if not str(value).strip():
        return "is empty"
    if str(value).strip().lower() == "null":
        return "is the string 'null'"
    return f"is set to {value!r}"


def environment_file_missing(environment: str, static_path: Path) -> str:
    """The refusal an operator acts on when an environment has no `static.json` to read a key out of.

    Spelled once because both halves of `Context.build` need it: the team-bearing one fails inside
    `Config`'s constructor, the team-less one in front of the file it was about to read. Same
    file, same missing key, same instruction — which is the point of not writing it twice.
    """
    return (
        f"environment '{environment}' does not address a cluster: {static_path} does not exist, "
        f"so {CLUSTER_KUBE_CONTEXT_KEY} cannot be read from it. There is no current-context "
        f"fallback — run kmt config init <dir> --environment {environment}, or name the context "
        f"in that file."
    )


def _environment_declaration(environment: str,
                             team: Optional[str],
                             platform_config_dir=None) -> Tuple[Config, Path, Dict[str, Any]]:
    """`(config, static_path, declared)` for the environment one command means.

    `platform_config_dir` is the root one command was given, and it is threaded into the one
    `Config` this function builds rather than passed beside it. A root that stopped at this
    signature would reach the registry below and not the file read here, and that is how one flag
    ends up answering two questions.

    The `Config` is built either way, and that is the whole of what a `Context` answers: `config`
    says *what to deploy* and `target` says *where*, and a command that sweeps namespaces has to be
    able to answer the first question even when it was never given a team. **With a team**,
    this is the `Config` every service class used to build for itself. **Without one**, the
    same two arguments reach `Config` with `team=None`, which is now a state that object supports
    rather than a lie it is told: the environment-level files resolve, the team-scoped paths are not
    built, and reading a team-scoped value raises `TeamNotSuppliedError` instead of interpolating
    `None` — or, from an empty team, reading the environment's own file and calling it the team's.

    What both paths do is read **one** file, and name it when it is not there. The environment's own
    declaration is the only configuration a cluster claim depends on, so it is the only one read
    here; every other file stays lazy, exactly as it was before the decorator, so that a broken
    team-level file keeps arriving as the sentence `Config.get` writes about that file instead of as
    a JSON error from a place that cannot say which of four files it opened.
    """
    config = _load_environment_config(environment, team, platform_config_dir)
    static_path = config.env_static_config_path
    if not static_path.is_file():
        raise ClusterNotConfiguredError(
            environment_file_missing(environment, static_path),
            reason=f"{CLUSTER_KUBE_CONTEXT_KEY} cannot be read, {static_path} does not exist",
        )
    return config, static_path, _static_declaration(static_path)


def _load_environment_config(environment: str, team: Optional[str],
                             platform_config_dir=None) -> Config:
    """The one `Config` a Context carries, built the lazy way.

    A root is passed through and never re-derived: `None` means "this command named none", and the
    precedence rule for that case is `Config`'s, not a guess made here. The
    flag that reaches this argument is the same spelling a `Config` keyword already has, so
    plumbing it changes no rule.

    `preload_configs=False` is not an optimisation. It is what keeps `Context.build` reading the
    single file a cluster claim lives in: `Config`'s eager read opens four and fails on whichever is
    wrong first, so a malformed *team* file would refuse a command whose cluster resolved perfectly,
    with a message that cannot say which of the four it opened. A `Context` that loaded eagerly would
    be a second, louder answer to a question one file already answers.

    Nothing else about the object changes. `get()` loads what it is asked for on first use, so a read
    of `team_static` still fails naming the file that was missing — and, for a Context given no team
    at all, fails naming the team it was given no name for.
    """
    return Config(environment, team, platform_config_dir=platform_config_dir,
                  preload_configs=False)


@dataclass(frozen=True)
class Context:
    """The environment, the team, the configuration, and the cluster — one object, one read.

    This exists rather than an extra argument on six constructors for a reason that is not obvious
    from the shape of the code: every
    service class used to resolve "which cluster" for itself, so the identity check ran as many
    times as there were services in a command, and one of those resolutions was apologising for
    being a second read in a docstring.

    `team` is nullable and `config` is not: a command with no team semantics resolves its
    cluster from the environment layer and carries the environment's configuration without a
    directory named after a team. That is the uniform type — one `Context`, either shape — and
    `require_team` is what a service that does need the other half says when the uniformity is not
    an answer. It is not a downgrade of what one object promises: a team-less command has no
    team-valued config
    to keep in step with the cluster, so there is nothing to drift. `config` exists either way
    because *what to deploy* is half of what a command is asked, and `uninstall` — a command with no
    team that has to know which namespaces to delete — is the proof that the other half is not
    optional.

    `target` is nullable only for `for_configuration` below, and `require_target` is what stops a
    command from treating that state as "no cluster" rather than "no cluster has been addressed
    yet".
    """

    environment: str
    team: Optional[str]
    config: Optional[Config]
    target: Optional[ClusterTarget]
    resolution_report: Tuple[str, ...] = field(default=(), compare=False)

    # `target` is optional for exactly one shape: the `for_configuration` Context below, which is
    # the same "this environment has not addressed a cluster yet" state
    # `EnvironmentRegistry.resolve` reports for an environment that declares nothing. Every
    # Context a command resolves carries a target; an empty one is a fact about the command, not an
    # unset field, and `require_target` is what stops a call site reading it as "no cluster".

    def require_team(self, what_is_missing: str) -> str:
        """This team's name, or a refusal that says what it was needed for.

        One `Context` type means a service cannot be told about the cluster a command resolved
        and left in the dark about the team it did not, so the service asks instead of guessing.
        `what_is_missing` is the caller's own noun — the thing it was about to read, write or
        *name* — because "no team" is a fact about the run and what the reader needs is the sentence
        saying which decision that fact stops.

        The sentence says what a team is *for*, in both of the two ways this tree uses one, and does
        not claim that the caller is about to open a file: some sites call this because they are
        about to read `platform_config/{environment}/<team>/`, and two call it because the team is
        an argv namespace (`kubectl run -n`) or a substring of a kubeconfig's context name. A
        message that only described the first kind told an operator running `kmt run-ci-container`
        that a file was missing when the code that refused it opens no file at all.

        There is no default to fall back to, and that is not an oversight: `DEFAULT_TEAM` was taken
        out for the reason `kmt users` already refuses a team it was not given, which is that
        guessing a team is guessing what a command means.
        """
        if self.team is None:
            raise TeamNotSuppliedError(
                f"{what_is_missing} belongs to a team, and this command was not given one. A team "
                f"is what platform_config/{self.environment}/<team> is keyed by, and what the "
                f"namespaces and account names it governs are built from; kubementat will not name "
                f"one for a command that did not name one. Pass --team (or set TEAM)."
            )
        return self.team

    def require_target(self, what_is_missing: str) -> ClusterTarget:
        """The cluster this command is about, or a refusal that says what it was needed for.

        The sibling of `require_team`, and for the same reason: this field is nullable in exactly
        one state — a `Context` that writes configuration and never addresses a cluster, which is
        `for_configuration`'s reason to exist and the reason not to make the target eager — and a
        call site that reads it as though it were always there turns "no cluster yet" into a
        command that addresses nothing and says nothing about it. So the state has a name, and
        anything that has to *use* a target asks for it.

        `ClusterNotConfiguredError` is the refusal, because that is what the state is: the key that
        names a cluster was never named for this environment, and the refusal's sentence is the one
        an operator acts on.
        It is not a new failure class, and there is no override for it.
        """
        if self.target is None:
            raise ClusterNotConfiguredError(
                f"{what_is_missing} needs the cluster environment '{self.environment}' addresses, "
                f"and this Context was built to write configuration without ever addressing one. "
                f"{CLUSTER_KUBE_CONTEXT_KEY} is what names it: set it in "
                f"platform_config/{self.environment}/static.json, or run the command that resolves "
                f"one instead of the one that configures.",
                reason=f"no ClusterTarget was resolved for environment '{self.environment}'",
            )
        return self.target

    @classmethod
    def for_configuration(cls, environment: str, team: str,
                          platform_config_dir=None) -> "Context":
        """A `Context` with its configuration and no cluster claim — laziness applied to the object
        a constructor is handed.

        `kmt config init` is the command this exists for. It writes the `static.json` a resolution
        reads, so asking it to resolve one first would make it the strictest command in the product
        for the least benefit: a tree that does not exist yet cannot say which cluster it is for,
        and the promise is that the first run works. The same argument against an eager
        `Automation` — a constructor that resolves a kubeconfig fails a command that
        touches no cluster — applies to the object a constructor is handed, so a verb that writes
        a configuration root gets
        this Context and nothing else.

        What is deliberately absent, and what each costs:

        * `target` is None: the environment has declared nothing, which is the state an unresolved
          environment is in. Nothing may address a cluster through this object, and
          `require_target` is what says so at the one point a caller might try.
        * the 1:1 check does not run. There is no claim to compare, which is the condition for
          skipping it rather than an exception to it.
        * `resolution_report` is empty. The report is a claim about a resolution that happened;
          a line printed for one that did not is the same line with a different meaning.

        A command that *does* address a cluster should build this by accident and never be told:
        `Context.build` is that command's constructor, and it cannot produce an object with no
        target. This one is for the command that configures a tree and stops.

        `platform_config_dir` is the root the operator named — `config init`'s positional directory,
        or the `--config-dir` every reading command carries.
        It reaches the `Config` this Context carries, and with it every directory and file
        the configuration half of a `Config` carries. Without it and without the variable there is
        no root to
        scaffold, and `Config` refuses rather than inventing a `platform_config` beside wherever
        the wheel was installed.
        """
        return cls(
            environment=environment,
            team=team,
            config=Config(environment, team, platform_config_dir=platform_config_dir,
                          preload_configs=False),
            target=None,
        )

    @classmethod
    def build(cls, environment: str, team: Optional[str] = None, context=None,
              kubeconfig=None, platform_config_dir=None) -> "Context":
        """Resolve in the order the layers are declared, check 1:1, and say what was compared.

        `platform_config_dir` is the root one command was given — the `--config-dir` a `kmt_context`
        command carries. It is threaded to the `Config`, and so to both halves of what this class
        reads: the environment's own `static.json` (through `_environment_declaration`) and the
        sibling sweep the 1:1 check compares (through `config.platform_config_dir` below). Handing
        one of the two and re-deriving the other is the bug this threading exists to close, which is
        why neither is spelled from the environment here.

        This is the only place the resolution order lives, and the only place a command runs the
        1:1 check — the check itself is `EnvironmentRegistry.collision_check`, which this method
        asks rather than writes out beside it. That is the point of one enumeration and one filter:
        one rule, one spelling, and the report line reading the same comparison that may refuse.
        It runs on reads too rather than only on writes, and the reason is stated rather than
        assumed: a check that skips reads needs a notion of purpose on the object, and the
        difference between that and running everywhere is a handful of local file reads.

        `team=None` is the shape the team-less commands hand up — `uninstall`, `cluster-capacity`,
        `pvc-usage`, the diagnostics reads — and they still resolve, because "which cluster" is not
        a question about teams. What they carry is the environment's own configuration and no
        team-scoped path, which `_environment_declaration` and `Config`'s own refusal of that state
        are the pair that makes true.

        Raises
        ------
        ClusterNotConfiguredError
            If no layer declared a context, or a layer declared one with an empty value. The
            message names the environment, the file that should have held the key, and the value
            that was found there.
        ClusterContextNotFoundError
            If the declared context is not in the kubeconfig that was resolved.
        StatedClusterMismatchError
            If a layer above `static.json` stated a cluster and the environment's declaration
            contradicts it. This is asked *before* a kubeconfig is opened or a target
            is built, so the refusal is the comparison and not the file the declaration pointed at.
        ClusterIdentityCollisionError
            If another environment in this root already addresses this cluster. There is no
            override, so nothing here can be argued with — except by the rule that removes the
            cluster this process is standing on from the comparison rather than adding a flag.

        The last layer sits behind all three of the declared ones: when nothing above it named
        a context, a process that is running inside a cluster resolves itself from the material the
        kubelet mounted. That is the only ambient resolution this code permits, it is last, and it
        says so in the report line; a process that is not inside a cluster gets the error below
        rather than an in-cluster guess.
        """
        config, static_path, declared = _environment_declaration(environment, team,
                                                                 platform_config_dir)

        layers = cls._declaration_layers(context, kubeconfig, static_path, declared)
        # The stated-versus-declared comparison, and it happens before anything resolves. Both
        # sides of it are already
        # in `layers`: the two above `static.json` are the run's own statement about where the work
        # runs, and `static.json` is the clone's claim about which environment it is for. Reading a
        # file is not what is being decided here, which is why a dead `CLUSTER_KUBECONFIG_PATH` in
        # the declaration cannot turn this refusal into a file-not-found error on its way past.
        cls._check_stated_cluster(environment, layers, static_path)
        resolved_context = None
        resolved_path = None
        resolved_source = None
        resolved_label = None
        for layer_context, layer_path, layer_source, layer_labels in layers:
            if resolved_context is None and layer_context is not None:
                resolved_context = layer_context
                resolved_source = layer_source
                resolved_label = layer_labels["context"]
            if resolved_path is None and layer_path is not None:
                resolved_path = layer_path
        # Nothing above this named a file, so the tree gets its own chance before the machine's does:
        # an environment that carries a kubeconfig is read from that file, and `$KUBECONFIG` is not
        # consulted. A path somebody declared is a different matter and has already won above.
        if resolved_path is None:
            resolved_path = in_tree_kubeconfig(config.platform_config_dir, environment)

        if resolved_context is None:
            # Layer 5, and the only reason it is last: an environment that declared a context
            # above this already answered, and the pod's own credentials must not outvote a
            # declaration. Nothing else about this object changes when it is used.
            target, label, refused = cls._service_account_layer(environment)
            if target is None:
                raise cls._nothing_declared(environment, static_path, layers, refused)
            resolved_source = target.source
            resolved_label = label
        else:
            target = ClusterTarget.from_kubeconfig(
                resolved_path, resolved_context, environment, source=resolved_source
            )
        # The root is read off the `Config` rather than re-derived, so a root this method was handed
        # and a root the environment variable says are the same one object here — which is the whole
        # point of threading it through the `Config` instead of around it.
        registry = EnvironmentRegistry(config.platform_config_dir)
        # The one 1:1 check, asked of the one primitive that owns it. What used to be here is the
        # filter this method wrote beside the method — same file, same rule, computed twice — and
        # the half it computed twice was the half the report line read, which is how a
        # docstring could promise agreement and the code deliver two answers.
        comparison = registry.collision_check(target)
        if comparison.collisions:
            raise cls._collision(target, comparison.collisions)

        return Context(
            environment=environment,
            team=team,
            config=config,
            target=target,
            resolution_report=(
                _report_line(target, resolved_label, comparison.others,
                             self_address=comparison.self_address),
            ),
        )

    @classmethod
    def _service_account_layer(cls, environment) -> Tuple[Optional[ClusterTarget], Optional[str], Optional[str]]:
        """The last layer as `(target, report label, reason it is not available)`.

        A refusal here is not an error: it is the fact that this process is not running inside a
        cluster, which the caller folds into the refusal's own sentence rather than letting it stand
        alone as
        the fifth way to fail. That ordering matters — the operator who typed `kmt install dev` on a
        laptop needs the file-and-key answer, not a lecture about service accounts that were never
        asked to exist.
        """
        try:
            target = ClusterTarget.from_service_account(environment)
        except ClusterResolutionError as exc:
            return None, None, exc.reason
        return (
            target,
            f"{SOURCE_SERVICE_ACCOUNT} {SERVICE_ACCOUNT_ROOT}",
            None,
        )

    @staticmethod
    def _declaration_layers(context, kubeconfig, static_path, declared):
        """The three *declared* layers, in order, as `(context, path, source, labels)`.

        The last layer is not in here, and it cannot be: it is a construction rather than a
        value somebody declared, so `Context.build` asks for it itself after these three come back
        silent. Everything else about the order is unchanged — a layer that reads the environment it
        is running in belongs behind the ones that read an operator's configuration, not among them.

        The source and the label come from the layer that declared the **context**, because the
        context is the claim the 1:1 check compares; a path declared lower down does not change what the
        environment declared about where it lives.

        `declared` is what the environment's own file says, read once by
        `_environment_declaration` — the same read `EnvironmentRegistry` makes, which is why a
        Context and a listing cannot report two different things about one `static.json`.
        """
        return (
            (
                Context._flag_value(context),
                Context._flag_value(kubeconfig),
                SOURCE_FLAG,
                {"context": "flag --context", "kubeconfig": "flag --kubeconfig"},
            ),
            (
                Context._process_value(CLUSTER_KUBE_CONTEXT_KEY),
                Context._process_value(CLUSTER_KUBECONFIG_PATH_KEY),
                SOURCE_ENV,
                {
                    "context": f"env {CLUSTER_KUBE_CONTEXT_KEY}",
                    "kubeconfig": f"env {CLUSTER_KUBECONFIG_PATH_KEY}",
                },
            ),
            (
                declared["context_value"],
                declared["path_value"],
                f"{STATIC_SOURCE_PREFIX}{static_path}",
                {"context": str(static_path), "kubeconfig": str(static_path)},
            ),
        )

    @classmethod
    def _statement_layers(cls, layers) -> Dict[str, Optional[str]]:
        """The run's own claim on its cluster, read out of the layers above `static.json`.

        A statement is either half: the context the run names, or the kubeconfig file it names. The
        file half is a claim too, and the reason it is compared rather than ignored is the CI pod:
        the file says which contexts exist *where the work runs*, so an environment whose context is
        not among them is asking to be installed somewhere the run is not.

        Each half keeps the label of the layer that wrote it, because the refusal is unusable
        without it — "the statement and the declaration" is two sentences, and each needs the flag,
        the variable, or the file that is worth opening.
        """
        statement = {"context": None, "context_source": None,
                     "kubeconfig": None, "kubeconfig_source": None}
        for layer_context, layer_path, _source, labels in layers[:-1]:
            if statement["context"] is None and layer_context is not None:
                statement["context"] = layer_context
                statement["context_source"] = labels["context"]
            if statement["kubeconfig"] is None and layer_path is not None:
                statement["kubeconfig"] = layer_path
                statement["kubeconfig_source"] = labels["kubeconfig"]
        return statement

    @classmethod
    def _check_stated_cluster(cls, environment: str, layers, static_path: Path) -> None:
        """A statement above the file may answer for an environment, not contradict it.

        The ordering is untouched here, and that is the point of putting a comparison in
        front of it instead of editing it. Layer 1 still beats layer 2, layer 2 still beats layer 3,
        and a stated `--kubeconfig` is still the file that gets opened; what is no longer allowed is
        for a stated *name* to disagree with a declared one, or for a stated *file* to not contain
        the declared context. The two claims answer different questions, so neither of them gets to
        be discarded by precedence — kubementat's own precedence would silently install `dev`'s
        platform into the cluster the TaskRun happens to be standing on.

        Both halves run *before* `from_kubeconfig`, which is what makes the CI case legible: when
        the clone declares a machine-local `CLUSTER_KUBECONFIG_PATH` that no pod has, the operator
        gets the comparison — both claims, both sources — and not a stack of paths that were tried.

        A declaration that answers nothing makes the comparison moot: there is no claim to disagree
        with, which is the state "the environment does not yet address a cluster" is about, and the
        flags' whole
        reason to exist. And a run that states nothing states nothing: the file answers, as always.
        """
        stated = cls._statement_layers(layers)
        declared_context = layers[-1][0]
        declared_source = layers[-1][2]
        if declared_context is None or (stated["context"] is None and stated["kubeconfig"] is None):
            return

        if stated["context"] is not None:
            if stated["context"] == declared_context:
                return
            raise cls._stated_cluster_mismatch(
                environment,
                stated=f"{stated['context']} (stated by {stated['context_source']})",
                declared=f"{declared_context} (declared in {static_path} as {CLUSTER_KUBE_CONTEXT_KEY})",
                declared_source=declared_source,
                fix=(f"state {declared_context}, which is what the environment says, or write "
                     f"{stated['context']} into the environment: kmt config set <configuration-root> "
                     f"{environment} {CLUSTER_KUBE_CONTEXT_KEY}={stated['context']}"),
            )

        # Only the file was stated. Its claim about the cluster is the set of contexts it declares,
        # so the declared context has to be one of them — the same test reading the file would make,
        # made here so the two claims are named instead of one file being blamed for the other.
        declared_files = _kubeconfig_files(stated["kubeconfig"])
        available = _read_kubeconfig(declared_files)["contexts"]
        if declared_context in available:
            return
        listing = ", ".join(sorted(available)) if available else "it declares none"
        raise cls._stated_cluster_mismatch(
            environment,
            stated=(f"the kubeconfig {stated['kubeconfig']} (stated by "
                    f"{stated['kubeconfig_source']}), whose contexts are: {listing}"),
            declared=f"{declared_context} (declared in {static_path} as {CLUSTER_KUBE_CONTEXT_KEY})",
            declared_source=declared_source,
            fix=(f"state a context this file has, mount the kubeconfig that has {declared_context}, "
                 f"or write the run's context into the environment: kmt config set "
                 f"<configuration-root> {environment} {CLUSTER_KUBE_CONTEXT_KEY}=<the-context>"),
        )

    @staticmethod
    def _stated_cluster_mismatch(environment: str, *, stated: str, declared: str,
                                 declared_source: str, fix: str) -> StatedClusterMismatchError:
        """The sentence a stated-versus-declared disagreement deserves, spelled once for both
        shapes of it.

        Four facts and no more: the two claims, the two sources that wrote them, and the fact that
        they mean different things. The reason a run that states one cluster while the environment
        declares another is not simply resolved to the stated one is written into the sentence
        rather than left to the ordering, because the ordering is what a reader would otherwise
        quote back as an argument.
        """
        return StatedClusterMismatchError(
            f"environment '{environment}' states its cluster two ways and they disagree: the run "
            f"says {stated}; the environment says {declared}. What the run states is where the work "
            f"runs; what the configuration declares is which environment that work is for. "
            f"kubementat will not choose between them. Until {declared_source} and the statement "
            f"agree: {fix}.",
            reason=f"stated and declared cluster disagree for environment '{environment}'",
            stated=stated,
            declared=declared,
        )

    @staticmethod
    def _process_value(key: str) -> Optional[str]:
        """A layer-2 variable, treated the way every other variable in the tree is treated.

        Set-but-empty is not "unset": an empty `CLUSTER_KUBE_CONTEXT` would otherwise fall back
        to a file and then be blamed for the file. `config.py:_directory_from_environment` refuses
        the same thing, and this is the same refusal.
        """
        if key not in os.environ:
            return None
        value = os.environ[key]
        if not value.strip():
            raise ClusterNotConfiguredError(
                f"{key} is set but empty. Give it the context it names, or unset it to read "
                f"{CLUSTER_KUBE_CONTEXT_KEY} from the environment's static.json.",
                reason=f"{key} is set but empty in the process environment",
            )
        return value.strip()

    @staticmethod
    def _flag_value(value) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _nothing_declared(environment: str, static_path: Path, layers,
                          in_cluster: Optional[str] = None) -> ClusterResolutionError:
        """The refusal an operator acts on now that the current-context fallback is gone.

        The environment, the file with a path, the key, and the value that was found in it. The
        sentence also says what to do, because an operator who got here by typing `kmt install
        dev` is not reading a stack trace by choice.

        `in_cluster` is the reason the last layer could not answer, when there was a last layer to
        ask. It is a clause and not a headline: the failure the operator can act on is still the
        unset key, and a sentence that made "am I in a pod?" the main verb would be a second answer
        to a question one file already answers.
        """
        _, _, _, labels = layers[-1]
        state = "is not set"
        try:
            state = _static_declaration(static_path)["context_state"]
        except ClusterResolutionError:
            pass
        sentence = (
            f"environment '{environment}' does not address a cluster: "
            f"{CLUSTER_KUBE_CONTEXT_KEY} {state} in {static_path}. There is no current-context "
            f"fallback — kubementat installs into what the environment declares, not into what "
            f"kubectl is pointed at. Name the context in {static_path}, pass --context, or set "
            f"{CLUSTER_KUBE_CONTEXT_KEY}."
        )
        if in_cluster:
            sentence += (
                f" In-cluster resolution was considered last, and this process could not use it: "
                f"{in_cluster}."
            )
        return ClusterNotConfiguredError(
            sentence,
            reason=f"{CLUSTER_KUBE_CONTEXT_KEY} {state} in {static_path}",
        )

    @staticmethod
    def _collision(target: ClusterTarget, collisions) -> ClusterIdentityCollisionError:
        """The collision refusal: both environments, both addresses, and where to see the whole
        picture.

        The addresses are equal — that is what the check found — but they are printed per
        environment rather than once, because "equal" is what the operator needs to see for
        themselves when the two declarations differ only in how they spell one host.
        """
        claimed = "; ".join(
            [f"'{target.environment}' is {target.address}"]
            + [f"'{record.environment}' is {record.target.address}" for record in collisions]
        )
        names = ", ".join(f"'{record.environment}'" for record in collisions)
        return ClusterIdentityCollisionError(
            f"environment '{target.environment}' and {names} address the same cluster "
            f"({claimed}). Two environments must not address one cluster: kubementat will not "
            f"let a command decide which of them it meant. Run kmt environments list to see "
            f"which environment claims which cluster, then point one of them somewhere else."
        )


def _report_line(target: ClusterTarget, label: str, others, *,
                 self_address: Optional[str] = None) -> str:
    """One of the resolution report's two shapes, and only ever one of them.

    `others` is every other environment in the root, the unresolved ones included — the line has
    to be able to say who was compared and who could not be.

    *verified* is said about the environments that were actually compared, so the number is in
    the line: "verified against 1 other environment" says what was checked and against how many.
    An environment that could not be read is named with the reason instead, and the word
    *verified* does not appear, because the comparison the line would be claiming did not happen.

    `self_address` is the running cluster's exclusion made visible. An environment that resolves to
    the cluster
    this command is standing on is not a second claim, so it is not compared — and the line may
    not say *verified* about a comparison that held itself back, which is why the exclusion is
    reported as *not verified: <reason>* and never as a silent subtraction. A command
    that is not in a cluster passes `None` here and gets exactly the line it got before.
    """
    header = f"cluster {target.environment}: {target.address} ({label})"
    unresolved = tuple(record for record in others if record.target is None)
    held_back = tuple(
        record for record in others
        if record.target is not None and _addresses_the_running_cluster(record, self_address)
    )
    if unresolved or held_back:
        notes = [
            f"{record.environment} could not be resolved ({record.reason})" for record in unresolved
        ]
        notes += [
            f"{record.environment} is the cluster this command is running on ({record.target.address})"
            for record in held_back
        ]
        return f"{header} — not verified: {'; '.join(notes)}"
    compared = tuple(record for record in others if record.target is not None)
    if not compared:
        return f"{header} — not verified: no other environment in this root declares a cluster"
    plural = "environment" if len(compared) == 1 else "environments"
    return f"{header} — verified against {len(compared)} other {plural}"
