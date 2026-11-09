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
from cluster_fixture import (DEV_SERVER, PROD_SERVER, checkout, redirect_checkout,
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


def test_the_report_says_which_cluster_the_command_resolved_and_from_what_file(tmp_path, monkeypatch,
                                                                               recorded_api):
    """A resolved cluster, reported once, with the file that declared the claim.

    The line is the command's answer to "which cluster" and it has to be readable on its own, so
    the address, the file, and the coverage of the comparison are all in it.
    """
    resolving_root(tmp_path, monkeypatch)

    result = run()

    assert result.exit_code == 0, result.stderr
    assert f"cluster dev: {DEV_SERVER} (" in result.stderr, result.stderr
    assert "platform_config/dev/static.json" in result.stderr
    # One environment in the root means nothing was compared, and the line says that instead of
    # claiming a verification it did not do.
    assert "not verified" in result.stderr, result.stderr
    assert result.stderr.count(f"cluster dev: {DEV_SERVER}") == 1, result.stderr


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


def test_the_context_a_command_passes_is_read_from_the_kubeconfig_the_command_names(tmp_path,
                                                                                     monkeypatch,
                                                                                     recorded_api):
    """The command's own flag above the layers, which is why the decorator is per-command and not
    on the group.

    A `--context` that the resolution order could not see would be the same class of bug as the
    `current-context` fallback it replaced: the answer decided outside the command's own arguments.
    The report is the assertion, because it records the layer the claim came out of.

    The flag states the name the environment already declares and points it at a file of its own.
    That is deliberate: a statement above the file may answer for an environment, and it is
    refused when it contradicts one, so a test that aims a command at a *different* cluster than
    the one its environment declared is no longer a resolution test — it is
    `test_a_stated_cluster_is_checked_against_the_cluster_the_environment_declares.py`.
    """
    root = resolving_root(tmp_path, monkeypatch)
    other = write_kubeconfig(root / "other-kubeconfig", {"context-of-dev": PROD_SERVER})

    result = run("--context", "context-of-dev", "--kubeconfig", str(other))

    assert result.exit_code == 0, result.stderr
    assert f"cluster dev: {PROD_SERVER} (" in result.stderr, result.stderr
    assert "flag --context" in result.stderr, result.stderr


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


def test_the_report_lands_on_stderr_so_a_commands_stdout_keeps_its_shape(tmp_path, monkeypatch,
                                                                         recorded_api):
    """Every command prints what it resolved; it does not say it may change the output.

    `create-ingress` puts a manifest on stdout so it can be piped into `kubectl apply`, and
    `service-account-token` prints a credential and nothing else — for both of them a line in front
    of the answer is the command breaking its own contract. So the report is stderr, asserted here
    once and then per command where the contract already depends on it.
    """
    resolving_root(tmp_path, monkeypatch)

    result = run()

    assert result.exit_code == 0, result.stderr
    assert "cluster dev:" not in result.stdout, result.stdout
    assert "cluster dev:" in result.stderr, result.stderr
