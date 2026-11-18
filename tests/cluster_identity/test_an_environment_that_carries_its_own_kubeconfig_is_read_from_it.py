"""An environment's own kubeconfig answers ahead of `$KUBECONFIG`."""

from pathlib import Path

import yaml

from kubementat.core.identity.context import Context

CONTEXT = "in-tree-cluster"
IN_TREE_ADDRESS = "https://in-tree.example.test:6443"
AMBIENT_ADDRESS = "https://ambient.example.test:6443"


def kubeconfig(address: str, context: str = CONTEXT) -> str:
    """A one-context kubeconfig, in the shape the loader reads."""
    return yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "c", "cluster": {"server": address}}],
        "contexts": [{"name": context, "context": {"cluster": "c", "user": "u"}}],
        "users": [{"name": "u", "user": {"token": "not-a-real-token-see-the-address"}}],
    })


def write_kubeconfig(path: Path, address: str) -> Path:
    """One kubeconfig at `path`, addressed at `address`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kubeconfig(address))
    return path


def environment_with_kubeconfig(fixture_repo, *, in_tree: bool, address=IN_TREE_ADDRESS,
                                declare_path: bool = False, declared_address=None):
    """A tree whose environment declares a context and *no* path, with or without its own kubeconfig.
    """
    keys = {"CLUSTER_KUBE_CONTEXT": CONTEXT, "CLUSTER_KUBECONFIG_PATH": None}
    if in_tree:
        write_kubeconfig(
            fixture_repo.platform_config / fixture_repo.environment / "kubeconfig.encrypted.yaml",
            address)
    if declare_path:
        keys["CLUSTER_KUBECONFIG_PATH"] = str(write_kubeconfig(
            fixture_repo.root / "declared.kubeconfig", declared_address or address))
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "static.json",
                            keys)
    return fixture_repo


# ------------------------------------------------------------------ the tree's file answers first


def test_an_environment_kubeconfig_is_read_ahead_of_the_variable(fixture_repo, monkeypatch):
    """The whole decision, in one assert: the address comes from the tree and not from the shell."""
    environment_with_kubeconfig(fixture_repo, in_tree=True)
    monkeypatch.setenv("KUBECONFIG", str(write_kubeconfig(
        fixture_repo.root / "ambient.kubeconfig", AMBIENT_ADDRESS)))

    target = Context.build("dev", platform_config_dir=str(fixture_repo.platform_config)).target

    assert target.address == IN_TREE_ADDRESS, target.address
    assert target.kubeconfig_path.name == "kubeconfig.encrypted.yaml", target.kubeconfig_path


def test_a_declared_path_still_beats_the_environment_kubeconfig(fixture_repo, monkeypatch):
    """The tree answers where nobody said anything, and that is all it does."""
    environment_with_kubeconfig(fixture_repo, in_tree=True, address=IN_TREE_ADDRESS,
                                declare_path=True, declared_address=AMBIENT_ADDRESS)
    monkeypatch.delenv("KUBECONFIG", raising=False)

    target = Context.build("dev", platform_config_dir=str(fixture_repo.platform_config)).target

    assert target.address == AMBIENT_ADDRESS, target.address


# ---------------------------------------------------------------- the name is the encryption


# -------------------------------------------------------------------- init does not quarantine it
