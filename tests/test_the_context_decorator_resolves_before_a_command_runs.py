"""What `@kmt_context` does before a command does anything: resolve, check, say it.

The decorator is the only place three things happen, so this file checks them in that place
and through real commands rather than against a fake command of its own:

* the layers, in order, with the command's own `--context` on top;
* the 1:1 check, which runs for every command and refuses a collision by writing nothing;
* the report, printed once, on stderr, before the body runs.

The ordering guarantee is checked the only way it can be from a CLI test — by making the resolution
fail and asking whether the cluster was reached anyway. A command that printed a report and then
made the call would be a command that told the operator what it chose and then chose something else,
and that is the failure the decorator's ordering is written to catch.
"""

from click.testing import CliRunner

from cli.commands import kubernetes
from cluster_fixture import (DEV_SERVER, checkout, redirect_checkout,
                            write_environment, write_kubeconfig)


def resolving_root(tmp_path, monkeypatch, environments=(("dev", DEV_SERVER),)):
    """A checkout whose environments each address their own cluster.

    One call per test, so the check's coverage of "who else is in this root" is something the test
    names rather than something it hopes the fixture left behind.
    """
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
    """`kmt kubernetes pod-images dev <namespace>`, which takes a namespace and nothing else.

    It is the cheapest command on the surface that reaches a cluster: one list call, answered by the
    recorder, and no team — so every assertion here is about the cluster and none of it is about a
    node's numbers. `--context` and `--kubeconfig` are what these tests pass to it.

    The environment is passed because it is the first positional now: leaving it out would put
    `some-namespace` in the environment's slot and resolve the wrong thing for the wrong reason.
    """
    return CliRunner(mix_stderr=False).invoke(
        kubernetes.pod_images, ["dev", "some-namespace", *arguments])


def test_the_environment_that_took_the_command_names_the_sibling_it_could_not_read(tmp_path,
                                                                                   monkeypatch,
                                                                                   recorded_api):
    """The report's second shape, and the reason it is not allowed to leave the row out.

    `staging` declares a context the kubeconfig does not have. The command is not blocked by that,
    and the report says so by name and reason — a check that reported only what it managed to read
    would be a check that lied about its coverage.
    """
    root = resolving_root(tmp_path, monkeypatch)
    write_environment(root, "staging", context="staging-eu", kubeconfig_path=root / "kubeconfig")

    result = run()

    assert result.exit_code == 0, result.stderr
    assert "not verified" in result.stderr
    assert "staging could not be resolved" in result.stderr, result.stderr
    assert "no such context 'staging-eu'" in result.stderr, result.stderr


def test_a_cluster_two_environments_have_both_claimed_refuses_the_command_and_writes_nothing(
        tmp_path, monkeypatch, recorded_api):
    """RC-3: no override, no second guess, no read. Both environments, both addresses, named.

    The addresses are equal by construction — the second environment was written pointing at the
    first one's server — which is the 1:1 check's whole job: no cluster is contacted to know it.
    """
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
    """RC-1, in the direction that matters: nothing in `src/` can read a cluster it did not name.

    The environment declares `null`, which is what `kmt initialize` ships. The command's
    answer is the file, the key, and the value found there — and no call.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, "dev", context=None)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    result = run()

    assert result.exit_code == 1, result.stderr
    assert "does not address a cluster" in result.stderr, result.stderr
    assert "static.json" in result.stderr
    assert recorded_api.methods() == [], "the command reached a cluster it could not name"
