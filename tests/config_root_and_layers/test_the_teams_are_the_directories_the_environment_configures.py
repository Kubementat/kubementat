"""Which teams exist is a fact about `platform_config`, not about a script."""


import pytest

from kubementat.core.config.config import Config
from kubementat.errors import EnvironmentConfigurationDirectoryMissingError

ENVIRONMENT = "dev"
TEAM = "fixture"


def config_for(root, environment=ENVIRONMENT):
    return Config(environment, TEAM, kubementat_main_dir=root,
                  platform_config_dir=root / "platform_config", preload_configs=False)


def test_an_environment_that_was_never_initialized_names_itself_rather_than_returning_nothing(tmp_path):
    """Empty-and-correct and absent-and-ignored must not look alike."""
    root = tmp_path / "checkout"
    (root / "platform_config").mkdir(parents=True)

    with pytest.raises(EnvironmentConfigurationDirectoryMissingError) as raised:
        config_for(root, environment="staging").teams()

    assert "staging" in str(raised.value)
