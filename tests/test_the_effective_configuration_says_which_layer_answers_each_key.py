"""What the effective configuration answers, and from which file.

`config show` is the whole surface, and one claim runs through it: **the report says what a running
`kmt` command sees, and nothing else.** That is why the library resolves every key through `Config`
and why none of these tests compares the report to a hand-computed precedence — if the report and
`Config` ever disagree, this file is the one that has to change, not the other way round.

Nine claims, and the interesting ones are the ones a "simpler" implementation breaks:

1. **Every key is tagged, not only the ones a file happens to mention.** `show` on a fresh tree
   prints forty keys: the two cluster keys `config init` writes as `null`, thirteen namespaces, five
   selectors, and the twenty knobs of the ten releases the shipped manifest labels. A key that
   answers as a default and appears in no file is still in the universe, because "which keys does this
    tree touch" is a different question from "what answers" — and the bar is the second one.
2. **The tag is the layer that answers, not the file that was read first.** A team that declares a
   key flips a key from `environment` to `team`; a team that leaves it alone does not, and the
   same key says `environment` in the same tree with the `TEAM` argument dropped. That difference is
   asserted in the tag and not only in the value, because a value that changed is a value and a tag
   that changed is precedence.
3. **The tag sits on the value's own line.** `show` prints `<KEY> = <value>   (<layer> from <path>)`,
   so the answer and the layer that answered it arrive in one line: a reader — a lane parsing it, or a
   human reading it — needs no second command to say where a value came from. The tag is separated by
   three spaces so the value stays greppable, which is the shape a lane cuts on.
4. **Read-only is a test and not a sentence.** The whole listing and every mtime of the tree are the
   same after the read as before it.
5. **No cluster is addressed.** `kubectl` and `helm` are patched to raise if they are called, and the
   verb never triggers them.
6. **A key nothing declares prints no line.** Printing `None` for a typo is how a typo becomes a
   fact, so a key no layer declares and no default derives is simply not in the listing.
7. **The universe contains nothing that no accessor can answer.** Every key `show` prints is planted
   with a sentinel value, and the accessor the report claims to derive it from has to answer that
   same key. This is the guard against the failure mode the ticket itself nearly shipped: the brief
   spelled the selector family `<NAME>_DEPLOYMENT_SERVICE`, a key that exists nowhere in `src/`, and
   a report that printed it would have tagged every real selector `default from constants.py` while a
   `static.json` declared it.

"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat import config_answers, shell
from kubementat.core.config import release_index as config_reads_index
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key
from kubementat.config_init import add_team, init_configuration

ENVIRONMENT = "prod"
TEAM = "alpha"

# Everything `kmt config init` and `config add-team` lay down, so the read-only test can say "the same
# fourteen paths, with the same bytes and the same mtimes" and mean the whole tree rather than the
# subset it happens to remember.
INITIALIZED_TREE = {
    "kubementat.yaml",
    ENVIRONMENT,
    f"{ENVIRONMENT}/static.json",
    f"{ENVIRONMENT}/static.encrypted.json",
    f"{ENVIRONMENT}/tekton",
    f"{ENVIRONMENT}/tekton/tasks",
    f"{ENVIRONMENT}/tekton/pipelines",
    f"{ENVIRONMENT}/{TEAM}",
    f"{ENVIRONMENT}/{TEAM}/static.json",
    f"{ENVIRONMENT}/{TEAM}/static.encrypted.json",
    f"{ENVIRONMENT}/{TEAM}/tekton",
    f"{ENVIRONMENT}/{TEAM}/tekton/tasks",
    f"{ENVIRONMENT}/{TEAM}/tekton/pipelines",
    f"{ENVIRONMENT}/{TEAM}/tekton/pipeline-runs",
    f"{ENVIRONMENT}/{TEAM}/tekton/triggers",
}


# ---------------------------------------------------------------------------------------------
# The universe, spelled as the five sources the module's docstring gives and not as a size.
#
# The component names below are hard-coded on purpose: a universe that grows because `constants.py`
# grew has to be noticed by a test and not by a comment. `test_the_hard-coded_families_match_the_sources`
# is the drift check that says these lists still describe what `constants.py` and the manifest say,
# and the assertion they feed says `show` prints exactly that set and nothing else.
# ---------------------------------------------------------------------------------------------

CLUSTER_KEYS = ("CLUSTER_KUBECONFIG_PATH", "CLUSTER_KUBE_CONTEXT")

NAMESPACE_COMPONENTS = ("cert-manager", "cluster-cronjobs", "goldilocks", "grafana", "loki", "openbao",
                        "polaris", "prometheus", "tekton", "tekton-dashboard",
                        "tekton-pipelines-resolvers", "traefik", "vpa")

SELECTOR_COMPONENTS = ("grafana", "openbao", "polaris", "tekton", "traefik")

RELEASE_COMPONENTS = ("alloy", "cert-manager", "grafana", "loki", "openbao", "polaris", "prometheus",
                      "prometheus-blackbox-exporter", "prometheus-helm-exporter", "traefik")

# The accessor each family is derived from, as `Config` spells it. Held here so the guard test can
# ask the same question twice — once of the report, once of the read path.
ACCESSORS = {
    "namespace": Config.component_namespace,
    "selector": Config.component_service_selector,
    "enabled": Config.component_release_enabled,
    "version": Config.component_release_version,
}


def derived_keys() -> dict:
    """`{key: (family, component)}` for every key the module derives rather than reads out of a file.

    The key comes from the module's own key function, so this asserts *which* keys exist and leaves
    "is that key the one the read path consults" to the guard. Those are two different claims and
    collapsing them is how a renamed suffix survives a test suite: the universe moves with the
    rename, and only the guard notices that nothing reads the new name. Going through the function and
    not a suffix constant is deliberate — it is the spelling owner, so a test that spelled the suffix
    itself would be a second place that has to agree.
    """
    entries = {}
    for component in NAMESPACE_COMPONENTS:
        entries[config_answers._namespace_key(component)] = ("namespace", component)
    for component in SELECTOR_COMPONENTS:
        entries[config_answers._service_selector_key(component)] = ("selector", component)
    for component in RELEASE_COMPONENTS:
        entries[config_answers._enabled_key(component)] = ("enabled", component)
        entries[config_answers._version_key(component)] = ("version", component)
    return entries


def expected_universe() -> set:
    """The whole universe of a fresh tree: declared keys, the three derived families, and the
    answers that live only in `constants` — the ten static ones the templated answers added, and the
    encrypted seed
    `config init` writes.

    That last source is the table of templated answers, and it is read out of the table rather than
    spelled out here on out here on
    purpose: a list of these ten names in this file would be a second place that has to agree with
    the first, which is the exact failure the drift check above exists to catch. The names come from
    `constants.py` and nothing else does, so the count below can only be right when the table is.

    The encrypted table joins it for the same reason and by the same route, and not as a name typed
    into this file. A key `config init` seeds is an *answered* key on a fresh tree, exactly the way a
    templated default is: a report that stayed silent about it would say "this tree answers nothing
    about grafana" while install quietly used the admin it was carrying. If that table ever grows a
    key, this universe grows with it and neither file has to be read again.
    """
    return (set(CLUSTER_KEYS) | set(derived_keys()) | set(constants.DEFAULT_STATIC_ANSWERS)
            | set(constants.DEFAULT_ENCRYPTED_ANSWERS) | set(constants.GENERATED_ENCRYPTED_ANSWERS))


def a_tree(tmp_path) -> Path:
    """One initialized environment, with a team layer under it, and no answers in either."""
    root = tmp_path / "config"
    init_configuration(root=root, environment=ENVIRONMENT)
    add_team(root=root, environment=ENVIRONMENT, team=TEAM)
    return root


def answers_by_key(root, team=None) -> dict:
    return {answer.key: answer for answer in config_answers.effective_answers(root, ENVIRONMENT, team)}


def run_show(root, team=None) -> str:
    argv = ["show", str(root), ENVIRONMENT] + ([team] if team else [])
    result = CliRunner(mix_stderr=False).invoke(config_module.config, argv)
    assert result.exit_code == 0, result.output + result.stderr
    return result.stdout


def plant(root, document: dict) -> None:
    """Write answers into the environment's own `static.json`, the way `config set` would.

    Written directly rather than through `set_answer` because several tests need dozens of answers at
    once and the verb is not the subject of this file.
    """
    path = root / ENVIRONMENT / "static.json"
    existing = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(existing | document, indent=2) + "\n", encoding="utf-8")


def snapshot(root: Path) -> list:
    """Every path in the tree with its mtime and its bytes.

    mtime is the half that catches "the file was rewritten with the same content", and the digest is
    the half that catches "the file was touched and not changed" — read-only claims have to fail on
    both, or an implementation that re-serializes a document passes by leaving the bytes alone.
    """
    listing = []
    for path in sorted(root.rglob("*")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        listing.append((str(path.relative_to(root)), path.stat().st_mtime_ns, digest))
    return listing


# ---------------------------------------------------------------------------------------------
# 1 — the universe, and the tags on a fresh tree.
# ---------------------------------------------------------------------------------------------

def test_show_on_a_fresh_tree_tags_every_key_and_prints_no_other_keys(tmp_path):
    """The whole of the tagging rule in one assertion: every key tagged, and no fifth kind.

    A fresh tree is the interesting input because *nothing* is answered by a file but the two cluster
    keys, so almost every line is a default — and a default line that does not say which file the
    default lives in is the sentence the tagging rule was written to replace.

    It is fifty keys and not forty since the templated answers became answered keys, and the
    difference is that the ten templated answers are now *answered* keys, so a report that stayed
    silent about them would be a
    report that said "this tree answers nothing about Tekton" while `kmt install` quietly used the
    versions it was carrying.
    """
    root = a_tree(tmp_path)

    answers = answers_by_key(root)

    assert set(answers) == expected_universe(), (
        "config show's universe drifted from the five sources it is defined by: "
        f"missing {sorted(expected_universe() - set(answers))}, extra {sorted(set(answers) - expected_universe())}")
    assert len(answers) == 52, \
        "the universe moved; 52 since config init also generated grafana's admin password"

    # A key `config init` wrote as `null` is declared, answers `null`, and is tagged `environment`.
    for key in CLUSTER_KEYS:
        assert answers[key].origin == "environment", key
        assert answers[key].value is None, key
        assert answers[key].source == (root / ENVIRONMENT / "static.json"), key

    # A component namespace that no file mentions is answered by constants.py, and says so.
    grafana = answers["GRAFANA_DEPLOYMENT_NAMESPACE"]
    assert (grafana.origin, grafana.value, grafana.source) == ("default", "grafana", None)
    assert grafana.default == constants.DEFAULT_NAMESPACES["grafana"]

    # A release knob's L0 is the manifest's own `default`, not a code table.
    assert answers["GRAFANA_ENABLED"].value == "true"
    assert answers["GRAFANA_ENABLED"].origin == "default"
    assert answers["GRAFANA_VERSION"].default == "14.2.0"
    assert answers["ALLOY_VERSION"].default == "1.15.0"

    # The templated answers are answered keys too, and the report says which of the two
    # `constants.py` tables owns them: this one is a key of `DEFAULT_STATIC_ANSWERS`, not a namespace.
    pipeline = answers["TEKTON_VERSION_PIPELINE"]
    assert (pipeline.origin, pipeline.value, pipeline.source) == ("default", "1.17.1", None)
    assert pipeline.default == constants.DEFAULT_STATIC_ANSWERS["TEKTON_VERSION_PIPELINE"]

    # ...and the line itself is the shape a `diff` reads, including the `null` that reads as JSON.
    lines = run_show(root).splitlines()
    assert len(lines) == 52, \
        "the printed universe moved with the answered one; 52 since config init generates grafana's password"
    assert lines == sorted(lines), "a diff of two runs is why a human pipes this, and order is that claim"
    assert "GRAFANA_DEPLOYMENT_NAMESPACE = grafana   (default from constants.py)" in lines
    assert "TEKTON_VERSION_PIPELINE = 1.17.1   (default from constants.py)" in lines
    cluster_file = root / ENVIRONMENT / "static.json"
    assert f"CLUSTER_KUBE_CONTEXT = null   (environment from {cluster_file})" in lines
    # The seeded key is the one line in this report that is *not* a default, and it is the reason the
    # two counts are no longer equal in the way they used to be. A key `config init` wrote into a file
    # is tagged `environment` and names that file, so a reader comparing two trees sees it as written
    # material and not as something the binary brought with it — which is the distinction the tagging
    # rule exists
    # to keep, and the difference between "this tree chose grafadmin" and "some build of kubementat
    # thinks grafadmin is a fine name". This line is what keeps a report from classifying a value
    # that is in the tree as one that is not.
    encrypted_file = root / ENVIRONMENT / "static.encrypted.json"
    assert f"GRAFANA_ADMIN_USER = grafadmin   (environment from {encrypted_file})" in lines, \
        "the seeded admin is reported as a default, or not at all; either way the tag would misread it"
    # The generated half cannot print a value this file can compare against, because the whole point of
    # generating is that no two trees agree. The line still has to be there and still has to name the
    # file: a report that said "GRAFANA_ADMIN_PASSWORD = <nothing>" would tell a reader the key is unset
    # on a tree that installed successfully with it. So the assert is about the prefix and the tag.
    password_lines = [line for line in lines if line.startswith("GRAFANA_ADMIN_PASSWORD = ")]
    assert len(password_lines) == 1, f"the generated password is reported {len(password_lines)} times"
    assert f"(environment from {encrypted_file})" in password_lines[0], \
        "a generated answer is reported as a default, which would send a reader to constants.py"
    assert "grafadmin" not in password_lines[0], \
        "the generated password reused the seeded admin name, which is not a secret at all"


def test_the_hardcoded_component_lists_match_their_sources(tmp_path):
    """The drift check behind the assertion above: the lists in this file still describe the sources.

    Without it, `test_show_on_a_fresh_tree...` would agree with itself forever: a universe that grows
    silently because `constants.py` gained a component is a universe the report does not grow with.
    """
    assert set(NAMESPACE_COMPONENTS) == set(constants.DEFAULT_NAMESPACES)
    assert set(SELECTOR_COMPONENTS) == set(constants.DEFAULT_SERVICE_SELECTORS)
    # Read the way the report reads it: the index is a filesystem answer, so no `HelmUtils` and no
    # `__new__` bypass of a constructor that addresses a cluster this check never addresses.
    assert set(RELEASE_COMPONENTS) == set(config_reads_index.read_release_index(
        Config(ENVIRONMENT, None, platform_config_dir=a_tree(tmp_path), preload_configs=False)))


# ---------------------------------------------------------------------------------------------
# 2 and 3 — the tag is the layer that answers.
# ---------------------------------------------------------------------------------------------

def test_a_key_answered_on_the_environment_or_the_team_flips_its_tag_and_names_the_file(tmp_path):
    """`default` → `environment` → `team`, and each answer names the exact file that wrote it.

    The value alone would not prove the order: a team file that was read *first* and an environment
    file that answers are two different claims, and only the tag plus the path distinguish them.
    """
    root = a_tree(tmp_path)
    key = "GRAFANA_DEPLOYMENT_NAMESPACE"

    assert answers_by_key(root)[key].origin == "default"
    assert answers_by_key(root)[key].source is None

    plant(root, {key: "team-ci"})
    answered = answers_by_key(root)[key]
    assert (answered.origin, answered.value) == ("environment", "team-ci")
    assert answered.source == (root / ENVIRONMENT / "static.json")
    # "what it would otherwise have had" is still on the record: answering a key does not erase its L0.
    assert answered.default == constants.DEFAULT_NAMESPACES["grafana"]

    # The team's answer, in the team's own file.
    (root / ENVIRONMENT / TEAM / "static.json").write_text(json.dumps({key: "team-alpha"}, indent=2) + "\n",
                                             encoding="utf-8")
    answered = answers_by_key(root, TEAM)[key]
    assert (answered.origin, answered.value) == ("team", "team-alpha")
    assert answered.source == (root / ENVIRONMENT / TEAM / "static.json"), "the path has to be the team's file"


def test_a_team_scoped_show_says_team_where_a_team_less_show_says_environment(tmp_path):
    """Precedence visible in the tag and not only in the value.

    One tree, two runs, the same key declared at both layers. Without `TEAM` the environment answers
    and the tag says so; with it the team's pair is consulted first and the same key says `team`. A
    report that printed the value but kept the old tag would still look right to a human scanning
    `show` for "which layer should I edit" — which is the question the tag exists to answer.
    """
    root = a_tree(tmp_path)
    key = "GRAFANA_DEPLOYMENT_NAMESPACE"
    plant(root, {key: "team-ci"})
    (root / ENVIRONMENT / TEAM / "static.json").write_text(json.dumps({key: "team-alpha"}, indent=2) + "\n",
                                             encoding="utf-8")

    without_team = answers_by_key(root)[key]
    with_team = answers_by_key(root, TEAM)[key]

    assert (without_team.origin, without_team.value) == ("environment", "team-ci")
    assert (with_team.origin, with_team.value) == ("team", "team-alpha")
    assert without_team.origin != with_team.origin, "the flip is precedence, and it has to be in the tag"
    assert without_team.source != with_team.source

    # And the team's answer is not an accident of which file was opened first: a key the team leaves
    # alone still answers from the environment, in a team-scoped run.
    plant(root, {"BASE_DOMAIN": "example.test"})
    fell_through = answers_by_key(root, TEAM)["BASE_DOMAIN"]
    assert fell_through.origin == "environment"
    assert fell_through.value == "example.test"


def test_the_universe_only_names_keys_an_accessor_can_answer(tmp_path):
    """The guard: every key `show` prints has to be answered by the accessor it claims to derive it from.

    For each derived key, a sentinel goes into the environment's file and the answer has to move with
    it. If a family ever names a key the read path never consults — a renamed suffix, or the
    `<NAME>_DEPLOYMENT_SERVICE` a spec sentence once asked for — the planted value stays invisible, the
    report keeps calling the key a `default`, and this test fails on its own. That is the difference
    between a universe that is defined by what answers and a universe that is defined by a list.
    """
    root = a_tree(tmp_path)
    entries = derived_keys()

    # Every sentinel is a value the L0 does *not* already answer, so a key that no accessor reads
    # cannot pass by coincidence: the planted answer has to show up in the report, and the accessor
    # has to agree with it. `"false"` for ENABLED is deliberate — the manifest's own default is
    # `"true"`, which is the one value that would have made this test tautological.
    sentinels = {}
    for key, (family, component) in entries.items():
        sentinels[key] = {"namespace": f"planted-{component}",
                          "selector": f"planted-{component}",
                          "enabled": "false",
                          "version": "9.9.9-guard"}[family]
    plant(root, sentinels)

    config = Config(ENVIRONMENT, None, platform_config_dir=root, preload_configs=False)
    answers = answers_by_key(root)
    for key, (family, component) in sorted(entries.items()):
        assert key in answers, f"{key} is derived by this module but config show does not print it"
        assert answers[key].value == sentinels[key], (
            f"{key} was declared at the layer that answers and the report still printed something "
            f"else, so no read path answers the key this module names")
        assert answers[key].origin == "environment", (
            f"{key} was planted at the layer that answers and the report still calls it a default, "
            f"so the module names a key the read path does not consult")
        assert answers[key].value == ACCESSORS[family](config, component), (
            f"{key} disagrees with Config.{ACCESSORS[family].__name__}({component!r}): the report and "
            f"the read path are answering two different keys")
        assert answers[key].source == (root / ENVIRONMENT / "static.json"), key





# ---------------------------------------------------------------------------------------------
# 6, 7, 8 — read-only, in every sense.
# ---------------------------------------------------------------------------------------------

def test_show_writes_nothing_and_moves_no_mtime(tmp_path):
    """The read-only claim, as a test.

    A sentence in a docstring says "read-only"; a listing that is identical before and after says
    nobody created the directory they needed, nobody re-serialized a document they only opened, and
    nobody touched an mtime. The whole listing is read, so every file of the tree is opened.
    """
    root = a_tree(tmp_path)
    before = snapshot(root)
    assert {entry[0] for entry in before} == INITIALIZED_TREE, (
        "the fixture tree is not the tree these tests think it is, so a passing equality proves nothing")

    run_show(root)
    run_show(root, TEAM)

    assert snapshot(root) == before, "a verb that answers must not create, rewrite or touch anything"


def test_show_addresses_no_cluster(tmp_path, monkeypatch):
    """The hard constraint: this verb is read-only *and* cluster-free.

    `shell.run` is the one seam every `kubectl`/`helm` call in `src/` goes through, and `subprocess` is
    the belt for anything that found a binary some other way. Both raise if called, so the report's
    own success is the evidence — a verb that resolved a cluster would fail here and not warn.
    """
    root = a_tree(tmp_path)

    def refuse(*args, **kwargs):
        raise AssertionError(f"a cluster was addressed by: {args}")

    monkeypatch.setattr(shell, "run", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    runner = CliRunner(mix_stderr=False)
    shown = runner.invoke(config_module.config, ["show", str(root), ENVIRONMENT])
    assert shown.exception is None and shown.exit_code == 0, shown.output + shown.stderr


def test_a_missing_layer_file_is_refused_the_way_config_set_refuses_it(tmp_path):
    """A layer that is not there is not half-read, and the sentence says which verb makes it.

    Consistency with `config set` is the point: a friendlier `show` that answered defaults from a tree
    whose environment layer was never laid down would report an answer for a tree that has none. And
    nothing is created on the way out of the refusal — the claim that made this a refusal.
    """
    root = a_tree(tmp_path)
    (root / ENVIRONMENT / "static.encrypted.json").unlink()
    before = snapshot(root)

    result = CliRunner(mix_stderr=False).invoke(config_module.config, ["show", str(root), ENVIRONMENT])

    assert result.exit_code != 0
    assert "static.encrypted.json" in result.stderr
    assert "add-environment" in result.stderr, "the refusal names the verb that makes the layer"
    assert snapshot(root) == before

    # ...and naming a team does not hide the half that is missing. A team-scoped read consults the
    # environment's pair underneath the team's, so a tree whose environment layer is incomplete has to
    # be refused for the same reason and not answered from a default under a team's own file.
    team_scoped = CliRunner(mix_stderr=False).invoke(
        config_module.config, ["show", str(root), ENVIRONMENT, TEAM])
    assert team_scoped.exit_code != 0
    assert "add-environment" in team_scoped.stderr
    assert snapshot(root) == before


# ---------------------------------------------------------------------------------------------
# 9 — a declared null is an answer, not a gap.
# ---------------------------------------------------------------------------------------------

def test_a_null_in_a_layer_file_reports_the_layer_and_the_null_and_not_the_default(tmp_path):
    """`config init` writes `null` for a cluster key nobody has answered, and that is what answers.

    The wrong implementation here is the one that treats "present but null" as "absent" and falls back
    to a default: the operator who ran `config init` would be told the tree had answered, and the key
    they had not touched yet would look configured.
    """
    root = a_tree(tmp_path)
    plant(root, {"BASE_DOMAIN": None})

    answer = answers_by_key(root)["BASE_DOMAIN"]

    assert (answer.origin, answer.value) == ("environment", None)
    assert answer.source == (root / ENVIRONMENT / "static.json")

    lines = run_show(root).splitlines()
    assert any(line.startswith("BASE_DOMAIN = null   (environment from ") for line in lines), lines


# ---------------------------------------------------------------------------------------------
# A half-typed answer, which is the only way a tag and a path can disagree.
# ---------------------------------------------------------------------------------------------

def test_a_declared_value_that_answers_nothing_is_tagged_default_and_named_by_the_file(tmp_path):
    """The case `Config._defaulted_warning` warns about, reported rather than warned.

    A blank in `static.json` is a declaration that did not answer, so the tag says `default` (that is
    what a running command would use) and the path still names the file that is holding the
    half-finished edit. Either fact alone would be a half-truth: the tag alone hides the file, the
    file alone hides that nothing it declares is what answers.
    """
    root = a_tree(tmp_path)
    key = "GRAFANA_DEPLOYMENT_NAMESPACE"
    plant(root, {key: "   "})

    answer = answers_by_key(root)[key]
    line = next(line for line in run_show(root).splitlines() if line.startswith(key + " = "))

    assert answer.origin == "default"
    assert answer.source == (root / ENVIRONMENT / "static.json")
    assert line == f"{key} = grafana   (default from {config_answers.CONSTANTS_DEFAULT_SOURCE})", (
        "the tag says the default answered and names which default it is, while the source above "
        "still names the file holding the half-finished edit")
