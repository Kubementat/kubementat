"""A rule says where things go. Deleting needs a file that said it.

Five answers are consequences of a team's name, and the owner ruled on 2026-11-03 which half of
that is allowed to reach the cluster: **derive for placement, delete only what a file named.** It is the
kind of ruling that is easy to satisfy by accident and easy to lose later, because both halves read the
same `Config` and only the verb in front of them differs — installing into `pandas` and deleting the
namespace called `pandas` are one method call apart. So this file states the difference at every layer
that could otherwise blur it: the two accessors, the entry a teardown builds from them, and the call that
removes a namespace.

The failure this is written against is concrete. `Config.get('team_static').get('PIPELINE_NAMESPACE')`
now answers `pandas-pipelines` for a directory that never wrote it down. That is right, and the same
answer handed to `delete_namespace` is not: this cluster may already hold a namespace by that name that
kubementat never created, and a name a rule produced is an address rather than a deed.
"""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from cluster_fixture import configuration_only_context
from kubementat.core.config.config import Config
from kubementat.errors import TeamNotSuppliedError
from kubementat.core.config.teams import declared_team_namespaces
from kubementat.automations.uninstall import pipelines as pipelines_slice
from kubementat.automations.uninstall.pipelines import uninstall_pipelines

ENVIRONMENT = "dev"
TEAM = "pandas"


@pytest.fixture
def tree(tmp_path):
    """A configuration root that answers nothing about namespaces.

    Deliberately not a complete tree: every test here is about the difference between "answered" and
    "written down", and a fixture that declared both namespaces would make that difference invisible.
    """
    root = tmp_path / "config"
    (root / ENVIRONMENT).mkdir(parents=True)
    (root / ENVIRONMENT / "static.json").write_text(json.dumps({"ENVIRONMENT": ENVIRONMENT}),
                                                    encoding="utf-8")
    (root / ENVIRONMENT / "static.encrypted.json").write_text("{}", encoding="utf-8")
    (root / ENVIRONMENT / TEAM).mkdir()
    (root / ENVIRONMENT / TEAM / "static.json").write_text(
        json.dumps({"ENVIRONMENT": ENVIRONMENT, "TEAM": TEAM}), encoding="utf-8")
    (root / ENVIRONMENT / TEAM / "static.encrypted.json").write_text("{}", encoding="utf-8")
    return root, Config(ENVIRONMENT, TEAM, platform_config_dir=root)


def test_a_rule_answers_where_things_go_and_is_silent_on_what_may_be_deleted(tree):
    """The two doors, and the sentence that separates them.

    One file, one key, two questions. `get` is asked where the pipelines live and answers
    `pandas-pipelines`, because that is a fact about the team and not a guess. `declared` is asked what
    kubementat may remove and answers nothing, because no file said it — and an answer here would be a
    name with no owner, which is the only thing this whole ticket is about.
    """
    _, config = tree
    assert config.get("team_static")["PIPELINE_NAMESPACE"] == "pandas-pipelines", \
        "the placement half of the rule has to answer, or installing needs an operator to type a rule"
    assert "PIPELINE_NAMESPACE" not in config.declared("team_static"), \
        f"a rule must not be able to name something to delete: {config.declared('team_static')}"


def test_what_a_file_actually_wrote_is_still_what_declared_answers(tree):
    """`declared` is not "the empty answer". It is the file's answer, layers and precedence intact.

    If turning the rules off also turned off the layers, this would be the test that says so, and the
    teardown it protects would be sweeping nothing for a tree that declared everything.
    """
    root, _ = tree
    (root / ENVIRONMENT / TEAM / "static.json").write_text(
        json.dumps({"ENVIRONMENT": ENVIRONMENT, "PIPELINE_NAMESPACE": "shared-pipelines"}),
        encoding="utf-8")
    config = Config(ENVIRONMENT, TEAM, platform_config_dir=root)
    assert config.declared("team_static")["PIPELINE_NAMESPACE"] == "shared-pipelines", \
        "declared dropped a value a file really set, which would silently stop a sweep"


def test_the_two_doors_fail_the_same_way_and_not_in_two_pieces(tree):
    """One problem, one failure. `get` and `declared` may answer differently and must not.

    A team-less read of either is the same mistake, and two different messages for it is the pattern
    this tree keeps having to remove. Same for the missing file.
    """
    root, _ = tree
    no_team = Config(ENVIRONMENT, None, platform_config_dir=root)
    for reader in (no_team.get, no_team.declared):
        with pytest.raises(TeamNotSuppliedError) as refused:
            reader("team_static")
        assert "team_static" in str(refused.value), str(refused.value)

    gone = root / "not-here"
    (gone / ENVIRONMENT).mkdir(parents=True)
    (gone / ENVIRONMENT / "static.json").write_text("{}", encoding="utf-8")
    (gone / ENVIRONMENT / "static.encrypted.json").write_text("{}", encoding="utf-8")
    missing = Config(ENVIRONMENT, TEAM, platform_config_dir=gone)
    with pytest.raises(Exception) as by_get:
        missing.get("team_static")
    with pytest.raises(Exception) as by_declared:
        missing.declared("team_static")
    assert str(by_get.value) == str(by_declared.value), \
        f"the same missing file described two ways: {by_get.value} / {by_declared.value}"


def test_the_teardown_reports_where_a_teams_objects_live_and_what_it_may_remove(tree):
    """The entry `namespace_sweep` builds carries both answers, named for what they are used for.

    `namespaces` is placement, `deletable` is permission. One dict, so the two can never be brought
    apart by a caller that read the wrong one — which is how this would otherwise come back.
    """
    root, _ = tree
    automation_context = configuration_only_context(ENVIRONMENT, TEAM,
                                                      config=Config(ENVIRONMENT, TEAM,
                                                                    platform_config_dir=root))
    entry = declared_team_namespaces(automation_context, TEAM)
    assert entry["namespaces"] == ["pandas-pipelines", "pandas"], \
        f"placement must be answered even for a tree that wrote nothing: {entry}"
    assert entry["deletable"] == [], \
        f"nothing here was named by a file, so nothing is deletable: {entry}"

    (root / ENVIRONMENT / TEAM / "static.json").write_text(
        json.dumps({"PIPELINE_NAMESPACE": "pandas-pipelines"}), encoding="utf-8")
    automation_context = configuration_only_context(ENVIRONMENT, TEAM,
                                                      config=Config(ENVIRONMENT, TEAM,
                                                                    platform_config_dir=root))
    half = declared_team_namespaces(automation_context, TEAM)
    assert half["deletable"] == ["pandas-pipelines"], \
        f"a key that is written down is deletable, and that is the point of the field: {half}"


def test_removing_a_teams_pipelines_refuses_when_only_the_rule_knows_the_namespace(tree, monkeypatch):
    """The one call that deletes a namespace on a team's word, and what it does with nothing to go on.

    The assertion is not "it deleted nothing" — it is that it said why. A silent no-op here is how a
    teardown becomes a claim about a cluster nobody looked at, and the message has to carry the
    distinction, not just the refusal.
    """
    root, config = tree
    deleted = []
    automation_context = configuration_only_context(ENVIRONMENT, TEAM, config=config)
    monkeypatch.setattr(pipelines_slice, "KubernetesUtils",
                        lambda ctx: SimpleNamespace(
                            delete_namespace=lambda name, ignore_not_found=True: deleted.append(name)))
    with pytest.raises(Exception) as refused:
        uninstall_pipelines(automation_context)

    assert deleted == [], f"a namespace was deleted that no file named: {deleted}"
    assert "PIPELINE_NAMESPACE" in str(refused.value), str(refused.value)
    assert str(config.team_static_config_path) in str(refused.value), \
        f"the refusal named no file: {refused.value}"
