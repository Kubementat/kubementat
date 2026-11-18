"""`config init` commits, so it has to be able to commit."""

import subprocess

import pytest

from kubementat.config import bootstrap as config_bootstrap


def git(root, *args, check=False):
    """Run git in *root*, inheriting the fixture's isolated HOME."""
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check)


@pytest.fixture
def fresh_repo(tmp_path, monkeypatch):
    """A repository with something staged and no committer identity anywhere."""
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
    """The regression. A fresh machine must not cost the operator its `config init`."""
    root, _home = fresh_repo
    assert git(root, "config", "user.email").returncode != 0, \
        "the fixture left an identity reachable, so the fallback has nothing to do"

    steps = config_bootstrap._commit(root, commit=True)  # noqa: SLF001

    assert _by_step(steps)[config_bootstrap.GIT_COMMIT_STEP].action == config_bootstrap.CREATED, \
        "the commit step did not report a commit on a tree that had something staged"
    assert git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0, \
        "git still refused; that is the exit 128 this file exists to close"


def test_an_identity_that_is_already_there_is_left_alone(fresh_repo):
    """The branch that writes nothing, checked rather than assumed."""
    root, _home = fresh_repo
    git(root, "config", "--local", "user.name", "J. Operator", check=True)
    git(root, "config", "--local", "user.email", "jop@example.test", check=True)

    config_bootstrap._commit(root, commit=True)  # noqa: SLF001

    assert git(root, "config", "--local", "user.name").stdout.strip() == "J. Operator"
    assert git(root, "config", "--local", "user.email").stdout.strip() == "jop@example.test"


def test_a_second_run_writes_no_identity_and_no_history(fresh_repo):
    """Idempotence, because `config init` is claimed to be safe to re-run."""
    root, _home = fresh_repo
    config_bootstrap._commit(root, commit=True)  # noqa: SLF001
    before = git(root, "log", "-1", "--format=%H").stdout.strip()

    steps = config_bootstrap._commit(root, commit=True)  # noqa: SLF001

    assert git(root, "log", "-1", "--format=%H").stdout.strip() == before, \
        "the tree was committed twice; a re-run is not supposed to add history"
    assert _by_step(steps)[config_bootstrap.GIT_COMMIT_STEP].action == config_bootstrap.SKIPPED
