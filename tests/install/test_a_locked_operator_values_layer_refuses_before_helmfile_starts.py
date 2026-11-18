"""A locked operator values layer is refused before helmfile starts, by name."""

import json

import pytest
from cluster_fixture import configuration_only_context

from kubementat.core.config.config import Config
from kubementat.errors import LockedConfigFileError
from kubementat.infrastructure import helm as helm_utils
from kubementat.infrastructure.helm import HelmUtils

ENVIRONMENT = "lockedlayers"


class RecordingShell:
    """Records every child process and answers it as a cluster with no releases."""

    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        return type("Result", (), {"returncode": 0, "ok": True, "argv": list(argv),
                                   "stdout": "[]", "stderr": ""})()


def seeded_tree(tmp_path, monkeypatch):
    """A configuration tree with only what an apply must read to reach the spawn."""
    root = tmp_path / "platform_config"
    environment = root / ENVIRONMENT
    environment.mkdir(parents=True)
    (environment / "static.json").write_text(json.dumps({
        "ENVIRONMENT": ENVIRONMENT, "CLUSTER_KUBE_CONTEXT": None}), encoding="utf-8")
    (environment / "static.encrypted.json").write_text(json.dumps(
        {"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "not-a-password"}),
        encoding="utf-8")
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(root))
    return Config(ENVIRONMENT, None, platform_config_dir=str(root)), root


def layer(root, component, contents):
    """One operator layer file, written as the bytes given."""
    path = root / ENVIRONMENT / "values"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{component}.encrypted.yaml").write_bytes(contents)
    return path / f"{component}.encrypted.yaml"


def apply(utils, monkeypatch, recorder):
    monkeypatch.setattr(helm_utils, "shell", recorder)
    return utils.helmfile_apply(environment_name=ENVIRONMENT,
                                helmfile_label_filter="group=standard")


def test_a_locked_layer_refuses_the_apply_before_a_child_ever_starts(tmp_path, monkeypatch):
    """The refusal is `LockedConfigFileError` and it names the file; no helmfile line was run."""
    config, root = seeded_tree(tmp_path, monkeypatch)
    written = layer(root, "grafana", b"\xc3\x28not-utf8\x00-ciphertext")
    recorder = RecordingShell()
    utils = HelmUtils(configuration_only_context(ENVIRONMENT, None, config))

    with pytest.raises(LockedConfigFileError) as refusal:
        apply(utils, monkeypatch, recorder)

    assert str(written) in str(refusal.value), \
        f"the refusal did not name the file: {refusal.value}"
    assert not [call for call in recorder.calls if call[:1] == ["helmfile"]], \
        f"helmfile was started even though the layer was refused: {recorder.calls}"


def test_an_absent_layer_directory_costs_the_apply_nothing(tmp_path, monkeypatch):
    """The tree that declares no values has no directory at all, and the guard must not notice."""
    config, root = seeded_tree(tmp_path, monkeypatch)
    assert not (root / ENVIRONMENT / "values").exists()
    recorder = RecordingShell()
    utils = HelmUtils(configuration_only_context(ENVIRONMENT, None, config))

    apply(utils, monkeypatch, recorder)

    assert any(call[:1] == ["helmfile"] for call in recorder.calls), recorder.calls
