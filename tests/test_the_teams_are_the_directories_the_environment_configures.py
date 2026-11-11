"""Which teams exist is a fact about `platform_config`, not about a script.

`uninstall_kubementat.sh` said:

    DEV1_APP_DEPLOYMENT_NAMESPACE=dev1
    SMOKE_APP_DEPLOYMENT_NAMESPACE=smoke

with a `TODO` above it admitting that a third team was left behind. Two names written down
by a person are a list of the teams somebody on the team had in 2022, and an operator who
created `ops` would watch its namespaces outlive a `kmt uninstall` that reported `DONE`.

The answer is a directory listing under `platform_config/{environment}/`: if a directory
carries a `static.json`, it is a team, because that file is what makes the team addressable.
Anything else in there (`kubementat_components` is the one kubementat writes itself) is the
environment's own business and not a team.
"""

from pathlib import Path

import pytest
from click.exceptions import ClickException

from kubementat.config import Config

ENVIRONMENT = "dev"
TEAM = "fixture"


def make_team(root, name):
    """A team directory with the `static.json` that makes it a team."""
    path = root / "platform_config" / ENVIRONMENT / name / "static.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"PIPELINE_NAMESPACE": "%s-pipelines"}' % name)
    return root / "platform_config" / ENVIRONMENT / name


def config_for(root, environment=ENVIRONMENT):
    return Config(environment, TEAM, kubementat_main_dir=root,
                  platform_config_dir=root / "platform_config", preload_configs=False)


def test_the_teams_are_the_directories_the_environment_has_configuration_for(tmp_path):
    root = tmp_path / "checkout"
    (root / "platform_config" / ENVIRONMENT).mkdir(parents=True)
    make_team(root, "dev1")
    make_team(root, "ops")

    assert config_for(root).teams() == ["dev1", "ops"]


def test_an_environment_that_configures_no_teams_is_empty_rather_than_an_error(tmp_path):
    """A freshly initialized environment has no teams; deleting nothing is the correct answer.

    Refusing to continue would turn `kmt uninstall` on a half-finished installation into an
    argument, and the team half of the sweep is a small part of what is deleted.
    """
    root = tmp_path / "checkout"
    (root / "platform_config" / ENVIRONMENT).mkdir(parents=True)

    assert config_for(root).teams() == []


def test_kubementats_own_component_directory_is_not_mistaken_for_a_team(tmp_path):
    """`platform_config/{env}/kubementat_components` is written by `initialize`.

    It is the directory most likely to be read as a team, because it sits in exactly the same
    place. Excluding it by name is not a guess: `kubementat_components` is spelled by the code
    that creates it, and a team that really wanted the name would have to be told not to.
    """
    root = tmp_path / "checkout"
    components = root / "platform_config" / ENVIRONMENT / "kubementat_components"
    components.mkdir(parents=True)
    (components / "helmfile.yaml.gotmpl").write_text("releases: []\n")
    make_team(root, "dev1")

    assert config_for(root).teams() == ["dev1"]


def test_a_directory_without_team_configuration_is_not_a_team(tmp_path):
    """A stray directory is residue, not a team with an empty namespace list.

    Deleting a namespace derived from a team that has no `static.json` is a guess about what
    it owned, so it is not swept. The listing says what is addressable.
    """
    root = tmp_path / "checkout"
    stray = root / "platform_config" / ENVIRONMENT / "notes"
    stray.mkdir(parents=True)
    (stray / "README").write_text("left by a migration\n")
    make_team(root, "dev1")

    assert config_for(root).teams() == ["dev1"]


def test_an_environment_that_was_never_initialized_names_itself_rather_than_returning_nothing(tmp_path):
    """Empty-and-correct and absent-and-ignored must not look alike.

    `uninstall` reports the namespaces it deleted. An environment with no directory at all
    would produce an empty list and a green exit, which is a lie about a filesystem.
    """
    root = tmp_path / "checkout"
    (root / "platform_config").mkdir(parents=True)

    with pytest.raises(ClickException) as raised:
        config_for(root, environment="staging").teams()

    assert "staging" in str(raised.value)
