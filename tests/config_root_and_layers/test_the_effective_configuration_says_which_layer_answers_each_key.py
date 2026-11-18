"""What the effective configuration answers, and from which file."""

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import answers as config_answers
from kubementat.config.init import add_team, init_configuration
from kubementat.core import constants
from kubementat.core.config import release_index as config_reads_index
from kubementat.core.config.config import Config

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
# The component families are read out of `constants.py` and out of the shipped manifest rather than
# typed here, so a component that arrives in a source arrives in the report with no edit to this
# file. The check that they are still populated is
# `test_the_hardcoded_component_lists_match_their_sources`, and the assertion they feed says `show`
# prints exactly that set and nothing else.
# ---------------------------------------------------------------------------------------------

CLUSTER_KEYS = ("CLUSTER_KUBECONFIG_PATH", "CLUSTER_KUBE_CONTEXT")

CLUSTER_KEYS = ("CLUSTER_KUBECONFIG_PATH", "CLUSTER_KUBE_CONTEXT")

# The component families are read from the tables and the manifest that own them, not listed here.
NAMESPACE_COMPONENTS = tuple(sorted(constants.DEFAULT_NAMESPACES))

SELECTOR_COMPONENTS = tuple(sorted(constants.DEFAULT_SERVICE_SELECTORS))


def release_components() -> tuple:
    """The components the shipped manifest labels, read the way the report reads it."""
    unread = Config(ENVIRONMENT, None,
                    platform_config_dir=Path("/a-root-this-suite-reads-no-configuration") / "platform_config",
                    preload_configs=False)
    return tuple(sorted(config_reads_index.read_release_index(unread)))


RELEASE_COMPONENTS = release_components()


def derived_keys() -> dict:
    """`{key: (family, component)}` for every key the module derives rather than reads out of a file.
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
    """The whole universe of a fresh tree: declared keys, the three derived families, and the answers
    that live only in `constants` — the ten static ones the templated answers added, and the
    encrypted seed `config init` writes.
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
    argv = (["show", ENVIRONMENT] + ([team] if team else [])
            + ["--config-dir", str(root)])
    result = CliRunner(mix_stderr=False).invoke(config_module.config, argv)
    assert result.exit_code == 0, result.output + result.stderr
    return result.stdout


def plant(root, document: dict) -> None:
    """Write answers into the environment's own `static.json`, the way `config set` would."""
    path = root / ENVIRONMENT / "static.json"
    existing = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(existing | document, indent=2) + "\n", encoding="utf-8")


def snapshot(root: Path) -> list:
    """Every path in the tree with its mtime and its bytes."""
    listing = []
    for path in sorted(root.rglob("*")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        listing.append((str(path.relative_to(root)), path.stat().st_mtime_ns, digest))
    return listing


# ---------------------------------------------------------------------------------------------
# 1 — the universe, and the tags on a fresh tree.
# ---------------------------------------------------------------------------------------------

def test_show_on_a_fresh_tree_tags_every_key_and_prints_no_other_keys(tmp_path):
    """The whole of the tagging rule in one assertion: every key tagged, and no fifth kind."""
    root = a_tree(tmp_path)

    answers = answers_by_key(root)

    assert set(answers) == expected_universe(), (
        "config show's universe drifted from the five sources it is defined by: "
        f"missing {sorted(expected_universe() - set(answers))}, extra {sorted(set(answers) - expected_universe())}")
    # The size of that universe is what the five sources add up to, so it is not written here. What is
    # written is the non-vacuity of each source, because a universe that shrank to nothing would pass a
    # set comparison against itself and a hard-coded count would have caught it for the wrong reason.
    for family, keys_in_family in (("cluster", CLUSTER_KEYS),
                                   ("derived", derived_keys()),
                                   ("static", constants.DEFAULT_STATIC_ANSWERS),
                                   ("encrypted", constants.DEFAULT_ENCRYPTED_ANSWERS),
                                   ("generated", constants.GENERATED_ENCRYPTED_ANSWERS)):
        assert keys_in_family, f"the {family} source of the universe is empty, so it answers nothing"
    assert len(answers) == len(expected_universe()), \
        "the answered universe and the declared one are the same keys, so their sizes cannot differ"

    # A key `config init` wrote as `null` is declared, answers `null`, and is tagged `environment`.
    for key in CLUSTER_KEYS:
        assert answers[key].origin == "environment", key
        assert answers[key].value is None, key
        assert answers[key].source == (root / ENVIRONMENT / "static.json"), key

    # A component namespace that no file mentions is answered by constants.py, and says so.
    grafana = answers["GRAFANA_DEPLOYMENT_NAMESPACE"]
    assert (grafana.origin, grafana.value, grafana.source) == ("default", "grafana", None)
    assert grafana.default == constants.DEFAULT_NAMESPACES["grafana"]

    # A release knob's default is the manifest's own `default`, not a code table.
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
    assert len(lines) == len(answers), \
        f"the report printed {len(lines)} lines for the {len(answers)} keys this tree answers, so a key " \
        "is either counted twice or dropped on the way to the terminal"
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


# ---------------------------------------------------------------------------------------------
# 2 and 3 — the tag is the layer that answers.
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# 6, 7, 8 — read-only, in every sense.
# ---------------------------------------------------------------------------------------------

def test_show_writes_nothing_and_moves_no_mtime(tmp_path):
    """The read-only claim, as a test."""
    root = a_tree(tmp_path)
    before = snapshot(root)
    assert {entry[0] for entry in before} == INITIALIZED_TREE, (
        "the fixture tree is not the tree these tests think it is, so a passing equality proves nothing")

    run_show(root)
    run_show(root, TEAM)

    assert snapshot(root) == before, "a verb that answers must not create, rewrite or touch anything"


# ---------------------------------------------------------------------------------------------
# 9 — a declared null is an answer, not a gap.
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# A half-typed answer, which is the only way a tag and a path can disagree.
# ---------------------------------------------------------------------------------------------

def test_a_declared_value_that_answers_nothing_is_tagged_default_and_named_by_the_file(tmp_path):
    """The case `Config._defaulted_warning` warns about, reported rather than warned."""
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
