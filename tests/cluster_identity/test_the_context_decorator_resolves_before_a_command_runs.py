"""What `@kmt_context` does before a command does anything: resolve, check, say it."""

from click.testing import CliRunner
from cluster_fixture import (
    DEV_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from cli.commands import kubernetes


def resolving_root(tmp_path, monkeypatch, environments=(("dev", DEV_SERVER),)):
    """A checkout whose environments each address their own cluster."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    contexts = {f"context-of-{name}": server for name, server in environments}
    kubeconfig = write_kubeconfig(root / "kubeconfig", contexts)
    for name, _server in environments:
        write_environment(root, name, context=f"context-of-{name}", kubeconfig_path=kubeconfig)
    # The environment is named as the first positional of the command these tests run, so the
    # variable below is only ever the fallback — it is still pinned, because every command in the
    # suite that takes no argument reads it and this file should not be able to resolve a cluster by
    # accident through it.
    monkeypatch.setenv("ENVIRONMENT", environments[0][0])
    return root


def run(*arguments):
    """`kmt kubernetes pod-images dev <namespace>`, which takes a namespace and nothing else."""
    return CliRunner(mix_stderr=False).invoke(
        kubernetes.pod_images, ["dev", "some-namespace", *arguments])


def test_the_environment_that_took_the_command_names_the_sibling_it_could_not_read(tmp_path,
                                                                                   monkeypatch,
                                                                                   recorded_api):
    """The report's second shape, and the reason it is not allowed to leave the row out."""
    root = resolving_root(tmp_path, monkeypatch)
    write_environment(root, "staging", context="staging-eu", kubeconfig_path=root / "kubeconfig")

    result = run()

    assert result.exit_code == 0, result.stderr
    assert "not verified" in result.stderr
    assert "staging could not be resolved" in result.stderr, result.stderr
    assert "no such context 'staging-eu'" in result.stderr, result.stderr


def test_a_cluster_two_environments_have_both_claimed_refuses_the_command_and_writes_nothing(
        tmp_path, monkeypatch, recorded_api):
    """RC-3: no override, no second guess, no read. Both environments, both addresses, named."""
    resolving_root(tmp_path, monkeypatch,
                   environments=[("dev", DEV_SERVER), ("prod", DEV_SERVER)])

    result = run()

    assert result.exit_code == 1, result.stderr
    assert "address the same cluster" in result.stderr, result.stderr
    assert "'dev'" in result.stderr and "'prod'" in result.stderr, result.stderr
    assert "kmt environments list" in result.stderr, "the refusal has to say where to look next"
    assert recorded_api.methods() == [], "a refused command reached the cluster anyway"


def test_a_command_that_cannot_name_a_cluster_never_builds_a_client(tmp_path, monkeypatch,
                                                                    recorded_api):
    """RC-1, in the direction that matters: nothing in `src/` can read a cluster it did not name."""
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, "dev", context=None)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    result = run()

    assert result.exit_code == 1, result.stderr
    assert "does not address a cluster" in result.stderr, result.stderr
    assert "static.json" in result.stderr
    assert recorded_api.methods() == [], "the command reached a cluster it could not name"
