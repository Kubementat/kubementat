"""`config validate` gives the tree a verdict, not an argument."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config.init import add_team, init_configuration
from kubementat.config.validate import validate_tree
from kubementat.core.constants import (
    CLUSTER_KUBE_CONTEXT_KEY,
    CLUSTER_KUBECONFIG_PATH_KEY,
)

ENVIRONMENT = "prod"
TEAM = "alpha"

# The server the collision rows point two environments at. One address, spelled once, because the
# 1:1 check compares servers and not context names (`EnvironmentRegistry.collision_check`, now in
# `core/identity/registry.py`), so "the same address
# twice" is the whole fixture this row needs.
SHARED_SERVER = "https://10.0.1.9:6443"


# ---------------------------------------------------------------------------------------------
# The fixtures, spelled as edits to a tree `config init` wrote and not as hand-written trees.
# ---------------------------------------------------------------------------------------------


def a_tree(tmp_path, team=TEAM) -> Path:
    """One initialized environment — manifest, both environment files, both team files, no answers.
    """
    root = tmp_path / "config"
    init_configuration(root=root, environment=ENVIRONMENT)
    if team is not None:
        add_team(root=root, environment=ENVIRONMENT, team=team)
    return root


def errors_by_code(root, team=None) -> dict:
    return {finding.code: finding for finding in validate_tree(root, ENVIRONMENT, team).errors}


def run_validate(root, environment=ENVIRONMENT, team=None) -> "CliRunner().Result":
    argv = (["validate", environment] + ([team] if team else [])
            + ["--config-dir", str(root)])
    return CliRunner(mix_stderr=False).invoke(config_module.config, argv)


def write_yaml(path: Path, document) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def plant(root: Path, document: dict, layer=None) -> None:
    """Answer keys into a layer's `static.json`, the way `config set` would."""
    path = root / ENVIRONMENT / "static.json" if layer is None else layer
    existing = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(existing | document, indent=2) + "\n", encoding="utf-8")


def digest(root: Path) -> dict:
    """Every path under the root — directory or file — with the file's bytes. The read-only claim.
    """
    state = {}
    for path in sorted(root.rglob("*")):
        entry = str(path.relative_to(root))
        if path.is_dir():
            state[entry] = ("dir", None)
        elif path.is_file():
            state[entry] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            state[entry] = ("other", None)
    return state


# ---------------------------------------------------------------------------------------------
# 1 — a fresh tree validates clean, and the verdict line is what stdout carries.
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# 2 — one row per error code, each planted as a single fault.
# ---------------------------------------------------------------------------------------------

# Rows: (code, editor). Each editor introduces exactly one fault, so the code it asserts fires for
# the reason it exists and not as collateral from the row's neighbours.
ERROR_ROWS = [
    # The example this rule was written from: `environments:` is a key kubementat does not know because
    # the directories, not the manifest, answer that question.
    ("unknown-manifest-key",
     lambda root: write_yaml(root / "kubementat.yaml",
                             {"schema_version": 1, "kubementat": ">=0.4,<2", "environments": ["dev"]})),
    ("duplicate-manifest",
     lambda root: write_yaml(root / ENVIRONMENT / "kubementat.yaml",
                             {"schema_version": 1, "kubementat": ">=0.4,<2"})),
    ("helmfile-in-config-tree",
     lambda root: (root / ENVIRONMENT / "helmfile.yaml.gotmpl").write_text("releases: []\n",
                                                                           encoding="utf-8")),
    ("static-json-wrong-depth",
     lambda root: ((root / ENVIRONMENT / TEAM / "deep").mkdir(),
                   (root / ENVIRONMENT / TEAM / "deep" / "static.json").write_text("{}", encoding="utf-8"))),
    ("encrypted-only-layer",
     lambda root: ((root / "orphan").mkdir(),
                   (root / "orphan" / "static.encrypted.json").write_text("{}", encoding="utf-8"))),
    ("team-named-like-layer-directory",
     lambda root: ((root / ENVIRONMENT / "values").mkdir(),
                   (root / ENVIRONMENT / "values" / "static.json").write_text("{}", encoding="utf-8"))),
    # Loading a layer checks only that it parses: an array is JSON,
    # and kubementat reads a layer as a mapping, so nothing in the file is ever asked for.
    ("layer-file-not-an-object",
     lambda root: (root / ENVIRONMENT / TEAM / "static.json").write_text("[1, 2]", encoding="utf-8")),
    ("layer-file-not-json",
     lambda root: (root / ENVIRONMENT / "static.json").write_text("{not json", encoding="utf-8")),
    ("placeholder-value",
     lambda root: plant(root, {"MYSQL_HOST": "REPLACEME_TEAM"})),
    ("missing-layer-file",
     lambda root: (root / ENVIRONMENT / "static.encrypted.json").unlink()),
]


@pytest.mark.parametrize("code,editor", ERROR_ROWS, ids=[row[0] for row in ERROR_ROWS])
def test_an_edited_tree_is_refused_by_the_one_code_that_describes_it(tmp_path, code, editor):
    """One fault per run, and the run names it. The code is the assertion, never the sentence."""
    root = a_tree(tmp_path)
    editor(root)

    findings = errors_by_code(root, TEAM)
    assert code in findings, f"{code} did not fire; errors were {sorted(findings)}"
    assert findings[code].message.strip()
    assert findings[code].kind == "error"


def test_two_environments_in_one_root_are_refused_by_the_collision_sentence(tmp_path):
    """The decision is the registry's and the report carries its text, not validate's own words."""
    root = a_tree(tmp_path, team=None)
    init_configuration(root=root, environment="dev")
    kubeconfig = tmp_path / "shared.kubeconfig"
    write_yaml(kubeconfig, {
        "apiVersion": "v1", "kind": "Config", "current-context": "shared",
        "clusters": [{"name": "shared", "cluster": {"server": SHARED_SERVER}}],
        "contexts": [{"name": "shared", "context": {"cluster": "shared", "user": "admin"}}],
        "users": [{"name": "admin", "user": {"token": "not-a-real-token"}}],
    })
    for environment in (ENVIRONMENT, "dev"):
        # The same two cluster keys in both environments' own `static.json`, the way the registry
        # reads them (`EnvironmentRegistry.resolve` and `_environment_declaration`, both in
        # `core/identity/registry.py`: the environment's file and no other).
        path = root / environment / "static.json"
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing.update({CLUSTER_KUBE_CONTEXT_KEY: "shared",
                         CLUSTER_KUBECONFIG_PATH_KEY: str(kubeconfig)})
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    findings = errors_by_code(root)
    assert "cluster-collision" in findings
    sentence = findings["cluster-collision"].message
    assert SHARED_SERVER in sentence, "the sentence must name the address, as the owner's does"
    assert "prod" in sentence and "dev" in sentence
    assert "kmt environments list" in sentence


# ---------------------------------------------------------------------------------------------
# 3 — warnings: printed, and never fatal.
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# 4 — a finding is a finding: code, path, and nothing dropped.
# ---------------------------------------------------------------------------------------------


def test_a_layer_whose_pair_is_both_absent_is_one_fault_and_not_two(tmp_path):
    """One layer, one fault — even when both of its files are gone."""
    root = a_tree(tmp_path)
    (root / ENVIRONMENT / "static.json").unlink()
    (root / ENVIRONMENT / "static.encrypted.json").unlink()

    report = validate_tree(root, ENVIRONMENT, TEAM)
    missing = [f for f in report.errors if f.code == "missing-layer-file"]
    assert len(missing) == 1, f"one missing environment layer, {len(missing)} findings: {missing}"
    assert "not a layer of" in missing[0].message, missing[0].message
    assert len(set((f.kind, f.code, f.message, str(f.path)) for f in report.errors)) == len(report.errors), \
        "two findings say the same thing"


# ---------------------------------------------------------------------------------------------
# 5 — read-only, and no cluster touched.
# ---------------------------------------------------------------------------------------------


def refuse(*args, **kwargs):
    raise AssertionError("config validate touched the machine it was not given: "
                         f"{args!r} {kwargs!r}")


# ---------------------------------------------------------------------------------------------
# 6 — the root-wide half outranks the named environment.
# ---------------------------------------------------------------------------------------------
