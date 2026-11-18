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


import pytest

from kubementat.core.config.config import Config
from kubementat.errors import EnvironmentConfigurationDirectoryMissingError

ENVIRONMENT = "dev"
TEAM = "fixture"


def config_for(root, environment=ENVIRONMENT):
    return Config(environment, TEAM, kubementat_main_dir=root,
                  platform_config_dir=root / "platform_config", preload_configs=False)


def test_an_environment_that_was_never_initialized_names_itself_rather_than_returning_nothing(tmp_path):
    """Empty-and-correct and absent-and-ignored must not look alike.

    `uninstall` reports the namespaces it deleted. An environment with no directory at all
    would produce an empty list and a green exit, which is a lie about a filesystem.
    """
    root = tmp_path / "checkout"
    (root / "platform_config").mkdir(parents=True)

    with pytest.raises(EnvironmentConfigurationDirectoryMissingError) as raised:
        config_for(root, environment="staging").teams()

    assert "staging" in str(raised.value)
