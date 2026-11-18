"""`kmt install` creates the configuration's git-crypt Secret only where a clone would need opening.
"""
import os
import shutil
import tempfile
from pathlib import Path

import gnupg
import pytest

EMAIL = "deployer@configuration-key-secret.test"

from types import SimpleNamespace

from kubementat.automations.install import pipelines as pipelines_slice
from kubementat.automations.install.pipelines import setup_pipelines
from kubementat.config import bootstrap as config_bootstrap
from kubementat.config import keys as config_keys

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
    """One real key's two armored halves, made once per module in a keyring that dies with it."""
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
    """The files install reads, with the two that matter switched on or off."""
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


def install_pipelines(directory: Path, monkeypatch) -> RecordingCluster:
    cluster = RecordingCluster()
    context = SimpleNamespace(environment=ENVIRONMENT, team=TEAM, config=PipelineConfig(directory))
    # The client and the two closing steps are seams on the module now, so each is replaced where
    # `setup_pipelines` reads it; what the directory holds is what these tests are about.
    monkeypatch.setattr(pipelines_slice, "KubernetesUtils", lambda ctx: cluster)
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_setup_role_bindings", lambda *args: None)
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_show_results", lambda *args: None)
    setup_pipelines(context)
    return cluster


def a_secret_named(cluster: RecordingCluster, name: str):
    found = [secret for secret in cluster.secrets if secret["secret_name"] == name]
    assert len(found) == 1, f"{name} was created {len(found)} times: {[s['secret_name'] for s in cluster.secrets]}"
    return found[0]


def test_a_tree_that_declares_the_filter_gets_the_secret_it_needs(tmp_path, monkeypatch, armored_deployer_key):
    """The configuration clone that `git-clone-configuration` fetches is locked, so the Secret must
    exist.
    """
    directory = a_configuration_directory(tmp_path, git_crypt=True, key=True, armor=armored_deployer_key)
    cluster = install_pipelines(directory, monkeypatch)

    secret = a_secret_named(cluster, CONFIGURATION_KEY_SECRET)
    assert secret["key_type"] == "Opaque", \
        "the Secret is not Opaque, which is what a symmetric key is"
    assert "git-crypt-key" in secret["string_data"], \
        "the Secret carries no `git-crypt-key`, so the unlock step's secretKeyRef resolves to nothing"


def test_a_tree_that_never_used_git_crypt_gets_no_secret_and_still_installs(tmp_path, monkeypatch, armored_deployer_key):
    """A configuration with nothing encrypted has no key to hand out, and install must not pretend.
    """
    directory = a_configuration_directory(tmp_path, git_crypt=False, key=False, armor=armored_deployer_key)
    cluster = install_pipelines(directory, monkeypatch)

    names = [secret["secret_name"] for secret in cluster.secrets]
    assert CONFIGURATION_KEY_SECRET not in names, \
        f"install created {CONFIGURATION_KEY_SECRET} for a directory that declares no git-crypt filter. " \
        "There is no key to put in it and nothing for it to open, and the pipeline's unlock step would " \
        "then be reading a Secret that is a lie about this configuration"

    # The deployer Secrets are still there, so this is a skip and not a failure of the whole method.
    assert names, "nothing was created at all, so the method stopped instead of skipping"
