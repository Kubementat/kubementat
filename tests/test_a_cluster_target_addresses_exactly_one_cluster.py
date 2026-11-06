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

import dataclasses
import os

import pytest

from kubementat.core.identity.target import ClusterTarget
from kubementat.core.constants import (
    SOURCE_CONNECTION,
    SOURCE_ENV,
    SOURCE_FLAG,
    STATIC_SOURCE_PREFIX,
)
from kubementat.errors import (
    ClusterFileUnreadableError,
    ClusterNotConfiguredError,
)

from cluster_fixture import (
    DEV_SERVER,
    STAGING_SERVER,
    PROD_SERVER,
    checkout,
    redirect_checkout,
    write_kubeconfig,
)

CONTEXTS = {"dev": DEV_SERVER, "prod-europe": PROD_SERVER}


def test_the_address_is_read_out_of_the_kubeconfig_and_asks_no_one_anything(tmp_path, monkeypatch):
    """Reading a server is a file read, and only a file read.

    The fixture kubeconfig names an exec credential plugin for its user. The plugin is the thing
    `load_kube_config` runs, so the marker is the proof that address resolution never went that
    way — and the monkeypatched loader is the proof it never went there at all.
    """
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    marker = root / "exec-ran"
    script = root / "credential-plugin.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    kubeconfig = write_kubeconfig(
        root / "kubeconfig", CONTEXTS, exec_plugin=script
    )

    def no_loader(*args, **kwargs):
        raise AssertionError("resolving an address must not load a client configuration")

    monkeypatch.setattr("kubernetes.config.load_kube_config", no_loader)
    target = ClusterTarget.from_kubeconfig(str(kubeconfig), "dev", "dev")

    assert target.address == DEV_SERVER
    assert not marker.exists(), "an address resolution ran the kubeconfig's credential plugin"


def test_the_address_is_the_file_the_target_read_and_not_a_lookup_of_its_own(tmp_path, monkeypatch):
    """The address is a field, so a target cannot be quietly re-pointed by the file moving.

    The reason for making it one: a check that computes the address is a check that can compute
    it at a different moment than the comparison. The file is edited after the fact here to say so.
    """
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS)
    target = ClusterTarget.from_kubeconfig(str(kubeconfig), "dev", "dev")

    write_kubeconfig(kubeconfig, {"dev": "https://10.9.9.9:6443"})

    assert target.address == DEV_SERVER
    assert any(f.name == "address" for f in dataclasses.fields(ClusterTarget)), (
        "the address is what the 1:1 check compares, so it has to be a field and not something a call site computes"
    )


def test_a_target_carries_where_the_claim_came_from(tmp_path, monkeypatch):
    """Three layers of the resolution order, one constructor, and `source` told apart by the caller."""
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS)
    static_path = root / "platform_config" / "dev" / "static.json"

    assert ClusterTarget.from_kubeconfig(str(kubeconfig), "dev", "dev").source == SOURCE_FLAG
    assert ClusterTarget.from_kubeconfig(
        str(kubeconfig), "dev", "dev", source=SOURCE_ENV
    ).source == SOURCE_ENV
    assert ClusterTarget.from_kubeconfig(
        str(kubeconfig), "dev", "dev", source=f"{STATIC_SOURCE_PREFIX}{static_path}"
    ).source == f"{STATIC_SOURCE_PREFIX}{static_path}"


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


def test_no_kubeconfig_path_means_the_libraries_own_rule_and_stays_recorded_as_none(tmp_path, monkeypatch):
    """`None` is a statement, not a gap. It says "the library's rule", and it is stored that way.

    The rule is `$KUBECONFIG`, then `~/.kube/config` — and it is applied when the target is built,
    not when this module was imported, so a process that sets the variable later gets the file it
    set (the resolution rule, and the import-time constant the fixtures in `tests/conftest.py:547`
    work around).
    """
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS)
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

    target = ClusterTarget.from_kubeconfig(None, "prod-europe", "prod")

    assert target.address == PROD_SERVER
    assert target.kubeconfig_path is None


def test_a_kubeconfig_list_is_read_as_one_document_with_the_first_file_winning(tmp_path, monkeypatch):
    """`$KUBECONFIG` can be a path-separated list, which is what a merged cluster looks like.

    The alternative — opening only the first file — would resolve a context that lives in the
    second one as "no such context", which is a wrong answer rather than a cautious one. So the
    merge is kubectl's: first declaration of a name wins, and a name that only the later file has
    resolves from that file.
    """
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    first = write_kubeconfig(root / "first", {"dev": DEV_SERVER, "shared": "https://10.0.8.1:6443"})
    second = write_kubeconfig(root / "second", {"dev": "https://10.9.9.9:6443", "staging": STAGING_SERVER})

    monkeypatch.setenv("KUBECONFIG", os.pathsep.join((str(first), str(second))))
    listed = os.pathsep.join((str(first), str(second)))

    assert ClusterTarget.from_kubeconfig(listed, "dev", "dev").address == DEV_SERVER
    assert ClusterTarget.from_kubeconfig(listed, "staging", "staging").address == STAGING_SERVER



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


def test_a_target_cannot_be_repointed_after_a_service_has_it(tmp_path, monkeypatch):
    """Frozen, because six services holding one target is the point."""
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS)
    target = ClusterTarget.from_kubeconfig(str(kubeconfig), "dev", "dev")

    with pytest.raises(Exception) as refused:  # dataclasses.FrozenInstanceError
        target.address = "https://someone-elses-cluster:6443"

    assert type(refused.value).__name__ == "FrozenInstanceError"


def test_a_kubeconfig_that_names_a_missing_file_says_which_one_it_wanted(tmp_path, monkeypatch):
    """An absent file is a named failure, and it names the key that pointed at it."""
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    with pytest.raises(ClusterFileUnreadableError) as refused:
        ClusterTarget.from_kubeconfig(str(root / "no-such-kubeconfig"), "dev", "dev")

    assert "no-such-kubeconfig" in str(refused.value)
    assert "CLUSTER_KUBECONFIG_PATH" in str(refused.value)


def test_a_connection_target_carries_no_kubeconfig_and_no_context(tmp_path, monkeypatch):
    """A target built from a connection has this shape, asserted rather than remembered: it never
    had a file to read.

    It skips the file-resolution rules, so `kubeconfig_path` is `None` by definition, there is no
    context to name, and `source` says `connection` — the label that keeps it out of the resolution order.
    """
    redirect_checkout(monkeypatch, checkout(tmp_path))

    target = ClusterTarget.from_connection("https://lib.example:6443/", None, "a-token", "library")

    assert target.source == SOURCE_CONNECTION
    assert target.kubeconfig_path is None
    assert target.context is None
    assert target.address == "https://lib.example:6443"


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


def test_a_target_binds_a_client_without_editing_the_libraries_default_configuration(tmp_path, monkeypatch):
    """`.client()` is the only behaviour the object owns, and it owns it locally.

    Two things are being pinned: the client is bound to the address and the context that were read,
    and the library's process-wide default `Configuration` is left alone — the side effect
    `kubernetes_utils.py:210` still has, and the reason two targets in one process can otherwise
    overwrite each other's cluster.
    """
    from kubernetes.client import Configuration

    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", CONTEXTS)
    before = Configuration._default

    client = ClusterTarget.from_kubeconfig(str(kubeconfig), "prod-europe", "prod").client()

    assert client.configuration.host == PROD_SERVER
    assert Configuration._default is before


def test_a_connection_target_binds_the_host_it_was_given_instead_of_a_file(tmp_path, monkeypatch):
    """The library path builds the same shape of client from credentials someone is holding.

    No kubeconfig is opened on the way, which is the whole boundary the kubeconfig's role draws: kubementat reads
    files, a library caller brings material.
    """
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    write_kubeconfig(root / "kubeconfig", CONTEXTS)
    monkeypatch.setattr(
        "kubernetes.config.load_kube_config",
        lambda *args, **kwargs: pytest.fail("a connection target must not read a kubeconfig"),
    )

    target = ClusterTarget.from_connection("https://lib.example:6443", None, "a-token", "library")

    assert target.client().configuration.host == "https://lib.example:6443"
