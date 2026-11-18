"""An install has to say what it actually left running."""

import json
from types import SimpleNamespace

import pytest
from asset_paths import REPO_ROOT, template_path
from cluster_fixture import configuration_only_context

from kubementat.automations.install import kubementat as install_slice
from kubementat.core.config import config as config_module
from kubementat.errors import TektonControllerWithoutConfiguredApiError
from kubementat.infrastructure.kubernetes import KubernetesUtils

from kubementat.infrastructure.tekton import install as tekton_install
from kubementat.infrastructure.tekton import preflight
from kubementat.infrastructure.tekton.install import TEKTON_RELEASE_BASE_URL

TEKTON_PACKAGE = REPO_ROOT / "src/kubementat/infrastructure/tekton"
STATIC_TEMPLATE = template_path("environment", "static.json.template")

# Where upstream serves releases, imported from the installer so the assertions move with the pins.
RELEASE_BASE_URL = TEKTON_RELEASE_BASE_URL

# The pinned triplet, read from the template so every assertion below is about the product and not
# about a version number repeated in a test file.
PINNED = json.loads(STATIC_TEMPLATE.read_text())
PIPELINES = PINNED["TEKTON_VERSION_PIPELINE"]
TRIGGERS = PINNED["TEKTON_VERSION_TRIGGERS"]
DASHBOARD = PINNED["TEKTON_VERSION_DASHBOARD"]

SERVES_BOTH_GROUPS = {"tekton.dev/v1", "triggers.tekton.dev/v1beta1"}


class ClusterAnsweringDiscovery:
    """The two discovery questions an install is supposed to have answered."""

    def __init__(self, served=(), appears_after=None, on_answer=None):
        self.served = set(served)
        self.appears_after = dict(appears_after or {})
        self.asked = []
        self.on_answer = on_answer
        self._answers = {}

    def api_version_is_served(self, group, version):
        key = f"{group}/{version}"
        self.asked.append(key)
        if self.on_answer is not None:
            self.on_answer(key)
        if key in self.served:
            return True
        says_no_before_it_says_yes = self.appears_after.get(key)
        if says_no_before_it_says_yes is None:
            return False
        self._answers[key] = self._answers.get(key, 0) + 1
        return self._answers[key] > says_no_before_it_says_yes


class FakeConfig:
    """The five constants and the two pinned versions the assertion reads."""

    def __init__(self, pinned_versions):
        self.TEKTON_API_GROUP = config_module.TEKTON_API_GROUP
        self.TEKTON_API_VERSION = config_module.TEKTON_API_VERSION
        self.TRIGGERS_API_GROUP = config_module.TRIGGERS_API_GROUP
        self.TRIGGERS_API_VERSION = config_module.TRIGGERS_API_VERSION
        self._pinned_versions = pinned_versions

    def get(self, category):
        assert category == "env_static", "the pins are environment configuration, not team config"
        return self._pinned_versions


def the_pinned_config():
    return FakeConfig({
        "TEKTON_VERSION_PIPELINE": PIPELINES,
        "TEKTON_VERSION_TRIGGERS": TRIGGERS,
        "TEKTON_VERSION_DASHBOARD": DASHBOARD,
    })


def an_install_assertion_on(cluster, attempts=6):
    """Ask the question with no waiting, because a test should not sleep for a controller that exists.
    """
    return preflight.preflight_installed_tekton_api_versions(cluster, the_pinned_config(),
                                                             attempts=attempts, delay_seconds=0)


def test_a_controller_that_never_serves_the_written_pipelines_version_names_the_pin_that_missed():
    """The message has to name 1.17.1-equivalent, not just the group: that is the half the operator is
    blind to.
    """
    cluster = ClusterAnsweringDiscovery(served=["triggers.tekton.dev/v1beta1"])

    with pytest.raises(TektonControllerWithoutConfiguredApiError) as error:
        an_install_assertion_on(cluster)

    message = str(error.value)
    assert PIPELINES in message, "the version the install tried is what a reader has to go compare"
    assert "tekton.dev/v1" in message
    assert "TEKTON_VERSION_PIPELINE" in message
    assert "kmt install" in message, "the way to a supported cluster is what a reader needs next"


def test_a_cluster_left_without_the_triggers_api_fails_on_the_beta_api_it_did_not_get():
    """Triggers is the half that is easy to get silently wrong: it is the one beta API still spoken.
    """
    cluster = ClusterAnsweringDiscovery(served=["tekton.dev/v1"])

    with pytest.raises(TektonControllerWithoutConfiguredApiError) as error:
        an_install_assertion_on(cluster)

    message = str(error.value)
    assert "triggers.tekton.dev/v1beta1" in message
    assert TRIGGERS in message
    assert "TEKTON_VERSION_TRIGGERS" in message
    # Triggers 0.37 serves no v1, so pointing a reader at v1 here would send them to a manifest
    # that no controller in this tree serves.
    assert "v1beta1" in message


def test_the_wait_is_bounded_so_a_control_plane_that_arrives_never_makes_an_install_hang():
    """Waiting is not the same as giving up: after the attempts run out the answer is still no, and
    that is an error.
    """
    cluster = ClusterAnsweringDiscovery(served=[])

    with pytest.raises(TektonControllerWithoutConfiguredApiError):
        preflight.preflight_installed_tekton_api_versions(cluster, the_pinned_config(),
                                                         attempts=3, delay_seconds=0)

    assert len(cluster.asked) == 3, "each group gets its bounded wait, not an unbounded one"


def test_an_install_asks_the_cluster_what_it_left_running_after_the_install_and_before_it_applies_anything(
        tmp_path, monkeypatch):
    """The check's whole value is its position: after the install, before helmfile."""
    journal = []

    def note_discovery(key):
        journal.append(f"discovery:{key}")

    cluster = ClusterAnsweringDiscovery(served=SERVES_BOTH_GROUPS, on_answer=note_discovery)

    class RecordingTektonInstall:
        """Stands in for `tekton_install.install()` so the ordering is what is under test."""

        def install(kubernetes_utils, config, environment):
            journal.append("tekton_install")

    monkeypatch.setattr(install_slice.tekton_install, "install", RecordingTektonInstall.install)

    # The `Context` the install path is handed, with the configuration this test pins. Every
    # collaborator the old `Automation` remembered on `self` is now built inside
    # `install_kubementat` from that `Context`, so each one is replaced on the module the
    # function reads it through — which is what makes the ordering below observable.
    context = configuration_only_context("dev", "dev1", the_pinned_config())
    context.config.kubementat_main_dir = str(tmp_path)
    monkeypatch.setattr(install_slice, "KubernetesUtils", lambda ctx: cluster)
    monkeypatch.setattr(install_slice, "HelmUtils",
                        lambda ctx: SimpleNamespace(
                            helmfile_apply=lambda **kwargs: journal.append("helmfile_apply")))
    monkeypatch.setattr(install_slice, "check_dependencies_are_installed", lambda dependencies: None)
    monkeypatch.setattr(install_slice, "validate_cluster", lambda ctx, consent=None: True)
    monkeypatch.setattr(install_slice, "check_cluster_permissions", lambda ctx: True)
    monkeypatch.setattr(install_slice, "_print_install_finish_message", lambda ctx: None)

    install_slice.install_kubementat(context, configure_tekton_pipelines=False)

    assert journal == [
        "tekton_install",
        "discovery:tekton.dev/v1",
        "discovery:triggers.tekton.dev/v1beta1",
        "helmfile_apply",
    ], f"the version question came at the wrong point in the install: {journal}"


def test_the_install_applies_the_urls_the_pinned_versions_actually_point_at(fixture_repo,
                                                                           fake_binaries,
                                                                           dry_run_env,
                                                                           recorded_api):
    """Run the installer for real and read the addresses it asked `kubectl` to fetch."""
    fixture_repo.environment_static(**{
        "TEKTON_VERSION_PIPELINE": PIPELINES,
        "TEKTON_VERSION_TRIGGERS": TRIGGERS,
        "TEKTON_VERSION_DASHBOARD": DASHBOARD,
    })

    # The client and configuration the install takes are read off the `Context` it was built from.
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    tekton_install.install(KubernetesUtils(context), context.config, context.environment)

    applied = [call[-1] for call in fake_binaries.commands()
               if call[:1] == ["kubectl"] and "-f" in call
               and call[-1].startswith("https://")]
    assert applied == [
        f"{RELEASE_BASE_URL}/pipeline/previous/v{PIPELINES}/release.yaml",
        f"{RELEASE_BASE_URL}/dashboard/previous/v{DASHBOARD}/release-full.yaml",
        f"{RELEASE_BASE_URL}/triggers/previous/v{TRIGGERS}/release.yaml",
        f"{RELEASE_BASE_URL}/triggers/previous/v{TRIGGERS}/interceptors.yaml",
    ], "the URLs the install builds are not what the pins mean"
