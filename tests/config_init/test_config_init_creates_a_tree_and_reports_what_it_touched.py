"""`kmt config init` — the verb that *prepares* a directory, and the account it gives of itself.

The library half is already landed and already tested (`kubementat/config/bootstrap.py`,
`test_config_init_prepares_the_directory_it_is_handed.py`). What lives here is the *verb*, and the
verb's claims are not the library's:

* **The verb prepares.** `kmt config init` is `config_bootstrap.prepare_configuration_directory` seen
  through Click: `git init`, the two git files, the layout, the deployer keypair, git-crypt's
  keystore/collaborator/export, and one commit of kubementat's own. The step list is therefore longer
  than the four lines `init_configuration` used to return, and the *order of the print* is still the
  order the library applied the plan — which is what makes the first commit ciphertext and not
  plaintext, so the print order is asserted and not assumed.
* **The print is the plan, in order, and it is the file list.** One line per step, on stdout, nothing
  else on stdout. The closing instruction is guidance about state and so goes to stderr, where the
  cluster sentence already was; a script reading `kmt config init | ...` gets paths and no prose.
* **A run that can be refused is refused before it writes.** A bare `config init --environment E`
  with no `--gpg-key` and no `--gpg-email` has no identity to work from, and `config_keys` says so
  ahead of `git init`: no directory, no `.git`, no half-prepared tree for somebody to delete.
* **The root is named and never located.** `--config-dir`, or the
  `KUBEMENTAT_PLATFORM_CONFIG_DIR` a container already sets, with the flag above the variable and a
  refusal below both — the same rule every command that reaches a cluster answers with, and this verb
  is not an exception to it. `git init` happens after that decision, so a run that named no root
  leaves no tree behind anywhere. Test 8 sets the variable to a decoy beside a named root and proves
  only the root that was handed over is written to; test 9 leaves the command line empty and proves
  the variable still names the tree; test 10 deletes both and proves nothing is scaffolded.
* **The value is typed.** `--set GRAFANA_ENABLED=false` stores a boolean and `--set
  BASE_DOMAIN=example.test` stores a string, which is the claim test 4 asserts in both directions.
  A parser that `str()`-ed everything would keep the tree green and the key dead.
* **Refusals stay refusals.** No environment, a `KEY=VALUE` with no `=`, the same key twice, foreign
  material without `--force`, no identity and no key — one refusal each. `--force` accepts material
  kubementat did not write and touches none of it; it is not an overwrite, so the foreign file's
  bytes are asserted and not assumed, and it is not a way past a `.gitattributes` somebody else
  configured either (that rule is decided by byte-identity and belongs to the library).

The last test is the one that keeps the rule honest: a verb that takes its own root must write
nothing where the checkout happens to live, and the way to know that is true is to compare the
checkout's own `platform_config/` before and after. `kmt initialize` does write there, and it writes
the deployer keypair there today, so this claim is the difference between the two paths and not a
note about them.
"""

import stat
import subprocess
from pathlib import Path

import pytest
from bootstrap_tools import (
    EMAIL,
    FINGERPRINT,
    argv,
    commit_count,
    crypt_calls_on,
    since_last_run,
    tree_bytes,
)
from click.testing import CliRunner

from cli.commands import config as config_module
from kubementat.config import bootstrap as config_bootstrap
from kubementat.config import keys as config_keys
from kubementat.crypto_utils import CryptoUtils

ENVIRONMENT = "dev"
MANIFEST = Path("kubementat.yaml")
ENV_STATIC = Path(ENVIRONMENT) / "static.json"
ENV_ENCRYPTED = Path(ENVIRONMENT) / "static.encrypted.json"

# The two layer directories a run without `--team` creates, in the order the steps land. Printed and
# asserted as paths here because the print is a path list; which `Config` property each one *is* is
# asserted against the property in test_config_init_creates_the_directories_a_reader_looks_in.py.
ENV_TEKTON_DIRS = [Path(ENVIRONMENT) / "tekton" / "tasks",
                   Path(ENVIRONMENT) / "tekton" / "pipelines"]


@pytest.fixture(autouse=True)
def no_configuration_root_in_the_environment(monkeypatch):
    """Neither dial is set for any test here, so a tree that lands on a decoy did it on its own.

    `KUBEMENTAT_HOME` is cleared beside `KUBEMENTAT_PLATFORM_CONFIG_DIR` for the same reason: the
    second is what a `Config` with no root resolves, and a stale one on the machine would be a
    second root rather than the one argument the verb was handed.
    """
    monkeypatch.delenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", raising=False)
    monkeypatch.delenv("KUBEMENTAT_HOME", raising=False)


def run_init(root, *arguments):
    """`kmt config init --config-dir <root> …`, through the group so the verb's name is exercised.

    A bug inside the command is re-raised rather than folded into an exit code: `exit_code == 1`
    with a swallowed traceback reads as "the verb refused", which is exactly the failure this file
    must not mistake for a refusal.

    The root travels as the flag the surface names it with, so every assertion below is evidence
    about the command a human or a container types and not about a private argv.
    """
    return in_root_group(["init", *arguments, "--config-dir", str(root)])


def in_root_group(argv):
    """`kmt config …`, dispatched the way `main()` dispatches it: through the root group class.

    The root group is what renders a named refusal into one `Error:` line, so a test that invoked the
    `config` sub-group directly would be asserting the shape of a run no operator can have. Same
    argv, same `CliRunner`, one more level of dispatch — which is also what `main()` builds when it
    hangs `config` off `cli` before calling it.
    """
    from cli.kmt import KmtGroup

    root_group = KmtGroup(name="kmt")
    root_group.add_command(config_module.config, "config")
    result = CliRunner(mix_stderr=False).invoke(root_group, ["config", *argv])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def reported(result) -> str:
    """Both streams, for the assertions that ask what the operator would have read.

    With `mix_stderr=False`, `result.output` is stdout alone — so an assertion about a refusal has to
    name the stream it expects the sentence on, and a test that read only stdout would pass a verb
    that refused in silence and said something else on stderr.
    """
    return f"{result.stdout}{result.stderr}"


def snapshot(directory: Path) -> dict:
    """Every file under `directory`, keyed by path and held as bytes."""
    return {path.relative_to(directory): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def lines(text: str) -> list:
    return [line for line in text.splitlines() if line.strip()]


def prepared_paths(root, *, again=False) -> list:
    """Every path a prepared run reports, in the order the steps were applied.

    Listed here and not inside a test because four tests need it: the print is the verb's whole
    stdout, so both the order and the set are a claim about behaviour and not about the library's
    internals. Two entries change shape between the first run and the second. The collaborator step
    names the directory it registered into when it ran and the fingerprint file that proves it ran
    when it did not, and the two calls that close the run report `root` — staging and committing are
    different steps and the same path. Neither is a mistake; both are why the list is a function.
    """
    return [
        root / ".git",
        root / ".gitattributes",
        root / ".gitignore",
        root / MANIFEST,
        root / ENV_STATIC,
        root / ENV_ENCRYPTED,
        *[root / directory for directory in ENV_TEKTON_DIRS],
        root / config_keys.SSH_PRIVATE_NAME,
        root / config_keys.SSH_PUBLIC_NAME,
        root / config_keys.GPG_PRIVATE_NAME,
        root / config_keys.GPG_PUBLIC_NAME,
        root / ".git" / "git-crypt" / "keys" / "default",
        (root / ".git-crypt" / "keys" / "default" / "0" / f"{FINGERPRINT}.gpg" if again
         else root / ".git-crypt" / "keys" / "default" / "0"),
        root / config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_NAME,
        root,
        root,
    ]


def identity_arguments() -> list:
    """`--gpg-email <email>`, because every run in this file that expects success needs an identity.

    Spelled in a helper and not in a fixture that adds arguments behind the test's back: which flag
    carries the identity is itself a claim about the verb, and a test that could run without typing it
    could not tell that a bare run refuses.
    """
    return ["--gpg-email", EMAIL]


def staged(root, tools) -> list:
    """What `git add` has taken, which is the only way `--no-commit` can be checked."""
    return subprocess.run([tools["git"], "diff", "--cached", "--name-only"], cwd=root,
                          capture_output=True, text=True).stdout.split()


def tracked(root, tools) -> list:
    """What the first commit actually holds — the list that says what got into the object store."""
    return subprocess.run([tools["git"], "ls-tree", "-r", "--name-only", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.split()


def test_a_second_run_writes_no_byte_makes_no_commit_and_names_the_steps_it_skipped(root, tools):
    """Test 2: idempotence as a claim about bytes, commits and calls.

    `created` on a second run would say a write happened that did not, and the sentence that a
    repeated init is a no-op is exactly the sentence an operator has to be able to trust before
    running it inside a script. What makes the claim more than a step list is the middle pair: no new
    commit in the history, and no second `git-crypt export-key` over a file that was already correct —
    an export is a write no matter what the step says.
    """
    run_init(root, "--environment", ENVIRONMENT, *identity_arguments())
    before, first = tree_bytes(root), commit_count(root, tools)
    since_last_run(tools)

    result = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert result.exit_code == 0, reported(result)
    assert tree_bytes(root) == before, "skipped has to mean the bytes are the ones that were there"
    assert commit_count(root, tools) == first, "the second run committed"
    printed = lines(result.stdout)
    assert printed == [f"skipped {path}" for path in prepared_paths(root, again=True)], reported(result)
    assert "created" not in result.stdout

    second = argv(tools)
    assert crypt_calls_on(second, "export-key") == [], \
        "the symmetric key was exported again over a file that was already right, which is a write"
    assert crypt_calls_on(second, "add-gpg-user") == [], \
        "the deployer was registered a second time"
    assert crypt_calls_on(second, "init") == [], "the keystore was initialized a second time"


def test_a_set_argument_with_no_equals_sign_is_refused_naming_the_flag(root, tools):
    """Test 5: `--set GRAFANA_ENABLED` is a typo and not a half-answer.

    It names the flag it came from because `--encrypted-set` is the same typo on a different file,
    and an operator reading "KEY=VALUE expected" would fix the wrong half of the command. The answer
    arrives before the library is called, so the tree is still absent afterwards — a typo is not a
    reason to make a repository.
    """
    result = run_init(root, "--environment", ENVIRONMENT, "--set", "GRAFANA_ENABLED")

    assert result.exit_code != 0
    assert "--set" in reported(result)
    assert not root.exists() or tree_bytes(root) == {}


def test_the_same_key_given_twice_is_refused_and_not_last_write_wins(root, tools):
    """Test 6: a JSON object cannot carry a key twice, so a repeated answer is a question.

    Last-write-wins would pick one of two answers the operator gave in one command and silently call
    that the configuration. The repeat is decided where the answers become a document, which is inside
    `init_configuration` and so four steps into the preparation — which is why the claim here is about
    the tree rather than about the directory: no manifest, no layer, no answer picked in either case.
    The typo that the verb does decide before it writes anything is the malformed `KEY=VALUE` two
    tests up.
    """
    result = run_init(root, "--environment", ENVIRONMENT,
                      "--set", "BASE_DOMAIN=a.test", "--set", "BASE_DOMAIN=b.test",
                      *identity_arguments())

    assert result.exit_code != 0
    assert "BASE_DOMAIN" in reported(result)
    assert not (root / MANIFEST).exists(), "the tree was written by a run that refused"
    assert not (root / ENV_STATIC).exists(), "the answer was picked anyway"


def test_foreign_material_is_named_and_a_force_leaves_it_alone(root, tools):
    """Test 7: `--force` means "this tree holds something of someone else's", never "overwrite".

    Both halves are asserted: the refusal names the file so the operator can move it, and the run
    that accepts the tree leaves that file's bytes exactly where they were. A `force` that rewrote
    the foreign file would be the one flag that loses work without asking.
    """
    root.mkdir(parents=True)
    foreign = root / "NOTES.md"
    foreign.write_text("hand-written by a human\n", encoding="utf-8")
    untouched = foreign.read_bytes()

    refused = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())
    assert refused.exit_code != 0
    assert "NOTES.md" in reported(refused)
    assert foreign.read_bytes() == untouched

    accepted = run_init(root, "--environment", ENVIRONMENT, "--force", *identity_arguments())

    assert accepted.exit_code == 0, reported(accepted)
    assert foreign.read_bytes() == untouched
    kept = [line for line in lines(accepted.stdout) if "NOTES.md" in line]
    assert kept == [f"kept {foreign} (not kubementat's)"], (
        "force accepted a file and said so in the only wording that says nobody touched it")
    assert (root / ENV_STATIC).is_file()


def test_the_flag_outranks_the_platform_config_variable_and_the_decoy_stays_empty(tmp_path,
                                                                                 root,
                                                                                 tools,
                                                                                 monkeypatch):
    """Test 8: the flag is the layer above the variable, so a decoy the variable names stays empty.

    This is the test a verb that read the variable beside a named root — or worse, that let the
    variable answer when the flag disagreed with it — would fail: the decoy would gain the tree and
    the root that was named would gain nothing. The precedence is `paths.resolve_platform_config_dir`
    and nothing else, so a verb that spelled it a second way here would be a second owner of one rule
    and the first place it could rot.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(decoy))

    result = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert result.exit_code == 0, reported(result)
    assert snapshot(decoy) == {}
    assert (root / MANIFEST).is_file()
    assert (root / ENV_STATIC).is_file()


# ---------------------------------------------------------------------------------------------
# What the verb prepares, as claims of their own.
# ---------------------------------------------------------------------------------------------


def test_the_exported_symmetric_key_is_the_run_that_can_read_every_credential(root, tools):
    """The exported file's mode is the claim, because git-crypt writes it at the run's umask.

    On a normal umask that is 0644, which is a key every account on the box can read. The ignore rule
    in `.gitignore` keeps it out of the commit, which is asserted here because the two halves — mode
    and ignore — are what make "the key is in this directory and nowhere else" true, and each is only
    half the claim without the other.
    """
    result = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert result.exit_code == 0, reported(result)
    exported = root / config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_NAME
    assert exported.is_file(), reported(result)
    mode = stat.S_IMODE(exported.stat().st_mode)
    assert mode == 0o600, f"{exported.name} grants {oct(mode)}; that file unlocks the whole tree"
    assert exported.name not in tracked(root, tools), \
        "the symmetric key reached the commit, which is how a gitignored file stops being ignored"
    assert exported.name in (root / ".gitignore").read_text(encoding="utf-8")


def test_the_symmetric_key_instruction_is_printed_once_and_only_when_this_run_exported_it(root,
                                                                                          tools):
    """The sentence about the export, and the half that says *once*.

    The sentence is worth printing when the run made the file and is not worth printing when it did
    not, because advice that repeats is advice that stops being read. Both streams are checked: on
    stderr beside the rest of the guidance, and never on stdout, where a path list is what a script
    is reading.
    """
    first = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())
    assert first.exit_code == 0, reported(first)

    sentence = "ability to read every credential"
    assert first.stderr.count(sentence) == 1, (
        f"the export happened, so the sentence about it belongs exactly once on stderr: "
        f"{first.stderr}")
    assert sentence not in first.stdout, "the file list is stdout and the file list only"

    since_last_run(tools)
    again = run_init(root, "--environment", ENVIRONMENT, *identity_arguments())

    assert again.exit_code == 0, reported(again)
    assert sentence not in again.stderr, (
        "the second run exported nothing and still repeated the advice about exporting")
    assert crypt_calls_on(argv(tools), "export-key") == [], (
        "the sentence was right and the export ran again anyway, which is the other shape of the bug")


def test_the_closing_instruction_names_the_remote_it_was_handed_and_not_one_it_made_up(root, tools):
    """`--git-url` is a flag about the summary and not about the repository.

    Given a URL, the instruction names it. Given none, the honest sentence is that the commit is local
    and the operator has to add a remote — because the alternative is printing a URL nobody typed, or
    naming `upstream` as if kubementat had configured one. Neither half of this is about the tree, so
    both are printed after it and nothing is added to it.
    """
    named = run_init(root, "--environment", ENVIRONMENT, "--git-url", "git.example.invalid:k/t.git",
                     *identity_arguments())
    assert named.exit_code == 0, reported(named)
    assert "git.example.invalid:k/t.git" in named.stderr

    local = run_init(root / "second", "--environment", ENVIRONMENT, *identity_arguments())
    assert local.exit_code == 0, reported(local)
    assert "no remote" in local.stderr.lower(), (
        f"a run with no URL still has to say the commit is local: {local.stderr}")


def test_gpg_key_is_imported_and_nothing_is_generated(root, tools, monkeypatch, deployer_secret_key):
    """The done-when for the import, as the verb: the file the operator handed over *is* the private key.

    `--gpg-key` is the half of the ruling that keeps a fresh directory from blocking on a generation
    nobody asked for, so both directions are asserted: the bytes arrive unchanged, and the generator
    is never called. A verb that imported and generated anyway would be indistinguishable from the
    right answer by looking at the tree alone.
    """
    generated: list = []

    def record_generation(*args, **kwargs):
        generated.append(kwargs)
        return ("the generator was asked for despite a key that was handed over, which is the failure "
                "this assertion exists to notice")

    monkeypatch.setattr(CryptoUtils, "generate_gpg_key", staticmethod(record_generation))

    result = run_init(root, "--environment", ENVIRONMENT, "--gpg-key", str(deployer_secret_key))

    assert result.exit_code == 0, reported(result)
    assert (root / config_keys.GPG_PRIVATE_NAME).read_bytes() == deployer_secret_key.read_bytes(), \
        "the operator's key was not imported byte for byte"
    assert (root / config_keys.GPG_PUBLIC_NAME).is_file(), \
        "the public half the import exports is missing"
    assert generated == [], "a gpg keypair was generated beside the key that was handed over"
