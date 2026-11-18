"""The cluster-shaped half of a throwaway checkout: environments that declare a cluster, and the
kubeconfigs that contain them.
"""

import json
from pathlib import Path

import yaml

from kubementat.core.config.config import Config
from kubementat.core.constants import SOURCE_CONNECTION, STATIC_SOURCE_PREFIX
from kubementat.core.identity import kubeconfig as _kubeconfig_module
from kubementat.core.identity.context import Context
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
    """The cluster identity a recorded client call carries, in the harness's own spelling."""
    from recorded_operations import UnaddressedOperationError, cluster_named

    named = cluster_named(context=target.context, kubeconfig=target.kubeconfig_path)
    if named is None:
        raise UnaddressedOperationError(
            f"{target!r} addresses a cluster through {target.source}, which names neither a "
            f"kubecontext nor a file, so a call made through it cannot say which cluster it asked. "
            f"The recorder refuses to guess, and one failure class for "
            f"\"which cluster?\" is better than two.")
    return named


def configuration_only_cluster(environment: str) -> str:
    """The cluster `configuration_only_context(environment, team)` addresses, as the record spells it.
    """
    from recorded_operations import cluster_named

    return cluster_named(context=environment)


def write_kubeconfig(path: Path, contexts: dict, *, current_context=None, exec_plugin=None) -> Path:
    """A kubeconfig with one cluster per `{context: server}` entry, plus a `current-context`."""
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
    """A kubeconfig whose clusters are spelled out, for the cases a cluster is missing from."""
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
    """`platform_config/{environment}/` as `kmt initialize` leaves it, plus what it declares."""
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
    """Point every path dial at `root`, so nothing in the test can reach this checkout."""
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
    """Fabricate the files the kubelet mounts, under `root`, and return the directory."""
    mount = Path(root) / "serviceaccount"
    mount.mkdir(parents=True, exist_ok=True)
    if token is not None:
        (mount / "token").write_text(token, encoding="utf-8")
    if ca is not None:
        (mount / "ca.crt").write_text(ca, encoding="utf-8")
    return mount


def mounted_service_account(monkeypatch, root, *, host="10.0.0.1", port="6443",
                            token=MOUNTED_TOKEN) -> str:
    """Say, in both halves, that this process is running on a cluster, and return its address."""
    mount = service_account_mount(root, token=token)
    monkeypatch.setattr(_kubeconfig_module, "SERVICE_ACCOUNT_ROOT", mount)
    monkeypatch.setenv(KUBERNETES_SERVICE_HOST_ENV, host)
    monkeypatch.setenv(KUBERNETES_SERVICE_PORT_ENV, port)
    return f"https://{host}:{port}"


def outside_a_cluster(monkeypatch) -> Path:
    """The other answer: this process is not inside a cluster, whatever the machine says."""
    mount = Path("/not-a-pod-this-process-has-no-service-account")
    monkeypatch.setattr(_kubeconfig_module, "SERVICE_ACCOUNT_ROOT", mount)
    monkeypatch.delenv(KUBERNETES_SERVICE_HOST_ENV, raising=False)
    monkeypatch.delenv(KUBERNETES_SERVICE_PORT_ENV, raising=False)
    return mount


def stamp_tree(root: Path) -> Path:
    """Write `<root>/platform_config/kubementat.yaml` and hand the path back."""
    from kubementat.core import constants
    path = Path(root) / "platform_config" / constants.CONFIG_MANIFEST_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'kubementat: "{constants.CONFIG_KUBEMENTAT_CONSTRAINT}"\n'
                    f"schema_version: {constants.CONFIG_SCHEMA_VERSION}\n")
    return path


def checkout(tmp_path: Path) -> Path:
    """The root of the throwaway tree, with the one directory `Config` insists on."""
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
    """One `Context` that carries a `Config` and no cluster claim."""
    if config is None:
        config = Config(environment, team, platform_config_dir=platform_config_dir,
                        preload_configs=False)
    return Context(environment=environment, team=team, config=config,
                   target=ClusterTarget(environment, environment, None, UNADDRESSED_SERVER,
                                        SOURCE_CONNECTION))
