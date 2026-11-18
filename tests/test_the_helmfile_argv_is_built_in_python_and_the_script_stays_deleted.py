"""helmfile apply is one argv assembled in Python, and the script that assembled it stays gone.

`scripts/automation/components/helmfile_apply.sh` is deleted. It is not a migration, because it
was never called: it `pushd`ed into `platform_config/{environment}/kubementat_components`, read
the Grafana pair out of `static.encrypted.json` with `jq`, and ran

    helmfile apply --color -f helmfile.yaml.gotmpl -l "$HELMFILE_LABEL_FILTER"

all of which `HelmUtils.helmfile_apply` (`src/kubementat/infrastructure/helm.py:31`) already does for
every path that could have reached the script:

* `kmt install` -- `install_kubementat` (`src/kubementat/automations/install/kubementat.py:115`)
* `kmt routing install` -- `install_routing` (`src/kubementat/automations/routing/routing.py:246`)
* `kmt helm apply` -- the CLI command (`helmfile_apply` in `src/cli/commands/helm.py`)

So the script's only remaining role was documentation of an argv, and a script that documents an
argv while a library assembles it is a second way to be wrong about the same `helmfile`. Two of
the script's own habits were already stale: it exported `GRAFANA_ADMIN_*` from the config with
`jq` while the library had moved to `Config`'s own accessors, and its `--interactive` came from a
bare `true`/`false` third argument while the CLI answers that question from `sys.stdin.isatty()`.

The old ledger row for this file said "still sourced by `install_routing.sh:32`,
`install_kubernetes_dashboard.sh:36` -> delete once those two are gone". Both of those scripts are gone:
`install_routing.sh` with the routing work that landed `kmt routing install`, and the dashboard
installer with the chart the upstream retired. That is the condition that row was waiting for.

The guards below are the two halves of that claim: the file is not in the tree and nothing that
could run it names it, and the argv the script was a record of is what `HelmUtils` hands
`shell.run` -- one entry per token, no shell, no `.sh` anywhere on the way to helmfile.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from kubementat.infrastructure import helm as helm_utils
from kubementat.core.config.config import Config
from cluster_fixture import configuration_only_context
from kubementat.infrastructure.helm import HelmUtils

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/automation/components/helmfile_apply.sh"

# The code roots a caller would have to live in. `docs/plans/**` and the READMEs are excluded on
# purpose: a sentence naming a deleted script is history, and this file is one of them.
CALLER_ROOTS = ("src", "tekton_ci", "examples")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.

ENVIRONMENT = "dev"
TEAM = "dev1"


class RecordingShell:
    """`shell.run` replaced by what it records: the argv, and nothing else.

    Recording the argv *and* the keyword arguments is the point. A call that passed a string, or
    `shell=True`, or `os.chdir` in front of it, would show up here as a shape this file does not
    recognise.
    """

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
    """Build droppings only. `__pycache__` and `*.egg-info` are copies of this checkout, not callers."""
    parts = [part.lower() for part in path.parts]
    return any(part == "__pycache__" or part.endswith(".egg-info") for part in parts)


def test_the_helmfile_file_the_argv_carries_is_the_one_the_call_names(tmp_path, monkeypatch):
    """`-f` is a *path*, assembled by `helm_utils`, and a caller can hand it a different one.

    The script had `HELMFILE_FILENAME="helmfile.yaml.gotmpl"` hardcoded and applied it from inside
    the components directory. `helmfile_apply` takes `helmfile_path` and resolves both it and the
    working directory to absolute paths first, because it is about to hand them to a child process
    and set `cwd` in the same call. That is the part of the argv the script never had: a caller
    that points helmfile somewhere else is not editing a constant.
    """
    (default_argv, _), components = apply_helmfile(tmp_path, monkeypatch)
    assert default_argv[default_argv.index("-f") + 1] == str(components / "helmfile.yaml.gotmpl")

    elsewhere = tmp_path / "a-different-tree"
    elsewhere.mkdir()
    (elsewhere / "helmfile.yaml").write_text("releases: []\n", encoding="utf-8")
    (chosen, _), _ = apply_helmfile(tmp_path, monkeypatch, helmfile_path=str(elsewhere / "helmfile.yaml"))

    assert chosen[chosen.index("-f") + 1] == str((elsewhere / "helmfile.yaml").absolute())
    assert Path(chosen[chosen.index("-f") + 1]).is_absolute(), \
        "a relative -f resolves against the child's cwd, not the caller's"
