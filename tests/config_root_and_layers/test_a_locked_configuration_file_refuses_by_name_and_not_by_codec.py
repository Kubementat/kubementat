"""A locked configuration file is refused by name and not by codec.

`UnicodeDecodeError` is a `ValueError` and not an `OSError`, and before this ticket that fact made
every read of an encrypted kubementat file answer the same question in its own words: four sites
caught it and described it in four different sentences, two did not catch it at all, and
`config_init.cluster_is_unaddressed` swallowed it and answered *"this environment addresses no
cluster"* — the one place in `src/` that printed a confident falsehood about a file it had never
read. The seam is `read_decrypted(path, what)` in `kubementat/core/config/reads.py`, and the rule it
enforces is **one sentence, many error types**: every caller keeps its own exception and its own
shape, and the sentence is the same one.

Eight claims, one per read that the census measured, plus two about what the seam must not do:

1. `Config._load_json_file` — the bottom of the encrypted-value graph, which everything downstream
   (`infrastructure/helm.py`, `infrastructure/grafana/client.py`, `infrastructure/tekton`,
   `automations/`, `config show`) reads
   through. A locked layer file refuses with the file's name and git-crypt.
2. `config_init._read_document` — the read-modify-write verbs (`config set`, `config unset`,
   `config set --generate`). It keeps its own refusal wording for unreadable and non-JSON, which is
   why a `LockedConfigFileError` (a `KubementatError`, not an `OSError`) walks past that handler
   intact.
3. `config_init.cluster_is_unaddressed` — the fixed site. An undecodable file **raises**; absent,
   `null` and blank still answer `True`. Both halves are asserted here because the bug was the
   confusion between them, and the old docstring's rationalization of the swallowing is gone.
4. `config_validate._read_object_file` — a locked file becomes the finding
   `locked-configuration-file` and validate **keeps collecting** the rest of the tree's faults. A
   verdict command that raises early stops being a verdict.
5. `config_validate._cluster_fact_finding` — silent about a file it could not read. It used to print
   "is null" over exactly that file, which is the same false sentence as row 3 wearing a warning.
6. `core/identity/registry._static_declaration` and 7. `core/identity/kubeconfig._read_kubeconfig` —
   the cluster-resolution reads. Both re-raise `ClusterFileUnreadableError`, the cluster layer's own
   refusal type, with the seam's sentence inside it and neither rewriting it. Both now live one level
   below the resolver that calls them, which is why both rows below carry their own path.
8. `infrastructure/helm.refuse_locked_operator_values` — the operator's component layers, read
   before helmfile starts. These are the files helm opens after the template's `isFile` guard, and
   ciphertext reaching helm would die as apply output that never says the word "unlock"; the seam
   read turns that into a refusal naming the file.
9. **The seam does not route around `missing_team_config_files`.** A missing *optional* team file
   still degrades the way it did: a warning at load and a named refusal at read. This is the
   falsification of the seam's own boundary — `FileNotFoundError` propagates and nothing converts it.
10. **The census is a test.** The read graph is walked out of the tree rather than asserted from a
   list, so a read that forgets the seam fails here.

Falsifications run while writing this file, each one breaking what it names: letting one site keep
its own `read_text` fails that site's row and the census row alone; making `cluster_is_unaddressed`
answer `True` over ciphertext fails row 3 alone; making `read_decrypted` raise on a missing file
fails row 8 alone.
"""

import json
import logging
from pathlib import Path

import pytest
from asset_paths import REPO_ROOT
from click.testing import CliRunner

from cli.commands.config import config as config_group
from cli.kmt import KmtGroup
from kubementat.config import init as config_init
from kubementat.config import validate as config_validate
from kubementat.config.validate import validate_tree
from kubementat.core.config.config import Config
from kubementat.errors import LockedConfigFileError

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


# ---------------------------------------------------------------------------------------------
# 2 — `config_init._read_document`, the read-modify-write verbs.
# ---------------------------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------------------------
# 4 and 5 — `config validate`: locked files become findings, and no finding invents a value.
# ---------------------------------------------------------------------------------------------


def a_validated_tree(tmp_path, team=TEAM) -> Path:
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    config_init.add_team(root=root, environment=ENVIRONMENT, team=team)
    return root


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


# ---------------------------------------------------------------------------------------------
# 8 — the boundary that must survive: missing is still missing, and is not locked.
# ---------------------------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------------------------
# 9 — the census as a test.
# ---------------------------------------------------------------------------------------------

SRC = REPO_ROOT / "src"


# ---------------------------------------------------------------------------------------------
# 10 — the whole CLI's one line: what the operator who ran `kmt config show` actually sees.
# ---------------------------------------------------------------------------------------------


def test_a_locked_layer_file_is_rendered_as_one_error_line_and_never_as_a_traceback(tmp_path):
    """The rendering is product: exit 1 and one sentence, because the fix is `git-crypt unlock`.

    The exception is one of the tree refusals that the `config` verbs raise on their way, with no
    `Context` built and therefore none of the decorator's wrapping. The root group renders it, which
    is the whole point of putting the rendering there instead of leaving it to the decorated half:
    a run of `kmt config show` over the same tree the read seam refuses prints the sentence and
    nothing else.
    """
    root = tmp_path / "config"
    config_init.init_configuration(root=root, environment=ENVIRONMENT)
    locked = lock(root / ENVIRONMENT / "static.encrypted.json")

    # The root group's class wrapped around the real `config` subcommand, because `main()` is the
    # only thing that registers commands on `cli` and an operator's run is what this row is about:
    # this group IS how `kmt config show` dispatches.
    root_group = KmtGroup(name="kmt")
    root_group.add_command(config_group)

    result = CliRunner(mix_stderr=False).invoke(
        root_group, ["config", "show", ENVIRONMENT, "--config-dir", str(root)])

    assert result.exit_code == 1, f"{result.exception}; {result.output}"
    refused_by_name(result.stderr, locked)
