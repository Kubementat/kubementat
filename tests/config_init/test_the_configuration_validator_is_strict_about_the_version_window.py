"""`config validate` is the strict home for the version window."""

from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_command
from kubementat.config.init import init_configuration
from kubementat.config.validate import validate_tree
from kubementat.core import constants

# `versions` is imported as a module and not by name because two of the sites below patch
# `product_version` on it: the patch has to land on the module the stamp reads through, which is
# `versions` and not the module the function used to be defined in.
from kubementat.core.config import versions

ENVIRONMENT = "prod"


def stamped_tree(tmp_path, *, schema=None, constraint=constants.CONFIG_KUBEMENTAT_CONSTRAINT) -> Path:
    """A tree `config init` laid down, with its stamp rewritten to the row the test is about."""
    root = tmp_path / "config"
    init_configuration(root=root, environment=ENVIRONMENT)
    (root / constants.CONFIG_MANIFEST_FILENAME).write_text(
        f'kubementat: "{constraint}"\n'
        f"schema_version: {constants.CONFIG_SCHEMA_VERSION if schema is None else schema}\n",
        encoding="utf-8")
    return root


def run_validate(root):
    """The verb itself, with both streams kept readable for an assertion about what was *said*."""
    result = CliRunner(mix_stderr=False).invoke(
        config_command.config, ["validate", "--config-dir", str(root), ENVIRONMENT])
    assert not isinstance(result.exception, Exception) or result.exit_code != 0, \
        f"validate crashed instead of answering: {result.exception!r}"
    return result


def said(result) -> str:
    return (result.output or "") + (result.stderr or "")


def codes(root) -> dict:
    report = validate_tree(root, ENVIRONMENT)
    return {f.code: f for f in (*report.errors, *report.warnings)}


# ---------------------------------------------------------------------------------------------
# Three stamps, three verdicts.
# ---------------------------------------------------------------------------------------------


def test_a_version_this_build_cannot_determine_warns_and_blames_nothing(tmp_path, monkeypatch):
    """A build that cannot name its own version is not the tree's mistake."""
    root = stamped_tree(tmp_path)
    monkeypatch.setattr(versions, "product_version", lambda: None)

    report = validate_tree(root, ENVIRONMENT)

    finding = codes(root)["version-undeterminable"]
    assert finding.kind == "warning"
    assert "kmt --version" in finding.message
    assert report.errors == ()


# ---------------------------------------------------------------------------------------------
# How this is collected, which is what makes the rule true.
# ---------------------------------------------------------------------------------------------


def test_the_verdict_is_one_exit_code_for_the_whole_window(tmp_path):
    """Both directions of the window driven through the verb, because the library cannot lie here.
    """
    too_old = run_validate(stamped_tree(tmp_path / "old",
                                        schema=constants.CONFIG_SCHEMA_VERSION - 2))
    assert too_old.exit_code != 0
    assert "schema_version" in said(too_old)
    assert "migrate" not in said(too_old), "the remedy is a human bump, not a verb"

    one_step = run_validate(stamped_tree(tmp_path / "new",
                                         schema=constants.CONFIG_SCHEMA_VERSION - 1))
    assert one_step.exit_code == 0, said(one_step)
