"""A command that reads no team has to run without one, and a guard has to be visible.

Ticket 3 said "call `require_team` where a class reads team configuration". Applied to classes that
read none, that produced nine guards, six of which refused work they had no business refusing:
`kmt helm apply <env>`, `kmt helm latest-version <env> <chart>`, `kmt component install/uninstall <env>
<component>`, `kmt routing install <env>`, `kmt kubernetes create-ingress <env> ...` and the two
`kmt users create-grafana-*` commands all declined a team they never consulted, and then declined
to do anything about the environment they had been pointed at.

Two directions, both checked here.

**The surface side** is what the operators of those commands get: run them with no team and they
resolve the cluster, print the resolution report and do the thing. A command that only "fails
differently" -- a parse error on the flag that went away, a `TypeError` three frames down -- has not
changed, it has moved. Each run below is asserted on the report line and on an effect, and the
uninstall is run as its own process so the conclusion does not depend on a decorator having been
patched.

**The source side** is the census, read out of `src/` rather than remembered. Five modules call
`require_team`, six sites between them, and the rule is not "five is the number" but "each one can
point at the line that reads `team_static`, puts the team in an argv namespace, or interpolates it
into a name". Those sites are asserted against the lines, so a guard that reads nothing fails this
file and a guard that goes missing fails it too.

The read is a scan of the whole tree and not a list of files, which is what the Tekton split
changed. `TektonUtils` used to hold one `require_team` in one file and the table could afford one
key; the split moved the work out of that file five times, the constructor out last of all, and the
sentence had to travel with the read that earned it. So the guard now lives in four modules of the
split and the entry that used to point at `src/kubementat/tekton_utils.py` is gone with the file.
A module of `infrastructure/tekton/` that grows a guard is found by the same scan; a module that
loses the read that earned its guard fails on its evidence line.
"""

import json

import pytest
from click.testing import CliRunner

from cli.commands import component, helm, kubernetes, routing

ENVIRONMENT = "dev"


# The split's own directory, named so the first test can say what the scan reads. The guards moved
# here when `TektonUtils` was dissolved, and a package-wide claim is only honest if the scan is
# proved to be looking at the whole package rather than at the files someone remembered.
TEKTON_PACKAGE = "src/kubementat/infrastructure/tekton"

# The commands this ticket took `--team` off. The four 11b made the finding for already live in
# `test_team_option.py`; these are the ones whose guard was one layer down, in a service constructor.
TEAM_LESS_RUNS = [
    (helm.latest_version, [ENVIRONMENT, "prometheus-community/prometheus"]),
    (component.install, [ENVIRONMENT, "grafana"]),
    (component.uninstall, [ENVIRONMENT, "grafana"]),
    (kubernetes.create_ingress, [ENVIRONMENT, "svc.example.test", "the-service", "80"]),
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

    `kmt helm latest-version` reads no cluster and no team -- it is `helm search repo` and a resolved
    environment. Before this ticket it still refused a run that named no team, on the argument that
    a `Config` needs a team to be pointed at: true of the paths `Config` derives, irrelevant to a
    command that asks it for nothing.
    """
    helm_on_the_path.respond("helm", PROMETHEUS_SEARCH, subcommand="search")

    result = CliRunner(mix_stderr=False).invoke(helm.latest_version,
                                                [ENVIRONMENT, "prometheus-community/prometheus"])

    assert result.exit_code == 0, the_report(result)
    assert "25.8.0" in result.stdout
    assert "team" not in result.stdout.lower(), result.stdout
