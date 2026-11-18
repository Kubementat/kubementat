"""The preflight that decides, once, whether this cluster can be spoken to."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kubementat.automations.install import pipelines
from kubementat.automations.install.pipelines import setup_pipelines
from kubementat.config import keys as config_keys
from kubementat.errors import TektonControllerWithoutConfiguredApiError
from kubementat.infrastructure.kubernetes import apply

# Imported as modules so a patch lands on the module that runs the check.
from kubementat.infrastructure.tekton import preflight, runs


class RecordingCluster:
    """A cluster that answers the version question and records everything else it was asked."""

    def __init__(self, served_versions=(), discovery_error=None):
        self.served_versions = set(served_versions)
        self.discovery_error = discovery_error
        self.asked = []
        self.calls = []

    def api_version_is_served(self, group, version):
        self.asked.append((group, version))
        if self.discovery_error is not None:
            raise self.discovery_error
        return version in self.served_versions

    def list_custom_resources(self, **kwargs):
        self.calls.append("list_custom_resources")
        return {"items": []}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def recorded(*args, **kwargs):
            self.calls.append(name)
            return None

        return recorded


class FakeConfig:
    """The handful of values `setup_pipelines` reads, without touching the filesystem."""

    TEKTON_API_GROUP = "tekton.dev"
    TEKTON_API_VERSION = "v1"

    def __init__(self, tmp_path=None):
        if tmp_path is not None:
            # Two trees, spelled on purpose. The checkout carries *output* and the installation
            # carries the product's own manifests, so a `Config` that puts both under one
            # root cannot be used to test which one a method applied from.
            checkout = tmp_path / "checkout"
            assets = tmp_path / "site-packages" / "kubementat" / "assets" / "tekton_ci"
            env = tmp_path / "platform_config" / "dev" / "tekton"
            self.kubementat_main_dir = str(checkout)
            self.platform_config_dir = tmp_path / "platform_config"
            self.tekton_task_asset_dir = str(assets / "tasks")
            self.tekton_pipeline_asset_dir = str(assets / "pipelines")
            # The environment layer, rooted under the configuration root and not under the checkout.
            self.tekton_env_task_dir = str(env / "tasks")
            self.tekton_env_pipeline_dir = str(env / "pipelines")
            self.tekton_team_pipeline_dir = str(checkout / "pipelines")
            self.tekton_team_task_dir = str(checkout / "tasks")
            self.tekton_team_pipeline_run_dir = str(checkout / "pipeline-runs")
            # A layer that is not a directory is a no-op and is not applied, so the tree this fake
            # hands over has to contain the directories for the calls to be observable.
            for directory in (self.tekton_task_asset_dir, self.tekton_pipeline_asset_dir,
                              self.tekton_env_task_dir, self.tekton_env_pipeline_dir):
                Path(directory).mkdir(parents=True, exist_ok=True)

    def get(self, section):
        return {
            "team_static": {
                "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer",
                "PIPELINE_NAMESPACE": "tekton-pipelines-team",
                "APP_DEPLOYMENT_NAMESPACE": "app",
            },
        }[section]


def a_setup_pipelines_call_on(tmp_path, cluster, applied, monkeypatch):
    """Run `setup_pipelines` without the constructor, which would want a live cluster and helm."""
    root = tmp_path / "platform_config"
    root.mkdir(parents=True, exist_ok=True)
    pair = {}
    for name in config_keys.KEYPAIR_FILE_NAMES:
        path = root / name
        path.write_text(f"fixture {name}\n")
        pair[name] = path
    monkeypatch.setattr(
        config_keys, "require_deployer_keypair",
        lambda directory: config_keys.DeployerKeypair(
            ssh_private=pair["deployer_ssh_key"], ssh_public=pair["deployer_ssh_key.pub"],
            gpg_private=pair["gpg_private.key"], gpg_public=pair["gpg_public_key.gpg"],
            gpg_email="deployer@example.invalid"))
    context = SimpleNamespace(environment="dev", team="dev1", config=FakeConfig(tmp_path))
    # The client, the role bindings and the closing printout are all seams on the module now: the
    # instance they were attributes of is gone, so each is replaced where the function reads it.
    monkeypatch.setattr(pipelines, "KubernetesUtils", lambda ctx: cluster)
    monkeypatch.setattr(pipelines, "_setup_pipelines_setup_role_bindings",
                        lambda *args: applied.append("roles"))
    monkeypatch.setattr(pipelines, "_setup_pipelines_show_results",
                        lambda *args: applied.append("results"))
    # The apply primitive is a module function now, so the recorder replaces it where it lives:
    # `_apply_manifest_layer` calls through `kubementat.infrastructure.kubernetes.apply` and not through this instance.
    monkeypatch.setattr(
        apply, "apply_resources",
        lambda kubernetes_utils, directory, namespace, kind, group, version: applied.append(
            (kind, str(directory))))
    return context


def test_setup_pipelines_never_starts_applying_until_the_controller_answers_the_question(tmp_path,
                                                                                       monkeypatch):
    """The whole point of the preflight: the refusal comes before the first resource."""
    cluster = RecordingCluster()
    applied = []

    with pytest.raises(TektonControllerWithoutConfiguredApiError):
        setup_pipelines(a_setup_pipelines_call_on(tmp_path, cluster, applied, monkeypatch))

    assert applied == [], f"resources were applied before the cluster was asked: {applied}"
    assert cluster.calls == [], f"the cluster was touched before the answer came back: {cluster.calls}"


def test_the_products_tasks_and_pipelines_are_applied_from_the_package_and_not_a_root(tmp_path,
                                                                                    monkeypatch):
    """The layers are applied product first, then environment, then team, read off the calls."""
    cluster = RecordingCluster(served_versions=["v1"])
    applied = []
    context = a_setup_pipelines_call_on(tmp_path, cluster, applied, monkeypatch)

    setup_pipelines(context)

    # Only the applies: the recorder also marks the role bindings and the result dump with bare
    # strings, and they are not part of this claim.
    calls = [entry for entry in applied if isinstance(entry, tuple)]
    assert calls == [
        ("Task", context.config.tekton_task_asset_dir),
        ("Pipeline", context.config.tekton_pipeline_asset_dir),
        # The environment layer, applied between the two: it tunes what the product ships and is
        # tuned by every team under it.
        ("Task", context.config.tekton_env_task_dir),
        ("Pipeline", context.config.tekton_env_pipeline_dir),
        # The team's own two directories are checkout-relative output, and they are still applied,
        # last.
        ("Task", context.config.tekton_team_task_dir),
        ("Pipeline", context.config.tekton_team_pipeline_dir),
    ], f"what `setup_pipelines` applied, in order: {calls}"


def test_a_cluster_that_does_not_serve_the_written_version_names_the_knob_that_changes_it(tmp_path):
    """The message has to be enough to act on, because nothing else will ever fire again."""
    cluster = RecordingCluster(served_versions=["v1beta1"])

    with pytest.raises(TektonControllerWithoutConfiguredApiError) as error:
        preflight.preflight_tekton_api_version(cluster, FakeConfig())

    message = str(error.value)
    assert "tekton.dev/v1" in message
    assert "kmt install" in message, "the upgrade path is what a reader needs next"
    assert "TEKTON_VERSION_PIPELINE" in message, "the platform_config key is what a reader changes"


def test_a_cluster_that_serves_the_written_version_is_asked_once_for_a_whole_command():
    """Listing four kinds of resource is one command, so it is one discovery call."""
    cluster = RecordingCluster(served_versions=["v1"])
    config = FakeConfig()
    namespace = "tekton-pipelines-team"

    # Called the way the command calls them now: the client, the config, the namespace. The
    # assertion is the one that was here before -- four listings, one `api_version_is_served` call.
    runs.list_pipelines(cluster, config, namespace)
    runs.list_tasks(cluster, config, namespace)
    runs.list_pipeline_runs(cluster, config, namespace)
    runs.list_taskruns(cluster, config, namespace)

    assert cluster.asked == [("tekton.dev", "v1")], (
        f"the question was asked {len(cluster.asked)} times; the check belongs once per command"
    )
