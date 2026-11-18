"""The version of a helm chart, read out of helm's own output instead of out of a pipe."""

import json

import pytest
from click.testing import CliRunner

from cli.commands import helm
from kubementat.core.identity.context import Context
from kubementat.infrastructure.helm import HelmUtils

REPO = "prometheus-community"
CHART = "prometheus"
TARGET = f"{REPO}/{CHART}"


def search_rows(rows):
    """The rows helm's search output is made of, keyed the way helm keys them."""
    return json.dumps([{"chart": f"{REPO}/{chart}", "name": chart, "version": version,
                        "app_version": app, "repository": REPO}
                       for chart, version, app in rows])


@pytest.fixture
def helm_on_the_path(fake_binaries):
    """`helm` as a shim, so the argv is what is asserted and nothing is downloaded."""
    return fake_binaries


def utils(fixture_repo, monkeypatch):
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    # The configuration root is named, so a fixture that wants its own tree read names it.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    return HelmUtils(Context.build(fixture_repo.environment, fixture_repo.team))


def test_a_repo_that_was_never_added_says_so_and_names_the_fix(
        helm_on_the_path, fixture_repo, monkeypatch):
    """The failure that used to be a blank line: an empty search, and the repo list says why."""
    helm_on_the_path.respond("helm", "[]", subcommand="search")
    helm_on_the_path.respond("helm", json.dumps([{"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"}]),
                             subcommand="repo")

    with pytest.raises(Exception) as error:
        utils(fixture_repo, monkeypatch).get_latest_chart_version(CHART, REPO)

    message = str(error.value)
    assert REPO in message
    assert "helm repo add" in message


def test_a_chart_that_does_not_exist_in_a_repo_that_does_names_the_chart(
        helm_on_the_path, fixture_repo, monkeypatch):
    """The other empty answer, with the other fix: the repo is there, the chart is not."""
    helm_on_the_path.respond("helm", "[]", subcommand="search")
    helm_on_the_path.respond("helm", json.dumps([{"name": REPO, "url": "https://example.test/charts"}]),
                             subcommand="repo")

    with pytest.raises(Exception) as error:
        utils(fixture_repo, monkeypatch).get_latest_chart_version("nope", REPO)

    message = str(error.value)
    assert f"{REPO}/nope" in message
    assert "helm repo add" not in message
    assert "helm repo update" in message


def test_helm_saying_no_is_passed_on_as_helm_saying_it(helm_on_the_path, fixture_repo,
                                                       monkeypatch):
    """A search that fails for a real reason keeps what helm said."""
    helm_on_the_path.respond("helm", "Error: no repositories found. You must add one before searching\n",
                             exit_code=1, subcommand="search")

    with pytest.raises(Exception) as error:
        utils(fixture_repo, monkeypatch).get_latest_chart_version(CHART, REPO)
    assert "no repositories found" in str(error.value)


def test_an_undeclared_chart_is_refused_before_helm_is_asked(fixture_repo, monkeypatch):
    """Nothing was named, so nothing is searched -- and the form is spelled out."""
    with pytest.raises(Exception) as error:
        utils(fixture_repo, monkeypatch).get_latest_chart_version("", REPO)
    assert "no chart" in str(error.value).lower()
    assert TARGET in str(error.value)


# ---------------------------------------------------------------------------
# the command, as registered
# ---------------------------------------------------------------------------
def test_the_command_prints_the_version_and_nothing_else(helm_on_the_path, fixture_repo,
                                                         monkeypatch, tmp_path):
    """`kmt helm latest-version <environment> <repo>/<chart>` answers a version, on one line."""
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    # The configuration root is named, so a fixture that wants its own tree read names it.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    helm_on_the_path.respond("helm", search_rows([(CHART, "25.8.0", "2.47.0")]),
                             subcommand="search")

    result = CliRunner().invoke(helm.latest_version,
                                [fixture_repo.environment, TARGET])

    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines()[-1] == "25.8.0"


def test_an_address_that_is_not_an_address_is_answered_before_helm_runs(helm_on_the_path,
                                                                        fixture_repo,
                                                                        monkeypatch):
    """`prometheus` alone says what the form is, and does not search every repo for it."""
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    helm_on_the_path.respond("helm", search_rows([(CHART, "25.8.0", "2.47.0")]),
                             subcommand="search")

    result = CliRunner().invoke(helm.latest_version, [fixture_repo.environment, CHART])

    assert result.exit_code == 1, result.output
    assert TARGET in result.output
    assert [call.argv for call in helm_on_the_path.take_calls()] == []
