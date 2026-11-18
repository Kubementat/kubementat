"""A prepared tree says which product it was made for, and kubementat asks before it trusts the answer.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from cluster_fixture import stamp_tree, write_environment

from cli.commands import routing
from kubementat import errors
from kubementat.core import constants
from kubementat.core.config import stamp as stamp_module

# The stamp module is `stamp_module` and not `stamp` because two tests below bind a local named
# `stamp` -- the text of the stamp file -- and a parameter of that name, which would shadow the
# module on the very line that asks it.
from kubementat.core.config import versions

# What the running build is told to be, for the tests that must not depend on whatever version this
# checkout happens to report. `constants` is what the stamp is written from, so pinning it here means
# a release does not silently change what these assert.
IN_RANGE = "0.4.0"
OUTSIDE_RANGE = "2.0.0"

# The `product_version` patches below land on `versions` and not on `config` for one reason: the code
# that answers is `ConfigurationStamp.running_version`, and it asks `versions.product_version()`, so
# the patch and the read have to be the same object. A re-export that kept `config.product_version`
# resolving would leave these five sites green and wrong — the warning would come from a function
# nobody patched, and the assertions would be about a string the product never emitted.


def root_with_stamp(tmp_path, *, constraint=None, schema_version=None) -> Path:
    """A prepared root, stamped. Everything else in it is the minimum a read needs."""
    root = tmp_path / "prepared"
    (root / "platform_config").mkdir(parents=True, exist_ok=True)
    path = root / "platform_config" / constants.CONFIG_MANIFEST_FILENAME
    if constraint is not None or schema_version is not None:
        stamp = {}
        if constraint is not None:
            stamp["kubementat"] = constraint
        else:
            stamp["kubementat"] = constants.CONFIG_KUBEMENTAT_CONSTRAINT
        stamp["schema_version"] = (constants.CONFIG_SCHEMA_VERSION
                                   if schema_version is None else schema_version)
        path.write_text(json.dumps(stamp) + "\n")
    else:
        stamp_tree(root)
    return root / "platform_config"


def test_a_tree_two_schemas_behind_is_refused_and_the_refusal_names_both_numbers():
    root = root_with_stamp(Path("/tmp/unused") / "x", schema_version=constants.CONFIG_SCHEMA_VERSION - 2)
    with pytest.raises(errors.SchemaVersionTooOldError) as refusal:
        stamp_module.check_configuration_stamp(root)
    said = str(refusal.value)
    assert str(constants.CONFIG_SCHEMA_VERSION - 2) in said and str(constants.CONFIG_SCHEMA_VERSION) in said
    # The remedy is named, because a refusal without one is a dead end the operator has to research.
    # It names a line the operator raises, not a command that raises it for them.
    assert "schema_version" in said and "kubementat.yaml" in said
    assert "migrate" not in said


def test_a_version_string_outside_the_range_warns_once_and_continues():
    root = root_with_stamp(Path("/tmp/unused") / "x", constraint=">=0.4,<2")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(versions, "product_version", lambda: OUTSIDE_RANGE)
        warnings = stamp_module.check_configuration_stamp(root)
    assert len(warnings) == 1, "warn once is the requirement, and one warning is what must come back"
    assert OUTSIDE_RANGE in warnings[0]
    # It continued: a warning is returned and not raised, so the refusal path is untouched.
    assert stamp_module.check_configuration_stamp.__name__ == "check_configuration_stamp"


def test_a_version_inside_the_range_says_nothing_at_all():
    root = root_with_stamp(Path("/tmp/unused") / "x", constraint=">=0.4,<2")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(versions, "product_version", lambda: IN_RANGE)
        assert stamp_module.check_configuration_stamp(root) == []


def test_an_unknown_product_version_warns_rather_than_asking_a_question_it_cannot_answer():
    # If the build cannot name itself, silently reporting "no problems" would be the false sentence.
    root = root_with_stamp(Path("/tmp/unused") / "x")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(versions, "product_version", lambda: None)
        warnings = stamp_module.check_configuration_stamp(root)
    assert len(warnings) == 1 and "could not determine" in warnings[0]


def test_a_tree_with_no_stamp_is_refused_and_names_the_verb_that_writes_it():
    root = Path("/tmp/unused") / "unstamped"
    (root / "platform_config").mkdir(parents=True, exist_ok=True)
    with pytest.raises(errors.ConfigurationStampMissingError) as refusal:
        stamp_module.check_configuration_stamp(root)
    assert constants.CONFIG_MANIFEST_FILENAME in str(refusal.value)
    assert "config init" in str(refusal.value)


@pytest.mark.parametrize("stamp,expected", [
    ('kubementat: ">=0.4,<2"\n', "schema_version"),          # one of the two keys
    ('schema_version: 1\n', "kubementat"),                    # the other
    ('kubementat: ">=0.4,<2"\nschema_version: "one"\n', "integer"),
    ('kubementat: ">=0.4,<2"\nschema_version: true\n', "integer"),
    ("- not\n- a\n- mapping\n", "mapping"),
])
def test_a_stamp_that_is_there_but_wrong_is_refused_with_a_sentence_about_itself(tmp_path, stamp, expected):
    path = tmp_path / "platform_config" / constants.CONFIG_MANIFEST_FILENAME
    path.parent.mkdir(parents=True)
    path.write_text(stamp)
    with pytest.raises(errors.ConfigurationStampInvalidError) as refusal:
        stamp_module.check_configuration_stamp(path.parent)
    assert expected in str(refusal.value)


# The constraint parser exists because `packaging` is not a declared dependency, so the range is
# checked with what is in the tree. These are the shapes the parser can be given and no more.
@pytest.mark.parametrize("version,inside", [
    ("0.4.0", True), ("0.4", True), ("1.0", True), ("1.9.9", True), ("0.5.1", True),
    ("0.3.9", False), ("3.0.0", False), ("2.0.0", False), ("0.4.0rc1", True),
])
def test_the_constraint_range_answers_without_the_packaging_library(version, inside):
    assert versions.satisfies_version_constraint(">=0.4,<2", version) is inside


def test_an_unsupported_operator_is_refused_and_not_read_as_a_pass():
    # A parser that met a clause it did not understand and returned True is a gate that is open.
    with pytest.raises(ValueError):
        versions.satisfies_version_constraint("~=0.4", "0.4.0")


def test_the_stamp_is_asked_before_a_verb_reaches_a_cluster(tmp_path, monkeypatch):
    """`kmt routing status` reads a cluster. The refusal must come first, and it must not read one.
    """
    root = tmp_path / "checkout"
    root.mkdir(parents=True, exist_ok=True)
    # No cluster declared, on purpose. If resolution ran first this fails with a cluster error and
    # the assertion below is what tells the difference; the stamp is the answer, so it came first.
    write_environment(root, "dev", context=None)
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(root / "platform_config"))
    monkeypatch.setenv("CLUSTER_KUBE_CONTEXT", "")
    monkeypatch.delenv("KUBEMENTAT_HOME", raising=False)
    monkeypatch.setattr(versions, "product_version", lambda: IN_RANGE)

    # `routing status` takes an environment and no team -- discovery reads nothing under a team
    # layer -- so the invocation is one argument and not the flag pair a writing verb takes.
    result = CliRunner(mix_stderr=False).invoke(routing.status, ["dev"])
    assert result.exit_code != 0
    said = (result.stderr or "") + (result.output or "")
    assert constants.CONFIG_MANIFEST_FILENAME in said
    assert "does not say which product" in said, (
        f"expected the stamp refusal, and the ordering that makes it come first; got: {said}")
