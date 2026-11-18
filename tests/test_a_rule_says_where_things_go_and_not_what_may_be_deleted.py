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

from cluster_fixture import configuration_only_context
from kubementat.core.config.config import Config
from kubementat.errors import TeamNotSuppliedError
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
