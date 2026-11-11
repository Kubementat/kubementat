"""`kmt install` creates the configuration's git-crypt Secret only where a clone would need opening.

Two decisions have to agree, and nothing in the tree forces them to:

* install's. It reads the configuration directory and decides whether to build
  `git-crypt-configuration-key`.
* the unlock step's. It reads the *clone's* `.gitattributes` and decides whether an absent key is a bug or
  the expected shape of an unencrypted configuration.

If install says "no Secret needed" and the step says "this clone is encrypted, refuse", a plaintext tree
fails every pipeline. If install says "Secret" and the step says "no key is fine", an encrypted tree whose
key went missing goes silently green, which is worse: the ciphertext is what the pipeline reads.

So the marker is the subject of this file, and it is `.gitattributes` naming `filter=git-crypt`. That is
the one git-crypt fact a clone carries: the keystore lives under `.git/`, which git does not clone, and
`git_crypt_symmetric.key` is gitignored, which is the entire reason this ticket exists. A predicate
built on either of those two would answer "no" to the exact tree that needs unlocking.

The measurements that back this are in
`test_exactly_one_base64_layer_sits_between_the_key_and_the_unlock_step.py`, which runs the shipped step
against real git-crypt repositories. This file is about the other side of the pair.
"""
import logging
import os
import shutil
import tempfile
from pathlib import Path

import gnupg
import pytest

EMAIL = "deployer@configuration-key-secret.test"

from kubementat.automation import Automation
from kubementat import config_bootstrap, config_keys

ENVIRONMENT = "dev"
TEAM = "dev1"
PIPELINE_NAMESPACE = "pipelines"
CONFIGURATION_KEY_SECRET = "git-crypt-configuration-key"

SSH_PRIVATE_BYTES = b"-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n-----END OPENSSH PRIVATE KEY-----\n"
GPG_PRIVATE = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nfixture\n-----END PGP PRIVATE KEY BLOCK-----\n"
GPG_PUBLIC = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nfixture\n-----END PGP PUBLIC KEY BLOCK-----\n"
GIT_CRYPT_FILTER = "*.encrypted.* filter=git-crypt diff=git-crypt\n"


class RecordingCluster:
    """Everything `setup_pipelines` sends, with the Secrets kept whole."""

    def __init__(self):
        self.secrets = []
        self.calls = []

    def api_version_is_served(self, group, version):
        return True

    def create_or_replace_secret(self, **kwargs):
        self.secrets.append(kwargs)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


class PipelineConfig:
    """The values `setup_pipelines` reads, rooted at the directory under test."""

    TEKTON_API_GROUP = "tekton.dev"
    TEKTON_API_VERSION = "v1"

    def __init__(self, root):
        self.kubementat_main_dir = root.parent / "checkout"
        self.platform_config_dir = root
        assets = root.parent / "site-packages" / "kubementat" / "assets" / "tekton_ci"
        self.tekton_task_asset_dir = assets / "tasks"
        self.tekton_pipeline_asset_dir = assets / "pipelines"
        self.tekton_env_task_dir = root / ENVIRONMENT / "tekton" / "tasks"
        self.tekton_env_pipeline_dir = root / ENVIRONMENT / "tekton" / "pipelines"
        team = root / ENVIRONMENT / TEAM / "tekton"
        self.tekton_team_task_dir = team / "tasks"
        self.tekton_team_pipeline_dir = team / "pipelines"
        self.tekton_team_pipeline_run_dir = team / "pipeline-runs"

    def get(self, section):
        return {
            "team_static": {
                "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer-sa",
                "PIPELINE_NAMESPACE": PIPELINE_NAMESPACE,
                "APP_DEPLOYMENT_NAMESPACE": TEAM,
            },
        }[section]


@pytest.fixture(scope="module")
def armored_deployer_key():
    """One real key's two armored halves, made once per module in a keyring that dies with it.

    Real armor and not a marker string, for the reason the deployer guard gives: `require_deployer_keypair`
    reads the deployer's identity out of the key itself, and a file that does not import is refused before
    this file's own subject is reached. A fixture that could not pass that check would be measuring nothing.
    """
    home = Path(tempfile.mkdtemp(prefix="kubementat-configuration-key-keyring-"))
    os.chmod(home, 0o700)
    try:
        gpg = gnupg.GPG(gnupghome=str(home), verbose=False)
        key = gpg.gen_key(gpg.gen_key_input(name_real=EMAIL, name_email=EMAIL, no_protection=True,
                                           key_type="RSA", key_length=2048))
        assert key.fingerprint, f"the fixture's keygen produced no key: {key}"
        yield {"public": gpg.export_keys(key.fingerprint),
               "secret": gpg.export_keys(keyids=key.fingerprint, secret=True,
                                         expect_passphrase=False)}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def a_configuration_directory(root: Path, *, git_crypt: bool, key: bool, armor) -> Path:
    """The files install reads, with the two that matter switched on or off.

    `git_crypt` writes the `.gitattributes` line the product's predicate looks for. `key` puts the exported
    symmetric key in the directory's root, which is what `config init` step 8 does and what a clone of the
    repository cannot have.
    """
    directory = root / "platform_config"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / config_keys.SSH_PRIVATE_NAME).write_bytes(SSH_PRIVATE_BYTES)
    (directory / config_keys.SSH_PUBLIC_NAME).write_text("ssh-rsa AAAAfixture deployer\n", encoding="ascii")
    (directory / config_keys.GPG_PRIVATE_NAME).write_text(armor["secret"], encoding="ascii")
    (directory / config_keys.GPG_PUBLIC_NAME).write_text(armor["public"], encoding="ascii")
    if git_crypt:
        (directory / ".gitattributes").write_text(GIT_CRYPT_FILTER, encoding="utf-8")
    if key:
        (directory / config_bootstrap.GIT_CRYPT_SYMMETRIC_KEY_NAME).write_bytes(b"symmetric-key-bytes\n")
    return directory


def install_pipelines(directory: Path) -> RecordingCluster:
    cluster = RecordingCluster()
    automation = Automation.__new__(Automation)
    automation.environment = ENVIRONMENT
    automation.team = TEAM
    automation.config = PipelineConfig(directory)
    automation.kubernetes_utils = cluster
    automation._setup_pipelines_setup_role_bindings = lambda *args: None
    automation._setup_pipelines_show_results = lambda *args: None
    automation.setup_pipelines()
    return cluster


def a_secret_named(cluster: RecordingCluster, name: str):
    found = [secret for secret in cluster.secrets if secret["secret_name"] == name]
    assert len(found) == 1, f"{name} was created {len(found)} times: {[s['secret_name'] for s in cluster.secrets]}"
    return found[0]


def test_a_tree_that_declares_the_filter_gets_the_secret_it_needs(tmp_path, armored_deployer_key):
    """The configuration clone that `git-clone-configuration` fetches is locked, so the Secret must exist.

    This is the case the ticket is *for*. If it is the only case the guard covers, everything below is
    decoration, which is why the next test asserts the opposite shape rather than repeating this one.
    """
    directory = a_configuration_directory(tmp_path, git_crypt=True, key=True, armor=armored_deployer_key)
    cluster = install_pipelines(directory)

    secret = a_secret_named(cluster, CONFIGURATION_KEY_SECRET)
    assert secret["key_type"] == "Opaque", \
        "the Secret is not Opaque, which is what a symmetric key is"
    assert "git-crypt-key" in secret["string_data"], \
        "the Secret carries no `git-crypt-key`, so the unlock step's secretKeyRef resolves to nothing"


def test_a_tree_that_never_used_git_crypt_gets_no_secret_and_still_installs(tmp_path, armored_deployer_key):
    """A configuration with nothing encrypted has no key to hand out, and install must not pretend.

    The important half is that `setup_pipelines` *completes*. Failing an install because a file is missing
    from a repository that never used git-crypt would be a regression this ticket introduced, and it is the
    kind of failure that shows up on somebody else's tree and not on yours.
    """
    directory = a_configuration_directory(tmp_path, git_crypt=False, key=False, armor=armored_deployer_key)
    cluster = install_pipelines(directory)

    names = [secret["secret_name"] for secret in cluster.secrets]
    assert CONFIGURATION_KEY_SECRET not in names, \
        f"install created {CONFIGURATION_KEY_SECRET} for a directory that declares no git-crypt filter. " \
        "There is no key to put in it and nothing for it to open, and the pipeline's unlock step would " \
        "then be reading a Secret that is a lie about this configuration"

    # The deployer Secrets are still there, so this is a skip and not a failure of the whole method.
    assert names, "nothing was created at all, so the method stopped instead of skipping"


def test_the_absence_of_the_key_is_said_out_loudly(tmp_path, caplog, armored_deployer_key):
    """When install skips the Secret it says why, because the next failure is somebody else's pipeline.

    A skipped Secret is invisible in the cluster. What it costs appears days later as a pipeline that reads
    ciphertext, and the one sentence that connects those two events has to be said while both are still
    here.
    """
    directory = a_configuration_directory(tmp_path, git_crypt=False, key=False, armor=armored_deployer_key)
    with caplog.at_level(logging.WARNING):
        install_pipelines(directory)

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert CONFIGURATION_KEY_SECRET in messages, \
        "install said nothing about not creating the Secret. The operator learns about it from a " \
        "pipeline that reads ciphertext, and that sentence is not written by anyone"
    assert "gitattributes" in messages or "encrypt" in messages.lower(), \
        "the warning does not name the marker it consulted, so the reader cannot tell an encrypted tree " \
        "from a plaintext one and has to go and look, which is the thing the warning was for"


def test_install_and_the_step_read_the_same_marker(tmp_path, armored_deployer_key):
    """Both sides ask one question of one file, so they cannot disagree about the same tree.

    Checked against the shipped step's script and not against a paraphrase of it: what is asserted is that
    the literal the predicate looks for is the literal the step greps for.
    """
    directory = a_configuration_directory(tmp_path, git_crypt=True, key=True, armor=armored_deployer_key)
    assert config_bootstrap.uses_git_crypt(directory) is True, \
        "the predicate says a directory with the standard `.gitattributes` line is not git-crypt managed, " \
        "which would mean install never makes the Secret and the step refuses a key that was never sent"

    encrypted_tree = a_configuration_directory(tmp_path / "other", git_crypt=True, key=False, armor=armored_deployer_key)
    assert config_bootstrap.uses_git_crypt(encrypted_tree) is True, \
        "the marker does not depend on the key file being present, and it must not: the key is gitignored, " \
        "so a clone answers this question from `.gitattributes` alone"

    plaintext_tree = a_configuration_directory(tmp_path / "plain", git_crypt=False, key=False, armor=armored_deployer_key)
    assert config_bootstrap.uses_git_crypt(plaintext_tree) is False, \
        "the predicate calls a tree with no git-crypt declaration encrypted, and that is the direction " \
        "that fails an install for a reason nobody can see"

    assert not config_bootstrap.uses_git_crypt(tmp_path / "nowhere"), \
        "a directory that does not exist reads as encrypted, so a typo in a path becomes a refusal that " \
        "blames the configuration"

    from asset_paths import tekton_ci_path
    step = tekton_ci_path("tasks", "git-crypt-unlock.yml").read_text(encoding="utf-8")
    assert "filter=git-crypt" in step, \
        "the unlock step does not look for the marker this predicate uses, so install's decision and the " \
        "step's decision are made from different facts about the same repository"
