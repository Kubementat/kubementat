"""A locked operator values layer is refused before helmfile starts, by name.

The component layer an operator writes is named `<component>.encrypted.yaml` because that is what
puts it under git-crypt's `*.encrypted.*` declaration — an unlocked checkout therefore leaves it
ciphertext exactly where helmfile's `isFile` guard asks for text. helmfile cannot tell the two
apart (ciphertext is bytes either way) and helm opens the document, so the failure would arrive as
apply output that never says the word "unlock" — the same class of false sentence the read seam
exists to end, wearing a different file.

So `helmfile_apply` reads each layer that exists through `read_decrypted` before the child starts.
These three tests state the rule from its three sides:

1. a locked layer raises `LockedConfigFileError`, naming the file, and helmfile was never asked;
2. a plain-text layer is only walked past, and the apply runs;
3. no layer is read when the directory is not there, because absence is what most trees look like
   and a read of nothing must not become a new failure mode.
"""

import json
from pathlib import Path

import pytest

from kubementat.infrastructure import helm as helm_utils
from kubementat.core.config.config import Config
from kubementat.infrastructure.helm import HelmUtils
from kubementat.errors import LockedConfigFileError
from cluster_fixture import configuration_only_context

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
    """The refusal is `LockedConfigFileError` and it names the file; no helmfile line was run.

    The order is the assertion: a refusal that arrives after the spawn is helm's output wearing
    our sentence, which is exactly the false shape this guard replaces.
    """
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


def test_a_readable_layer_is_walked_past_and_the_apply_runs(tmp_path, monkeypatch):
    """The guard reads, and only says `locked` about ciphertext: a readable layer changes nothing."""
    config, root = seeded_tree(tmp_path, monkeypatch)
    layer(root, "grafana", b"adminUser: grafadmin\n")
    recorder = RecordingShell()
    utils = HelmUtils(configuration_only_context(ENVIRONMENT, None, config))

    apply(utils, monkeypatch, recorder)

    assert any(call[:1] == ["helmfile"] for call in recorder.calls), recorder.calls


def test_an_absent_layer_directory_costs_the_apply_nothing(tmp_path, monkeypatch):
    """The tree that declares no values has no directory at all, and the guard must not notice.

    A guard that globbed a directory that is not there and raised its own `FileNotFoundError`
    would turn the most common tree in the product into a failure — presence is the guard's whole
    decision, stated first.
    """
    config, root = seeded_tree(tmp_path, monkeypatch)
    assert not (root / ENVIRONMENT / "values").exists()
    recorder = RecordingShell()
    utils = HelmUtils(configuration_only_context(ENVIRONMENT, None, config))

    apply(utils, monkeypatch, recorder)

    assert any(call[:1] == ["helmfile"] for call in recorder.calls), recorder.calls
