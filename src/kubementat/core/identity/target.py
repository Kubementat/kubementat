"""The one description of a cluster: *which* one, *where* it was declared, and its address.

This is `ClusterTarget`, and the two argv builders that stand below it and spell its address for
the binaries that still run — here rather than in a generic `core/kubectl.py` because both read
this class's fields, and a helper that reads a class's fields belongs beside it.
`ClusterTarget` itself moved out of `cluster.py` whole — decorator, field order, docstrings, the
lot — because the value of the class is that there is exactly one of it: the
`isinstance(ctx_or_target, ClusterTarget)` branch of `KubernetesUtils.__init__` answers a question
about a class object, and two class objects reached by two import paths answer it `False` in a way
that reads as a refusal about configuration. So there is one home, one binding per importer, and no
`cluster.py` copy standing behind the real one.

Two shapes here are load-bearing and do not follow from the rest of the file:

* **`address` is a field, not a property.** A check that must ask for the address is a check that
  can be skipped by a call site; a field is compared by the same code that compares everything
  else. The address is therefore only ever as current as the file it was read from, and building a
  target never makes a network call.
* **the credential material is not a field.** `from_connection` hangs it off the instance under
  `_CONNECTION_FIELD`, `__repr__` drops it, and `client()` hands it to the library. Both names are
  owned by `kubeconfig.py`, one level down — where the document builder that reads them lives too
  — and this module reaches them across that edge rather than holding a copy.

What this module does *not* answer is which layer said a cluster: the flag, the process, the
environment's own `static.json`, the mounted service account. That ordering belongs above it, and
the class is what those layers all build. It imports `kubeconfig` (the leaf), `constants`, and the
kubernetes library inside `client()` only — never `core/identity/context.py`, which is the module
that stands above it and builds the object this one is read out of.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from kubementat.core import constants
from kubementat.core.identity import kubeconfig as _kubeconfig
# Bound by name rather than as `_kubeconfig._connection_document` for one reason: the class body
# below is the source it had in `cluster.py`, and the only way to keep a verbatim move verbatim is
# to import the name it calls. It is the same function object `kubeconfig.py` owns, nothing patches
# it, and `kubeconfig.py` is below this one, so the edge still points down.
from kubementat.core.identity.kubeconfig import _connection_document
from kubementat.errors import (
    ClusterNotConfiguredError,
    ServiceAccountNotMountedError,
)


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
        source: str = constants.SOURCE_FLAG,
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
                f"{constants.CLUSTER_KUBE_CONTEXT_KEY}.",
                reason=f"{constants.CLUSTER_KUBE_CONTEXT_KEY} is not set for '{environment}'",
            )
        files = _kubeconfig._kubeconfig_files(None if path is None else str(path))
        target = cls(
            environment=environment,
            context=str(context).strip(),
            kubeconfig_path=None if path is None else Path(path).expanduser(),
            address=_kubeconfig._address_in_kubeconfig(files, str(context).strip()),
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
        source: str = constants.SOURCE_CONNECTION,
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
            address=_kubeconfig._normalise_address(str(host)),
            source=source,
        )
        object.__setattr__(target, _kubeconfig._CONNECTION_FIELD, material)
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
        address = _kubeconfig._service_account_address(mount_root)
        token_path, ca_path = _kubeconfig._service_account_files(mount_root)
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
            address, ca_path, token, environment, source=constants.SOURCE_SERVICE_ACCOUNT
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

        material = getattr(self, _kubeconfig._CONNECTION_FIELD, None)
        if material is not None:
            configuration = client.Configuration()
            kubernetes_config.load_kube_config_from_dict(
                _connection_document(self.address, material),
                context=_kubeconfig._CONNECTION_DOCUMENT_CONTEXT,
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
            config_file=_kubeconfig._library_config_file(self.kubeconfig_path),
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
        credentials = getattr(self, _kubeconfig._CONNECTION_FIELD, None)
        redacted = "" if credentials is None else ", credentials='<redacted>'"
        kubeconfig = "None" if self.kubeconfig_path is None else f"'{self.kubeconfig_path}'"
        return (
            f"ClusterTarget(environment={self.environment!r}, context={self.context!r}, "
            f"kubeconfig_path={kubeconfig}, address={self.address!r}, source={self.source!r}"
            f"{redacted})"
        )

# --------------------------------------------------------------------------
# The address spelled for the two binaries that still run. Both are here and not in a
# generic kubectl helper for the reason the bodies give: they read `ClusterTarget` fields
# and refuse when the target names neither a context nor a file, so which class answers
# the question is what the answer is made of. Moved whole out of `kubernetes_utils.py`
# (P2-4), bodies verbatim, so that the module which says what a cluster is also says how
# it is addressed -- and so that `kubernetes_utils.py`, which is being left to be split,
# stops being the place a decision about addressing lives.
# --------------------------------------------------------------------------

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
