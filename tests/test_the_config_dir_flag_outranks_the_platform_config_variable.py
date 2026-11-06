"""Which root one command reads, when two dials disagree — and only one of them is typed now.

The plumbing: `--config-dir` arrives on every command that carries `@kmt_context`, beside
`--context`/`--kubeconfig`, and its one job is to reach `Config(platform_config_dir=...)`. The
precedence it lands in is `paths.py`'s, unchanged — and it is spelled **once**, in
`paths.resolve_platform_config_dir`, which `kmt environments list` calls too:

```
1. --config-dir                       this ticket's addition, so it is the operator speaking now
2. KUBEMENTAT_PLATFORM_CONFIG_DIR     what CI and a container already say
3. nothing                            a root is named and never located, so a run that
                                      named nothing gets one sentence and no cluster
```

Two properties are worth more than the flag's help text:

* **The flag must not be decoration.** Both tests below set the variable to a *decoy* tree on
  purpose, so an inverted precedence — or a root that stopped at the CLI's argument and never
  reached `Config` — answers with the decoy's cluster instead of the flag's.
* **And it must not replace the rule.** The variable and the default still answer every run that
  passes no flag, which is why the control case is in this file and not left to the `Config` tests:
  a flag wired as an early return would pass the first test and break this one.
"""

import pytest
from click.exceptions import ClickException
from click.testing import CliRunner

from cli.commands import environments
from cluster_fixture import (DEV_SERVER, PROD_SERVER, redirect_checkout, STAGING_SERVER,
                             write_environment, write_kubeconfig)
from kubementat.core.identity.context import Context

PLATFORM_CONFIG_VARIABLE = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def two_roots(tmp_path, monkeypatch, *, environment, servers):
    """Two roots, both declaring *environment*, pointing at different clusters.

    `servers` maps which root says what: `variable` is the tree the process's own environment
    names, `flag` is the tree a command can only be told about on the command line. Same shape on
    both sides — that is what makes this a precedence test and not a missing-file test.
    """
    roots = {}
    for which, server in servers.items():
        root = tmp_path / f"root-the-{which}-names"
        root.mkdir(parents=True, exist_ok=True)
        kubeconfig = write_kubeconfig(root / "kubeconfig", {
            f"context-of-{environment}-in-{which}": server,
        })
        write_environment(root, environment,
                          context=f"context-of-{environment}-in-{which}",
                          kubeconfig_path=kubeconfig)
        roots[which] = root
    # The variable's tree is also `KUBEMENTAT_HOME`'s, so a root that never got read at all shows
    # up as the decoy's answer rather than as an error about a missing file.
    redirect_checkout(monkeypatch, roots["variable"])
    monkeypatch.setenv(PLATFORM_CONFIG_VARIABLE, str(roots["variable"] / "platform_config"))
    return roots


def platform_of(root):
    return root / "platform_config"


def test_the_root_the_flag_names_is_the_one_the_listing_prints(tmp_path, monkeypatch):
    """The command with no `Config` of its own: `kmt environments list` must still read the flag.

    Its root used to come from `default_platform_config_dir()` alone, which is the one place in
    `src/` that answered that question for itself; this is the same root the 1:1 check resolves
    against, so the listing and a command's refusal cannot drift apart.
    """
    roots = two_roots(tmp_path, monkeypatch, environment="dev",
                      servers={"variable": DEV_SERVER, "flag": PROD_SERVER})

    result = CliRunner(mix_stderr=False).invoke(
        environments.environments,
        ["list", "--config-dir", str(platform_of(roots["flag"]))])

    assert result.exit_code == 0, result.stderr
    assert PROD_SERVER in result.stdout, \
        f"the listing read the variable's tree: {result.stdout}"
    assert DEV_SERVER not in result.stdout, \
        f"the variable outvoted the flag: {result.stdout}"
    assert str(platform_of(roots["flag"]) / "dev" / "static.json") in result.stdout, \
        "the row names the file it read, so the wrong root is not a silent one"


def test_the_root_the_flag_names_is_the_one_a_context_resolves(tmp_path, monkeypatch):
    """The library half of the same rule: `Context.build` carries the root to both places it reads.

    `config.platform_config_dir` is asserted too, because that is the value the 1:1 check's
    sibling sweep is built from — a `Config` that resolved the *other* root here would resolve a
    sibling out of a tree this command was never told about.
    """
    roots = two_roots(tmp_path, monkeypatch, environment="dev",
                      servers={"variable": DEV_SERVER, "flag": PROD_SERVER})

    resolved = Context.build("dev", platform_config_dir=platform_of(roots["flag"]))

    assert resolved.target.address == PROD_SERVER, \
        "the cluster came out of the variable's tree, not the flag's"
    assert resolved.config.platform_config_dir == platform_of(roots["flag"]).resolve()


def test_without_the_flag_the_variable_is_still_the_rule(tmp_path, monkeypatch):
    """The control. A flag wired as an early return passes the tests above and fails this one.

    Nothing about the precedence moved, so the variable still answers a run that typed nothing —
    and it answers *its* tree rather than the checkout's, which is the D11 rule `config.py` keeps.
    """
    roots = two_roots(tmp_path, monkeypatch, environment="dev",
                      servers={"variable": DEV_SERVER, "flag": PROD_SERVER})

    resolved = Context.build("dev")

    assert resolved.target.address == DEV_SERVER, \
        "the variable is still the rule when no flag was typed"
    assert resolved.config.platform_config_dir == platform_of(roots["variable"]).resolve()


def test_neither_dial_named_is_a_refusal_and_not_the_checkouts_own_answer(tmp_path, monkeypatch):
    """The third layer is gone, and this test is the replacement for the one that asserted it.

    The checkout-relative default is an error rather than an answer, so a run that named
    neither dial now refuses. The tree below is one that would have answered under the old rule —
    `KUBEMENTAT_HOME` set, a `platform_config` under it declaring a cluster — which is what makes
    the refusal an assertion and not a missing-file error. Both spellings that would have fixed it
    are in the sentence, and no cluster is named in it.
    """
    root = tmp_path / "root-with-no-variables"
    root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(root / "kubeconfig",
                                  {"context-of-dev-in-checkout": STAGING_SERVER})
    write_environment(root, "dev", context="context-of-dev-in-checkout",
                      kubeconfig_path=kubeconfig)
    redirect_checkout(monkeypatch, root)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    with pytest.raises(ClickException) as raised:
        Context.build("dev", platform_config_dir=None)

    assert PLATFORM_CONFIG_VARIABLE in str(raised.value), "the variable was not named"
    assert "--config-dir" in str(raised.value), "the flag was not named"
    assert STAGING_SERVER not in str(raised.value), \
        "the refusal named the cluster it was refusing to address"


def test_the_listing_and_a_command_answer_one_root_because_they_read_one_rule(tmp_path,
                                                                              monkeypatch):
    """Two readers of one root, on every layer, because the rule has one owner now.

    `kmt environments list` used to spell the precedence beside its own `EnvironmentRegistry`:
    `default_platform_config_dir() if config_dir is None else Path(config_dir).resolve()`. That
    agreed with `Config`, so nothing failed and the duplication had no symptom — which is exactly
    the shape two owners of one rule take: they are correct right up until one of them is edited.
    One consolidation moved the spelling into `paths.resolve_platform_config_dir` and had both call
    it, so the
    check here is not "do they agree today" (they always did) but *how* they agree: the same value
    on the two dials, and — the tell of a shared owner — byte-identical sentences on the layer
    where neither names anything.

    The printed row is asserted against the `Config`'s own root rather than against a literal, so
    a change that moved what the listing prints fails here even when it keeps printing something
    plausible.
    """
    roots = two_roots(tmp_path, monkeypatch, environment="dev",
                      servers={"variable": DEV_SERVER, "flag": PROD_SERVER})

    # Layer 1: the flag. Same value in, same tree out, for a command that builds a `Config` and
    # for one that never does.
    with_the_flag = CliRunner(mix_stderr=False).invoke(
        environments.environments, ["list", "--config-dir", str(platform_of(roots["flag"]))])
    resolved_with_the_flag = Context.build("dev", platform_config_dir=platform_of(roots["flag"]))
    assert with_the_flag.exit_code == 0, with_the_flag.stderr
    assert str(platform_of(roots["flag"]) / "dev" / "static.json") in with_the_flag.stdout
    assert str(resolved_with_the_flag.config.platform_config_dir / "dev" / "static.json") \
        in with_the_flag.stdout, \
        "the listing and the command named two different files, so one of them is a second owner"
    assert resolved_with_the_flag.target.address == PROD_SERVER

    # Layer 2: the variable, with nothing typed. Both readers must still answer from it.
    with_the_variable = CliRunner(mix_stderr=False).invoke(environments.environments, ["list"])
    resolved_with_the_variable = Context.build("dev")
    assert with_the_variable.exit_code == 0, with_the_variable.stderr
    assert DEV_SERVER in with_the_variable.stdout, \
        "the listing stopped answering from the variable when the rule moved"
    assert resolved_with_the_variable.target.address == DEV_SERVER
    assert str(resolved_with_the_variable.config.platform_config_dir / "dev" / "static.json") \
        in with_the_variable.stdout

    # Layer 3: neither dial. One refusal, printed twice — the sentence is the evidence.
    root = tmp_path / "root-with-no-variables"
    root.mkdir(parents=True, exist_ok=True)
    kubeconfig = write_kubeconfig(root / "kubeconfig",
                                  {"context-of-dev-in-checkout": STAGING_SERVER})
    write_environment(root, "dev", context="context-of-dev-in-checkout",
                      kubeconfig_path=kubeconfig)
    redirect_checkout(monkeypatch, root)
    monkeypatch.delenv(PLATFORM_CONFIG_VARIABLE, raising=False)

    with pytest.raises(ClickException) as raised:
        Context.build("dev")

    refusal = CliRunner(mix_stderr=False).invoke(environments.environments, ["list"])
    assert refusal.exit_code != 0, "the listing answered a run that named no root"
    assert str(raised.value) in refusal.stderr, \
        "the listing refuses in its own words now, which is what a second owner of the rule does"
