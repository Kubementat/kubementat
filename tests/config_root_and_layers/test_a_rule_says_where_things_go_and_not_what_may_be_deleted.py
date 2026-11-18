"""A rule says where things go. Deleting needs a file that said it."""

import json
from types import SimpleNamespace

import pytest
from cluster_fixture import configuration_only_context

from kubementat.automations.uninstall import pipelines as pipelines_slice
from kubementat.automations.uninstall.pipelines import uninstall_pipelines
from kubementat.core.config.config import Config
from kubementat.errors import TeamNotSuppliedError

ENVIRONMENT = "dev"
TEAM = "pandas"


@pytest.fixture
def tree(tmp_path):
    """A configuration root that answers nothing about namespaces."""
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
    """One problem, one failure. `get` and `declared` may answer differently and must not."""
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
