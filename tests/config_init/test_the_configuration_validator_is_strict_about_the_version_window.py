"""`config validate` is the strict home for the version window.

The stamp — `schema_version` and the `kubementat:` constraint in `<root>/kubementat.yaml` — is read on
every cluster command, where the severity split applies: the version **string** warns and
continues, because a working cluster must not be bricked by a version string, and the **schema**
refuses, because two steps behind means every answer the tree does not name for itself comes out of a
built-in default. That part landed. What did not, and what this file measures, is the
other sentence of that split: **the command that exits non-zero about the window is `config validate`.** For a
while it said out loud that it deliberately skipped the window, on the sentence that the window
belonged to a stamp ticket — and the stamp ticket had landed without picking it up, so a tree whose
stamp was three steps stale still came back "clean". That is the exact state this verb must refuse.
The verb this rule used to share the verdict with was deleted on 2026-11-05, so the comparison is
this verb's alone and nothing reports what a bump would cost.

So the rule this file keeps is:

* the window is collected **with** every other fault, in one pass, and the exit code is the only
  verdict — the same idiom the rest of the verb uses, and the reason the old `validate` could say
  "clean" and be wrong;
* the two facts it reports are read from `stamp.ConfigurationStamp`, the one object that owns the
  comparison, because a cluster command and this verb that measure one window twice leave a tree with
  a verdict that depends on which one was asked;
* the severities stay split the same way. A cluster command warns and continues about a version string; this verb
  does not, because its whole product is the verdict. A tree one step behind is inside the window and
  is a warning everywhere, because one step is counted as nothing to do.

Rows plant one stamp each into an otherwise clean tree, so a code that fires for an unrelated reason
fails the row it is not in.
"""

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
    """A tree `config init` laid down, with its stamp rewritten to the row the test is about.

    Editing `kubementat.yaml` by hand is deliberate. That is what a bump PR and "three versions
    behind" mean in practice — one line changed by a release and not by kubementat — and the rule that
    no `config` verb writes the stamp makes it so: no verb may be the way a test gets there either.
    """
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
    """A build that cannot name its own version is not the tree's mistake.

    An unknown version warns instead of passing silently. It has to stay a warning: a tree
    whose stamp is fine must not be refused because this binary's packaging metadata is missing.
    """
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

    One run per stamp, and the only assertion is the code plus the line that explains it: the too-old
    stamp leaves a non-zero run that names the fault, the in-window stamp leaves a zero run that
    printed a warning.
    """
    too_old = run_validate(stamped_tree(tmp_path / "old",
                                        schema=constants.CONFIG_SCHEMA_VERSION - 2))
    assert too_old.exit_code != 0
    assert "schema_version" in said(too_old)
    assert "migrate" not in said(too_old), "the remedy is a human bump, not a verb"

    one_step = run_validate(stamped_tree(tmp_path / "new",
                                         schema=constants.CONFIG_SCHEMA_VERSION - 1))
    assert one_step.exit_code == 0, said(one_step)
