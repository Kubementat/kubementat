"""The two deployer Secrets are built from the configuration directory's keypair, or not at all."""

import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import gnupg
import pytest

from kubementat.automations.install import pipelines as pipelines_slice
from kubementat.automations.install.pipelines import setup_pipelines
from kubementat.config import keys as config_keys
from kubementat.errors import DeployerKeyMissingError

ENVIRONMENT = "dev"
TEAM = "dev1"
PIPELINE_NAMESPACE = "dev1-pipelines"
EMAIL = "deployer@example.invalid"

SSH_PRIVATE_BYTES = (b"-----BEGIN RSA PRIVATE KEY-----\n"
                     b"fixture-secret-bytes\n"
                     b"-----END RSA PRIVATE KEY-----\n")


@pytest.fixture(scope="module")
def gpg_armor():
    """One real key's two armored halves, made once per module in a keyring that dies with it."""
    home = Path(tempfile.mkdtemp(prefix="kubementat-secret-test-keyring-"))
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


@pytest.fixture
def configuration_root(tmp_path, gpg_armor):
    """A configuration root holding the four keypair files, the way `kmt config init` leaves one."""
    root = tmp_path / "platform_config"
    root.mkdir()
    (root / config_keys.SSH_PRIVATE_NAME).write_bytes(SSH_PRIVATE_BYTES)
    (root / config_keys.SSH_PUBLIC_NAME).write_text("ssh-rsa AAAAfixture deployer\n")
    (root / config_keys.GPG_PRIVATE_NAME).write_text(gpg_armor["secret"], encoding="ascii")
    (root / config_keys.GPG_PUBLIC_NAME).write_text(gpg_armor["public"], encoding="ascii")
    return root


class SecretRecordingCluster:
    """Records everything `setup_pipelines` asks of the cluster, with the Secrets in full."""

    def __init__(self):
        self.calls = []
        self.secrets = []

    def api_version_is_served(self, group, version):
        self.calls.append(("api_version_is_served", group, version))
        return True

    def create_or_replace_secret(self, **kwargs):
        self.calls.append(("create_or_replace_secret", kwargs.get("secret_name")))
        self.secrets.append(kwargs)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return record


class PipelineConfig:
    """The values `setup_pipelines` reads, rooted at the configuration directory under test."""

    TEKTON_API_GROUP = "tekton.dev"
    TEKTON_API_VERSION = "v1"

    def __init__(self, root, environment=ENVIRONMENT, team=TEAM):
        self.kubementat_main_dir = root.parent / "checkout"
        self.platform_config_dir = root
        assets = root.parent / "site-packages" / "kubementat" / "assets" / "tekton_ci"
        self.tekton_task_asset_dir = assets / "tasks"
        self.tekton_pipeline_asset_dir = assets / "pipelines"
        self.tekton_env_task_dir = root / environment / "tekton" / "tasks"
        self.tekton_env_pipeline_dir = root / environment / "tekton" / "pipelines"
        team_tekton = root / environment / team / "tekton"
        self.tekton_team_task_dir = team_tekton / "tasks"
        self.tekton_team_pipeline_dir = team_tekton / "pipelines"
        self.tekton_team_pipeline_run_dir = team_tekton / "pipeline-runs"

    def get(self, section):
        # No `env_static_encrypted`: nothing in `setup_pipelines` reads the deployer keys out of a
        # layer any more, and a fake that still answered the old keys would let a regression read
        # them without failing here.
        return {
            "team_static": {
                "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer-sa",
                "PIPELINE_NAMESPACE": PIPELINE_NAMESPACE,
                "APP_DEPLOYMENT_NAMESPACE": TEAM,
            },
        }[section]


def a_setup_pipelines(root, cluster, monkeypatch):
    """The real `setup_pipelines` against the recording cluster, without the constructor."""
    context = SimpleNamespace(environment=ENVIRONMENT, team=TEAM, config=PipelineConfig(root))
    monkeypatch.setattr(pipelines_slice, "KubernetesUtils", lambda ctx: cluster)
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_setup_role_bindings", lambda *args: None)
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_show_results", lambda *args: None)
    setup_pipelines(context)
    return context


def a_secret_named(cluster, name):
    found = [secret for secret in cluster.secrets if secret["secret_name"] == name]
    assert len(found) == 1, f"{name} was created {len(found)} times: {cluster.secrets}"
    return found[0]


def test_a_root_without_a_keypair_refuses_by_name_before_touching_a_cluster(tmp_path, monkeypatch):
    """The guard census L found missing: named, early, and with nothing asked of the cluster."""
    root = tmp_path / "platform_config"
    root.mkdir()
    cluster = SecretRecordingCluster()

    with pytest.raises(DeployerKeyMissingError) as refused:
        a_setup_pipelines(root, cluster, monkeypatch)

    message = str(refused.value)
    assert str(root) in message, f"the path that was looked in: {message}"
    assert "config init" in message, f"the command that prepares the pair: {message}"
    for name in config_keys.KEYPAIR_FILE_NAMES:
        assert name in message, f"the four names are the answer to 'what is missing': {message}"
    assert cluster.calls == [], f"the run touched the cluster before it refused: {cluster.calls}"
