"""A run that resolves no configuration root stops at the sentence, and asks nothing of a cluster.

"Named, never located" is the rule, and the failure mode it replaces is the interesting half:
`kmt` used to answer a command that had been told nothing, from the `platform_config` sitting beside
wherever the wheel had been installed. So the command resolved a cluster from a tree nobody pointed
at, printed what it had found, and dialed it. Deleting the fallback does not just change an error
message — it means a whole class of run stops existing.

Two claims are worth more than the sentence's wording, and they are asserted here rather than in
`test_config_path_resolution.py` because both belong to the *run* and not to the constructor:

* **The refusal is first.** `Context.build` reads the environment's own `static.json` before it
  reads a kubeconfig and before anything addresses a cluster, and that read needs a root. So the
  run cannot get as far as a `kubectl` call, a shim on `PATH` being the witness: a command that
  would have made one records none.
* **The tree that used to answer still does not.** Every tree below declares a real cluster under
  `KUBEMENTAT_HOME/platform_config`, which is exactly the shape the deleted rule answered from. An
  environment that declared nothing would refuse for a different reason, and the test would be
  about that reason.
"""

from pathlib import Path

import pytest
from click.exceptions import ClickException
from click.testing import CliRunner

from cli.commands import config as config_command
from cli.commands import kubementat
from cluster_fixture import (DEV_SERVER, checkout, redirect_checkout, write_environment,
                             write_kubeconfig)
from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"
ENVIRONMENT = "dev"
TEAM = "fixture"


def a_tree_that_the_deleted_rule_would_have_answered_from(tmp_path, monkeypatch):
    """A checkout that declares one cluster, named by nothing but `KUBEMENTAT_HOME`.

    `redirect_checkout` names both roots, and the configuration one is then cleared here: that is
    that is the state this rule calls *no root*, in a tree that would otherwise have answered every
    command a person could run against it.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {f"context-of-{ENVIRONMENT}": DEV_SERVER})
    write_environment(root, ENVIRONMENT, context=f"context-of-{ENVIRONMENT}",
                      kubeconfig_path=kubeconfig)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    return root


def test_context_build_refuses_before_it_builds_a_target(tmp_path, monkeypatch):
    """The library half: no root, and no `ClusterTarget` — which is the half that addresses a cluster."""
    a_tree_that_the_deleted_rule_would_have_answered_from(tmp_path, monkeypatch)
    built = []
    monkeypatch.setattr(ClusterTarget, "from_kubeconfig",
                        lambda *args, **kwargs: built.append(args) or None)

    with pytest.raises(ClickException) as raised:
        Context.build(ENVIRONMENT)

    assert built == [], "a run with no configuration root read a kubeconfig and addressed a cluster"
    assert PLATFORM_CONFIG_VARIABLE in str(raised.value)
    assert "--config-dir" in str(raised.value)


def test_a_command_that_refuses_asks_nothing_of_the_cluster(tmp_path, monkeypatch, fake_binaries):
    """The CLI half, on the command that would otherwise have started installing.

    `kmt install` resolves, reports, and then reaches for `helmfile` and `kubectl`. The report is
    the promise that the run knows which cluster it is about to touch, so the two things asserted
    here are that it printed no such promise and that the shims were asked for nothing.
    """
    a_tree_that_the_deleted_rule_would_have_answered_from(tmp_path, monkeypatch)

    result = CliRunner().invoke(kubementat.install, [ENVIRONMENT, TEAM, "--yes"])

    assert result.exit_code == 1, result.output
    assert "Error:" in result.output, result.output
    assert PLATFORM_CONFIG_VARIABLE in result.output, "the variable was not named to the operator"
    assert "--config-dir" in result.output, "the flag was not named to the operator"
    assert "resolved" not in result.output.lower(), \
        f"a run that resolved nothing reported a resolution: {result.output}"
    assert fake_binaries.calls() == [], \
        f"the command reached for a cluster after refusing: {[c.argv for c in fake_binaries.calls()]}"


def test_an_install_handed_a_root_that_holds_no_environment_refuses_it_and_not_the_checkout(
        tmp_path, monkeypatch, fake_binaries):
    """The same refusal, on the other failure mode: a root that *is* named and holds nothing.

    The wrong-root case is not "no dial set" — that one refuses with the two spellings — it is
    "the dial that is set points at a tree that configures no environment". Measured, not reasoned:
    `kmt install` reads the root it was given, finds no `<environment>/static.json`, and says so,
    naming *that* file. It never opens the checkout beside it. So the half of the plan's `Done when`
    that asks for `main_dir` holding no `platform_config/` answers itself: `main_dir` is not
    consulted for a read at all, so a bare checkout is not a state install can be wrong about — and
    the state it *can* be wrong about is this one, where a root was named and a second, answerable
    tree sits under `$KUBEMENTAT_HOME` waiting to be read by mistake.

    The decoy is therefore the whole test. It declares a real cluster the way a freshly prepared
    tree declares one once a human has named it, which is exactly what the deleted
    checkout-relative rule used to answer from.
    """
    given_root = tmp_path / "the-root-this-command-was-given" / "platform_config"
    given_root.mkdir(parents=True)
    # A prepared root that simply has no environments. Without this the run refuses on the stamp
    # before it gets to the claim this test is about, which is true and would bury the point.
    from kubementat.core import constants
    (given_root / constants.CONFIG_MANIFEST_FILENAME).write_text(
        f'kubementat: "{constants.CONFIG_KUBEMENTAT_CONSTRAINT}"\n'
        f"schema_version: {constants.CONFIG_SCHEMA_VERSION}\n")

    checkout_beside_it = tmp_path / "the-checkout-beside-it"
    checkout_beside_it.mkdir(parents=True)
    decoy_kubeconfig = write_kubeconfig(checkout_beside_it / "kubeconfig",
                                       {"context-of-a-cluster-nobody-named": DEV_SERVER})
    write_environment(checkout_beside_it, ENVIRONMENT, context="context-of-a-cluster-nobody-named",
                      kubeconfig_path=decoy_kubeconfig)

    redirect_checkout(monkeypatch, checkout_beside_it)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    result = CliRunner().invoke(kubementat.install,
                               [ENVIRONMENT, TEAM, "--yes", "--config-dir", str(given_root)])

    assert result.exit_code == 1, result.output
    missing = str((given_root / ENVIRONMENT / "static.json").resolve())
    assert missing in result.output, \
        f"the refusal did not name the file in the root it was given: {result.output}"
    assert str((checkout_beside_it / "platform_config" / ENVIRONMENT / "static.json").resolve()) \
        not in result.output, "the run read the checkout beside the root it was handed"
    assert "does not address a cluster" in result.output, result.output
    assert fake_binaries.calls() == [], \
        f"it reached for a cluster before it refused: {[c.argv for c in fake_binaries.calls()]}"


def test_config_init_names_no_root_and_scaffolds_nowhere(tmp_path, monkeypatch):
    """`config init` writes a root, and a root it was not given is refused before it writes.

    A run that names neither `--config-dir` nor `$KUBEMENTAT_PLATFORM_CONFIG_DIR` is refused by the
    one function that owns that precedence, before the library is called, so nothing is scaffolded —
    which is why this test's checkout starts empty rather than seeded. A verb that wrote first and
    refused later would leave the directory behind, and a verb that located a root beside the wheel it
    was installed from would be the D11 failure under a nicer error message, which is why the variable
    is deleted from the environment for this run.
    """
    root = tmp_path / "empty-checkout"
    root.mkdir(parents=True, exist_ok=True)
    redirect_checkout(monkeypatch, root)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    result = CliRunner().invoke(config_command.init_command,
                                ["--environment", ENVIRONMENT, "--team", TEAM])

    assert result.exit_code != 0, result.output
    assert "--config-dir" in result.output, \
        f"the refusal did not name the flag that would have answered it: {result.output}"
    assert PLATFORM_CONFIG_VARIABLE in result.output, \
        f"the refusal did not name the variable that would have answered it: {result.output}"
    assert not (root / "platform_config").exists(), \
        "config init scaffolded a tree it had been told nothing about"


def test_config_init_writes_the_root_it_was_given_and_not_the_checkout(tmp_path, monkeypatch):
    """The directory the verb was handed is the directory the tree lands in.

    `config init` is the one command that writes a configuration root rather than reading one, so a
    root that stopped being named would leave it writing beside the installed package — which is the
    same bug with a different verb on it. The library is replaced by a recorder so the claim is about
    which root the verb hands over, not about the write pass itself.
    """
    root = tmp_path / "checkout"
    root.mkdir(parents=True, exist_ok=True)
    redirect_checkout(monkeypatch, root)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)
    destination = tmp_path / "the-root-i-named" / "platform_config"
    handed = []
    monkeypatch.setattr("kubementat.config_bootstrap.prepare_configuration_directory",
                        lambda given, *args, **kwargs: handed.append(given) or ())

    result = CliRunner().invoke(config_command.init_command,
                                ["--environment", ENVIRONMENT, "--team", TEAM,
                                 "--config-dir", str(destination)])

    assert result.exit_code == 0, result.output
    assert handed == [destination], \
        f"the library was handed {handed}, not the root the run named"
    assert not (root / "platform_config").exists(), \
        "config init wrote beside the checkout it was told to ignore"
