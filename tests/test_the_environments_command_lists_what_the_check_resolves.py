"""`kmt environments list` says, in one command, which environments this tree has and which cluster
each addresses, and its rows are the check's own answers.

The command exists because kubementat owns no cluster registry: the inventory *is*
`platform_config`, and the operator's question — "what environments do I have, and which cluster is
each one?" — otherwise costs a directory walk they have to do themselves, on a machine where the
answer is already written down in `static.json`.

Two properties worth more than the table's formatting:

* **It is the same primitive the 1:1 check resolves against**. A listing that worked
  its answers out for itself would be a second read of one key, and the check and the listing would
  be the two places that disagree about what is configured.
* **An environment that cannot be resolved is on the list anyway**, named with the reason.
  Report-and-skip is what stops one broken sibling blocking a command; here it is what stops the
  listing from quietly dropping the environment the operator is trying to fix.

No row of it is a cluster contact: the address comes out of a kubeconfig file, so the command
answers on a machine with nothing reachable — which is exactly when it is worth running.
"""

from click.testing import CliRunner

from cli.commands import environments
from cluster_fixture import (DEV_SERVER, PROD_SERVER, checkout, redirect_checkout,
                             write_environment, write_kubeconfig)
from kubementat.cluster import EnvironmentRegistry


def listed(root):
    """`kmt environments list`, run against *root* as its own command."""
    return CliRunner(mix_stderr=False).invoke(environments.environments, ["list"])


def a_root(tmp_path, monkeypatch, declarations):
    """One `platform_config` with the environments the test names, and the kubeconfigs they read.

    `declarations` is `{environment: context_or_None}`; the contexts are read out of one kubeconfig
    per call so a name that is *not* in it is a declared-but-unresolvable environment rather than a
    typo in a fixture that nobody meant to write.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    contexts = {f"context-of-{name}": server
                for name, server in declarations["addresses"].items()}
    kubeconfig = write_kubeconfig(root / "kubeconfig", contexts)
    for name in declarations["environments"]:
        write_environment(root, name, context=f"context-of-{name}", kubeconfig_path=kubeconfig)
    return root


def table_rows(result):
    """The table, split into columns — the shape a human reads, which is what is under test.

    The last column is a sentence rather than a field, so it is kept whole: `no such context
    'staging-eu' in ...` is the answer, and cutting it short would make the row useless for the one
    thing it is there for.
    """
    return [line.split() for line in result.stdout.splitlines() if line.strip()]


def test_the_header_is_the_five_answers_the_command_owns(tmp_path, monkeypatch):
    """The listing's table, in its own words: name, address, context, source, and what was checked."""
    a_root(tmp_path, monkeypatch, {"environments": ["dev"], "addresses": {"dev": DEV_SERVER}})

    result = listed(root=tmp_path / "checkout")

    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[0].split() == [
        "ENVIRONMENT", "CLUSTER_ADDRESS", "CONTEXT", "SOURCE", "CHECK"]


def test_one_row_per_environment_in_the_root_and_nothing_in_between(tmp_path, monkeypatch):
    """Depth 1 of `platform_config` with a `static.json` of its own, and no other rule.

    `kubementat_components/` is in the root on purpose: it carries no environment-level file, so it
    is not an environment and no row says it is. A team's `static.json` sits a directory deeper for
    the same reason.
    """
    root = a_root(tmp_path, monkeypatch,
                  {"environments": ["dev", "prod"], "addresses":
                      {"dev": DEV_SERVER, "prod": PROD_SERVER}})
    (root / "platform_config" / "kubementat_components").mkdir()
    (root / "platform_config" / "dev" / "dev1").mkdir()
    (root / "platform_config" / "dev" / "dev1" / "static.json").write_text("{}")

    result = listed(root=root)

    assert result.exit_code == 0, result.stderr
    rows = [row[0] for row in table_rows(result)[1:]]
    assert rows == ["dev", "prod"], result.stdout


def test_a_resolved_row_gives_the_address_the_context_and_the_file_it_came_out_of(tmp_path,
                                                                                 monkeypatch):
    """What the listing is asked for, in one line per environment.

    The `SOURCE` column is the path, not the word: "this is what the file said" is a different claim
    from "the operator passed `--context`", and when the wrong cluster is being blamed for something,
    that difference is the debugging session.
    """
    root = a_root(tmp_path, monkeypatch, {"environments": ["dev"],
                                          "addresses": {"dev": DEV_SERVER}})

    result = listed(root=root)

    row = table_rows(result)[1]
    assert row[0] == "dev"
    assert row[1] == DEV_SERVER
    assert row[2] == "context-of-dev"
    assert str(root / "platform_config" / "dev" / "static.json") in result.stdout
    assert "ok" in result.stdout


def test_an_environment_that_resolves_to_nothing_is_named_with_the_reason_not_left_off(
        tmp_path, monkeypatch):
    """The rule that every environment is covered, on the command's own output.

    `staging` declares a context the kubeconfig does not have. The listing that dropped it would be
    the same lie as the check that skipped it: the operator would see two rows and conclude the
    third environment does not exist, which is the opposite of what `static.json` says.
    """
    root = a_root(tmp_path, monkeypatch,
                  {"environments": ["dev", "staging"], "addresses": {"dev": DEV_SERVER}})
    write_environment(root, "staging", context="staging-eu", kubeconfig_path=root / "kubeconfig")

    result = listed(root=root)

    assert "staging" in result.stdout, result.stdout
    assert "(unresolved)" in result.stdout, result.stdout
    assert "no such context 'staging-eu'" in result.stdout, result.stdout
    # The environment that did resolve is still answered in full; one bad file is not a reason to
    # say nothing about the machine's configuration.
    assert DEV_SERVER in result.stdout


def test_an_environment_that_declared_nothing_shows_its_context_as_not_declared(tmp_path,
                                                                                monkeypatch):
    """The state `kmt initialize` ships: the environment exists, and addresses no cluster.

    The row says that instead of inventing a context name, because the file really does not contain
    one and an operator reading it is about to be told which key to add.
    """
    root = a_root(tmp_path, monkeypatch, {"environments": ["dev"], "addresses": {"dev": DEV_SERVER}})
    write_environment(root, "fresh", context=None)

    result = listed(root=root)

    assert "fresh" in result.stdout
    assert "(not declared)" in result.stdout, result.stdout


def test_the_listing_resolves_no_context_and_contacts_no_cluster(tmp_path, monkeypatch,
                                                                 fake_binaries):
    """The two properties this command is judged on: no `Context`, and no cluster contact.

    A `--context` flag here would mean the listing resolved something, which is the listing's rule
    against
    a second answer; a shim log with nothing in it is the same rule from the other direction — a
    server address read out of a file costs a file read, and the command that is always safe to
    run must not cost a connection.
    """
    root = a_root(tmp_path, monkeypatch,
                  {"environments": ["dev"], "addresses": {"dev": DEV_SERVER}})

    result = listed(root=root)

    assert result.exit_code == 0, result.stderr
    assert fake_binaries.calls() == [], "the listing contacted a cluster or a binary"
    assert "cluster dev:" not in result.stdout, "the listing is not a command building a Context"


def test_the_listing_and_the_check_agree_about_what_the_root_contains(tmp_path, monkeypatch):
    """The listing's identity, asserted as a fact rather than argued.

    The rows come out of `EnvironmentRegistry.all()`, the same call the 1:1 check makes, so a
    disagreement between them is impossible without one of them lying. This is the assertion that
    makes the paragraph above true rather than aspirational.
    """
    root = a_root(tmp_path, monkeypatch,
                  {"environments": ["dev", "prod"],
                   "addresses": {"dev": DEV_SERVER, "prod": PROD_SERVER}})

    the_command = listed(root=root)
    from_the_check = EnvironmentRegistry(root / "platform_config").all()

    assert from_the_check, "the primitive answered nothing, so the comparison means nothing"
    for record in from_the_check:
        assert record.environment in the_command.stdout
        assert record.target.address in the_command.stdout
        assert record.target.context in the_command.stdout


def test_a_root_with_no_declared_clusters_says_so_and_where_to_look(tmp_path, monkeypatch):
    """Nothing configured is not nothing to report: it names the directory and the verb.

    `platform_config` may be empty because nothing was initialized, and a table with only a header
    would leave the operator to guess which of those it was.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)

    result = listed(root=root)

    assert result.exit_code == 0, result.stderr
    assert "no environment" in result.stdout, result.stdout
    assert str(root / "platform_config") in result.stdout, result.stdout
