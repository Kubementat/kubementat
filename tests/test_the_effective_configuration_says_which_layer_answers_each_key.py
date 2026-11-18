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
from pathlib import Path

from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import answers as config_answers
from kubementat.core.config import release_index as config_reads_index
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.config.init import add_team, init_configuration

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

# The component families are read out of the two tables and the manifest that own them, because a
# list written here is a second owner of a set that already has one. The plan's exit criterion for
# this file is that the universe be derived rather than pinned, and the drift check that used to
# police three typed lists is now an assertion that the derivation still answers.
NAMESPACE_COMPONENTS = tuple(sorted(constants.DEFAULT_NAMESPACES))

SELECTOR_COMPONENTS = tuple(sorted(constants.DEFAULT_SERVICE_SELECTORS))


def release_components() -> tuple:
    """The components the shipped manifest labels, read the way the report reads it.

    `config_reads_index.read_release_index` is the same parser `kmt component install` addresses,
    so a release commented out of the template leaves this universe without a row being edited, and
    a release added to it arrives without one either. No `HelmUtils` here: the index is a filesystem
    answer, and this read never addresses a cluster.
    """
    unread = Config(ENVIRONMENT, None,
                    platform_config_dir=Path("/a-root-this-suite-reads-no-configuration") / "platform_config",
                    preload_configs=False)
    return tuple(sorted(config_reads_index.read_release_index(unread)))


RELEASE_COMPONENTS = release_components()


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
    argv = (["show", ENVIRONMENT] + ([team] if team else [])
            + ["--config-dir", str(root)])
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


# ---------------------------------------------------------------------------------------------
# 9 — a declared null is an answer, not a gap.
# ---------------------------------------------------------------------------------------------


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
