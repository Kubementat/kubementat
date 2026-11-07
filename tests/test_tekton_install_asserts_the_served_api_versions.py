"""An install has to say what it actually left running.

The install ends with four `kubectl apply` commands having exited 0, and that is a claim about the
manifests they sent and not about the controller that is now running. The failure this closes is the
one the pins make easy: upstream moved where releases are served, so a version that is not in the
old bucket can 404 its way through an install, or land a controller that answers a different API
version than the tree writes. Either way the install prints success and the first pipeline run
prints forty 404s.

So the install asks the API server the same question the preflight asks before applying
(`KubernetesUtils.api_version_is_served`) for both groups it brought up, and refuses to go on
when the answer is no — naming the version the install tried to apply, because that is the half
the operator cannot read off a cluster.

Half of that is the other direction: what the install sends. The pins live in one JSON file and the
release addresses are built from them, so the install is run for real against the recording fakes
and the four URLs it asks for are asserted against the pins. That is the check that says the two
halves mean the same release.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kubementat.core.config import config as config_module
from kubementat.automations.install import kubementat as install_slice
from kubementat.errors import TektonControllerWithoutConfiguredApiError
from kubementat.tekton_utils import TektonUtils
# The post-install assertion moved to `preflight.py` (P4-2) and the release URLs, their base and the
# ClusterRole they are authorised through moved to `install.py` (P4-4), both read module-qualified:
# `tekton_utils.py` holds no copy of either name to import.
from kubementat.infrastructure.tekton import install as tekton_install
from kubementat.infrastructure.tekton import preflight
from kubementat.infrastructure.tekton.install import TEKTON_RELEASE_BASE_URL
from cluster_fixture import configuration_only_context
from asset_paths import template_path

REPO_ROOT = Path(__file__).resolve().parents[1]
# What the installer is made of, read as a package and not as a file: the base URL, the manifest
# table and the builder that turns the pins into addresses all moved into
# `infrastructure/tekton/install.py` (P4-4), and "one base URL" is a rule about the split, not about
# whichever file held it when the rule was written. `tekton_utils.py` is deliberately not in the set:
# after this move it holds none of the install names, and P4-6 deletes it.
TEKTON_PACKAGE = REPO_ROOT / "src/kubementat/infrastructure/tekton"
TEKTON_SOURCES = sorted(TEKTON_PACKAGE.glob("*.py"))
STATIC_TEMPLATE = template_path("environment", "static.json.template")
TEKTON_DOC = REPO_ROOT / "docs/README_TEKTON.md"

# Where upstream serves releases, spelled once because the whole point of the ticket is that
# the pins and this string move together. Imported from the source rather than repeated here so
# the assertions below cannot drift away from the code they check.
RELEASE_BASE_URL = TEKTON_RELEASE_BASE_URL

# The pinned triplet, read from the template so every assertion below is about the product and not
# about a version number repeated in a test file.
PINNED = json.loads(STATIC_TEMPLATE.read_text())
PIPELINES = PINNED["TEKTON_VERSION_PIPELINE"]
TRIGGERS = PINNED["TEKTON_VERSION_TRIGGERS"]
DASHBOARD = PINNED["TEKTON_VERSION_DASHBOARD"]

SERVES_BOTH_GROUPS = {"tekton.dev/v1", "triggers.tekton.dev/v1beta1"}


class ClusterAnsweringDiscovery:
    """The two discovery questions an install is supposed to have answered.

    `appears_after` maps a `group/version` to the number of times it says no before saying yes,
    which is what a CRD that `kubectl apply` accepted but has not yet registered looks like.
    """

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
    """Ask the question with no waiting, because a test should not sleep for a controller that exists."""
    return preflight.preflight_installed_tekton_api_versions(cluster, the_pinned_config(),
                                                             attempts=attempts, delay_seconds=0)


def test_a_cluster_that_serves_both_installed_groups_passes_without_a_second_look():
    """One question per group is the whole check; a healthy cluster is not poked twice."""
    cluster = ClusterAnsweringDiscovery(served=SERVES_BOTH_GROUPS)

    an_install_assertion_on(cluster)

    assert cluster.asked == ["tekton.dev/v1", "triggers.tekton.dev/v1beta1"]


def test_a_controller_that_never_serves_the_written_pipelines_version_names_the_pin_that_missed():
    """The message has to name 1.17.1-equivalent, not just the group: that is the half the operator is blind to."""
    cluster = ClusterAnsweringDiscovery(served=["triggers.tekton.dev/v1beta1"])

    with pytest.raises(TektonControllerWithoutConfiguredApiError) as error:
        an_install_assertion_on(cluster)

    message = str(error.value)
    assert PIPELINES in message, "the version the install tried is what a reader has to go compare"
    assert "tekton.dev/v1" in message
    assert "TEKTON_VERSION_PIPELINE" in message
    assert "kmt install" in message, "the way to a supported cluster is what a reader needs next"


def test_a_cluster_left_without_the_triggers_api_fails_on_the_beta_api_it_did_not_get():
    """Triggers is the half that is easy to get silently wrong: it is the one beta API still spoken."""
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


def test_a_controller_that_needs_a_moment_after_the_apply_is_not_reported_as_missing():
    """`apply` returns before discovery opens the path. Believing the first 404 would fail a good install."""
    cluster = ClusterAnsweringDiscovery(
        served=["triggers.tekton.dev/v1beta1"],
        appears_after={"tekton.dev/v1": 2},
    )

    an_install_assertion_on(cluster, attempts=6)

    assert cluster.asked.count("tekton.dev/v1") == 3, "the wait should stop at the first yes"


def test_the_wait_is_bounded_so_a_control_plane_that_arrives_never_makes_an_install_hang():
    """Waiting is not the same as giving up: after the attempts run out the answer is still no, and that is an error."""
    cluster = ClusterAnsweringDiscovery(served=[])

    with pytest.raises(TektonControllerWithoutConfiguredApiError):
        preflight.preflight_installed_tekton_api_versions(cluster, the_pinned_config(),
                                                         attempts=3, delay_seconds=0)

    assert len(cluster.asked) == 3, "each group gets its bounded wait, not an unbounded one"


def test_there_is_no_second_api_version_for_the_triggers_group():
    """Measured against Triggers 0.37.2's release.yaml: v1beta1 served and stored, no v1 anywhere.

    The string is a constant with no override, so this is the guard against quietly "supporting"
    a v1 that this release cannot serve.
    """
    assert config_module.TRIGGERS_API_VERSION == "v1beta1"
    assert config_module.TRIGGERS_API_GROUP == "triggers.tekton.dev"
    assert config_module.TEKTON_API_VERSION == "v1"




def test_the_installer_builds_every_url_from_one_base_and_never_from_the_old_bucket():
    """The base URL and the pins move together, so one place must own the address.

    The bash applied four release manifests from four string literals; that is how a bump
    half-lands. Now the URLs are assembled from `RELEASE_MANIFESTS` plus one base-URL constant, so
    this asserts the shape of the code rather than the shape of a script: no old bucket, one base
    URL, and every component's address carrying its own pin.
    """
    source = "\n".join(path.read_text(encoding="utf-8") for path in TEKTON_SOURCES)

    assert "storage.googleapis.com/tekton-releases" not in source, (
        "that bucket serves only what it kept, and the pins have moved past it"
    )
    assert source.count(RELEASE_BASE_URL) == 1, (
        "one base URL assignment; four literals is how the pins used to half-land"
    )
    for component, version_key in (("pipeline", "PIPELINE"), ("triggers", "TRIGGERS"),
                                   ("dashboard", "DASHBOARD")):
        assert f"TEKTON_VERSION_{version_key}" in source, (
            f"nothing builds the {component} URL from its pin")
    assert "previous/v" in source, (
        "the URLs must be built, not written out per component")


def test_an_install_asks_the_cluster_what_it_left_running_after_the_install_and_before_it_applies_anything(
        tmp_path, monkeypatch):
    """The check's whole value is its position: after the install, before helmfile.

    Anything that ran before the install would answer about the cluster as it was found, and
    anything after helmfile would let a wrong pin already be applied. Both halves are what this
    asserts, so the ordering is written out rather than implied.
    """
    journal = []

    def note_discovery(key):
        journal.append(f"discovery:{key}")

    cluster = ClusterAnsweringDiscovery(served=SERVES_BOTH_GROUPS, on_answer=note_discovery)

    class RecordingTektonInstall:
        """Stands in for `tekton_install.install()` so the ordering is what is under test.

        Patched on the module the install slice calls into — the function moved out of the class in
        P4-4, so the class is no longer where this step can be intercepted, and patching it is also
        the assertion that the step is Python: a script call would not be reachable here at all.
        """

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
    monkeypatch.setattr(install_slice, "validate_cluster_prompt", lambda ctx, assume_yes=False: None)
    monkeypatch.setattr(install_slice, "check_cluster_permissions", lambda ctx: None)
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
    """Run the installer for real and read the addresses it asked `kubectl` to fetch.

    The pins live in a JSON file and the URLs are built from them in Python, so this is the check
    that says the two halves line up: a pin that resolves against a bucket upstream stopped serving
    is caught here, against the recording fakes, the way `kmt install` will run it.
    """
    fixture_repo.environment_static(**{
        "TEKTON_VERSION_PIPELINE": PIPELINES,
        "TEKTON_VERSION_TRIGGERS": TRIGGERS,
        "TEKTON_VERSION_DASHBOARD": DASHBOARD,
    })

    # The install itself, module-qualified now (P4-4); the class is still what resolves the `Context`
    # into the client and the configuration the function takes.
    utils = TektonUtils(configuration_only_context(fixture_repo.environment, fixture_repo.team))
    tekton_install.install(utils.kubernetes_utils, utils.config, utils.environment)

    applied = [call[-1] for call in fake_binaries.commands()
               if call[:1] == ["kubectl"] and "-f" in call
               and call[-1].startswith("https://")]
    assert applied == [
        f"{RELEASE_BASE_URL}/pipeline/previous/v{PIPELINES}/release.yaml",
        f"{RELEASE_BASE_URL}/dashboard/previous/v{DASHBOARD}/release-full.yaml",
        f"{RELEASE_BASE_URL}/triggers/previous/v{TRIGGERS}/release.yaml",
        f"{RELEASE_BASE_URL}/triggers/previous/v{TRIGGERS}/interceptors.yaml",
    ], "the URLs the install builds are not what the pins mean"


def test_the_pinned_versions_and_the_documented_version_table_agree():
    """The table is where a reader looks up what is supported, so drift there is a wrong instruction."""
    documented = TEKTON_DOC.read_text()

    for version in (PIPELINES, TRIGGERS, DASHBOARD):
        assert version in documented, f"{version} is pinned but the documentation does not list it"

    for retired in ("1.4.0", "0.33.0", "0.61.0"):
        assert retired not in json.dumps(PINNED), f"{retired} is a stale pin still shipping in the template"
