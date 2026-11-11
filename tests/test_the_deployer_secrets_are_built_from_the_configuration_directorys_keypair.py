"""The two deployer Secrets are built from the configuration directory's keypair, or not at all.

The other half of the deployer keypair rule: `initialize` was the only writer of the two
`GIT_DEPLOYER_*_BASE64`
answers `setup_pipelines` used to read out of `env_static_encrypted`, and it is deleted. The
Secrets are now built from the keypair `kmt config init` wrote into the configuration root, and a
root without one is refused by name — `DeployerKeyMissingError`, whose message names `kmt config
init` — before the run touches a cluster, instead of dying inside the Kubernetes client's
serializer with `raw_data={"ssh-privatekey": None}` (census L, Q2 item 8).

The encoding claims are about *layers*, because the consumers on the other side decode a fixed
number of times:

* `git-deployer-ssh-key` (`kubernetes.io/ssh-auth`): the `git-clone-with-ssh-auth` task writes the
  environment variable straight to `$HOME/.ssh/deploy_key`, so the Secret's `data` is the key
  file's bytes under exactly one base64 layer — the transport encoding kubelet removes on the way
  into the container.
* `git-deployer-gpg-key` (`Opaque`): the two `git-clone*.yml` Tasks pipe the variable through
  exactly one `base64 -d` before `gpg --import` (`git-clone.yml`, `git-clone-with-ssh-auth.yml`),
  so what the variable carries is the key file's bytes base64-encoded once. It goes in as
  `stringData` — the API server applies the transport encoding — and a second encoding on
  kubementat's side is the bug no assertion here would forgive twice.
"""

import base64
import os
import shutil
import tempfile
from pathlib import Path

import gnupg
import pytest

from kubementat import config_keys
from kubementat.automation import Automation
from kubementat.config_keys import DeployerKeyMissingError

ENVIRONMENT = "dev"
TEAM = "dev1"
PIPELINE_NAMESPACE = "dev1-pipelines"
EMAIL = "deployer@example.invalid"

SSH_PRIVATE_BYTES = (b"-----BEGIN RSA PRIVATE KEY-----\n"
                     b"fixture-secret-bytes\n"
                     b"-----END RSA PRIVATE KEY-----\n")


@pytest.fixture(scope="module")
def gpg_armor():
    """One real key's two armored halves, made once per module in a keyring that dies with it.

    Real armor and not a marker string, because `require_deployer_keypair` reads the deployer's
    identity back out of the key itself — a file that does not import is a file the guard refuses.
    """
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
    """Records everything `setup_pipelines` asks of the cluster, with the Secrets in full.

    Every method is on the record, so "refused before it touched a cluster" is an assertion about
    an empty log and not about a method the fake happened not to have.
    """

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


def a_setup_pipelines(root, cluster):
    """The real `setup_pipelines` against the recording cluster, without the constructor.

    The constructor wants a resolved `Context`; what is under test is what the method asks of the
    configuration root and what it sends the cluster, so the two halves that are neither — the
    role bindings and the closing print — are stubbed and everything else runs.
    """
    automation = Automation.__new__(Automation)
    automation.environment = ENVIRONMENT
    automation.team = TEAM
    automation.config = PipelineConfig(root)
    automation.kubernetes_utils = cluster
    automation._setup_pipelines_setup_role_bindings = lambda *args: None
    automation._setup_pipelines_show_results = lambda *args: None
    automation.setup_pipelines()
    return automation


def a_secret_named(cluster, name):
    found = [secret for secret in cluster.secrets if secret["secret_name"] == name]
    assert len(found) == 1, f"{name} was created {len(found)} times: {cluster.secrets}"
    return found[0]


def test_the_ssh_secret_is_the_key_files_bytes_under_one_base64_layer(configuration_root):
    """`raw_data` is the `data` field: one layer, and under it the file's bytes and nothing else.

    The task on the other side writes the decoded variable to `$HOME/.ssh/deploy_key` as-is, so
    anything but the file's own bytes under the transport layer is a deploy key ssh cannot read.
    """
    cluster = SecretRecordingCluster()

    a_setup_pipelines(configuration_root, cluster)

    secret = a_secret_named(cluster, "git-deployer-ssh-key")
    assert secret["key_type"] == "kubernetes.io/ssh-auth"
    assert secret["namespace"] == PIPELINE_NAMESPACE
    assert base64.b64decode(secret["raw_data"]["ssh-privatekey"]) == SSH_PRIVATE_BYTES


def test_the_gpg_secret_decodes_to_the_key_file_after_exactly_one_base64_d(configuration_root):
    """`string_data`, encoded once by kubementat: the task's one `base64 -d` lands on the armor.

    A second encoding would decode to base64 text where `gpg --import` expects key material, which
    is the failure shape that only ever showed up inside a pipeline — so the armor header is
    asserted after one decode, and the stringData layer is asserted to be the only one added here.
    """
    cluster = SecretRecordingCluster()

    a_setup_pipelines(configuration_root, cluster)

    secret = a_secret_named(cluster, "git-deployer-gpg-key")
    assert secret["key_type"] == "Opaque"
    assert secret["namespace"] == PIPELINE_NAMESPACE
    variable = secret["string_data"]["private-key"]
    decoded = base64.b64decode(variable)
    assert decoded == (configuration_root / config_keys.GPG_PRIVATE_NAME).read_bytes()
    assert decoded.startswith(b"-----BEGIN PGP PRIVATE KEY BLOCK-----"), \
        "one decode did not land on key material: a second base64 layer crept in"


def test_a_root_without_a_keypair_refuses_by_name_before_touching_a_cluster(tmp_path):
    """The guard census L found missing: named, early, and with nothing asked of the cluster.

    The message owes the operator the path it looked in, the four names the deployer is made of,
    and the command that creates them — and the cluster log owes the claim "before": an empty
    record, including the read preflight, because the keypair is a fact about the tree and nothing
    about the cluster can excuse its absence.
    """
    root = tmp_path / "platform_config"
    root.mkdir()
    cluster = SecretRecordingCluster()

    with pytest.raises(DeployerKeyMissingError) as refused:
        a_setup_pipelines(root, cluster)

    message = str(refused.value)
    assert str(root) in message, f"the path that was looked in: {message}"
    assert "config init" in message, f"the command that prepares the pair: {message}"
    for name in config_keys.KEYPAIR_FILE_NAMES:
        assert name in message, f"the four names are the answer to 'what is missing': {message}"
    assert cluster.calls == [], f"the run touched the cluster before it refused: {cluster.calls}"
