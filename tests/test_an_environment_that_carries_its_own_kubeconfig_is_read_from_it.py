"""An environment's own kubeconfig answers ahead of `$KUBECONFIG`.

`platform_config/<environment>/kubeconfig.encrypted.yaml` is the file kubementat tells an operator to
put there, and it is read when nothing above it declared a path. Three properties are worth writing
down, because none of them is what the machinery would do by default:

* the tree's own file beats `$KUBECONFIG`, which is the point of moving the default inside the tree;
* a path someone *did* declare still beats the tree, so this is a default and not a second writer;
* a file that is not there changes nothing, which is why the change can land without touching a
  single existing installation.

The name is load-bearing: git-crypt's filter in a prepared tree is `*.encrypted.*`, so a kubeconfig
called anything else is plaintext with an encrypted-looking path. One test asserts the name, one
asserts that the ciphertext refusal arrives rather than a YAML error, and one asserts `config init`
does not quarantine the file it is telling people to create.
"""

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from kubementat import cluster
from kubementat.cluster import Context
from kubementat.config_init import init_configuration
from conftest import cluster_state

CONTEXT = "in-tree-cluster"
IN_TREE_ADDRESS = "https://in-tree.example.test:6443"
AMBIENT_ADDRESS = "https://ambient.example.test:6443"


def kubeconfig(address: str, context: str = CONTEXT) -> str:
    """A one-context kubeconfig, in the shape the loader reads."""
    return yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "c", "cluster": {"server": address}}],
        "contexts": [{"name": context, "context": {"cluster": "c", "user": "u"}}],
        "users": [{"name": "u", "user": {"token": "not-a-real-token-see-the-address"}}],
    })


def write_kubeconfig(path: Path, address: str) -> Path:
    """One kubeconfig at `path`, addressed at `address`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kubeconfig(address))
    return path


def environment_with_kubeconfig(fixture_repo, *, in_tree: bool, address=IN_TREE_ADDRESS,
                                declare_path: bool = False, declared_address=None):
    """A tree whose environment declares a context and *no* path, with or without its own kubeconfig.

    `CLUSTER_KUBECONFIG_PATH` is written here and not inherited: the fixture's own
    `environment_static` merges a declared path into every tree it makes, and a test that used it
    would be asserting about a path it had already declared, which is the one case this file is not
    about. An empty pair of braces is the whole difference between "the environment carries nothing"
    and "the environment carries its own file".
    """
    keys = {"CLUSTER_KUBE_CONTEXT": CONTEXT, "CLUSTER_KUBECONFIG_PATH": None}
    if in_tree:
        write_kubeconfig(
            fixture_repo.platform_config / fixture_repo.environment / "kubeconfig.encrypted.yaml",
            address)
    if declare_path:
        keys["CLUSTER_KUBECONFIG_PATH"] = str(write_kubeconfig(
            fixture_repo.root / "declared.kubeconfig", declared_address or address))
    fixture_repo.write_json(fixture_repo.platform_config / fixture_repo.environment / "static.json",
                            keys)
    return fixture_repo


def resolved_address(fixture_repo, monkeypatch, *, ambient=None):
    """The address `Context.build` lands on, with `$KUBECONFIG` optionally set first.

    The ambient file is written to a real path in the tmp directory, because the rule is a path and a
    fake would not prove the file was opened.
    """
    if ambient is not None:
        ambient_path = write_kubeconfig(fixture_repo.root / "ambient.kubeconfig", AMBIENT_ADDRESS)
        monkeypatch.setenv("KUBECONFIG", str(ambient_path))
    else:
        monkeypatch.delenv("KUBECONFIG", raising=False)
    target = Context.build("dev", platform_config_dir=str(fixture_repo.platform_config)).target
    return target.address


# ------------------------------------------------------------------ the tree's file answers first


def test_an_environment_kubeconfig_is_read_ahead_of_the_variable(fixture_repo, monkeypatch):
    """The whole decision, in one assert: the address comes from the tree and not from the shell.

    `$KUBECONFIG` is set to a file that would have answered with a *different* address, so the result
    cannot be read as "both point at the same cluster".
    """
    environment_with_kubeconfig(fixture_repo, in_tree=True)
    monkeypatch.setenv("KUBECONFIG", str(write_kubeconfig(
        fixture_repo.root / "ambient.kubeconfig", AMBIENT_ADDRESS)))

    target = Context.build("dev", platform_config_dir=str(fixture_repo.platform_config)).target

    assert target.address == IN_TREE_ADDRESS, target.address
    assert target.kubeconfig_path.name == "kubeconfig.encrypted.yaml", target.kubeconfig_path


def test_where_no_tree_file_exists_the_variable_still_answers(fixture_repo, monkeypatch):
    """An installation that has never heard of this keeps behaving as it did.

    This is the case that makes the change safe to land: nothing is demanded of a tree that predates
    the advice, and `$KUBECONFIG` is still the answer when the tree carries no file of its own. The
    ambient file here is what proves it was read, because it carries a different address.
    """
    environment_with_kubeconfig(fixture_repo, in_tree=False)

    assert resolved_address(fixture_repo, monkeypatch, ambient=AMBIENT_ADDRESS) == AMBIENT_ADDRESS


def test_a_declared_path_still_beats_the_environment_kubeconfig(fixture_repo, monkeypatch):
    """The tree answers where nobody said anything, and that is all it does.

    A `CLUSTER_KUBECONFIG_PATH` is a declaration, and a default that outvotes a declaration is not a
    default, it is a second writer. The declared file is the one opened, and its address is the one
    reported.
    """
    environment_with_kubeconfig(fixture_repo, in_tree=True, address=IN_TREE_ADDRESS,
                                declare_path=True, declared_address=AMBIENT_ADDRESS)
    monkeypatch.delenv("KUBECONFIG", raising=False)

    target = Context.build("dev", platform_config_dir=str(fixture_repo.platform_config)).target

    assert target.address == AMBIENT_ADDRESS, target.address


# ---------------------------------------------------------------- the name is the encryption


def test_the_filename_is_the_one_the_trees_git_crypt_filter_encodes():
    """`*.encrypted.*` is what a prepared tree declares; this file has to match it.

    A rename would silently un-encrypt every kubeconfig that follows the advice, and nothing would
    fail — git-crypt would simply stop touching the file. Asserting the spelling is the cheapest way
    to keep that from being a documentation-only bug.
    """
    assert cluster.IN_TREE_KUBECONFIG_FILENAME == "kubeconfig.encrypted.yaml"
    assert "*.encrypted.*" in (Path("src/kubementat/config_bootstrap.py").read_text())


def test_a_locked_in_tree_kubeconfig_says_it_is_ciphertext_and_not_broken(fixture_repo, monkeypatch):
    """An operator on a locked checkout gets the sentence about the lock.

    A kubeconfig is read through `read_decrypted` like every other encrypted file, so the failure here
    is `LockedConfigFileError` and not the YAML parse error that reading raw bytes would give. It is
    the difference between "unlock this" and "your file is corrupt".

    The read is asked of `ClusterTarget`, not of the path check: existence is decided first, and a
    file that exists is not yet a file that was decrypted.
    """
    path = fixture_repo.platform_config / fixture_repo.environment / "kubeconfig.encrypted.yaml"
    path.write_bytes(b"\x83\x00\x01binary-ciphertext-not-utf8")

    from kubementat.cluster import ClusterFileUnreadableError, ClusterTarget
    with pytest.raises(ClusterFileUnreadableError) as refused:
        ClusterTarget.from_kubeconfig(str(path), CONTEXT, "dev")
    assert "ciphertext" in str(refused.value), refused.value
    assert "git-crypt" in str(refused.value), "the sentence did not say what to do about it"


# -------------------------------------------------------------------- init does not quarantine it


def test_config_init_accepts_a_tree_that_follows_the_advice(tmp_path):
    """Putting the file where the advice says must not make `config init` refuse.

    The guard's rule is that any file kubementat did not write is foreign material, and that rule was
    correct until this file existed. It is now expected material, so a root that arrives with a
    kubeconfig in place is prepared normally and no step blames the operator for it.

    The root is empty rather than built by a fixture, because the thing under test is what init does
    to a tree it did not lay out -- and a fixture that had already written `static.json` would answer
    a different question and report it as this one.
    """
    root = tmp_path / "platform_config"
    root.mkdir()
    (root / "dev").mkdir()
    (root / "dev" / "kubeconfig.encrypted.yaml").write_text(kubeconfig(IN_TREE_ADDRESS))

    steps = init_configuration(root, "dev", "fixture")

    assert steps, "init reported no steps, so there is nothing here that says the tree was prepared"
    assert (root / "dev" / "static.json").is_file(), "the tree was not prepared around the kubeconfig"
    assert (root / "dev" / "kubeconfig.encrypted.yaml").is_file(), \
        "init touched the file it was told to expect"


def test_a_genuinely_foreign_file_is_still_refused_after_the_exception(tmp_path):
    """The exception did not widen the rule.

    One file is expected because kubementat reads it. A file kubementat reads nowhere is still
    evidence that something else wrote into the tree, and init still says so.
    """
    root = tmp_path / "platform_config"
    root.mkdir()
    (root / "dev").mkdir()
    (root / "dev" / "kubeconfig.encrypted.yaml").write_text(kubeconfig(IN_TREE_ADDRESS))
    (root / "dev" / "somebody-elses-file.json").write_text("{}")

    import click
    with pytest.raises(click.ClickException) as refused:
        init_configuration(root, "dev", "fixture")
    assert "foreign" in str(refused.value)
