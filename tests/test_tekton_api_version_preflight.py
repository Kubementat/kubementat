"""The preflight that decides, once, whether this cluster can be spoken to.

kubementat writes and reads one `tekton.dev` API version: `v1`. The old failure mode this
replaces is the interesting one — a cluster running Pipelines 0.x serves only
`tekton.dev/v1beta1`, and `setup_pipelines` would then be told to apply every Task and
Pipeline in `tekton_ci/` one at a time and would report forty-odd 404s without ever saying
that the controller behind the group is not there. That is a wall of errors with no
explanation, and it is the only symptom of a problem with one cause.

So the check has two halves: something that asks the cluster one question
(`KubernetesUtils.api_version_is_served`), and something that refuses to go on when the
answer is no (`preflight_tekton_api_version`), asked before anything is applied.
"""

import pytest

from pathlib import Path

from kubementat.core.config import config as config_module
from kubementat import config_keys
from kubementat.infrastructure.kubernetes import apply
from kubementat.automation import Automation
from kubementat.errors import TektonControllerWithoutConfiguredApiError
from kubementat.tekton_utils import (
    TektonUtils,
    preflight_tekton_api_version,
)


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
    """Run `setup_pipelines` without the constructor, which would want a live cluster and helm.

    Everything past the version question is replaced by a recorder, because this file is
    about where the refusal sits, not about what secrets get created after it. The deployer
    keypair guard that runs before the question is answered with a stand-in pair over real
    files: its own claims live in `test_the_deployer_keypair_is_a_property_of_the_`
    `configuration_directory.py` and `test_the_deployer_secrets_are_built_from_the_`
    `configuration_directorys_keypair.py`, and re-measuring them here would put a gpg keygen
    in the way of an ordering claim.
    """
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
    automation = Automation.__new__(Automation)
    automation.environment = "dev"
    automation.team = "dev1"
    automation.config = FakeConfig(tmp_path)
    automation.kubernetes_utils = cluster
    automation.helm_utils = None
    automation._setup_pipelines_setup_role_bindings = lambda *args: applied.append("roles")
    automation._setup_pipelines_show_results = lambda *args: applied.append("results")
    # The apply primitive is a module function now, so the recorder replaces it where it lives:
    # `_apply_manifest_layer` calls through `kubementat.infrastructure.kubernetes.apply` and not through this instance.
    monkeypatch.setattr(
        apply, "apply_resources",
        lambda kubernetes_utils, directory, namespace, kind, group, version: applied.append(
            (kind, str(directory))))
    return automation


def test_setup_pipelines_never_starts_applying_until_the_controller_answers_the_question(tmp_path,
                                                                                       monkeypatch):
    """The whole point of the preflight: the refusal comes before the first resource.

    A too-old controller is not forty failures here; it is one failure, once, at the front.
    If the ordering is wrong this says so by name, because the recorder still picked up the
    `applied:` entries.
    """
    cluster = RecordingCluster()
    applied = []

    with pytest.raises(TektonControllerWithoutConfiguredApiError):
        a_setup_pipelines_call_on(tmp_path, cluster, applied, monkeypatch).setup_pipelines()

    assert applied == [], f"resources were applied before the cluster was asked: {applied}"
    assert cluster.calls == [], f"the cluster was touched before the answer came back: {cluster.calls}"


def test_the_products_tasks_and_pipelines_are_applied_from_the_package_and_not_a_root(tmp_path,
                                                                                    monkeypatch):
    """The sentence this ticket exists for, read off the calls rather than off a comment.

    `setup_pipelines` used to build `{kubementat_main_dir}/tekton_ci/{tasks,pipelines}` inline, so a
    `kmt` installed from a wheel aimed `apply_resources` at a directory that does not exist there.
    Measured, not guessed: the old path raised `FileNotFoundError: Resource directory does not
    exist: <venv>/tekton_ci/tasks` (a missing directory is not the same as a team with no resources,
    which `apply_resources`' own docstring says), and with `KUBEMENTAT_HOME` pointed at a checkout it
    applied *that checkout's* manifests — so one install answered one question two ways depending on
    a variable unrelated to the cluster. Either way the claim here is not "the path is right" but
    "the accessor is asked", which is what this recorder can see — the two global directories handed
    to `apply_resources` are the asset accessors' values, and the checkout-relative root that used to
    be interpolated into them is not in what they came from.
    """
    cluster = RecordingCluster(served_versions=["v1"])
    applied = []
    automation = a_setup_pipelines_call_on(tmp_path, cluster, applied, monkeypatch)

    automation.setup_pipelines()

    # Only the applies: the recorder also marks the role bindings and the result dump with bare
    # strings, and they are not part of this claim.
    calls = [entry for entry in applied if isinstance(entry, tuple)]
    assert calls == [
        ("Task", automation.config.tekton_task_asset_dir),
        ("Pipeline", automation.config.tekton_pipeline_asset_dir),
        # The environment layer, applied between the two: it tunes what the product ships and is
        # tuned by every team under it.
        ("Task", automation.config.tekton_env_task_dir),
        ("Pipeline", automation.config.tekton_env_pipeline_dir),
        # The team's own two directories are checkout-relative output, and they are still applied,
        # last: this ticket moved which layer sits in the middle, not the team's place at the end.
        ("Task", automation.config.tekton_team_task_dir),
        ("Pipeline", automation.config.tekton_team_pipeline_dir),
    ], f"what `setup_pipelines` applied, in order: {calls}"


def test_a_cluster_that_does_not_serve_the_written_version_names_the_knob_that_changes_it(tmp_path):
    """The message has to be enough to act on, because nothing else will ever fire again."""
    cluster = RecordingCluster(served_versions=["v1beta1"])

    with pytest.raises(TektonControllerWithoutConfiguredApiError) as error:
        preflight_tekton_api_version(cluster, FakeConfig())

    message = str(error.value)
    assert "tekton.dev/v1" in message
    assert "kmt install" in message, "the upgrade path is what a reader needs next"
    assert "TEKTON_VERSION_PIPELINE" in message, "the platform_config key is what a reader changes"


def test_a_cluster_that_serves_the_written_version_is_asked_once_for_a_whole_command():
    """Listing four kinds of resource is one command, so it is one discovery call."""
    cluster = RecordingCluster(served_versions=["v1"])

    utils = TektonUtils.__new__(TektonUtils)
    utils.kubernetes_utils = cluster
    utils.config = FakeConfig()
    utils.pipeline_namespace = "tekton-pipelines-team"
    utils.list_pipelines()
    utils.list_tasks()
    utils.list_pipeline_runs()
    utils.list_taskruns()

    assert cluster.asked == [("tekton.dev", "v1")], (
        f"the question was asked {len(cluster.asked)} times; the check belongs once per command"
    )


def test_a_discovery_failure_that_is_not_a_missing_version_is_still_its_own_error(tmp_path):
    """A dead token or an unreachable cluster is not an old controller.

    Both arrive as an `ApiException`, and reading the second one out of the first would send
    the reader to upgrade a controller that is already fine. 404 is the only answer that
    means "not here".
    """
    from kubernetes.client.exceptions import ApiException

    cluster = RecordingCluster(discovery_error=ApiException(status=401, reason="Unauthorized"))

    with pytest.raises(ApiException) as error:
        preflight_tekton_api_version(cluster, FakeConfig())

    assert not isinstance(error.value, TektonControllerWithoutConfiguredApiError)
    assert error.value.status == 401


def test_kubementat_speaks_exactly_one_tekton_api_version_and_it_is_v1(tmp_path, monkeypatch):
    """The version is a constant, so nothing can set it back to what the tree no longer is.

    `tmp_path` and `preload_configs=False` are load-bearing and not tidying. `TEKTON_API_VERSION` is
    assigned from the module constant (`config.py:153`) three lines before `Config.__init__` opens any
    file, so this assertion never needed the checkout's config — but a bare `Config("dev", "dev1")`
    resolves to this checkout and opens `platform_config/dev/static.json`, which is `initialize` output
    and is not tracked in git. That made this test green on a machine that had once run `initialize`
    and red on a clean checkout (plan row 14). The guard that keeps it that way is
    `tests/test_no_test_loads_config_from_the_checkout_root.py`; do not shorten this call back.
    """
    assert config_module.TEKTON_API_VERSION == "v1"
    assert config_module.TEKTON_API_GROUP == "tekton.dev"

    monkeypatch.setenv("TEKTON_API_VERSION", "v1beta1")
    from kubementat.core.config.config import Config

    assert Config("dev", "dev1", kubementat_main_dir=str(tmp_path),
                  platform_config_dir=str(tmp_path / "platform_config"),
                  preload_configs=False).TEKTON_API_VERSION == "v1", (
        "an environment override crept back in")
