"""The environment that does not name a cluster names the file that should have contained it."""

import json

import pytest
from cluster_fixture import (
    DEV_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from kubementat.core.identity.context import Context
from kubementat.errors import ClusterNotConfiguredError


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A checkout with a kubeconfig that answers, and one environment that does not declare."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_kubeconfig(root / "kubeconfig", {"dev": DEV_SERVER})
    return root


def _refusal_for(root, environment="dev"):
    """Build a command against `environment` and hand back its refusal, as a sentence."""
    write_environment(root, environment, context=None)
    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build(environment, "the-team")
    return str(refused.value)


def test_the_environment_that_declared_nothing_says_so_by_environment_name_and_file(root):
    """The two nouns a message has to have before the operator can act on it."""
    static_path = root / "platform_config" / "dev" / "static.json"

    refused = _refusal_for(root)

    assert "'dev'" in refused
    assert str(static_path) in refused


def test_a_key_that_was_never_written_says_it_is_not_set_and_not_that_it_is_null(root):
    """Absent and null both refuse, but they do not read the same in the file."""
    static_path = root / "platform_config" / "dev" / "static.json"
    static_path.parent.mkdir(parents=True, exist_ok=True)
    static_path.write_text(json.dumps({"ENVIRONMENT": "dev"}), encoding="utf-8")
    (static_path.parent / "static.encrypted.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert "is not set" in str(refused.value)
    assert "is null" not in str(refused.value)


def test_a_value_left_empty_by_hand_is_refused_as_empty(root):
    """The third shape: a key present with nothing in it."""
    static_path = write_environment(root, "dev", context="dev")
    document = json.loads((static_path / "static.json").read_text())
    document["CLUSTER_KUBE_CONTEXT"] = "  "
    static_path.joinpath("static.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert "empty" in str(refused.value)
    assert str(static_path / "static.json") in str(refused.value)


def test_the_environment_that_has_no_configuration_at_all_names_the_file_that_is_missing(root):
    """A directory that was never initialized, as opposed to one initialized and unfilled."""
    (root / "platform_config" / "dev").mkdir(parents=True)

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert str(root / "platform_config" / "dev" / "static.json") in str(refused.value)
    assert "kmt config init" in str(refused.value)
