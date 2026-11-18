"""The three manifest apply layers, and the order that resolves a collision between them."""

import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import gnupg

from kubementat.automations.install import pipelines as pipelines_slice
from kubementat.automations.install.pipelines import setup_pipelines

# One manifest, three spellings of the same object. The `layer` label and the string the step echoes
# are the two things that let a test say *which* layer a write came from: the cluster sees one
# object, and these are what distinguishes the four writes a fake records.
MANIFEST = """apiVersion: tekton.dev/v1
kind: {kind}
metadata:
  name: {name}
  labels:
    layer: {layer}
spec:
  steps:
    - name: echo
      image: alpine
      command: ["echo", "{layer}"]
"""

# What a manifest that is a generator template and not a resource looks like, so a test can write
# one under a `templates/` directory and show it never reached the cluster.
TEMPLATE = """apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: deploy-pipeline-APP_NAME_PLACEHOLDER
spec:
  tasks:
    - name: deploy
"""


def write_manifest(directory, kind, name, layer):
    """One manifest file in `directory`, tagged so a received write names its layer."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(MANIFEST.format(kind=kind, name=name, layer=layer))
    return path


def product_dirs(root):
    """The product layer: package data, so it is not under the configuration root at all."""
    assets = root / "site-packages" / "kubementat" / "assets" / "tekton_ci"
    return assets / "tasks", assets / "pipelines"


def environment_dirs(root, environment="dev"):
    """The environment layer, spelled from the configuration root — the same spelling `Config` uses.
    """
    env = root / "platform_config" / environment / "tekton"
    return env / "tasks", env / "pipelines"


def team_dirs(root, environment="dev", team="dev1"):
    """The team layer: generated output under the team's own directory."""
    team_root = root / "platform_config" / environment / team / "tekton"
    return team_root / "tasks", team_root / "pipelines"


class RecordingCluster:
    """Records every write `setup_pipelines` sends, as the write was made."""

    def __init__(self):
        self.applied = []
        self.asked = []

    def api_version_is_served(self, group, version):
        self.asked.append((group, version))
        return version == "v1"

    def create_or_replace_custom_resource(self, group, version, namespace, kind, body,
                                          plural_identifier=None):
        metadata = body.get("metadata") or {}
        steps = ((body.get("spec") or {}).get("steps")) or [{}]
        self.applied.append({
            "kind": kind,
            "name": metadata.get("name"),
            "layer": (metadata.get("labels") or {}).get("layer"),
            # What the step echoes is the document's own voice, so a write that claims to be the
            # team's but carries the product's payload is caught by this and not by the label.
            "echo": (steps[0].get("command") or ["", ""])[1],
            "namespace": namespace,
        })

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


class LayeredConfig:
    """The values `setup_pipelines` reads, with the three layers rooted under one temporary tree."""

    TEKTON_API_GROUP = "tekton.dev"
    TEKTON_API_VERSION = "v1"

    def __init__(self, root, environment="dev", team="dev1"):
        self.kubementat_main_dir = root / "checkout"
        self.platform_config_dir = root / "platform_config"
        self.tekton_task_asset_dir, self.tekton_pipeline_asset_dir = product_dirs(root)
        self.tekton_env_task_dir, self.tekton_env_pipeline_dir = environment_dirs(root, environment)
        self.tekton_team_task_dir, self.tekton_team_pipeline_dir = team_dirs(root, environment, team)
        self.tekton_team_pipeline_run_dir = (root / "platform_config" / environment / team /
                                             "tekton" / "pipeline-runs")

    def get(self, section):
        return {
            "team_static": {
                "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer",
                "PIPELINE_NAMESPACE": "tekton-pipelines-dev1",
                "APP_DEPLOYMENT_NAMESPACE": "app-dev1",
            },
        }[section]


_DEPLOYER_ARMOR = {}


def deployer_armor():
    """One 2048-bit key's armor, generated once per module and thrown away with its keyring."""
    if "armor" not in _DEPLOYER_ARMOR:
        home = Path(tempfile.mkdtemp(prefix="kubementat-layer-test-keyring-"))
        os.chmod(home, 0o700)
        try:
            gpg = gnupg.GPG(gnupghome=str(home), verbose=False)
            email = "dev-deployer@example.invalid"
            key = gpg.gen_key(gpg.gen_key_input(name_real=email, name_email=email,
                                                no_protection=True, key_type="RSA",
                                                key_length=2048))
            _DEPLOYER_ARMOR["armor"] = {
                "public": gpg.export_keys(key.fingerprint),
                "secret": gpg.export_keys(keyids=key.fingerprint, secret=True,
                                          expect_passphrase=False),
            }
        finally:
            shutil.rmtree(home, ignore_errors=True)
    return _DEPLOYER_ARMOR["armor"]


def plant_deployer_keypair(root):
    """The four files `require_deployer_keypair` asks for, inside the tree's configuration root."""
    directory = root / "platform_config"
    directory.mkdir(parents=True, exist_ok=True)
    armor = deployer_armor()
    (directory / "deployer_ssh_key").write_text("ssh-private-half\n")
    (directory / "deployer_ssh_key.pub").write_text("ssh-public-half\n")
    (directory / "gpg_private.key").write_text(armor["secret"], encoding="ascii")
    (directory / "gpg_public_key.gpg").write_text(armor["public"], encoding="ascii")
    return directory


def a_setup_pipelines(root, cluster, monkeypatch, environment="dev", team="dev1"):
    """Run the real `setup_pipelines` against a fake cluster, from a `Context` and nothing else."""
    plant_deployer_keypair(root)
    context = SimpleNamespace(environment=environment, team=team,
                              config=LayeredConfig(root, environment, team))
    monkeypatch.setattr(pipelines_slice, "KubernetesUtils",
                        lambda ctx: cluster or RecordingCluster())
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_setup_role_bindings", lambda *args: None)
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_show_results", lambda *args: None)
    setup_pipelines(context)
    return context


def written(cluster, kind):
    """Every write of one kind, in the order the cluster was asked to take them."""
    return [entry for entry in cluster.applied if entry["kind"] == kind]


def test_all_three_layers_are_applied_in_one_order_for_one_kind(tmp_path, monkeypatch):
    """One sequence, six directories, and no place for a second one to hide."""
    for directory, layer in ([(d, "product") for d in product_dirs(tmp_path)]
                             + [(d, "environment") for d in environment_dirs(tmp_path)]
                             + [(d, "team") for d in team_dirs(tmp_path)]):
        write_manifest(directory, "Task" if directory.name == "tasks" else "Pipeline",
                       f"one-{layer}-{directory.name}", layer)
    cluster = RecordingCluster()

    a_setup_pipelines(tmp_path, cluster, monkeypatch)

    assert [entry["layer"] for entry in cluster.applied] == [
        "product", "product", "environment", "environment", "team", "team",
    ], f"the layers were applied out of order: {[(e['kind'], e['layer']) for e in cluster.applied]}"


# ---------------------------------------------------------------------------
# (e) templates are never applied
# ---------------------------------------------------------------------------
def test_a_generator_template_under_a_layer_is_never_applied_as_a_manifest(tmp_path, monkeypatch):
    """A `templates/` directory under the environment or the team layer is generator input."""
    for root in [d for dirs in (environment_dirs(tmp_path), team_dirs(tmp_path)) for d in dirs]:
        (root / "templates").mkdir(parents=True, exist_ok=True)
        (root / "templates" / "deploy-pipeline-APP_NAME_PLACEHOLDER.yaml").write_text(TEMPLATE)
    # A real document next to the template, so the layer is not empty and the skip is visible as a
    # skip rather than as an empty directory.
    write_manifest(environment_dirs(tmp_path)[1], "Pipeline", "deploy", "environment")
    cluster = RecordingCluster()

    a_setup_pipelines(tmp_path, cluster, monkeypatch)

    names = [entry["name"] for entry in cluster.applied]
    assert "deploy-pipeline-APP_NAME_PLACEHOLDER" not in names
    assert [entry["name"] for entry in written(cluster, "Pipeline")] == ["deploy"]
