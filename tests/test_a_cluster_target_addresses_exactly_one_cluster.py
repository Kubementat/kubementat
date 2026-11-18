"""A cluster is described once, and the description travels with the address it resolved to.

`ClusterTarget` is the value object that says which cluster, and these are the four properties that make the object
worth having rather than a tuple someone passes around:

* **The address comes out of the file.** It is read locally, before anything that could ask a
  cluster something — no `load_kube_config`, no exec plugin, no API call. That is what makes the
  1:1 check affordable for every environment in a root, and it is why resolving
  `kmt environments list` cannot refresh a credential.
* **The address is a field.** A target built from a file that changed afterwards keeps the address
  it read. A property would make the check's answer depend on when it ran, and a comparison that
  has to be computed is a comparison that gets skipped.
* **The source says where the claim came from.** "`static.json` said so" and "the operator typed
  `--context`" are different claims, and when the wrong cluster is blamed, that difference is the
  debugging session.
* **`from_connection` is a constructor and not a resolution option.** It never consults a
  kubeconfig, so `kubeconfig_path` is `None` by definition and no exec plugin is representable.

The rule that runs underneath is the refusal to fall back: nothing here ever reads `current-context`.
"""


import pytest

from kubementat.core.identity.target import ClusterTarget
from kubementat.errors import ClusterNotConfiguredError

from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    checkout,
    redirect_checkout,
    write_kubeconfig,
)

CONTEXTS = {"dev": DEV_SERVER, "prod-europe": PROD_SERVER}


def test_an_environment_that_names_no_context_is_refused_instead_of_taking_whichever_kubectl_sees(
    tmp_path, monkeypatch
):
    """A key nothing declared is a refusal at the object boundary too.

    The fixture kubeconfig has a `current-context`, so a fallback would have produced a plausible
    answer — and that is exactly the answer kubementat is no longer allowed to give.
    """
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

    An operator writes the path the way they write it in a shell. `~` is expanded where the file is
    opened and where a client is later bound, so the two can never disagree about which file they
    mean, and the object that reports the address reports a path a human can open.
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
    """An addressless target addresses nothing, so a blank host is not a configuration state.

    The other half of its honesty: a library caller is in the credential business, not a
    kubementat operator, and the failure that names the constructor is faster than the failure
    that names a cluster that was never there.
    """
    with pytest.raises(ValueError):
        ClusterTarget.from_connection("", None, "a-token", "library")
    with pytest.raises(ValueError):
        ClusterTarget.from_connection("https://lib.example:6443", None, None, "library")
    with pytest.raises(ValueError):
        ClusterTarget.from_connection("https://lib.example:6443", None, ("only-a-cert",), "library")
