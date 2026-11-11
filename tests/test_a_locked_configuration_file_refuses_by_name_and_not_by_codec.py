"""A locked configuration file is refused by name and not by codec.

`UnicodeDecodeError` is a `ValueError` and not an `OSError`, and before this ticket that fact made
every read of an encrypted kubementat file answer the same question in its own words: four sites
caught it and described it in four different sentences, two did not catch it at all, and
`config_init.cluster_is_unaddressed` swallowed it and answered *"this environment addresses no
cluster"* — the one place in `src/` that printed a confident falsehood about a file it had never
read. The seam is `read_decrypted(path, what)` in `kubementat/config_reads.py`, and the rule it
enforces is **one sentence, many error types**: every caller keeps its own exception and its own
shape, and the sentence is the same one.

Seven claims, one per read that the census measured, plus two about what the seam must not do:

1. `Config._load_json_file` — the bottom of the encrypted-value graph, which everything downstream
   (`helm_utils`, `grafana_client`, `tekton_utils`, `automation`, `config show`) reads
   through. A locked layer file refuses with the file's name and git-crypt.
2. `config_init._read_document` — the read-modify-write verbs (`config set`, `config unset`,
   `config set --generate`). It keeps its own refusal wording for unreadable and non-JSON, which is
   why a `LockedConfigFileError` (a `ClickException`, not an `OSError`) walks past that handler
   intact.
3. `config_init.cluster_is_unaddressed` — the fixed site. An undecodable file **raises**; absent,
   `null` and blank still answer `True`. Both halves are asserted here because the bug was the
   confusion between them, and the old docstring's rationalization of the swallowing is gone.
4. `config_validate._read_object_file` — a locked file becomes the finding
   `locked-configuration-file` and validate **keeps collecting** the rest of the tree's faults. A
   verdict command that raises early stops being a verdict.
5. `config_validate._cluster_fact_finding` — silent about a file it could not read. It used to print
   "is null" over exactly that file, which is the same false sentence as row 3 wearing a warning.
6. `cluster._static_declaration` and 7. `cluster._read_kubeconfig` — the cluster-resolution reads.
   Both re-raise `ClusterFileUnreadableError`, the module's own type, with the seam's sentence inside
   it and neither rewriting it.
8. **The seam does not route around `missing_team_config_files`.** A missing *optional* team file
   still degrades the way it did: a warning at load and a named refusal at read. This is the
   falsification of the seam's own boundary — `FileNotFoundError` propagates and nothing converts it.
9. **The census is a test.** The read graph is walked out of the tree rather than asserted from a
   list, so a sixth read that forgets the seam fails here.

Falsifications run while writing this file, each one breaking what it names: letting one site keep
its own `read_text` fails that site's row and the census row alone; making `cluster_is_unaddressed`
answer `True` over ciphertext fails row 3 alone; making `read_decrypted` raise on a missing file
fails row 8 alone.
"""

import ast
import io
import json
import logging
import re
import tokenize
from pathlib import Path

import pytest

from kubementat import config_init, config_validate
from kubementat.cluster import ClusterFileUnreadableError, ClusterTarget, _read_kubeconfig, _static_declaration
from kubementat.config import Config
from kubementat.config_reads import LockedConfigFileError, read_decrypted
from kubementat.config_validate import validate_tree

ENVIRONMENT = "dev"
TEAM = "alpha"

# A stub that is not valid UTF-8, which is all "locked" means to these tests. Real git-crypt
# ciphertext is binary and nobody is expected to install it to read this file; the seam is a decode
# boundary and nothing else.
LOCKED = b"\x00\x01git-crypt\xc3\x28\xc3\xb1"

# The one thing every one of these refusals must say, and the one thing none of them may say.
GIT_CRYPT = "git-crypt"
CODEC = "codec"


def refused_by_name(message: str, path) -> None:
    """The sentence these tests actually check: the file, asked about as git-crypt's.

    Deliberately not `pytest.raises`-style matching on the exception: the row is about what an
    operator reads, so it is asserted on the string and the same assertion holds whether the site
    raised a `ClickException`, its own named error, or reported a finding.
    """
    text = str(message)
    assert str(path) in text, f"the refusal does not name the file: {text}"
    assert GIT_CRYPT in text, f"the refusal never asks about git-crypt: {text}"
    assert CODEC not in text.lower(), f"the refusal blames the codec and not the lock: {text}"


def lock(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(LOCKED)
    return path


# ---------------------------------------------------------------------------------------------
# 1 — `Config._load_json_file`, the read every other encrypted value read reaches.
# ---------------------------------------------------------------------------------------------


def config_root(tmp_path, environment=ENVIRONMENT, team=None) -> Path:
    """A `Config` pointed at a throwaway tree, reading lazily so the read is inside the command."""
    root = tmp_path / "checkout"
    (root / "platform_config" / environment).mkdir(parents=True)
    (root / "platform_config" / environment / "static.json").write_text(
        json.dumps({"ENVIRONMENT": environment, "CLUSTER_KUBE_CONTEXT": None}), encoding="utf-8")
    (root / "platform_config" / environment / "static.encrypted.json").write_text(
        json.dumps({}), encoding="utf-8")
    if team is not None:
        team_dir = root / "platform_config" / environment / team
        team_dir.mkdir()
        for name in ("static.json", "static.encrypted.json"):
            (team_dir / name).write_text("{}", encoding="utf-8")
    return root


def a_config(root, team=None) -> Config:
    return Config(ENVIRONMENT, team, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"), preload_configs=False)


def test_a_locked_layer_file_refuses_the_command_that_reads_it_through_config(tmp_path):
    """Every `config.get('..._encrypted')` in the product is this read, so one refusal covers them.

    A `Config` built `preload_configs=False` answers nothing until asked, which is what makes this
    a command and not a constructor: the lazy gate is where the census said the read lives.
    """
    root = config_root(tmp_path)
    locked = lock(root / "platform_config" / ENVIRONMENT / "static.encrypted.json")

    config = a_config(root)
    with pytest.raises(LockedConfigFileError) as refused:
        config.get("env_static_encrypted")

    refused_by_name(refused.value.format_message(), locked)


def test_a_locked_layer_file_refuses_a_plain_key_read_too_because_one_load_reads_all_four(tmp_path):
    """The consequence the census warned about, said rather than discovered.

    `Config` loads all four layer files on the first answer of any category, so asking a plain
    `static.json` key over a locked encrypted file still refuses. That is a question about *when*
    layers load and not about how one file is read; the seam does not quietly change it.
    """
    root = config_root(tmp_path)
    lock(root / "platform_config" / ENVIRONMENT / "static.encrypted.json")

    config = a_config(root)
    with pytest.raises(LockedConfigFileError):
        config.get("env_static")


# ---------------------------------------------------------------------------------------------
# 2 — `config_init._read_document`, the read-modify-write verbs.
# ---------------------------------------------------------------------------------------------


def test_a_locked_file_refuses_config_set_without_editing_the_ciphertext(tmp_path):
    """`config set --encrypted` reads the file it is about to edit. It cannot go on over ciphertext.

    This is the row that used to be a `UnicodeDecodeError` traceback: the docstring promised "a
    refusal that says which file is unreadable" and this input never reached it.
    """
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    locked = lock(root / ENVIRONMENT / "static.encrypted.json")
    before = locked.read_bytes()

    with pytest.raises(Exception) as refused:
        config_init.set_answer(root, ENVIRONMENT, "GRAFANA_ADMIN_USER", "admin", encrypted=True)

    refused_by_name(str(refused.value), locked)
    assert locked.read_bytes() == before, "the verb wrote through a file it could not read"


def test_a_locked_file_refuses_config_unset_because_unset_reads_the_whole_layer_pair(tmp_path):
    """The verb the spec never named: `unset` opens both files of the layer, locked or not.

    It is the same refusal wearing a different verb, which is the point of one seam — the sentence
    is not written per call site.
    """
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    locked = lock(root / ENVIRONMENT / "static.encrypted.json")

    with pytest.raises(Exception) as refused:
        config_init.unset_answer(root, ENVIRONMENT, "GRAFANA_ADMIN_USER")

    refused_by_name(str(refused.value), locked)


# ---------------------------------------------------------------------------------------------
# 3 — `cluster_is_unaddressed`, the fixed site.
# ---------------------------------------------------------------------------------------------


def test_cluster_is_unaddressed_raises_over_a_locked_file_and_never_answers_true(tmp_path):
    """The worst failure in the tree used to be this function's silence.

    It answered *"this environment addresses no cluster"* over bytes it had not decoded, and its
    docstring argued that a broken read is "the tree's own problem". Both halves are gone: the
    predicate is allowed to say `True` only about a file that told it so.
    """
    locked = lock(tmp_path / "static.json")

    with pytest.raises(LockedConfigFileError) as refused:
        config_init.cluster_is_unaddressed(locked)

    refused_by_name(str(refused.value), locked)


@pytest.mark.parametrize("document", [None, {"CLUSTER_KUBE_CONTEXT": None},
                                      {"CLUSTER_KUBE_CONTEXT": "   "}])
def test_cluster_is_unaddressed_still_answers_true_for_the_three_states_that_mean_nothing(tmp_path, document):
    """Absent, `null`, and blank — the half of the predicate that is *not* the bug.

    A fixed refusal must not become a predicate that refuses to answer its real question, because
    `config init` prints this sentence on every fresh tree that has not named a context yet.
    """
    path = tmp_path / "static.json"
    if document is None:
        assert config_init.cluster_is_unaddressed(path) is True
        return
    path.write_text(json.dumps(document), encoding="utf-8")
    assert config_init.cluster_is_unaddressed(path) is True


def test_cluster_is_unaddressed_answers_false_about_a_file_that_named_a_context(tmp_path):
    """And the third answer, so the row above cannot be read as "this function always says `True`"."""
    path = tmp_path / "static.json"
    path.write_text(json.dumps({"CLUSTER_KUBE_CONTEXT": "dev-cluster"}), encoding="utf-8")
    assert config_init.cluster_is_unaddressed(path) is False


# ---------------------------------------------------------------------------------------------
# 4 and 5 — `config validate`: locked files become findings, and no finding invents a value.
# ---------------------------------------------------------------------------------------------


def a_validated_tree(tmp_path, team=TEAM) -> Path:
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    config_init.add_team(root=root, environment=ENVIRONMENT, team=team)
    return root


def test_validate_reports_a_locked_layer_file_and_keeps_collecting_the_rest(tmp_path):
    """The seam must not turn a verdict command into an early raise.

    Two faults are planted and both come back in one run: the locked encrypted layer and a team file
    that is not an object. A validate that raises on the lock would report one fault and hide the
    other, which is the opposite of what the earlier check for this command settled.
    """
    root = a_validated_tree(tmp_path)
    locked = lock(root / ENVIRONMENT / "static.encrypted.json")
    (root / ENVIRONMENT / TEAM / "static.json").write_text("[1, 2]", encoding="utf-8")

    report = validate_tree(root, ENVIRONMENT, TEAM)
    by_code = {finding.code: finding for finding in report.errors}

    assert "locked-configuration-file" in by_code, f"the lock was not reported: {sorted(by_code)}"
    refused_by_name(by_code["locked-configuration-file"].message, locked)
    assert "layer-file-not-an-object" in by_code, "validate stopped collecting at the first fault"


def test_validate_never_prints_cluster_not_addressed_about_a_file_it_could_not_read(tmp_path):
    """The rule that a warning may not state what a file never answered, aimed at the second site
    that was saying it wrongly.

    With `CLUSTER_KUBE_CONTEXT` unreadable the honest statement is the locked-file finding; a
    "the cluster is null" warning would put the falsehood back in, only quieter.
    """
    root = a_validated_tree(tmp_path)
    lock(root / ENVIRONMENT / "static.json")

    report = validate_tree(root, ENVIRONMENT, TEAM)

    assert "locked-configuration-file" in {f.code for f in report.errors}
    assert "cluster-not-addressed" not in {f.code for f in report.warnings}


def test_the_predicate_says_nothing_about_a_locked_file_to_the_verdict_that_asks(tmp_path):
    """The same claim one level down, where the warning is built: no finding at all, not a raise.

    `_cluster_fact_finding` is called on a tree it cannot read. It collects nothing and prints
    nothing, and the finding that *is* about the file is the one `validate` raises on its own read.
    """
    root = a_validated_tree(tmp_path)
    lock(root / ENVIRONMENT / "static.json")
    findings = []

    config_validate._cluster_fact_finding(root, ENVIRONMENT, findings)

    assert findings == [], f"it answered about a file it could not read: {findings}"


# ---------------------------------------------------------------------------------------------
# 6 and 7 — the cluster-resolution reads, which keep their own exception and the seam's sentence.
# ---------------------------------------------------------------------------------------------


def test_a_locked_static_json_is_refused_by_the_reader_that_resolves_a_cluster(tmp_path):
    """`cluster._static_declaration` keeps `ClusterFileUnreadableError` and borrows the sentence.

    One sentence, many error types: the type is what a machine-readable report needs, and the
    sentence is what
    an operator has to act on. Before this ticket a `UnicodeDecodeError` walked straight past the
    two handlers here and printed a codec traceback about a file that was simply locked.
    """
    locked = lock(tmp_path / "static.json")

    with pytest.raises(ClusterFileUnreadableError) as refused:
        _static_declaration(locked)

    refused_by_name(str(refused.value), locked)


def test_a_locked_file_pointed_at_as_a_kubeconfig_is_refused_by_name(tmp_path):
    """The kubeconfig graph, which is the second read graph and not part of the encrypted one.

    Someone pointed `CLUSTER_KUBECONFIG_PATH` at a locked file. The answer is the same sentence as
    the layer files' and this module's own error type — the same "one sentence, many error types"
    applies to the kubeconfig too, even though kubeconfig material is not itself encrypted.
    """
    locked = lock(tmp_path / "locked-kubeconfig")

    with pytest.raises(ClusterFileUnreadableError) as refused:
        ClusterTarget.from_kubeconfig(str(locked), "dev-cluster", ENVIRONMENT)

    refused_by_name(str(refused.value), locked)


def test_the_kubeconfig_reader_names_the_entry_that_locked_and_not_the_one_that_worked(tmp_path):
    """`$KUBECONFIG` is a list, and the refusal has to say which entry failed.

    The second file is fine, so the failure cannot be blamed on "the kubeconfig" as a whole.
    """
    good = tmp_path / "good-kubeconfig"
    good.write_text("apiVersion: v1\ncontexts: []\nclusters: []\n", encoding="utf-8")
    locked = lock(tmp_path / "locked-kubeconfig")

    with pytest.raises(ClusterFileUnreadableError) as refused:
        _read_kubeconfig((locked, good))

    refused_by_name(str(refused.value), locked)


# ---------------------------------------------------------------------------------------------
# 8 — the boundary that must survive: missing is still missing, and is not locked.
# ---------------------------------------------------------------------------------------------


def test_a_missing_optional_team_file_still_degrades_and_does_not_become_a_lock(tmp_path):
    """The proof `read_decrypted` did not route around `missing_team_config_files`.

    The team's file is optional: `Config` warns at load, records the category as missing, and
    refuses at read with the file's name. A helper that raised its own error on a missing file would
    have made every team without an encrypted file fail like a locked one, which is the second
    falsehood this ticket had to avoid.
    """
    root = config_root(tmp_path, team=TEAM)
    missing = root / "platform_config" / ENVIRONMENT / TEAM / "static.encrypted.json"
    missing.unlink()

    config = a_config(root, team=TEAM)
    config.get("env_static")  # the lazy gate is where the census says the four files are read
    assert config.missing_team_config_files.get("team_static_encrypted") == \
        str(config.team_static_encrypted_config_path)
    with pytest.raises(Exception) as refused:
        config.get("team_static_encrypted")

    message = str(refused.value)
    assert str(missing) in message
    assert GIT_CRYPT not in message, f"a missing file was described as a locked one: {message}"


def test_a_missing_environment_layer_file_is_still_file_not_found(tmp_path):
    """The same boundary one level up, where nothing guards the read first.

    The environment-level files are not optional, so `Config` reaches them without asking whether
    they are there, and the answer has to stay "this file was never created" — not "locked", which
    would send an operator to run `git-crypt unlock` against a tree that has no ciphertext in it.
    """
    root = config_root(tmp_path)
    (root / "platform_config" / ENVIRONMENT / "static.encrypted.json").unlink()

    config = a_config(root)
    with pytest.raises(FileNotFoundError):
        config.get("env_static_encrypted")


def test_a_missing_optional_team_file_warns_once_at_load(tmp_path, caplog):
    """The warning half of the same machinery, which the seam did not touch either."""
    root = config_root(tmp_path, team=TEAM)
    (root / "platform_config" / ENVIRONMENT / TEAM / "static.encrypted.json").unlink()

    with caplog.at_level(logging.WARNING):
        a_config(root, team=TEAM).get("env_static")

    assert any("static encrypted config" in record.getMessage().lower()
               or "no team" in record.getMessage().lower() for record in caplog.records), \
        "the missing team file stopped being a warning and became silence"


def test_read_decrypted_answers_a_missing_file_with_file_not_found_error(tmp_path):
    """The seam's own boundary, stated once: it reads, and existence is the caller's question."""
    with pytest.raises(FileNotFoundError):
        read_decrypted(tmp_path / "not-here.json")


# ---------------------------------------------------------------------------------------------
# 9 — the census as a test.
# ---------------------------------------------------------------------------------------------

SRC = Path(__file__).resolve().parents[1] / "src"

# What counts as configuration material: the four layer files, and the kubeconfig the cluster layer
# reads instead of them.
MATERIAL = re.compile(r"static\.json|static\.encrypted\.json|env_static|team_static|kubeconfig"
                      r"|KUBECONFIG|\.kube/config|values\.encrypted")

# The seam's own module: it is the bottom of the graph and not a consumer of it.
SEAM_MODULE = "kubementat/config_reads.py"

# Reads that are deliberately not this seam's, each with the reason it is exempt. The Kubernetes
# client's own read and `kubectl config view` are on this list in prose rather than as a line: they
# run outside Python, so no function here calls them.
EXEMPT = {
    ("kubementat/config_init.py", "set_answer"):
        "the bytes-compare that decides whether the document changed; a write-side guard, not a read "
        "of answers, and it never decodes",
    ("kubementat/config_validate.py", "_manifest_findings"):
        "`kubementat.yaml`, which is not encrypted material — a seam wrapped around it would promise "
        "coverage it does not need",
    ("kubementat/cluster.py", "from_service_account"):
        "the token the kubelet mounted for this pod, which is a credential the process is running "
        "with and not a configuration file it was handed",
    ("kubementat/config_init.py", "_apply_layer_plan"):
        "the bytes-compare before a whole-document write; it reads a file it is about to write, not "
        "a file it is about to answer, and it never decodes (census R7)",
    ("kubementat/tekton_utils.py", "run_pipeline"):
        "a pipeline-run manifest kubementat generated itself, in `tekton_ci/pipeline-runs/`",
}

# The seven reads the seam is expected to cover, spelled as (file, function). A read that is not here
# and is not exempt has to be caught by `reads_outside_the_seam` below, but this list is what makes the number
# itself a fact and not a guess.
DOCUMENTED_SEAM_SITES = [
    ("kubementat/config.py", "_load_json_file"),
    ("kubementat/config_init.py", "_read_document"),
    ("kubementat/config_init.py", "cluster_is_unaddressed"),
    ("kubementat/config_validate.py", "_read_object_file"),
    ("kubementat/config_validate.py", "_cluster_fact_finding"),
    ("kubementat/cluster.py", "_static_declaration"),
    ("kubementat/cluster.py", "_read_kubeconfig"),
]

def _spliced(lines, node) -> str:
    """A node's own text, joined out of the file's lines.

    `ast.get_source_segment` re-splits the whole source on every call, which makes a walk of every
    node in every file quadratic; this is the same answer in linear time.
    """
    return " ".join(lines[node.lineno - 1:node.end_lineno])


def _prose_lines(src: str, tree) -> set:
    """Lines that are comments or docstrings — prose, which names files without reading them."""
    prose = set()
    for token in tokenize.generate_tokens(io.StringIO(src).readline):
        if token.type == tokenize.COMMENT:
            prose.add(token.start[0])
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                prose.update(range(body[0].lineno, body[0].end_lineno + 1))
    return prose


def _is_read(node: ast.Call) -> bool:
    called = node.func
    if isinstance(called, ast.Attribute):
        if called.attr in ("read_text", "read_bytes") or called.attr == "open":
            return True
        return (called.attr == "load" and isinstance(called.value, ast.Name)
                and called.value.id == "json")
    return isinstance(called, ast.Name) and called.id == "open"


def _modules():
    """Every shipped module, parsed once, with its lines and its prose lines already split out.

    The census walks this whole set for each of its two rules, so one parse per file is what keeps it
    a test and not a pause in the suite.
    """
    parsed = []
    for path in sorted(SRC.rglob("*.py")):
        if "assets" in path.parts or path.relative_to(SRC).as_posix() == SEAM_MODULE:
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        lines = src.splitlines()
        prose = _prose_lines(src, tree)
        code_lines = ["" if number in prose else line for number, line in enumerate(lines, 1)]
        parsed.append((path, tree, lines, code_lines))
    return parsed


def _call_sites(modules) -> dict:
    """Every call to every function name in `src/`, keyed by name.

    This is what makes the census more than a grep for `static.json` on the same line as a read:
    `Config._load_json_file` names its argument `file_path` and says nothing about layers, and it is
    the *caller* that says which file is meant. Ignoring call sites is how a sixth read in exactly
    that shape escapes.
    """
    sites = {}
    for _path, tree, lines, _code in modules:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = called.id if isinstance(called, ast.Name) else (
                called.attr if isinstance(called, ast.Attribute) else None)
            if name:
                sites.setdefault(name, []).append(_spliced(lines, node))
    return sites


def reads_outside_the_seam() -> list:
    """Every read of configuration material in `src/` that does not go through the seam.

    Two rules decide what is "configuration material", because a read almost never names the file on
    the line it reads it:

    * the **function** mentions a layer or kubeconfig path in its code — prose and docstrings are
      stripped first, or a comment about `static.json` would count as a read;
    * or its **call sites** do, which is the shape most readers actually have: a parameter named
      `file_path` whose caller passed `self.env_static_encrypted_config_path`.

    Exemptions are declared above with a reason, so the list can only be extended by someone who has
    decided what the read is.
    """
    modules = _modules()
    call_sites = _call_sites(modules)
    found = []
    for path, tree, lines, code_lines in modules:
        source = "\n".join(code_lines)
        if not MATERIAL.search(source):
            continue
        # A module that already calls the seam is in the read graph, so every read inside it is
        # this seam's business — a function that reads and does not call it is either exempt or new.
        shares_the_seam = "read_decrypted" in source
        scopes = [(node.name, node) for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        inside = {id(node) for _name, scope in scopes for node in ast.walk(scope)}
        # The module itself is a scope only for the code that is not inside a function, or every
        # read in the file is counted twice and the function that made it disappears.
        scopes.append(("<module>", [node for node in ast.walk(tree) if id(node) not in inside]))
        for name, scope in scopes:
            if name == "<module>":
                scope = [scope] if isinstance(scope, list) else scope
            reads = []
            nodes = scope if isinstance(scope, list) else list(ast.walk(scope))
            for node in nodes:
                if not isinstance(node, ast.Call) or not _is_read(node):
                    continue
                statement = _spliced(lines, node)
                if "read_decrypted" in statement:
                    continue
                if re.search(r"['\"]w[b+]?['\"]", statement):
                    continue  # a write-mode open is a writer, not a reader
                reads.append((node.lineno, statement.strip()))
            if not reads:
                continue
            body = ("\n".join(code_lines) if isinstance(scope, list) else
                    " ".join(code_lines[scope.lineno - 1:scope.end_lineno]))
            relevant = (bool(MATERIAL.search(body)) or shares_the_seam or any(
                MATERIAL.search(segment) for segment in call_sites.get(name, [])))
            if not relevant:
                continue
            for line, source in reads:
                found.append({
                    "file": path.relative_to(SRC).as_posix(),
                    "function": name,
                    "line": line,
                    "source": source,
                })
    return found


def test_every_read_of_configuration_material_in_src_goes_through_the_seam():
    """The census that made the count seven, running as a test instead of as a note.

    A sixth read that forgets `read_decrypted` fails here and nowhere else needs to fail — which is
    how the seam stays one, rather than a rule a reviewer checks by hand and forgets next month.
    """
    strays = [hit for hit in reads_outside_the_seam()
              if (hit["file"], hit["function"]) not in EXEMPT]
    assert strays == [], (
        "reads of configuration material that skip read_decrypted:\n"
        + "\n".join(f"  {hit['file']}:{hit['line']} in {hit['function']}(): {hit['source']}"
                    for hit in strays)
        + "\nEither route it through read_decrypted or add the reason it is exempt to EXEMPT.")


def test_the_seven_documented_reads_still_call_the_seam():
    """The count itself is the claim, so it is asserted and not merely implied.

    An earlier claim of "three sites" was three names for fewer reads than that and four reads nobody
    had counted; this list is what the 2026-11-03 census measured, and the row below is what breaks when
    someone adds a seventh or silently drops one.
    """
    missing = []
    for relative, function in DOCUMENTED_SEAM_SITES:
        lines = (SRC / relative).read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(lines))
        bodies = {node.name: _spliced(lines, node) for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "read_decrypted" not in bodies.get(function, ""):
            missing.append(f"{relative}::{function}")
    assert missing == [], f"these reads no longer go through read_decrypted: {missing}"


def test_the_exemptions_are_all_still_reads_and_all_still_needed():
    """A stale exempt list is as bad as an unlisted read.

    If an exempt read starts calling the seam, or the function goes away, the entry has to go with
    it; otherwise the census rots into a list of things nobody checks.
    """
    hits = reads_outside_the_seam()
    seen = {(hit["file"], hit["function"]) for hit in hits}
    now_stale = set(EXEMPT) - seen
    assert now_stale == set(), f"exemptions that no longer describe a read: {sorted(now_stale)}"
    assert all(len(reason.split()) > 3 for reason in EXEMPT.values()), \
        "an exemption with no reason under it is a TODO and not an exemption"


def test_the_seam_never_runs_git_crypt_and_never_parses(tmp_path):
    """The other half: a refusal is not a fix, and kubementat does not unlock.

    Checked against the module's own source rather than against a shim, because there is nothing to
    run: a seam that execed `git-crypt` would be indistinguishable from a helper that decodes.
    """
    tree = ast.parse((SRC / "kubementat" / "config_reads.py").read_text(encoding="utf-8"))
    imports = {getattr(node, "module", None) or getattr(node, "names", [None])[0].name
               for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))}
    calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)} \
        if hasattr(ast, "unparse") else set()
    assert "subprocess" not in imports and "os" not in imports, \
        f"the seam runs something: {sorted(imports)}"
    assert not [call for call in calls if call.startswith(("json.", "yaml."))], \
        f"the seam parses what it only reads: {sorted(calls)}"
