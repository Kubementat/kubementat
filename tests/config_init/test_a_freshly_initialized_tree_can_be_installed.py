"""The tree a human gets from `kmt config init` has to be installable.

Before this test existed, no test in the suite could answer that question, and the answer was *no*.

`HelmUtils.helmfile_apply` (`infrastructure/helm.py`) reads `GRAFANA_ADMIN_USER` out of the environment's
`static.encrypted.json` and refuses to apply anything without it — deliberately so, and by its own
comment for a good reason. What it did not expect was that after `kmt initialize` was deleted
(`7888bdf`), nothing in the product wrote that key any more. The verb that used to copy
the environment layer's encrypted shape into the tree was that template's only
reader, so a tree built by `kmt config init` carried `{}` in its encrypted file and every install on a
genuinely fresh tree died on a key nobody had had the chance to set.

The suite could not see it, and it is worth being exact about why, because this is the hazard the
project keeps naming rather than a surprise: `tests/conftest.py` seeds `"GRAFANA_ADMIN_USER":
"grafadmin"` into every fixture tree it builds. 1687 tests passed while the product could not install
the one tree shape a human is actually handed. A fixture that is richer than the product does not just
hide a bug of this kind — it hides it permanently, because nothing about the fixture can be traced
back to the verb that stopped writing.

The only test that can answer the question is one that starts from the verb a human runs and ends at
the code that reads. That is what this file does, and it is deliberately short: every extra fixture
placed between `config init` and `helmfile_apply` is another place a bug gets to hide.
"""

import json
from pathlib import Path

from bootstrap_tools import EMAIL
from click.testing import CliRunner

from cli.commands import config as config_command

ENVIRONMENT = "dev"
TEAM = "dev1"
ADMIN_USER_KEY = "GRAFANA_ADMIN_USER"


def _init(root: Path, tools, *extra: str) -> None:
    """Run `kmt config init` exactly as the CLI does, and assert it was happy.

    Going through the verb and not `init_configuration` is the point: the bug lived in what the verb
    hands down, and a test of the library function alone would have stayed green through it. `tools`
    is what keeps the deployer keypair a fixture instead of four minutes of real key generation.
    """
    root.mkdir(parents=True, exist_ok=True)
    result = CliRunner().invoke(
        config_command.init_command,
        ["--environment", ENVIRONMENT, "--team", TEAM, "--gpg-email", EMAIL, *extra,
         "--config-dir", str(root)])
    assert result.exit_code == 0, result.output


def test_an_operator_who_names_the_key_wins_over_the_seed(tmp_path, tools):
    """`--encrypted-set` answers a question, and a default must not answer it a second time.

    Order is the whole claim: seed first, apply answers second, so `--encrypted-set
    GRAFANA_ADMIN_USER=alice` writes `alice`. Reversing those two lines is green everywhere else in
    this file and silently wrong.
    """
    root = tmp_path / "platform_config"
    _init(root, tools, "--encrypted-set", f"{ADMIN_USER_KEY}=alice")

    written = json.loads((root / ENVIRONMENT / "static.encrypted.json").read_text())
    assert written[ADMIN_USER_KEY] == "alice", \
        "the seed overrode what the operator typed; init answered a question it was asked"
