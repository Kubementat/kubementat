"""The version of a helm chart, read out of helm's own output instead of out of a pipe.

`get_latest_version_for_helm_chart.sh` was one line of pipeline:

    helm search repo -l "$1" | head -n 2 | tail -n 1 | awk '{ print $2 }'

and it has two failure modes, both silent. If the repository was not added, `helm` printed
nothing and exited 0 -- `head` hides the emptiness, `awk` prints nothing, and the exit code is
0, so writing the answer into a values file wrote a blank line. If the search term matched more
than one chart, and `prometheus-community/prometheus` matches
`kube-prometheus-stack` and `prometheus-node-exporter` because helm treats the term as a
case-insensitive regular expression, `head -n 2 | tail -n 1` reported whichever of those helm
happened to sort first. So this file asserts the two corrections rather than the version number:

* the row that comes back must *be* the chart that was asked for, compared as an address and
  not as a prefix;
* an empty answer is traced to the half that was missing -- a repo that is not added is not a
  chart that does not exist, and the fix is a different command.

The read itself is `helm search repo … --output json` parsed with `json.loads`. No `jq`, no
`awk`, no `head`: `-l/--latest` is deliberately not used, because without it helm already
answers one row per chart at its newest version, and asking for `--versions` would make "the
newest" a version sort this file would have to invent.
"""

import json

import pytest
from click.testing import CliRunner

from cli.commands import helm
from kubementat.infrastructure.helm import HelmUtils
from kubementat.core.identity.context import Context

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


def test_the_newest_version_of_a_chart_comes_from_one_search(helm_on_the_path, fixture_repo,
                                                             monkeypatch):
    """One `helm search repo` call, asked for its machine-readable output.

    The argv is the interesting half: the address travels as its own entry, and nothing pipes
    anything into anything.
    """
    helm_on_the_path.respond("helm", search_rows([(CHART, "25.8.0", "2.47.0")]),
                             subcommand="search")

    assert utils(fixture_repo, monkeypatch).get_latest_chart_version(CHART, REPO) == "25.8.0"

    calls = [call.argv for call in helm_on_the_path.take_calls()]
    assert calls == [["helm", "search", "repo", TARGET, "--output", "json"]]


def test_a_search_that_matched_the_neighbourhood_answers_for_the_chart_asked_for(
        helm_on_the_path, fixture_repo, monkeypatch):
    """The row has to *be* the chart, not merely have matched it.

    helm sorted the neighbours first, which is exactly what `head -n 2 | tail -n 1` handed back
    as the answer. Here the address is compared, so the version that comes back is the version
    of `prometheus` and not of `prometheus-node-exporter`.
    """
    helm_on_the_path.respond("helm", search_rows([
        ("prometheus-node-exporter", "4.29.0", "1.8.2"),
        (CHART, "25.8.0", "2.47.0"),
    ]), subcommand="search")

    assert utils(fixture_repo, monkeypatch).get_latest_chart_version(CHART, REPO) == "25.8.0"


def test_a_repo_that_was_never_added_says_so_and_names_the_fix(
        helm_on_the_path, fixture_repo, monkeypatch):
    """The failure that used to be a blank line: an empty search, and the repo list says why.

    `helm search repo` exits 0 with nothing in it for a repo nobody added, which is why the
    empty answer is not the end of the reasoning. The repo list is read once to tell "not
    added" apart from "not there", and the message names both the repo and the command that
    fixes it.
    """
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
    """The other empty answer, with the other fix: the repo is there, the chart is not.

    Same shape of failure, different sentence -- which is the point. Telling an operator to add
    a repository they already have is worse than telling them nothing.
    """
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
    """A search that fails for a real reason keeps what helm said.

    Not every empty answer is an absent chart: an unreachable index is a `helm` error with
    text attached, and that text is the operator's only lead.
    """
    helm_on_the_path.respond("helm", "Error: no repositories found. You must add one before searching\n",
                             exit_code=1, subcommand="search")

    with pytest.raises(Exception) as error:
        utils(fixture_repo, monkeypatch).get_latest_chart_version(CHART, REPO)
    assert "no repositories found" in str(error.value)


def test_an_undeclared_chart_is_refused_before_helm_is_asked(fixture_repo, monkeypatch):
    """Nothing was named, so nothing is searched -- and the form is spelled out.

    The scripts took `$1` and piped it into helm whether or not it was there. This one says so
    before a child process exists, because the answer to "which chart" is a usage line and not
    a helm error.
    """
    with pytest.raises(Exception) as error:
        utils(fixture_repo, monkeypatch).get_latest_chart_version("", REPO)
    assert "no chart" in str(error.value).lower()
    assert TARGET in str(error.value)


# ---------------------------------------------------------------------------
# the command, as registered
# ---------------------------------------------------------------------------
def test_the_command_prints_the_version_and_nothing_else(helm_on_the_path, fixture_repo,
                                                         monkeypatch, tmp_path):
    """`kmt helm latest-version <environment> <repo>/<chart>` answers a version, on one line.

    The environment is the first positional — this command reads no cluster, but it answers the
    question for an environment, and the rule admits no exception. The address the script took is kept
    after it, because that is the shape a reader of the old usage line already knows. Where it is
    split is the command's business: the library answers repo and chart separately so that a failure
    can say which half was wrong.
    """
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
    """`prometheus` alone says what the form is, and does not search every repo for it.

    Searching every repository is what that argument would do, and the answer would be a
    plausible-looking version of a chart nobody pointed at. So the split is required, and the
    one failure the command owns is the one it can name.
    """
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    helm_on_the_path.respond("helm", search_rows([(CHART, "25.8.0", "2.47.0")]),
                             subcommand="search")

    result = CliRunner().invoke(helm.latest_version, [fixture_repo.environment, CHART])

    assert result.exit_code == 1, result.output
    assert TARGET in result.output
    assert [call.argv for call in helm_on_the_path.take_calls()] == []
