"""A command that reads no team has to run without one, and a guard has to be visible.

Ticket 3 said "call `require_team` where a class reads team configuration". Applied to classes that
read none, that produced nine guards, six of which refused work they had no business refusing:
`kmt helm apply <env>`, `kmt helm-version <env> <chart>`, `kmt component install/uninstall <env>
<component>`, `kmt routing install <env>`, `kmt create-ingress <env> ...` and the two
`kmt users create-grafana-*` commands all declined a team they never consulted, and then declined
to do anything about the environment they had been pointed at.

Two directions, both checked here.

**The surface side** is what the operators of those commands get: run them with no team and they
resolve the cluster, print the resolution report and do the thing. A command that only "fails
differently" -- a parse error on the flag that went away, a `TypeError` three frames down -- has not
changed, it has moved. Each run below is asserted on the report line and on an effect, and the
uninstall is run as its own process so the conclusion does not depend on a decorator having been
patched.

**The source side** is the census, read out of `src/` rather than remembered. Three `require_team`
call sites survived, and the rule is not "three is the number" but "each one can point at the line
that reads `team_static`, puts the team in an argv namespace, or interpolates it into a name". Those
three are asserted against the lines, so a fourth guard fails this file and a third guard that
stopped reading anything fails it too.
"""

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.commands import component, helm, routing, utilities
from kubementat.automations.routing import routing as routing_module
from kubementat.core.identity.context import Context

REPO_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENT = "dev"

# The three survivors, keyed by module, each with the sentence it was given and the line that earns
# it. A file that cannot produce its evidence is the same failure as a file that should not have one,
# so both halves are asserted: the sentence this file claims, and the code the claim is about.
SURVIVORS = {
    "src/kubementat/ci_utils.py": ("the namespace `-n` puts the CI container in",
                                   '"-n", str(self.team)'),
    "src/kubementat/tekton_utils.py": ("the namespace the Tekton objects live in",
                                       "config.get('team_static')"),
    "src/kubementat/user_management.py": ("the name the account kubeconfig's context is built from",
                                          '-cluster-{team}-{service_account_name}'),
}

# The commands this ticket took `--team` off. The four 11b made the finding for already live in
# `test_team_option.py`; these are the ones whose guard was one layer down, in a service constructor.
TEAM_LESS_RUNS = [
    (helm.helm_version, [ENVIRONMENT, "prometheus-community/prometheus"]),
    (component.install, [ENVIRONMENT, "grafana"]),
    (component.uninstall, [ENVIRONMENT, "grafana"]),
    (utilities.create_ingress, [ENVIRONMENT, "svc.example.test", "the-service", "80"]),
    (routing.install, [ENVIRONMENT, "--yes"]),
]

PROMETHEUS_SEARCH = json.dumps([{"chart": "prometheus-community/prometheus",
                                 "name": "prometheus", "version": "25.8.0",
                                 "app_version": "2.47.0", "repository": "prometheus-community"}])
GRAFANA_RELEASE = """
- name: grafana
  labels:
    group: standard
    component_name: grafana
"""


def require_team_call_sites():
    """Every call of `require_team` in `src/`, as `(file, the sentence it was given)` pairs.

    Read out of the syntax tree and not with a grep, because the claim is about calls and not about
    the word: the same name in a docstring, in `context.py`'s definition, or in a `helpers.py`
    comment is not what the census counts.
    """
    sites = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "require_team"):
                continue
            sentence = (node.args[0].value if node.args and isinstance(node.args[0], ast.Constant)
                        else None)
            sites.append((str(path.relative_to(REPO_ROOT)), sentence))
    return sites


def the_report(result):
    """Both streams at once, because `@kmt_context` puts the resolution report on stderr.

    It is the first thing a command says and the only thing in the run that answers "which cluster",
    so every test below reads it as evidence that what stopped the run was the team and not the
    cluster resolution behind it.
    """
    return (result.stdout or "") + (result.stderr or "")


@pytest.fixture
def environment_only(fixture_repo, dry_run_env, monkeypatch):
    """A checkout with the environment layer written and no team directory at all.

    That is the whole fixture. A command that still needed `platform_config/dev/<team>/` would have
    nothing to find and would say which file it wanted; a command that never needed one carries on
    and prints the cluster it resolved. `CLUSTER_MANAGER_EMAIL` is spelled here because the routing
    install reads it out of the same file and has no default for an address -- leaving it out would
    have tested the error branch instead of the install.
    """
    monkeypatch.delenv("TEAM", raising=False)
    fixture_repo.environment_static(CLUSTER_MANAGER_EMAIL="operator@example.test")
    return fixture_repo


@pytest.fixture
def helm_on_the_path(environment_only, fake_binaries):
    """`helm` answering that nothing is deployed, so no apply finds a release to move."""
    fake_binaries.respond("helm", json.dumps([]), subcommand="list")
    fake_binaries.take_calls()
    return fake_binaries


def write_manifest(fixture_repo, body=GRAFANA_RELEASE):
    """The copy of the release manifest helmfile applies, under the environment's own directory."""
    manifest = (fixture_repo.platform_config / ENVIRONMENT / "kubementat_components"
                / "helmfile.yaml.gotmpl")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(body)
    return manifest


# ---------------------------------------------------------------------------------------
# the census
# ---------------------------------------------------------------------------------------


def test_exactly_three_services_still_ask_a_context_for_a_team():
    """`helm_utils`, `ingress_generator`, `routing` and `grafana_accounts` no longer do.

    The count is asserted as a set of files rather than a number so the failure names the file that
    grew or lost a guard. Nothing here is a list of permissions: any of these three can lose its
    guard the day its read goes away, which is what happened to the six that went.
    """
    assert sorted(path for path, _ in require_team_call_sites()) == sorted(SURVIVORS)


def test_each_survivor_points_at_the_line_that_needs_the_team():
    """A guard with no reader behind it is the bug, not the guard.

    This is the assertion that has to be made by reading and the one a grep cannot make: the file
    that calls `require_team` has to contain the read that earns it. `ci_utils` puts the team in an
    argv namespace (`kubectl run -n`), `user_management` interpolates it into the kubeconfig's
    context name, and `tekton_utils` reads `team_static.PIPELINE_NAMESPACE` out of the
    configuration -- the three reasons a team is asked for, and the only three this tree
    has.
    """
    sites = require_team_call_sites()
    for path, (sentence, evidence) in sorted(SURVIVORS.items()):
        assert (path, sentence) in sites, (
            f"{path} does not call `require_team` with the sentence this file claims: {sentence!r}")
        assert evidence in (REPO_ROOT / path).read_text(encoding="utf-8"), (
            f"{path} keeps a `require_team` but no longer contains {evidence!r}. Either the read "
            "that needed the team is gone and the guard goes with it, or the evidence moved and "
            "this file has the wrong line to point at.")


def test_the_two_classes_that_had_false_guards_no_longer_carry_the_half_they_never_read():
    """An attribute that is set and never read is the guard's corpse, and this tree deletes corpses.

    Two of the deleted guards kept `self.team` for exactly one statement -- the refusal -- so
    deleting the guard means deleting the attribute with it. This asserts that per file rather than
    trusting the comment that says so: a class that still stores a team it never reads has either
    found a real read since (and then it wants the guard back, here and in the census) or is
    carrying a value that appears only in its own `__init__`. The two survivors that do store a team
    -- `CiUtils` for an argv namespace, `TektonUtils` for a configuration read -- are covered by the
    census above, which is the file that decides whether the attribute is dead.
    """
    for path in ("src/kubementat/infrastructure/helm.py", "src/kubementat/ingress_generator.py"):
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=str(path))
        stored = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assign) and any(
                      isinstance(target, ast.Attribute) and target.attr == "team"
                      for target in node.targets)]
        assert not stored, f"{path} still stores the team it stopped reading: {[n.lineno for n in stored]}"


# ---------------------------------------------------------------------------------------
# the surface, run without the flag that used to stop it
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("command,argv", TEAM_LESS_RUNS,
                         ids=[command.name for command, _ in TEAM_LESS_RUNS])
def test_a_team_less_run_names_the_cluster_and_is_not_a_refusal(command, argv,
                                                                environment_only, helm_on_the_path):
    """No `--team`, no `TEAM`, and five commands that used to say they could not go on.

    Two things are asserted for all five: the resolution report, which proves the `Context` resolved
    rather than declined, and the absence of the refusal sentence, which proves the command did not
    just fail somewhere else instead. The effect each one produces is asserted per command below,
    because "did the work" means something different for a search, an apply and a manifest.
    """
    result = CliRunner(mix_stderr=False).invoke(command, list(argv))

    report = the_report(result)
    assert f"cluster {ENVIRONMENT}:" in report, f"kmt {command.name} resolved no cluster: {report}"
    assert "belongs to a team" not in report, (
        f"kmt {command.name} still refuses a run that named none: {report}")


def test_the_chart_version_command_prints_the_version_it_searched_for(helm_on_the_path):
    """The cheapest of the seven, so the cheapest place to see the whole shape.

    `helm-version` reads no cluster and no team -- it is `helm search repo` and a resolved
    environment. Before this ticket it still refused a run that named no team, on the argument that
    a `Config` needs a team to be pointed at: true of the paths `Config` derives, irrelevant to a
    command that asks it for nothing.
    """
    helm_on_the_path.respond("helm", PROMETHEUS_SEARCH, subcommand="search")

    result = CliRunner(mix_stderr=False).invoke(helm.helm_version,
                                                [ENVIRONMENT, "prometheus-community/prometheus"])

    assert result.exit_code == 0, the_report(result)
    assert "25.8.0" in result.stdout
    assert "team" not in result.stdout.lower(), result.stdout


def test_the_apply_reaches_helmfile_with_no_team_named_anywhere_in_its_arguments(
        environment_only, helm_on_the_path):
    """`kmt helm apply <env>`, the largest of the six false refusals.

    The guard stood in `HelmUtils.__init__`, so the refusal arrived before the class found out that
    everything it asks of configuration is environment-level: grafana's admin pair out of
    `env_static_encrypted`, every release namespace out of `component_namespace`, and the manifest
    out of the environment's own `kubementat_components/`. This is that apply, with no team,
    asserted on the helmfile argv and on the cluster the report names.
    """
    write_manifest(environment_only)

    result = CliRunner(mix_stderr=False).invoke(
        helm.helmfile_apply, [ENVIRONMENT, "--helmfile-label-filter", "group=standard", "--yes"])

    assert result.exit_code == 0, the_report(result)
    assert "environment: dev" in result.stdout
    applied = [argv for argv in helm_on_the_path.commands() if argv[:1] == ["helmfile"]]
    assert applied, helm_on_the_path.commands()
    assert f"cluster {ENVIRONMENT}:" in the_report(result), the_report(result)


def test_an_install_selects_by_the_label_the_environment_names_and_by_no_team(environment_only,
                                                                             helm_on_the_path):
    """`kmt component install <env> <component>`: the label addresses the release.

    The `--team` this command carried was a question the operator could answer and the command
    could not. What selects a release is `component_name=`, read out of the environment's manifest
    copy, and what receives it is the namespace `env_static` gives the component.
    """
    write_manifest(environment_only)

    result = CliRunner(mix_stderr=False).invoke(component.install, [ENVIRONMENT, "grafana"])

    assert result.exit_code == 0, the_report(result)
    selectors = [argv[argv.index("-l") + 1] for argv in helm_on_the_path.commands()
                 if argv[:1] == ["helmfile"] and "-l" in argv]
    assert selectors == ["component_name=grafana"], helm_on_the_path.commands()


def test_a_component_uninstall_needs_no_team_and_reports_the_namespace_it_looked_in(
        environment_only, dry_run_env, fake_binaries, recorded_api):
    """The other half of the group, run as its own process.

    `CliRunner` proves the command body; this proves `main()` registered it and that the shim lookup
    and the cluster read through `$KUBECONFIG` are the real ones. The namespace line is the part of
    an uninstall that leaves a trace: it is the one answer that comes from configuration and not
    from the cluster, and it is the answer a team used to be refused before.
    """
    from conftest import run_kmt

    write_manifest(environment_only)
    fake_binaries.respond("helm", json.dumps([]), subcommand="list")

    result = run_kmt(["component", "uninstall", ENVIRONMENT, "grafana"], dry_run_env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert f"cluster {ENVIRONMENT}:" in result.stderr, result.stderr
    assert "namespace:" in result.stdout, result.stdout


def test_generating_a_manifest_with_no_team_names_the_class_and_the_environment(
        environment_only, fake_binaries):
    """`kmt create-ingress <env> ...`: one key is read, it is `TRAEFIK_INGRESS_CLASS`, it is environment-level.

    The comment that justified the option here said a `Config` needs a team to be pointed at. That is
    true of `Config`'s team-scoped paths, which `_team_scoped_path` refuses at the read, and false of
    a generator that reads one key out of the environment's `static.json` and writes a document.
    """
    result = CliRunner(mix_stderr=False).invoke(
        utilities.create_ingress,
        [ENVIRONMENT, "svc.example.test", "the-service", "80"])

    assert result.exit_code == 0, the_report(result)
    assert "kind: Ingress" in result.stdout
    assert f"cluster {ENVIRONMENT}:" in the_report(result), the_report(result)


def test_the_routing_group_is_applied_by_a_context_that_was_given_no_team(environment_only,
                                                                         fake_binaries,
                                                                         recorded_api):
    """`install_routing(Context.build(env, None))`, which is the state the e2e harness runs in.

    The harness line is `kmt routing install <env> --yes`, and it was correct: the release and the
    two issuers land in the namespace `Config.component_namespace("traefik")` names, which is
    `env_static`, and the only other key the path reads is `CLUSTER_MANAGER_EMAIL`, which is
    `env_static` too. It was `install_routing` that was wrong, and this line is the fix rather than
    a workaround ticket 14 would have had to write.
    """
    write_manifest(environment_only)
    fake_binaries.respond("helm", json.dumps([]), subcommand="list")
    fake_binaries.take_calls()

    result = routing_module.install_routing(Context.build(ENVIRONMENT, None), assume_yes=True)

    assert result.action == routing_module.ACTION_INSTALL
    assert result.namespace, "the namespace came back empty, so nothing was addressed"
    assert [argv for argv in fake_binaries.commands() if argv[:1] == ["helmfile"]], \
        fake_binaries.commands()
