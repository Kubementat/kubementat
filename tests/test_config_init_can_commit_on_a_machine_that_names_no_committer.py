"""`config init` commits, so it has to be able to commit.

The verb runs `git init` and then makes the first commit itself, because the assert runs against a
tree whose first commit is already ciphertext. Both halves are needed, and the second had no test — so on the
first real VM run of 2026-11-05 stage 02 died inside `config init` with exit 128.

Git had refused to commit: the guest had no `user.name` and no `user.email` anywhere, which is the normal
state of a freshly created machine. The error sent the operator to `git config --global`, a
machine-level setting, to fix something inside a repository kubementat had itself just created. Nothing
in the lane had reached stage 02 since 2026-11-02, and nothing in the unit suite could have seen it
either, for the ordinary reason: the fixture that asserts the steps declares a machine that already
names somebody, which is the case where kubementat writes nothing.

So this file exercises real git in a real repo with a real refusal.

HOME is repointed into the fixture's temporary directory for the whole test, and that is load-bearing
rather than tidiness: this developer machine *has* a global identity, so a test that inherited HOME would
find one, kubementat would correctly write nothing, and every claim here about the fallback would be
measuring a machine that does not exist. A git that reads an empty HOME is a fresh VM.
"""

import subprocess

import pytest

from kubementat.config import bootstrap as config_bootstrap


def git(root, *args, check=False):
    """Run git in *root*, inheriting the fixture's isolated HOME.

    `check` defaults to False because half of this file's claims are about what git refuses, and a
    raising wrapper would answer the question by failing instead of by reporting.
    """
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check)


@pytest.fixture
def fresh_repo(tmp_path, monkeypatch):
    """A repository with something staged and no committer identity anywhere.

    Yields `(root, home)`; `home` is what to point `git --global` reads at, so a claim about not writing
    globally can be checked against the same isolation the product was run in.
    """
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "configuration"
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # The real global config this machine owns must be out of reach too, or a stale $XDG_CONFIG_HOME
    # would leak this developer's identity into a fixture whose whole premise is that it has none.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    git(root, "init", "-q", check=True)
    (root / "kubementat.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    git(root, "add", "-A", check=True)
    return root, home


def _by_step(steps):
    return {step.name: step for step in steps}


def test_a_machine_that_names_no_committer_still_gets_its_first_commit(fresh_repo):
    """The regression. A fresh machine must not cost the operator its `config init`.

    The assert is that HEAD exists, which is exactly what the VM run did not produce.
    """
    root, _home = fresh_repo
    assert git(root, "config", "user.email").returncode != 0, \
        "the fixture left an identity reachable, so the fallback has nothing to do"

    steps = config_bootstrap._commit(root, commit=True)  # noqa: SLF001

    assert _by_step(steps)[config_bootstrap.GIT_COMMIT_STEP].action == config_bootstrap.CREATED, \
        "the commit step did not report a commit on a tree that had something staged"
    assert git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0, \
        "git still refused; that is the exit 128 this file exists to close"


def test_an_identity_that_is_already_there_is_left_alone(fresh_repo):
    """The branch that writes nothing, checked rather than assumed.

    This is the case `bootstrap_tools` stands in for by answering 0, and it is where a bug in the read
    hides: if kubementat wrote its own name despite a real identity, commits would start attributing to
    kubementat and this is the only assert that would notice.
    """
    root, _home = fresh_repo
    git(root, "config", "--local", "user.name", "J. Operator", check=True)
    git(root, "config", "--local", "user.email", "jop@example.test", check=True)

    config_bootstrap._commit(root, commit=True)  # noqa: SLF001

    assert git(root, "config", "--local", "user.name").stdout.strip() == "J. Operator"
    assert git(root, "config", "--local", "user.email").stdout.strip() == "jop@example.test"


def test_a_second_run_writes_no_identity_and_no_history(fresh_repo):
    """Idempotence, because `config init` is claimed to be safe to re-run.

    A run that commits nothing should also change no configuration. This catches the fallback being
    applied on a path that never needed it.
    """
    root, _home = fresh_repo
    config_bootstrap._commit(root, commit=True)  # noqa: SLF001
    before = git(root, "log", "-1", "--format=%H").stdout.strip()

    steps = config_bootstrap._commit(root, commit=True)  # noqa: SLF001

    assert git(root, "log", "-1", "--format=%H").stdout.strip() == before, \
        "the tree was committed twice; a re-run is not supposed to add history"
    assert _by_step(steps)[config_bootstrap.GIT_COMMIT_STEP].action == config_bootstrap.SKIPPED
