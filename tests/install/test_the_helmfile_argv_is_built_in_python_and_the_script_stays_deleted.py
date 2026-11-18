"""helmfile apply is one argv assembled in Python, and the script that assembled it stays gone."""

import json
from pathlib import Path
from types import SimpleNamespace

from cluster_fixture import configuration_only_context

from kubementat.core.config.config import Config
from kubementat.infrastructure import helm as helm_utils
from kubementat.infrastructure.helm import HelmUtils

SCRIPT = "scripts/automation/components/helmfile_apply.sh"

CALLER_ROOTS = ("src", "tekton_ci", "examples")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.

ENVIRONMENT = "dev"
TEAM = "dev1"


class RecordingShell:
    """`shell.run` replaced by what it records: the argv, and nothing else."""

    def __init__(self):
        self.calls = []

    def run(self, argv, **options):
        self.calls.append((list(argv), dict(options)))
        return SimpleNamespace(returncode=0, stdout="", stderr="", ok=True)


def apply_helmfile(tmp_path, monkeypatch, label_filter="group=standard", **kwargs):
    """`HelmUtils` over a seeded `platform_config`, with `shell` reduced to a recorder."""
    config_root = tmp_path / "platform_config"
    config_root.mkdir(parents=True, exist_ok=True)
    # The root this seeds is the root it names. Clearing the variable used to be enough,
    # because `config_root` hung off `tmp_path` by rule; now a seed that is not named is a seed
    # nobody reads.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(config_root))
    # Seeded on purpose and then deliberately not used: the packaged manifest is the answer,
    # so this directory is here to prove a checkout tree that still has one cannot be mistaken for
    # the file helmfile is handed.
    (config_root / ENVIRONMENT / "kubementat_components").mkdir(parents=True, exist_ok=True)
    for path, body in (
        (config_root / ENVIRONMENT / "static.json", {"ENVIRONMENT": ENVIRONMENT}),
        (config_root / ENVIRONMENT / "static.encrypted.json",
         {"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "not-a-password"}),
        (config_root / ENVIRONMENT / TEAM / "static.json", {}),
        (config_root / ENVIRONMENT / TEAM / "static.encrypted.json", {}),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")

    config = Config(ENVIRONMENT, TEAM, kubementat_main_dir=str(tmp_path))
    # The seeded `Config` goes into the `Context` `HelmUtils` takes, instead of patching a name
    # the class used to reach for and does not any more. The same object, read the same way.
    recorder = RecordingShell()
    monkeypatch.setattr(helm_utils, "shell", recorder)
    components = Path(config.helmfile_asset_dir)

    utils = HelmUtils(configuration_only_context(ENVIRONMENT, TEAM, config))
    utils.helmfile_apply(environment_name=ENVIRONMENT,
                         helmfile_label_filter=label_filter, **kwargs)
    return recorder.calls[-1], components


def _is_packaging_leftover(path):
    """Build droppings only. `__pycache__` and `*.egg-info` are copies of this checkout, not callers.
    """
    parts = [part.lower() for part in path.parts]
    return any(part == "__pycache__" or part.endswith(".egg-info") for part in parts)


def test_the_helmfile_file_the_argv_carries_is_the_one_the_call_names(tmp_path, monkeypatch):
    """`-f` is a *path*, assembled by `helm_utils`, and a caller can hand it a different one."""
    (default_argv, _), components = apply_helmfile(tmp_path, monkeypatch)
    assert default_argv[default_argv.index("-f") + 1] == str(components / "helmfile.yaml.gotmpl")

    elsewhere = tmp_path / "a-different-tree"
    elsewhere.mkdir()
    (elsewhere / "helmfile.yaml").write_text("releases: []\n", encoding="utf-8")
    (chosen, _), _ = apply_helmfile(tmp_path, monkeypatch, helmfile_path=str(elsewhere / "helmfile.yaml"))

    assert chosen[chosen.index("-f") + 1] == str((elsewhere / "helmfile.yaml").absolute())
    assert Path(chosen[chosen.index("-f") + 1]).is_absolute(), \
        "a relative -f resolves against the child's cwd, not the caller's"
