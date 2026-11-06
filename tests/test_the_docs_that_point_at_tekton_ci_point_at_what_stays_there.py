"""The operator-facing prose that says where `tekton_ci/` is, kept true.

This is the class of defect a move leaves behind rather than causes: nothing crashes when the
product's Tasks and Pipelines stop living in a directory called `tekton_ci/tasks`, the reader simply
receives a direction that does not exist. A wheel install cannot `cd` into `tekton_ci/tasks` and an
operator who tries gets one sentence telling them the directory is missing — and the sentence is
*right*, which is what makes prose the last place the truth lives.

So this file checks the four documents an operator actually reads, and it does not care about the
plans or the changelog: `docs/plans/**`, `docs/research/**` and `CHANGELOG.md` are the record of
decisions taken against the tree that existed then, and rewriting them would replace history with a
comment about it. What is checked here is the present tense.

The rule has one exception that is not about wording. `tekton_ci/pipeline-runs/` really is in the
checkout — it is output, it is read back by `kmt tekton run`, and moving it would put a
half-written manifest inside site-packages. A sentence naming it is correct however many assets
move, and this file would be wrong to flag one.

The other half of the check is `templates/`, which moved whole and so left no checkout
half behind. It is checked the same way — line by line, with the same kind of exception table, and
with the same demand that the filesystem agree with the prose rather than with a constant here.
A sentence about `templates/` is a direction twice over: the reader is sent to a directory that is
not there, and the command they run there answers nothing.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The four documents that describe the running installation to a human. `docs/specifications/` is
# deliberately out: it is a record of the move itself and it quotes the pre-move paths as
# the thing being changed. `docs/README_PLATFORM_CONFIG.md` joined the list because the
# routing keys it documents are the same shipped data, and a guard that checked three of the four
# operator documents would leave the fourth one free to drift.
OPERATOR_DOCUMENTS = (REPO_ROOT / "README.md",
                      REPO_ROOT / "docs/README_TEKTON.md",
                      REPO_ROOT / "docs/README_PLATFORM_CONFIG.md",
                      REPO_ROOT / "docs/index-overview.md")

# A path that used to exist is not a direction. Each of these is a sentence about a deletion, and
# each says so in its own words, so the exception is stated by line rather than guessed at.
HISTORICAL = (
    # A tombstone: the Task is deleted, and naming it is the point.
    "run-automation-script-for-environment.yml",
    # The measurement that proves the directory once held 20, quoted from `git ls-tree`.
    "git ls-tree",
    # Where the *output* used to be written, which is the comparison this sentence exists to make.
    "it used to be written beside the template",
    # A tombstone for MYSQL_DATABASE_CONFIGURATION: the sentence names the template only to
    # say which two files the key left, and after that deletion nothing points there any more.
    "and `automation.py` first (FIN-52)",
)

# Spellings that are not a direction anywhere: the examples tree, the output directory, and the
# correct post-move spelling of the asset root.
EXCUSED = ("examples/tekton_ci", "tekton_ci/pipeline-runs", "assets/tekton_ci",
           "assets/templates", "src/kubementat/core/assets")

MOVED_ASSET_SUBDIRECTORY = re.compile(r"tekton_ci/(?:tasks|pipelines|triggers)(?:[/{,]|\b)")

# `templates/` spelled from the checkout root. The lookbehind is what makes this a check of the
# *spelling* rather than of the word: `assets/templates/` and `nginx-example/templates/` are paths
# that exist, and a bare `templates/...` is the one that does not.
MOVED_TEMPLATE_ROOT = re.compile(r"(?<![\w/.])templates/")

# A command that names the bare directory without a slash after it, which is the other way the
# sentence fails: `find templates -type f | wc -l` does not print a stale path, it prints an error
# nobody reads because the `wc` swallows it. `find src/kubementat/core/assets/templates ...` does not
# match, because the path that follows the command is what the check is about.
COMMAND_NAMING_A_MOVED_ROOT = re.compile(r"\b(?:cd|cat|cp|find|ls|rsync)\s+(?:-\S+\s+)*templates\b")


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def _stale_lines(document, *patterns):
    """`(line_number, line)` for every line of `document` that matches a pattern and no exemption.

    Line by line, so the failure names the sentence: the same shape as
    `test_no_operator_document_sends_a_reader_to_a_moved_asset_directory`, which is the half this
    was factored out of rather than a new idea.
    """
    hits = []
    for number, line in enumerate(_lines(document), start=1):
        if not any(pattern.search(line) for pattern in patterns):
            continue
        if any(excuse in line for excuse in EXCUSED) or any(old in line for old in HISTORICAL):
            continue
        hits.append(f"{document.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    return hits


def test_no_operator_document_sends_a_reader_to_a_moved_asset_directory():
    """Every `tekton_ci/{tasks,pipelines,triggers}` in the operator docs is either history or wrong.

    Checked line by line rather than file by file, so the answer names the sentence rather than the
    document — which is the difference between fixing one line and reading three files to find out
    which one this is about.
    """
    stale = []
    for document in OPERATOR_DOCUMENTS:
        stale += _stale_lines(document, MOVED_ASSET_SUBDIRECTORY)

    assert stale == [], "the reader is sent somewhere nothing is:\n  " + "\n  ".join(stale)


def test_no_operator_document_sends_a_reader_to_a_checkout_templates_directory():
    """Every bare `templates/` in the operator docs is either history or wrong, same shape as above.

    `templates/` moved whole into the package on 2026-11-02 and kept no half in the
    checkout, so there is no directory of that name for a sentence to mean. This is the same check
    `tekton_ci/**` already gets, applied to the other half of the moved data, and it fails on one
    sentence rather than one file for the same reason.
    """
    stale = []
    for document in OPERATOR_DOCUMENTS:
        stale += _stale_lines(document, MOVED_TEMPLATE_ROOT, COMMAND_NAMING_A_MOVED_ROOT)

    assert stale == [], "the reader is sent to a templates directory no install has:\n  " \
           + "\n  ".join(stale)


def test_the_directory_list_says_what_tekton_ci_holds_now():
    """`README.md` still lists the checkout's directories, and `tekton_ci` is not what it was.

    The list is what a reader uses to decide where to look, so the half that moved has to be said
    there and not only in a changelog nobody reads: what stays is output, what shipped is in the
    package. If the sentence is dropped this fails, and if `pipeline-runs` is dropped from the
    checkout's directory list the sentence above stops being true.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pipeline-runs" in readme, "the one directory that stayed is missing from the list"
    assert "inside the installed" in readme or "ships inside" in readme.replace("  ", " "), \
        "the list no longer says where the Tasks and Pipelines live: " \
        "a reader is left with `tekton_ci/` and no hint that it is output"


def test_the_lists_say_which_paths_are_shipped_data_and_which_are_checkout_output():
    """A reader decides where to look from two places: README's directory list and the overview's diagram.

    Both have to say what is product data and what is this checkout's own output, because the two
    answers lead to different files: the shipped half is what `kmt` reads and cannot be edited in
    place, the checkout half is what a command writes and a reader may edit. If the diagram drops
    the label, the sentence in the README list is still true but the reader who was deciding where
    to `cd` has already picked wrong.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    overview = (REPO_ROOT / "docs/index-overview.md").read_text(encoding="utf-8")

    assert "shipped data" in readme, \
        "README's directory list no longer names the shipped half: " \
        "the reader has `tekton_ci/` and `platform_config/` and nothing telling them which is which"
    assert "product data" in overview, \
        "the overview's diagram no longer says which arrow is product data"
    assert "output" in overview, \
        "and which arrow is what a command writes here"


def test_the_templates_the_prose_names_are_where_the_prose_says_they_are():
    """The same demand as the manifest check, made of the shipped trees: 7 templates, 11 values.

    The counts are the ones `docs/index-overview.md` prints for both roots, so adding or deleting a
    shipped file moves this guard and the document together. The named files are the ones the operator
    docs cite by path, which is the half no count can catch: a file renamed under either asset root
    leaves every count intact and leaves the sentence pointing at a path that is not there.

    The migration split the two roots. `templates/` kept the files `initialize` still copies; `values/` got the
    manifest and its defaults, which are applied and not copied, so a guard that counted only the old
    root would be counting a tree that is no longer the whole answer.

    `kubementat_components/` used to earn an assert of its own — one directory, the component copy.
    It does not exist any more, and that is a fact about the tree rather than a count of zero: everything
    applied moved out of it into `values/`, and the last file in it was a `Service` nobody
    read. Asserting a count over a directory that is absent would be asserting it by `iterdir()`, and
    `iterdir()` on a missing path is an error the operator does not benefit from.
    """
    from asset_paths import asset_path, values_path

    root = asset_path("templates")
    shipped = sorted(p for p in root.rglob("*") if p.is_file())
    values = sorted(p for p in shipped if "values" in p.name)
    shipped_values = sorted(p for p in values_path().rglob("*") if p.is_file())

    assert len(shipped) == 7, f"{len(shipped)} templates ship, not the 7 the docs count"
    assert len(values) == 2, f"{len(values)} values templates ship, not the 2 the docs count"
    assert not (root / "environment" / "kubementat_components").exists(), \
        "the old component copy came back: it is applied by nothing and copied by nothing"
    assert len(shipped_values) == 11, \
        f"{len(shipped_values)} files ship under values/, not the 11 (one manifest, ten defaults)" \
        "the docs count"
    assert not (REPO_ROOT / "templates").exists(), \
        "the checkout carries templates/ again, so the two trees have diverged"
    assert not (REPO_ROOT / "values").exists(), \
        "the checkout carries values/ too, which is the same divergence one directory over"

    for relative in ("environment/static.json.template",
                     "environment/static.encrypted.json.template",
                     "environment/team/static.encrypted.json.template"):
        assert (root / relative).is_file(), f"the operator docs name `templates/{relative}`; it does not ship"

    for relative in ("helmfile.yaml.gotmpl", "traefik/values.yaml",
                     "monitoring/prometheus_values.yaml"):
        assert (values_path() / relative).is_file(), \
            f"the operator docs name `values/{relative}`; it does not ship"



def test_the_moved_assets_are_still_where_the_checkout_says_they_are():
    """The prose is only worth reading if the tree agrees with it.

    Both halves of the split are counted and the numbers are the ones the README names, so a manifest
    added or deleted moves this guard and the document together -- which is the only way a count in prose
    stays honest. The shipped half is 38 and the checkout half is still 4.
    """
    from asset_paths import tekton_ci_path

    shipped = sorted(tekton_ci_path(".").rglob("*.yml"))
    output = sorted((REPO_ROOT / "tekton_ci/pipeline-runs").rglob("*.yml"))

    assert len(shipped) == 38, f"{len(shipped)} manifests ship, not the 38 the docs count"
    assert len(output) == 4, f"{len(output)} pipeline runs in the checkout, not the 4 the docs count"
    assert not (REPO_ROOT / "tekton_ci/tasks").exists(), \
        "the checkout carries the Tasks again, so the two trees have diverged"
    assert not (REPO_ROOT / "tekton_ci/pipelines").exists(), \
        "the checkout carries the Pipelines again, so the two trees have diverged"
    assert not (REPO_ROOT / "tekton_ci/triggers").exists(), \
        "the checkout carries the trigger templates again, so the two trees have diverged"


def test_the_readme_directory_list_names_directories_that_exist():
    """A list of the checkout's directories is the one part of a README a reader skims and trusts.

    It is checked against the filesystem for the same reason every other sentence here is: the list
    said `cli/` and `lib/` for as long as `src/` has existed, and both are phantoms — the packages
    are `src/cli` and `src/kubementat`. A reader who `cd`s into a directory the list promises gets a
    shell error and no hint, which is the defect this file keeps calling a direction.

    Only the top-level `__name__` entries and the one sub-bullet that gives a path are checked. The
    prose under each is prose.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    listed = re.findall(r"^- __([^_]+)__(?::|$)", readme, flags=re.MULTILINE)
    assert listed, "README no longer has a `__directory__` list for this guard to read"

    missing = [name for name in listed if not (REPO_ROOT / name).exists()]
    assert missing == [], (
        f"README's directory list promises {missing}, which this checkout does not have; "
        "either the path is wrong in prose or the tree moved and the list did not follow")

    assert "examples/helm_charts/nginx-example" in readme, \
        "the example chart's real location is no longer spelled out"
    assert (REPO_ROOT / "examples" / "helm_charts" / "nginx-example").is_dir(), \
        "the path README gives for the example chart does not exist"
