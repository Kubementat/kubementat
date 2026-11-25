"""The end-to-end harness resolves its version facts from `versions.yaml` and owns none of them.

These are the harness-side consumers of the file: the tables in `harness/config.py`, the probe
manifest in `harness/stages.py`, and the k3s drift check that used to grep the number out of the
rendered provisioning script. Equality against the file's own values is what distinguishes a
consumer that reads from one that copies while both happen to agree.
"""

import sys
from pathlib import Path

from kubementat.core import versions

# The harness imports as the `harness` package the same way suite.py puts its directory on the
# path; nothing under tests/e2e runs a VM or touches a host binary at import time.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

from harness import config, stages  # noqa: E402


def test_the_harness_resolves_its_container_images_from_the_versions_file():
    assert config.DOCKERBUILD_BUILDER_IMAGE == versions.image_ref("buildkit")
    assert config.DOCKERBUILD_REGISTRY_IMAGE == versions.image_ref("registry")
    assert config.ROUTING_LANE["image"] == versions.image_ref("nginx")


def test_the_storage_probe_pod_runs_the_alpine_reference_the_file_declares():
    assert "image: " + versions.image_ref("alpine") in stages._probe_manifest("standard")


def test_the_k3s_drift_check_answers_from_the_versions_file():
    pinned, problem = stages.LaneRunner._k3s_pinned_version()
    assert problem is None
    assert pinned == versions.e2e_version("k3s")


def test_a_versions_file_that_stops_answering_k3s_is_reported_and_not_defaulted(monkeypatch):
    """The check's half of "no fallbacks": a missing line surfaces as drift, not as a guessed pin."""
    monkeypatch.setattr(versions, "_DOCUMENT", {})
    pinned, problem = stages.LaneRunner._k3s_pinned_version()
    assert pinned is None
    assert "e2e.k3s" in problem, problem
