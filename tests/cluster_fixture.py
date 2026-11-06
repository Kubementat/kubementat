"""The cluster-shaped half of a throwaway checkout: environments that declare a cluster, and the
kubeconfigs that contain them.

Everything here writes into a `tmp_path` and points `KUBEMENTAT_HOME` at it, so no test that uses
it can read this checkout's `platform_config/` or a developer's `~/.kube/config`. That is the same
shape `tests/conftest.py`'s `fixture_repo` uses, narrowed to the one question the cluster layer
asks: which environment declares which cluster, and in which file.

Two conventions make the tests readable:

* **One cluster per context.** `write_kubeconfig` takes `{context: server}` and gives each context
  its own cluster, so a test says "these two contexts reach the same address" by writing the same
  server string twice, which is exactly what the 1:1 check compares.
* **A kubeconfig here always has a `current-context`.** The fallback that used to resolve the
  cluster from the running kubectl context is gone, so it is the one thing a test can only prove by
  having the answer available and checking that nothing took it, so the fixtures supply it on
  purpose.
"""

import json
import os
from pathlib import Path

import yaml

from kubementat.cluster import Context
from kubementat.core.config.config import Config
from kubementat.core.constants import SOURCE_CONNECTION, STATIC_SOURCE_PREFIX
from kubementat.core.identity import kubeconfig as _kubeconfig_module
from kubementat.core.identity.kubeconfig import (
    KUBERNETES_SERVICE_HOST_ENV,
    KUBERNETES_SERVICE_PORT_ENV,
)
from kubementat.core.identity.target import ClusterTarget

# The credentials a fixture kubeconfig carries. They are never real, and one test's whole job is
# to prove none of these strings ever reaches a `repr`.
FIXTURE_TOKEN = "the-token-a-fixture-carries-and-a-repr-must-not"

# Where a fixture environment's cluster lives, per context name.
DEV_SERVER = "https://10.0.1.9:6443"
PROD_SERVER = "https://10.0.5.1:6443"
STAGING_SERVER = "https://10.0.7.2:6443"

# The variables the second layer of the resolution order reads, plus the two the kubelet injects
# someone's shell — or by a CI runner that is itself a pod — they would answer every test from
# outside the test, which is the one way a resolution-order test can go quietly wrong. The
# kubelet's pair decides whether the last layer of the order is available at all, so it belongs in
# same clearing loop for the same reason.
PROCESS_VARIABLES = ("CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH",
                     KUBERNETES_SERVICE_HOST_ENV, KUBERNETES_SERVICE_PORT_ENV)

# The cluster a unit test addresses when it is not contacting one. Building a
# `ClusterTarget` is a constructor call, so a test that records the API calls it would have made
# needs no kubeconfig file, no `$KUBECONFIG`, and nothing on the machine running pytest — which is
# the whole reason the `load_kube_config` monkeypatches are gone rather than rewritten. The address
# is unreachable by construction, so a green test that records still proves nothing was contacted.
TARGET_DEV = ClusterTarget(environment="dev", context="dev", kubeconfig_path=None,
                           address=DEV_SERVER,
                           source=f"{STATIC_SOURCE_PREFIX}platform_config/dev/static.json")


def target_cluster(target) -> str:
    """The cluster identity a recorded client call carries, in the harness's own spelling.

    The same rule `cluster_parity.cluster_identity` applies to a `kubectl` argv — the context when
    there is one, the file when there is no context — so that a client call and a binary call say
    the same thing about the same run and can be compared. The address is deliberately not
    the identity: the `kubectl` side never carries one, and the whole point of the field is that
    both sides can be read out of what the call actually did.

    A target that names neither is a target the harness cannot name, and it says so instead of
    recording an operation that looks addressed. That is the case the queue leaves to the end: a
    `ClusterTarget` built from a connection document has a host and no context.
    """
    from cluster_parity import ParityError, cluster_named

    named = cluster_named(context=target.context, kubeconfig=target.kubeconfig_path)
    if named is None:
        raise ParityError(
            f"{target!r} addresses a cluster through {target.source}, which names neither a "
            f"kubecontext nor a file, so a call made through it cannot say which cluster it asked. "
            f"The recorder refuses to guess, and one failure class for "
            f"\"which cluster?\" is better than two.")
    return named


def configuration_only_cluster(environment: str) -> str:
    """The cluster `configuration_only_context(environment, team)` addresses, as the record spells it.

    A test that drives a service class with that helper has a target whose context is the
    environment's own name (see the caveat written into `configuration_only_context`), and the
    record says so. Where the test also compares the *other* channel — a record of what a
    deleted script asked — this is the value that record has to be declared with, so the two sides
    state the same cluster and a drift in the code's target is the only way they can disagree.

    It is a value read out of the fixture and not a claim that a cluster was contacted: the address
    behind it is still the sentinel that addresses nothing.
    """
    from cluster_parity import cluster_named

    return cluster_named(context=environment)


def write_kubeconfig(path: Path, contexts: dict, *, current_context=None, exec_plugin=None) -> Path:
    """A kubeconfig with one cluster per `{context: server}` entry, plus a `current-context`.

    `exec_plugin` is a command the file names as a credential plugin for the single user. It is
    there so a test can prove that resolving an address does not run one, and that a target built
    from this file is not secretly a call to a binary.
    """
    path = Path(path)
    clusters = []
    context_entries = []
    for context_name, server in contexts.items():
        # The cluster name is derived from the context rather than from a counter so two fixtures
        # merged into one `$KUBECONFIG` list do not collide on a name by accident — which would
        # make the merge rule under test invisible.
        clusters.append({"name": f"cluster-of-{context_name}", "cluster": {"server": server}})
        context_entries.append({
            "name": context_name,
            "context": {"cluster": f"cluster-of-{context_name}", "user": "fixture-admin"},
        })
    user = {"token": FIXTURE_TOKEN}
    if exec_plugin:
        user = {"exec": {
            "command": str(exec_plugin),
            "args": [],
            "provideClusterInfo": False,
        }}
    document = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": current_context or (next(iter(contexts)) if contexts else None),
        "clusters": clusters,
        "contexts": context_entries,
        "users": [{"name": "fixture-admin", "user": user}],
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def write_kubeconfig_with_cluster(path: Path, contexts: dict, clusters: dict) -> Path:
    """A kubeconfig whose clusters are spelled out, for the cases a cluster is missing from.

    `contexts` maps a context name to a cluster *name*, `clusters` maps that name to what the file
    says about it. That is the raw shape of the file, so a test can leave the cluster entry out,
    or leave its `server` out, without hand-writing YAML.
    """
    path = Path(path)
    document = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": next(iter(contexts), None),
        "clusters": [{"name": name, "cluster": body} for name, body in clusters.items()],
        "contexts": [
            {"name": name, "context": {"cluster": cluster, "user": "fixture-admin"}}
            for name, cluster in contexts.items()
        ],
        "users": [{"name": "fixture-admin", "user": {"token": FIXTURE_TOKEN}}],
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def write_environment(root: Path, environment: str, context=None, kubeconfig_path=None) -> Path:
    """`platform_config/{environment}/` as `kmt initialize` leaves it, plus what it declares.

    A `None` context writes the `null` the template ships, which is the state kubementat calls
    *intentionally invalid*: the environment exists, and it does not yet address a cluster. Pass a
    string to have it declare one. `kubeconfig_path` is written only when given, because an absent
    key and a null one have to stay different things.
    """
    directory = Path(root) / "platform_config" / environment
    directory.mkdir(parents=True, exist_ok=True)
    declaration = {
        "ENVIRONMENT": environment,
        "CLUSTER_MANAGER_EMAIL": "cluster-manager@example.test",
        "CLUSTER_KUBE_CONTEXT": context,
    }
    if kubeconfig_path is not None:
        declaration["CLUSTER_KUBECONFIG_PATH"] = str(kubeconfig_path)
    (directory / "static.json").write_text(json.dumps(declaration, indent=2), encoding="utf-8")
    # Config opens both environment-level files; the encrypted one is empty for these purposes and
    # has to exist or the read fails for a reason no test is about.
    (directory / "static.encrypted.json").write_text("{}", encoding="utf-8")
    return directory


def redirect_checkout(monkeypatch, root: Path) -> Path:
    """Point every path dial at `root`, so nothing in the test can reach this checkout.

    Both roots are named, not located: `KUBEMENTAT_HOME` for the tree's assets, and
    `KUBEMENTAT_PLATFORM_CONFIG_DIR` for the configuration it reads. Clearing the second is what
    makes fatal — a `Config` with no root refuses rather than reading `platform_config` out of
    wherever the wheel sits — so a fixture that wants its own tree read has to say so here.

    `KUBECONFIG` is pointed at a file inside `root` that does not exist unless a test writes it:
    a test that forgets to say which kubeconfig it means then fails with the file's name rather
    than quietly resolving the developer's own cluster.
    """
    root = Path(root)
    monkeypatch.setenv("KUBEMENTAT_HOME", str(root))
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(root / "platform_config"))
    for name in PROCESS_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KUBECONFIG", str(root / "kubeconfig"))
    # The last layer of the resolution order asks whether this process is inside a cluster, and the
    # honest answer
    # for every test that does not say otherwise is no. Asserting that here rather than in each
    # test is what stops a suite that happens to run inside a pod from answering a question none
    # of its tests asked.
    outside_a_cluster(monkeypatch)
    return root


# The credential material a fabricated mount carries. Neither is real; one test's whole job is to
# prove the first never reaches a `repr`.
MOUNTED_TOKEN = "the-token-a-mounted-service-account-carries-and-a-repr-must-not"
MOUNTED_CA_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "ZmFrZWNVQXV0aG9yaXR5Rm9yVGhlVW5pdFN1aXRlT25l\n"
    "-----END CERTIFICATE-----\n"
)


def service_account_mount(root, *, token=MOUNTED_TOKEN, ca=MOUNTED_CA_PEM) -> Path:
    """Fabricate the files the kubelet mounts, under `root`, and return the directory.

    A unit test is not a pod, so the material in-cluster resolution reads has to be written to disk
    somewhere. That is why setting only the host variable is not enough: the constructor's answer
    comes from the files, and a test that never writes them is asserting about an absent mount.

    Passing `ca=None` or `token=None` leaves that one file out, which is how a test asks about a
    half-mounted pod without pretending to be one.
    """
    mount = Path(root) / "serviceaccount"
    mount.mkdir(parents=True, exist_ok=True)
    if token is not None:
        (mount / "token").write_text(token, encoding="utf-8")
    if ca is not None:
        (mount / "ca.crt").write_text(ca, encoding="utf-8")
    return mount


def mounted_service_account(monkeypatch, root, *, host="10.0.0.1", port="6443",
                            token=MOUNTED_TOKEN) -> str:
    """Say, in both halves, that this process is running on a cluster, and return its address.

    Two halves, because the code looks in two places: the material on disk and the variables the
    kubelet injects. Patching only the files would leave the API server unnamed; setting only the
    variables would name a server whose credentials were never mounted — and by the same rule that
    second one is not a test of this path.

    Returns the address a target built from it carries, so a test can compare a declared
    environment against the running cluster without spelling the string out twice.

    The root is patched on `kubementat.core.identity.kubeconfig`, the module that owns the name and
    that `cluster.py` therefore reads through, and not on `kubementat.cluster`. Patching the module
    the constant left would leave this fixture believing it had unmounted the pod while the layer
    under test read the real `/var/run/secrets/kubernetes.io/serviceaccount` off the machine — a
    green run that proved nothing about the layer it was about.
    """
    mount = service_account_mount(root, token=token)
    monkeypatch.setattr(_kubeconfig_module, "SERVICE_ACCOUNT_ROOT", mount)
    monkeypatch.setenv(KUBERNETES_SERVICE_HOST_ENV, host)
    monkeypatch.setenv(KUBERNETES_SERVICE_PORT_ENV, port)
    return f"https://{host}:{port}"


def outside_a_cluster(monkeypatch) -> Path:
    """The other answer: this process is not inside a cluster, whatever the machine says.

    The mount root is pointed at a path that cannot exist rather than left alone, so a suite
    running on a real cluster and a suite running on a laptop agree.
    """
    mount = Path("/not-a-pod-this-process-has-no-service-account")
    monkeypatch.setattr(_kubeconfig_module, "SERVICE_ACCOUNT_ROOT", mount)
    monkeypatch.delenv(KUBERNETES_SERVICE_HOST_ENV, raising=False)
    monkeypatch.delenv(KUBERNETES_SERVICE_PORT_ENV, raising=False)
    return mount


def stamp_tree(root: Path) -> Path:
    """Write `<root>/platform_config/kubementat.yaml` and hand the path back.

    For a test that builds its own `platform_config` inline instead of through `checkout` or the
    fixture class. A verb that resolves a cluster asks this tree which product it was prepared for
    before it resolves anything, so an unstamped root is refused and the test fails on a sentence
    about the stamp instead of about its own subject.
    """
    from kubementat.core import constants
    path = Path(root) / "platform_config" / constants.CONFIG_MANIFEST_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'kubementat: "{constants.CONFIG_KUBEMENTAT_CONSTRAINT}"\n'
                    f"schema_version: {constants.CONFIG_SCHEMA_VERSION}\n")
    return path


def checkout(tmp_path: Path) -> Path:
    """The root of the throwaway tree, with the one directory `Config` insists on.

    The stamp is written here for the same reason `conftest.py`'s fixture class writes its own: this
    function is the whole claim that a test's root is a prepared tree, and a prepared tree says which
    product it was made for. A verb that resolves a cluster asks that question first, so a root
    without it is refused rather than trusted.
    """
    from kubementat.core import constants
    root = tmp_path / "checkout"
    (root / "platform_config").mkdir(parents=True, exist_ok=True)
    (root / "platform_config" / constants.CONFIG_MANIFEST_FILENAME).write_text(
        f'kubementat: "{constants.CONFIG_KUBEMENTAT_CONSTRAINT}"\n'
        f"schema_version: {constants.CONFIG_SCHEMA_VERSION}\n")
    return root


# The address a fabricated `ClusterTarget` carries. Nothing in `Context` reads it, and the tests
# that use the helper below address no cluster at all; it is spelled as unreachable on purpose, so
# that a change which started asking for a cluster in one of those tests would fail rather than
# quietly get an answer out of whoever is running pytest.
UNADDRESSED_SERVER = "https://this-test-addresses-no-cluster.invalid:6443"


# A root, named, that no test is expected to read.
#
# A `Config` has no root to fall back on, and a test whose subject is a document, a
# command line, or a manifest still has to build one to hold the environment and the team. The
# alternative was to seed a tree none of those tests asserts about, or to let the object resolve
# this checkout. This path does neither: it is absolute, it carries nothing, it is never created,
# and a read that outgrew its test fails on the name in the message instead of answering out of a
# working tree.
UNREAD_ROOT = Path("/a-root-this-suite-reads-no-configuration") / "platform_config"


def configuration_only_context(environment: str, team, config=None,
                               platform_config_dir=None) -> Context:
    """One `Context` that carries a `Config` and no cluster claim.

    Every service class takes a `Context` now, and most of the tests that build one assert about
    configuration — which file a key came from, which namespace a component deploys into. The
    resolution half (`Context.build`) is not what those tests need, and on a seeded tree that
    declares no `CLUSTER_KUBE_CONTEXT` it would refuse to run at all, so the object is built by its
    dataclass constructor with the real seeded `Config` inside it. That keeps the read under test
    exactly as the class performed it.

    `config=None` means "the `Config` the class would have read itself": lazy, pointed at whatever
    the fixture's environment and team say, so a test that writes a file and then builds the service
    sees the same read. Pass a `Config` explicitly only when the test is asserting about *that*
    instance — a mutated path, a preload, a second environment's file.

    `platform_config_dir` names the root that `Config` resolves when no `Config` is passed. An
    unnamed root is an error rather than a checkout, so a test that needs the object and reads no
    file from it still has to name one: that is what `UNREAD_ROOT` is for.

    `team=None` is legal and is the shape a command runs with when no team is named: environment-level configuration,
    no team directory, and a named refusal on any team-scoped read.

    The caveat is the one worth writing down, because it is a limit and not a detail: the `target`
    here is inert. A test that uses this helper asserts nothing about cluster addressing, and a
    class that began reading `ctx.target` in one of them would be doing something no test here is
    watching. If a change makes a class address a cluster, this is the wrong fixture — write the
    environment with `write_environment` and resolve it for real.

    It is inert and not *empty*, and the difference started to matter when session commands stayed
    `kubectl` calls. The context name is the environment's own, because a resolved target that
    carries none cannot hand its cluster to the `kubectl` calls still in use (`port-forward`,
    `logs`, `exec`, `config view`) — a
    call that would be refused before it reached the shim, which is a failure about the fixture and
    not about the code. The address stays the sentinel that addresses nothing, so no test can
    accidentally dial it.
    """
    if config is None:
        config = Config(environment, team, platform_config_dir=platform_config_dir,
                        preload_configs=False)
    return Context(environment=environment, team=team, config=config,
                   target=ClusterTarget(environment, environment, None, UNADDRESSED_SERVER,
                                        SOURCE_CONNECTION))
