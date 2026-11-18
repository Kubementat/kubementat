"""A command that reads no team has to run without one, and a guard has to be visible."""

import json

import pytest
from click.testing import CliRunner

from cli.commands import component, helm, kubernetes, routing

ENVIRONMENT = "dev"


# The split's own directory, named so the first test can say what the scan reads. The guards moved
# here when `TektonUtils` was dissolved, and a package-wide claim is only honest if the scan is
# proved to be looking at the whole package rather than at the files someone remembered.
TEKTON_PACKAGE = "src/kubementat/infrastructure/tekton"

# Commands that read no team configuration, so they take no `--team` and must not refuse without one.
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
    """Both streams at once, because `@kmt_context` puts the resolution report on stderr."""
    return (result.stdout or "") + (result.stderr or "")


@pytest.fixture
def environment_only(fixture_repo, dry_run_env, monkeypatch):
    """A checkout with the environment layer written and no team directory at all."""
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
    """No `--team`, no `TEAM`, and five commands that used to say they could not go on."""
    result = CliRunner(mix_stderr=False).invoke(command, list(argv))

    report = the_report(result)
    assert f"cluster {ENVIRONMENT}:" in report, f"kmt {command.name} resolved no cluster: {report}"
    assert "belongs to a team" not in report, (
        f"kmt {command.name} still refuses a run that named none: {report}")


def test_the_chart_version_command_prints_the_version_it_searched_for(helm_on_the_path):
    """The cheapest of the seven, so the cheapest place to see the whole shape."""
    helm_on_the_path.respond("helm", PROMETHEUS_SEARCH, subcommand="search")

    result = CliRunner(mix_stderr=False).invoke(helm.latest_version,
                                                [ENVIRONMENT, "prometheus-community/prometheus"])

    assert result.exit_code == 0, the_report(result)
    assert "25.8.0" in result.stdout
    assert "team" not in result.stdout.lower(), result.stdout
