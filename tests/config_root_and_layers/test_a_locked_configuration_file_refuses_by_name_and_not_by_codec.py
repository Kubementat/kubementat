"""A locked configuration file is refused by name and not by codec."""

import json
import logging
from pathlib import Path

import pytest
from asset_paths import REPO_ROOT
from click.testing import CliRunner

from cli.commands.config import config as config_group
from cli.kmt import KmtGroup
from kubementat.config import init as config_init
from kubementat.config import validate as config_validate
from kubementat.config.validate import validate_tree
from kubementat.core.config.config import Config
from kubementat.errors import LockedConfigFileError

ENVIRONMENT = "dev"
TEAM = "alpha"

# A stub that is not valid UTF-8, which is all "locked" means to these tests. Real git-crypt
# ciphertext is binary and nobody is expected to install it to read this file; the seam is a decode
# boundary and nothing else.
LOCKED = b"\x00\x01git-crypt\xc3\x28\xc3\xb1"

# The one thing every one of these refusals must say, and the one thing none of them may say.
GIT_CRYPT = "git-crypt"
CODEC = "codec"


def refused_by_name(message: str, path) -> None:
    """The sentence these tests actually check: the file, asked about as git-crypt's."""
    text = str(message)
    assert str(path) in text, f"the refusal does not name the file: {text}"
    assert GIT_CRYPT in text, f"the refusal never asks about git-crypt: {text}"
    assert CODEC not in text.lower(), f"the refusal blames the codec and not the lock: {text}"


def lock(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(LOCKED)
    return path


# ---------------------------------------------------------------------------------------------
# 1 — `Config._load_json_file`, the read every other encrypted value read reaches.
# ---------------------------------------------------------------------------------------------


def config_root(tmp_path, environment=ENVIRONMENT, team=None) -> Path:
    """A `Config` pointed at a throwaway tree, reading lazily so the read is inside the command."""
    root = tmp_path / "checkout"
    (root / "platform_config" / environment).mkdir(parents=True)
    (root / "platform_config" / environment / "static.json").write_text(
        json.dumps({"ENVIRONMENT": environment, "CLUSTER_KUBE_CONTEXT": None}), encoding="utf-8")
    (root / "platform_config" / environment / "static.encrypted.json").write_text(
        json.dumps({}), encoding="utf-8")
    if team is not None:
        team_dir = root / "platform_config" / environment / team
        team_dir.mkdir()
        for name in ("static.json", "static.encrypted.json"):
            (team_dir / name).write_text("{}", encoding="utf-8")
    return root


def a_config(root, team=None) -> Config:
    return Config(ENVIRONMENT, team, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"), preload_configs=False)


# ---------------------------------------------------------------------------------------------
# 2 — `config_init._read_document`, the read-modify-write verbs.
# ---------------------------------------------------------------------------------------------


def test_a_locked_file_refuses_config_unset_because_unset_reads_the_whole_layer_pair(tmp_path):
    """`config unset` opens both files of the layer, so a locked one refuses it."""
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    locked = lock(root / ENVIRONMENT / "static.encrypted.json")

    with pytest.raises(Exception) as refused:
        config_init.unset_answer(root, ENVIRONMENT, "GRAFANA_ADMIN_USER")

    refused_by_name(str(refused.value), locked)


# ---------------------------------------------------------------------------------------------
# 3 — `cluster_is_unaddressed`, the fixed site.
# ---------------------------------------------------------------------------------------------


def test_cluster_is_unaddressed_raises_over_a_locked_file_and_never_answers_true(tmp_path):
    """The worst failure in the tree used to be this function's silence."""
    locked = lock(tmp_path / "static.json")

    with pytest.raises(LockedConfigFileError) as refused:
        config_init.cluster_is_unaddressed(locked)

    refused_by_name(str(refused.value), locked)


@pytest.mark.parametrize("document", [None, {"CLUSTER_KUBE_CONTEXT": None},
                                      {"CLUSTER_KUBE_CONTEXT": "   "}])
def test_cluster_is_unaddressed_still_answers_true_for_the_three_states_that_mean_nothing(tmp_path, document):
    """Absent, `null`, and blank — the half of the predicate that is *not* the bug."""
    path = tmp_path / "static.json"
    if document is None:
        assert config_init.cluster_is_unaddressed(path) is True
        return
    path.write_text(json.dumps(document), encoding="utf-8")
    assert config_init.cluster_is_unaddressed(path) is True


# ---------------------------------------------------------------------------------------------
# 4 and 5 — `config validate`: locked files become findings, and no finding invents a value.
# ---------------------------------------------------------------------------------------------


def a_validated_tree(tmp_path, team=TEAM) -> Path:
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    config_init.add_team(root=root, environment=ENVIRONMENT, team=team)
    return root


def test_validate_never_prints_cluster_not_addressed_about_a_file_it_could_not_read(tmp_path):
    """The rule that a warning may not state what a file never answered, aimed at the second site that
    was saying it wrongly.
    """
    root = a_validated_tree(tmp_path)
    lock(root / ENVIRONMENT / "static.json")

    report = validate_tree(root, ENVIRONMENT, TEAM)

    assert "locked-configuration-file" in {f.code for f in report.errors}
    assert "cluster-not-addressed" not in {f.code for f in report.warnings}


def test_the_predicate_says_nothing_about_a_locked_file_to_the_verdict_that_asks(tmp_path):
    """The same claim one level down, where the warning is built: no finding at all, not a raise."""
    root = a_validated_tree(tmp_path)
    lock(root / ENVIRONMENT / "static.json")
    findings = []

    config_validate._cluster_fact_finding(root, ENVIRONMENT, findings)

    assert findings == [], f"it answered about a file it could not read: {findings}"


# ---------------------------------------------------------------------------------------------
# 6 and 7 — the cluster-resolution reads, which keep their own exception and the seam's sentence.
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# 8 — the boundary that must survive: missing is still missing, and is not locked.
# ---------------------------------------------------------------------------------------------


def test_a_missing_environment_layer_file_is_still_file_not_found(tmp_path):
    """The same boundary one level up, where nothing guards the read first."""
    root = config_root(tmp_path)
    (root / "platform_config" / ENVIRONMENT / "static.encrypted.json").unlink()

    config = a_config(root)
    with pytest.raises(FileNotFoundError):
        config.get("env_static_encrypted")


def test_a_missing_optional_team_file_warns_once_at_load(tmp_path, caplog):
    """The warning half of the same machinery, which the seam did not touch either."""
    root = config_root(tmp_path, team=TEAM)
    (root / "platform_config" / ENVIRONMENT / TEAM / "static.encrypted.json").unlink()

    with caplog.at_level(logging.WARNING):
        a_config(root, team=TEAM).get("env_static")

    assert any("static encrypted config" in record.getMessage().lower()
               or "no team" in record.getMessage().lower() for record in caplog.records), \
        "the missing team file stopped being a warning and became silence"


# ---------------------------------------------------------------------------------------------
# 9 — the census as a test.
# ---------------------------------------------------------------------------------------------

SRC = REPO_ROOT / "src"


# ---------------------------------------------------------------------------------------------
# 10 — the whole CLI's one line: what the operator who ran `kmt config show` actually sees.
# ---------------------------------------------------------------------------------------------


def test_a_locked_layer_file_is_rendered_as_one_error_line_and_never_as_a_traceback(tmp_path):
    """The rendering is product: exit 1 and one sentence, because the fix is `git-crypt unlock`."""
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    locked = lock(root / ENVIRONMENT / "static.encrypted.json")

    # The root group's class wrapped around the real `config` subcommand, because `main()` is the
    # only thing that registers commands on `cli` and an operator's run is what this row is about:
    # this group IS how `kmt config show` dispatches.
    root_group = KmtGroup(name="kmt")
    root_group.add_command(config_group)

    result = CliRunner(mix_stderr=False).invoke(
        root_group, ["config", "show", ENVIRONMENT, "--config-dir", str(root)])

    assert result.exit_code == 1, f"{result.exception}; {result.output}"
    refused_by_name(result.stderr, locked)
