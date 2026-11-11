"""The tree a human gets from `kmt config init` has to be installable.

Before this test existed, no test in the suite could answer that question, and the answer was *no*.

`helm_utils.helmfile_apply` reads `GRAFANA_ADMIN_USER` out of the environment's
`static.encrypted.json` and refuses to apply anything without it — deliberately so, and by its own
comment for a good reason. What it did not expect was that after `kmt initialize` was deleted
(`7888bdf`), nothing in the product wrote that key any more. The verb that used to copy
`assets/templates/environment/static.encrypted.json.template` into the tree was that template's only
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

from bootstrap_tools import EMAIL, tools  # noqa: F401  - the fixture is made available by importing it
from click.testing import CliRunner

from cli.commands import config as config_command
from kubementat import constants
from kubementat.cluster import Context
from kubementat.helm_utils import HelmUtils

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
        [str(root), "--environment", ENVIRONMENT, "--team", TEAM, "--gpg-email", EMAIL, *extra])
    assert result.exit_code == 0, result.output


def test_the_key_grafana_needs_lands_without_an_operator_typing_it(tmp_path, tools):
    """`GRAFANA_ADMIN_USER` is present in a fresh tree before anyone names it.

    The regression net for the deletion itself: a mutation that stops seeding — dropping
    `constants.DEFAULT_ENCRYPTED_ANSWERS` out of the encrypted document, or applying the seed *after*
    the operator's answers so a bare `init` still gets `{}` — fails here.
    """
    root = tmp_path / "platform_config"
    _init(root, tools)

    encrypted = Context.for_configuration(ENVIRONMENT, TEAM,
                                          platform_config_dir=root).config.get("env_static_encrypted")

    assert encrypted.get(ADMIN_USER_KEY) == "grafadmin", \
        "a fresh tree does not declare the admin grafana is built from, so install refuses"


def test_a_fresh_tree_does_not_refuse_an_install_for_a_missing_admin(tmp_path, tools):
    """The credential read `helmfile_apply` performs must not raise on a tree from `config init`.

    This asserts that the refusal did *not* happen, not that an install succeeded — this is not a
    cluster test, and "kmt install is green" is another ticket's claim. The check is the single call
    that used to abort the whole apply.
    """
    root = tmp_path / "platform_config"
    _init(root, tools)

    admin_user, _admin_password = HelmUtils(
        Context.for_configuration(
            ENVIRONMENT, TEAM,
            platform_config_dir=root))._grafana_admin_credentials()  # noqa: SLF001

    assert admin_user == "grafadmin"


def test_the_seed_is_one_key_and_not_a_return_to_copying_templates():
    """`DEFAULT_ENCRYPTED_ANSWERS` names exactly one key, and that key is not a secret.

    The template `initialize` used to copy carried six keys, four of them `REPLACEME_` sentinels. If
    this table grows a placeholder, init will be writing fake credentials into a file read as a
    credential store, which is worse than the absence it replaced: an absent key raises a useful error,
    while a sentinel quietly installs `replaceme@example.com` as a person.
    """
    assert set(constants.DEFAULT_ENCRYPTED_ANSWERS) == {ADMIN_USER_KEY}, \
        "the seed table changed shape; every key added here is a value init writes into a tree"
    for key, value in constants.DEFAULT_ENCRYPTED_ANSWERS.items():
        assert "REPLACEME" not in str(value), f"{key} is seeded with a placeholder"


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


def test_the_seed_is_not_written_to_the_team_layer(tmp_path, tools):
    """Grafana's admin belongs to an environment, and one directory is one cluster.

    Seeding the team's encrypted file too would give two layers an answer for one key, which is the
    disagreement this tree exists to end. It is the cheap mutation this assert catches: the seed
    written into the wrong half of the pair.
    """
    root = tmp_path / "platform_config"
    _init(root, tools)

    team_file = root / ENVIRONMENT / TEAM / "static.encrypted.json"
    assert json.loads(team_file.read_text()) == {}, \
        "the seed was written one layer down, where a team cannot address a cluster anyway"
