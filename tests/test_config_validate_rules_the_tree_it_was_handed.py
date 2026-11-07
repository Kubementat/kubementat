"""`config validate` gives the tree a verdict, not an argument.

The claim this file exists to keep is one sentence: **every fault the tree warrants is named in one
run, warnings cannot fail the run, and the exit code is the only verdict.** No CLI command collected
findings and *then* chose an exit code before this verb (census Q3 said so plainly), so the
convention has to be asserted rather than inherited: warnings print with `err=True` and exit 0,
and errors come back as one `ClickException` that lists all of them rather than the first — the
library's own `HelmUtils.uninstall_release` idiom, now spelled on the surface.

Six claims, and which half of each is load-bearing:

1. **A tree `config init` laid down is clean.** Zero errors, and the printed line names the
   environment and the warning count. What that count is is a fact about a fresh tree: the
   cluster key is null and that is the single thing validate says about it, so the row asserts
   the *count* and not the shape of the one warning.
2. **Every error code is reachable from a tree with that one fault in it.** One row per code, each
   edited into an otherwise-clean tree, so a code that fires for an unrelated reason fails its row.
   A check that cannot be planted cannot be trusted to fire.
3. **Warnings cannot fail the run** — and printing them is not optional. An absolute kubeconfig path
   and an unaddressed cluster both exit 0 *and* say so.
4. **A finding is (kind, code, message, path)** and never prose alone; three faults come back as
   three lines inside one exception, so nothing silently reports the first fault and stops.
5. **Validate is read-only and addresses no cluster.** Byte-identical tree after the run, and
   `kubectl`/`helm`'s only paths into the world patched to raise.
6. **The root-wide half is not hidden by naming one environment.** A fault two directories over
   still fails the run, because "validate my environment" must not read as "validate my blind spot".

Falsifications that made these rows mean something (all four make exactly one row fail, which is
what "the test is about that rule" means): removing the second-manifest check kills row 2's
`duplicate-manifest` row alone; turning the absolute path into an error kills row 2's warning row
alone; raising on the first error instead of listing them kills row 4 alone; making validate create
a directory or rewrite a file kills row 5.

What this file keeps: manifest keys stay in the one manifest and no product shape lands in the tree,
the machine and not the configuration says how to reach the cluster, the layout is enumerated rather
than guessed, the verb's shape is a verdict and not an argument, and a tree that addresses no cluster
is warned about and not failed.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.infrastructure import shell
from kubementat.config_init import add_team, init_configuration
from kubementat.config_validate import validate_tree
from kubementat.core.constants import CLUSTER_KUBECONFIG_PATH_KEY, CLUSTER_KUBE_CONTEXT_KEY

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
    """One initialized environment — manifest, both environment files, both team files, no answers."""
    root = tmp_path / "config"
    init_configuration(root=root, environment=ENVIRONMENT)
    if team is not None:
        add_team(root=root, environment=ENVIRONMENT, team=team)
    return root


def errors_by_code(root, team=None) -> dict:
    return {finding.code: finding for finding in validate_tree(root, ENVIRONMENT, team).errors}


def warnings_by_code(root, team=None) -> dict:
    return {finding.code: finding for finding in validate_tree(root, ENVIRONMENT, team).warnings}


def run_validate(root, environment=ENVIRONMENT, team=None) -> "CliRunner().Result":
    argv = ["validate", str(root), environment] + ([team] if team else [])
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

    Directories are in the snapshot on purpose: a validate that mkdirs a `.kubementat-*` marker would
    be invisible to a file-only digest, and "it wrote nothing" has to mean nothing, not merely
    "nothing readable changed".
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


def test_the_tree_config_init_wrote_validates_clean_and_prints_one_verdict_line(tmp_path):
    """Zero errors, one warning (the cluster key is null), and stdout is exactly the verdict line.

    The count is asserted and not the prose: the only thing validate can say about a fresh
    tree is that nobody has named a cluster yet, so "exactly one warning, zero errors" is the whole
    expectation and a reworded sentence must not be able to break it.
    """
    root = a_tree(tmp_path)

    report = validate_tree(root, ENVIRONMENT, TEAM)
    assert report.errors == ()
    assert len(report.warnings) == 1
    assert report.warnings[0].code == "cluster-not-addressed"

    result = run_validate(root, team=TEAM)
    assert result.exit_code == 0, result.output + result.stderr
    assert result.stdout == f"no errors for environment {ENVIRONMENT} (1 warnings)\n"
    assert "WARNING:" in result.stderr


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
    # The gap `_load_json_file` leaves open (`core/config/config.py:903` checks no shape): an array is JSON,
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
    """The decision is the registry's and the report carries its text, not validate's own words.

    Both environments address one server, spelled as the same server string twice, and the sentence
    that comes back is `Context._collision`'s — the one that already tells the operator to run
    `kmt environments list`. Validate paraphrases nothing here, which is the difference between
    reporting an owner's decision and restating it (census Q1).
    """
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


def test_an_absolute_kubeconfig_path_warns_and_still_exits_zero(tmp_path):
    """The config declares the cluster, the machine declares how to reach it.

    The path is a real absolute path to a file that is not there — the shape of "worked on my
    laptop". Validate must warn and exit 0, and must not mistake it for an error: the machine that
    runs the next command gets a different answer, which is a portability warning and not a fault.
    """
    root = a_tree(tmp_path)
    plant(root, {CLUSTER_KUBE_CONTEXT_KEY: "somewhere",
                 CLUSTER_KUBECONFIG_PATH_KEY: str(tmp_path / "everywhere.kubeconfig")})

    assert warnings_by_code(root, TEAM)["absolute-kubeconfig-path"].kind == "warning"

    result = run_validate(root, team=TEAM)
    assert result.exit_code == 0, result.output + result.stderr
    assert "WARNING:" in result.stderr
    assert "no errors for environment" in result.stdout


def test_a_fresh_tree_warns_that_it_addresses_no_cluster_and_does_not_fail(tmp_path):
    """The one sentence a fresh tree deserves: present, on stderr, and not an error."""
    root = a_tree(tmp_path)
    result = run_validate(root, team=TEAM)

    assert result.exit_code == 0
    assert f"environment {ENVIRONMENT!r} is configured" in result.stderr
    assert "WARNING:" in result.stderr


# ---------------------------------------------------------------------------------------------
# 4 — a finding is a finding: code, path, and nothing dropped.
# ---------------------------------------------------------------------------------------------


def test_every_finding_carries_a_code_a_kind_and_a_path(tmp_path):
    """No finding is allowed to be prose only — codes are what tests assert and paths are what sorting uses."""
    root = a_tree(tmp_path)
    (root / ENVIRONMENT / "helmfile.yaml.gotmpl").write_text("releases: []\n", encoding="utf-8")
    write_yaml(root / "kubementat.yaml", {"schema_version": 1, "kubementat": ">=0.4,<2",
                                          "teams": ["alpha"]})

    report = validate_tree(root, ENVIRONMENT, TEAM)
    assert report.findings, "a tree with faults produced no findings at all"
    for finding in report.findings:
        assert finding.kind in ("error", "warning"), finding
        assert finding.code and " " not in finding.code, finding
        assert finding.message.strip()
        assert finding.path is not None and str(finding.path).startswith(str(root)), finding


def test_the_error_list_is_not_truncated_to_the_first(tmp_path):
    """Three faults in, three faults out — in one exception, not three runs and one guess.

    The alternative this row defends is the shape every other CLI verb has out of necessity (one
    problem, one raise). `validate` is the verb that knows there may be more than one, so raising on
    the first and letting the operator rediscover the rest one run at a time is the failure mode.
    """
    root = a_tree(tmp_path)
    (root / ENVIRONMENT / "helmfile.yaml.gotmpl").write_text("releases: []\n", encoding="utf-8")
    (root / "second").mkdir()
    (root / "second" / "helmfile.yaml.gotmpl").write_text("releases: []\n", encoding="utf-8")
    plant(root, {"BASE_DOMAIN": "REPLACEME_BASE_DOMAIN"})

    report = validate_tree(root, ENVIRONMENT, TEAM)
    assert len(report.errors) >= 3
    assert {f.code for f in report.errors}.issubset(
        {"helmfile-in-config-tree", "placeholder-value"})

    result = run_validate(root, team=TEAM)
    assert result.exit_code == 1
    stderr = result.stderr
    for finding in report.errors:
        assert finding.message.strip().splitlines()[0] in stderr, finding.code
    assert stderr.count("helmfile-in-config-tree") == 2, "two gotmpl faults, two lines — not one and a guess"
    assert "every one of them listed rather than only the first" in stderr


def test_a_layer_whose_pair_is_both_absent_is_one_fault_and_not_two(tmp_path):
    """One layer, one fault — even when both of its files are gone.

    A layer is its pair, and `_require_answerable_layer` says so in one sentence naming the missing
    half. Probing that pair once per *file label* instead of once per *layer* printed the same fault
    twice and handed the second finding a sentence about the file the first had just named: a report
    that says "2 faults" and shows the same sentence twice is worse than a silent one, because the
    operator starts counting instead of editing. This is the live run the parametrized row cannot see
    — that row deletes one file of the pair, so the duplicate never appears there.
    """
    root = a_tree(tmp_path)
    (root / ENVIRONMENT / "static.json").unlink()
    (root / ENVIRONMENT / "static.encrypted.json").unlink()

    report = validate_tree(root, ENVIRONMENT, TEAM)
    missing = [f for f in report.errors if f.code == "missing-layer-file"]
    assert len(missing) == 1, f"one missing environment layer, {len(missing)} findings: {missing}"
    assert "not a layer of" in missing[0].message, missing[0].message
    assert len(set((f.kind, f.code, f.message, str(f.path)) for f in report.errors)) == len(report.errors), \
        "two findings say the same thing"


def test_no_two_findings_ever_say_the_same_thing(tmp_path):
    """The invariant the verdict rests on: a fault count is a count of faults.

    `validate`'s promise is that its count and its exit code mean the same thing, and that holds only
    if nothing reports one fact twice. A fault-rich tree is the fixture because a fault-free tree
    proves nothing about a report, and the case above is what broke the rule when the loop keyed on
    the file's label instead of the layer it belongs to.
    """
    root = a_tree(tmp_path)
    (root / ENVIRONMENT / "helmfile.yaml.gotmpl").write_text("releases: []\n", encoding="utf-8")
    plant(root, {"BASE_DOMAIN": "REPLACEME_BASE_DOMAIN"})
    (root / ENVIRONMENT / "beta").mkdir()
    (root / ENVIRONMENT / "beta" / "static.json").write_text("{}\n", encoding="utf-8")

    for report in (validate_tree(root, ENVIRONMENT, TEAM), validate_tree(root, ENVIRONMENT)):
        seen = [(f.kind, f.code, f.message, str(f.path)) for f in report.findings]
        assert len(seen) == len(set(seen)), f"one fault reported twice: {seen}"


# ---------------------------------------------------------------------------------------------
# 5 — read-only, and no cluster touched.
# ---------------------------------------------------------------------------------------------


def refuse(*args, **kwargs):
    raise AssertionError("config validate touched the machine it was not given: "
                         f"{args!r} {kwargs!r}")


def test_validate_is_read_only_and_addresses_no_cluster(tmp_path, monkeypatch):
    """Byte-identical tree, and neither binary ever gets the chance to run.

    The mtime-free half of this claim is deliberate: a rewrite that produces the same bytes is still
    a write, and validate promises none.
    """
    root = a_tree(tmp_path)
    before = digest(root)
    monkeypatch.setattr(shell, "run", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    validate_tree(root, ENVIRONMENT, TEAM)
    assert digest(root) == before

    result = run_validate(root, team=TEAM)
    assert result.exit_code == 0, result.output + result.stderr
    assert digest(root) == before


def test_validate_touches_no_cluster_even_when_the_environment_addresses_one(tmp_path, monkeypatch):
    """The collision half reads the registry, and the registry is allowed to read a file. Nothing else.

    An addressed environment is the case where a `Context` would be built and a client would be
    created; validate must never reach that construction, so the same refusal is asserted on a tree
    whose environment resolves to a real address.
    """
    root = a_tree(tmp_path, team=None)
    kubeconfig = tmp_path / "one.kubeconfig"
    write_yaml(kubeconfig, {
        "apiVersion": "v1", "kind": "Config", "current-context": "one",
        "clusters": [{"name": "one", "cluster": {"server": SHARED_SERVER}}],
        "contexts": [{"name": "one", "context": {"cluster": "one", "user": "admin"}}],
        "users": [{"name": "admin", "user": {"token": "not-a-real-token"}}],
    })
    # A *relative* path so this row says exactly one thing: a resolved-but-unwarned environment.
    # Spelling it with `tmp_path` would also fire the absolute-path warning and make this row read
    # on the portability rule rather than on its own.
    monkeypatch.chdir(tmp_path)
    plant(root, {CLUSTER_KUBE_CONTEXT_KEY: "one", CLUSTER_KUBECONFIG_PATH_KEY: "one.kubeconfig"})
    monkeypatch.setattr(shell, "run", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    before = digest(root)
    report = validate_tree(root, ENVIRONMENT)
    assert report.errors == (), "an addressed environment is not a fault"
    assert digest(root) == before


# ---------------------------------------------------------------------------------------------
# 6 — the root-wide half outranks the named environment.
# ---------------------------------------------------------------------------------------------


def test_a_fault_two_directories_over_still_fails_the_run(tmp_path):
    """Naming one environment scopes the layer checks and nothing else.

    The named environment here is clean on purpose. A run that quietly reported "no errors" because
    it only looked inside `prod/` would be the most expensive kind of wrong: the operator's tree is
    broken and the verdict says otherwise.
    """
    root = a_tree(tmp_path)
    (root / "other").mkdir()
    (root / "other" / "helmfile.yaml.gotmpl").write_text("releases: []\n", encoding="utf-8")

    findings = errors_by_code(root, TEAM)
    assert "helmfile-in-config-tree" in findings

    result = run_validate(root, team=TEAM)
    assert result.exit_code == 1
