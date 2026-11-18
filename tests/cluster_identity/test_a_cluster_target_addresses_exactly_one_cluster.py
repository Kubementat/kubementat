"""A cluster is described once, and the description travels with the address it resolved to."""


import pytest
from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    checkout,
    redirect_checkout,
    write_kubeconfig,
)

from kubementat.core.identity.target import ClusterTarget
from kubementat.errors import ClusterNotConfiguredError

CONTEXTS = {"dev": DEV_SERVER, "prod-europe": PROD_SERVER}


def test_an_environment_that_names_no_context_is_refused_instead_of_taking_whichever_kubectl_sees(
    tmp_path, monkeypatch
):
    """A key nothing declared is a refusal at the object boundary too."""
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS, current_context="dev")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        ClusterTarget.from_kubeconfig(str(kubeconfig), None, "dev")

    assert "current-context" in str(refused.value)
    assert "fallback" in str(refused.value)


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_context_that_declared_nothing_is_not_an_addressable_cluster(blank, tmp_path, monkeypatch):
    """Blank in any shape is the same refusal: the operator declared nothing."""
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS)

    with pytest.raises(ClusterNotConfiguredError):
        ClusterTarget.from_kubeconfig(str(kubeconfig), blank, "dev")


def test_a_home_relative_path_is_expanded_once_and_stays_the_file_that_was_read(tmp_path, monkeypatch):
    """`~/kube/prod` in a `static.json` is a real path, and it is recorded as the file, not as a clue.
    """
    home = tmp_path / "home"
    (home / "kube").mkdir(parents=True)
    kubeconfig = write_kubeconfig(home / "kube" / "prod", {"prod": PROD_SERVER})
    redirect_checkout(monkeypatch, checkout(tmp_path))
    monkeypatch.setenv("HOME", str(home))  # what `os.path.expanduser` consults

    target = ClusterTarget.from_kubeconfig("~/kube/prod", "prod", "prod")

    assert target.address == PROD_SERVER
    assert target.kubeconfig_path == kubeconfig


def test_a_connection_target_that_cannot_name_a_host_is_a_programming_error():
    """An addressless target addresses nothing, so a blank host is not a configuration state."""
    with pytest.raises(ValueError):
        ClusterTarget.from_connection("", None, "a-token", "library")
    with pytest.raises(ValueError):
        ClusterTarget.from_connection("https://lib.example:6443", None, None, "library")
    with pytest.raises(ValueError):
        ClusterTarget.from_connection("https://lib.example:6443", None, ("only-a-cert",), "library")
