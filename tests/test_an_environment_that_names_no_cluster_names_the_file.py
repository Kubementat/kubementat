"""The environment that does not name a cluster names the file that should have contained it.

With the `current-context` fallback gone (the declared value is now the answer), the error is the
interface. What the message must contain is what these tests read the message the
way an operator would:

* the environment,
* the file it was read from, with a path,
* the key and the value found in it,
* and what to do next.

The three states — absent, null, present — are also distinguished on purpose. A template
that ships `"CLUSTER_KUBE_CONTEXT": null` is what `kmt initialize` leaves behind (the
environment exists, and it does not yet address a cluster), and a file that was hand-edited until
the key disappeared is a different file with a different story to tell.

"""

import json
import pytest

from kubementat.core.identity.context import Context
from kubementat.errors import ClusterNotConfiguredError

from cluster_fixture import DEV_SERVER, checkout, redirect_checkout, write_environment, write_kubeconfig


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A checkout with a kubeconfig that answers, and one environment that does not declare.

    The kubeconfig is pointed at by `KUBECONFIG` so a fallback would find it: every test in here
    asks whether the refusal happened *instead of* that answer.
    """
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
    """The two nouns a message has to have before the operator can act on it.

    Everything else in the sentence is advice; without the environment and the path it is advice
    about a file the operator will have to find themselves.
    """
    static_path = root / "platform_config" / "dev" / "static.json"

    refused = _refusal_for(root)

    assert "'dev'" in refused
    assert str(static_path) in refused


def test_the_key_and_the_null_value_are_named_because_the_template_shipped_them(tmp_path, monkeypatch):
    """A freshly initialized environment is *intentionally* invalid, and the message says which.

    `null` is what `templates/environment/static.json.template` carries, so the sentence that
    reads "the key is null" sends the operator to edit the file. One that reads "the key is not
    set" would send them to write it, and they would find nothing there to write over.
    """
    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    static_path = write_environment(root, "dev", context=None)
    key = json.loads((static_path / "static.json").read_text())["CLUSTER_KUBE_CONTEXT"]
    assert key is None  # the shape the template ships, asserted here so the test below means something

    refused = _refusal_for(root)

    assert "CLUSTER_KUBE_CONTEXT" in refused
    assert "null" in refused
    assert str(static_path / "static.json") in refused


def test_a_key_that_was_never_written_says_it_is_not_set_and_not_that_it_is_null(root):
    """Absent and null both refuse, but they do not read the same in the file.

    A message that says `null` about a file that never had the key is a message that will be
    believed about the wrong file, which is the kind of overstatement this project rules out.
    """
    static_path = root / "platform_config" / "dev" / "static.json"
    static_path.parent.mkdir(parents=True, exist_ok=True)
    static_path.write_text(json.dumps({"ENVIRONMENT": "dev"}), encoding="utf-8")
    (static_path.parent / "static.encrypted.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert "is not set" in str(refused.value)
    assert "is null" not in str(refused.value)


def test_a_value_left_empty_by_hand_is_refused_as_empty(root):
    """The third shape: a key present with nothing in it.

    Empty is not the same mistake as null — somebody typed something and dropped the value. The
    message says which, so the fix is the edit they actually need.
    """
    static_path = write_environment(root, "dev", context="dev")
    document = json.loads((static_path / "static.json").read_text())
    document["CLUSTER_KUBE_CONTEXT"] = "  "
    static_path.joinpath("static.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert "empty" in str(refused.value)
    assert str(static_path / "static.json") in str(refused.value)


def test_the_environment_that_has_no_configuration_at_all_names_the_file_that_is_missing(root):
    """A directory that was never initialized, as opposed to one initialized and unfilled.

    `Config` raises a bare `FileNotFoundError` for this; the refusal is what turns it into a
    sentence with the path and the command that would have created the file.
    """
    (root / "platform_config" / "dev").mkdir(parents=True)

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert str(root / "platform_config" / "dev" / "static.json") in str(refused.value)
    assert "kmt config init" in str(refused.value)


def test_the_refusal_says_what_kubementat_will_not_do_instead(root):
    """The sentence that keeps the removed fallback out of the operator's head.

    Someone who has used kubementat before will look for the fallback that is not there, and an
    error that does not say "this is deliberate" is an error they will route around by pointing
    kubectl at the cluster they meant.
    """
    refused = _refusal_for(root)

    assert "no current-context fallback" in refused
    assert "kmt install" not in refused
