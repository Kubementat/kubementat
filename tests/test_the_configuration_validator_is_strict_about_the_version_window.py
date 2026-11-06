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
* the two facts it reports are read from `config.ConfigurationStamp`, the one object that owns the
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
from kubementat.core.config import config as config_lib
from kubementat.core import constants
from kubementat.core.config.config import check_configuration_stamp
from kubementat.config_init import init_configuration
from kubementat.config_validate import validate_tree

ENVIRONMENT = "prod"

# The codes this rule can produce. A clean tree is expected to touch none of them.
WINDOW_CODES = {"schema-version-too-old", "schema-version-behind-window",
                "version-outside-window", "version-undeterminable"}


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
        config_command.config, ["validate", str(root), ENVIRONMENT])
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


def test_a_tree_whose_stamp_is_current_is_not_mentioned_at_all(tmp_path):
    """Nothing to say: a current stamp is not a finding.

    This is the row that keeps the check honest. A stamp rule that reported "schema_version is
    correct" would turn every clean tree into a list with a line of nothing in it, and the verdict
    line is the thing a human reads.
    """
    root = stamped_tree(tmp_path)

    report = validate_tree(root, ENVIRONMENT)

    assert not WINDOW_CODES & {f.code for f in (*report.errors, *report.warnings)}
    assert report.errors == ()


def test_a_stamp_two_steps_behind_refuses_the_run_and_names_the_bump(tmp_path):
    """The refusal at the boundary, in the verb that gets to refuse.

    Two steps behind is where every answer the tree does not name comes out of kubementat's own
    defaults, so the tree is configuring a cluster on assumptions its owner never made. The sentence
    carries both versions and the one line that closes the gap, and the run does not walk past it.
    """
    behind = constants.CONFIG_SCHEMA_VERSION - 2
    root = stamped_tree(tmp_path, schema=behind)

    finding = {f.code: f for f in validate_tree(root, ENVIRONMENT).errors}["schema-version-too-old"]

    assert str(behind) in finding.message
    assert str(constants.CONFIG_SCHEMA_VERSION) in finding.message
    assert "schema_version" in finding.message
    assert str(root / "kubementat.yaml") in finding.message
    assert "migrate" not in finding.message, "no verb closes this gap"

    result = run_validate(root)
    assert result.exit_code != 0
    assert "schema-version-too-old" in said(result)


def test_a_stamp_one_step_behind_is_inside_the_window_and_warns(tmp_path):
    """The same arithmetic, one lower: the boundary pinned from both sides.

    The window is one step wide and that is the whole rule — one step behind changes nothing a
    cluster can notice. So this is a warning that exits zero, and the row is what an off-by-one in the
    comparison costs.
    """
    root = stamped_tree(tmp_path, schema=constants.CONFIG_SCHEMA_VERSION - 1)

    report = validate_tree(root, ENVIRONMENT)

    assert report.errors == ()
    assert codes(root)["schema-version-behind-window"].kind == "warning"

    result = run_validate(root)
    assert result.exit_code == 0, said(result)


def test_a_constraint_this_build_falls_outside_refuses_the_run(tmp_path):
    """The same fact as a warning, said as a refusal, because this verb's product is the verdict.

    A cluster command warns and continues about a version string. Here the same fact is an error, and
    the remedy named is the bump PR — one line, and not a key an operator has a verb for. The
    message names the constraint it compared and says nothing about what the gap would cost, because
    that is no longer measurable and an error that cannot be quoted back is a paragraph.
    """
    root = stamped_tree(tmp_path, constraint=">=0.0.1,<0.0.2")

    finding = {f.code: f for f in validate_tree(root, ENVIRONMENT).errors}["version-outside-window"]

    assert ">=0.0.1,<0.0.2" in finding.message
    assert str(config_lib.product_version()) in finding.message
    assert constants.CONFIG_KUBEMENTAT_CONSTRAINT not in finding.message

    result = run_validate(root)
    assert result.exit_code != 0
    assert ">=0.0.1,<0.0.2" in said(result)


def test_a_version_this_build_cannot_determine_warns_and_blames_nothing(tmp_path, monkeypatch):
    """A build that cannot name its own version is not the tree's mistake.

    An unknown version warns instead of passing silently. It has to stay a warning: a tree
    whose stamp is fine must not be refused because this binary's packaging metadata is missing.
    """
    root = stamped_tree(tmp_path)
    monkeypatch.setattr(config_lib, "product_version", lambda: None)

    report = validate_tree(root, ENVIRONMENT)

    finding = codes(root)["version-undeterminable"]
    assert finding.kind == "warning"
    assert "kmt --version" in finding.message
    assert report.errors == ()


# ---------------------------------------------------------------------------------------------
# How this is collected, which is what makes the rule true.
# ---------------------------------------------------------------------------------------------


def test_the_stamp_is_one_fault_among_many_and_not_a_refusal_on_the_first(tmp_path):
    """The idiom and not the sentence: collect, then exit.

    A stamp violation is the kind of fact that is allowed to raise on the spot, and that is exactly how
    a verb reports one problem and hides a second. A tree carrying the window fault and an unrelated
    manifest fault comes back with both, in one pass.
    """
    root = stamped_tree(tmp_path, schema=constants.CONFIG_SCHEMA_VERSION - 2)
    manifest = root / constants.CONFIG_MANIFEST_FILENAME
    manifest.write_text(manifest.read_text(encoding="utf-8") + "environments: []\n", encoding="utf-8")

    planted = {f.code for f in validate_tree(root, ENVIRONMENT).errors}

    assert "schema-version-too-old" in planted
    assert "unknown-manifest-key" in planted


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


def test_a_cluster_command_still_warns_about_the_gap_this_refuses_to_be_strict_about(tmp_path):
    """The severity split, measured on one tree instead of asserted twice.

    Same tree, two severities, no contradiction: a cluster verb gets a warning it walks past and this
    verb gets the verdict. If the two were the same rule written twice, this row is where the split
    would survive.
    """
    root = stamped_tree(tmp_path, constraint=">=0.0.1,<0.0.2")

    warnings = check_configuration_stamp(root)

    assert len(warnings) == 1
    assert warnings[0].startswith("warning:")
    assert ">=0.0.1,<0.0.2" in warnings[0]
    assert {f.code for f in validate_tree(root, ENVIRONMENT).errors} == {"version-outside-window"}
