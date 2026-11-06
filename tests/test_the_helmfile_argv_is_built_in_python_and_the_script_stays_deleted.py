"""helmfile apply is one argv assembled in Python, and the script that assembled it stays gone.

`scripts/automation/components/helmfile_apply.sh` is deleted. It is not a migration, because it
was never called: it `pushd`ed into `platform_config/{environment}/kubementat_components`, read
the Grafana pair out of `static.encrypted.json` with `jq`, and ran

    helmfile apply --color -f helmfile.yaml.gotmpl -l "$HELMFILE_LABEL_FILTER"

all of which `HelmUtils.helmfile_apply` (`src/kubementat/helm_utils.py:31`) already does for
every path that could have reached the script:

* `kmt install` -- `Automation.install_kubementat` (`src/kubementat/automation.py:543`)
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

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from kubementat import config_reads_index, helm_utils
from kubementat.config import Config
from cluster_fixture import configuration_only_context
from kubementat.helm_utils import HelmUtils

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


def without_cluster_address(argv):
    """*argv* with the cluster's flags and their values left out.

    The address sits between the verb and the rest of the line, so a positional read of the
    arguments that follow is now a read of `--kube-context`. This file is about *which program runs
    and how its arguments are spelled*, and the address is a different file's claim
    (`test_no_helm_call_is_addressed_by_the_process_any_more.py`); dropping it here keeps the
    assertions about quoting, `cwd` and `-f` answering the question they were written to answer.
    """
    kept, skipping = [], False
    for token in argv:
        if skipping:
            skipping = False
            continue
        if token in ("--kubeconfig", "--kube-context", "--context"):
            skipping = True
            continue
        kept.append(token)
    return kept


def _is_packaging_leftover(path):
    """Build droppings only. `__pycache__` and `*.egg-info` are copies of this checkout, not callers."""
    parts = [part.lower() for part in path.parts]
    return any(part == "__pycache__" or part.endswith(".egg-info") for part in parts)


def test_the_script_is_not_in_the_tree():
    """The deletion itself, asserted where a re-added file would land."""
    assert not (REPO_ROOT / SCRIPT).exists(), \
        f"{SCRIPT} is back; the library call named in this file's docstring is its replacement"


def test_no_code_root_names_a_script_that_is_not_there():
    """No caller, in the only directories a caller could live in.

    `src/` matters most, because `tests/test_command_execution_policy.py` fails when `src/` names
    a `.sh` the ledger has deleted -- but a `tekton_ci/` task, a template, an example, or a
    sibling script that `source`s the file would be a live caller that file never reaches.
    """
    offenders = []
    for root in CALLER_ROOTS:
        for path in (REPO_ROOT / root).rglob("*"):
            if not path.is_file() or _is_packaging_leftover(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if SCRIPT in line or "helmfile_apply.sh" in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    for path in (REPO_ROOT / "scripts").rglob("*.sh"):
        if SCRIPT in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == [], f"a caller is still here: {offenders}"


def test_the_sentence_about_where_the_manifest_comes_from_names_the_package():
    """The docstring is the sentence a reader trusts, and the packaged manifest is what it says.

    The sentence moved to `config_reads_index.helmfile_paths_for` when the read lost its `Context`:
    which manifest the product ships is a fact about a configuration and a filesystem, not about a
    cluster, so that is where the words have to live. `HelmUtils.helmfile_paths` is the call that
    reads them, and a guard pointed at the caller would still pass with nothing behind it. So the
    guard is pointed at the function that owns the answer.

    It used to say that `kmt initialize` copies
    `templates/environment/kubementat_components/helmfile.yaml.gotmpl` into the environment's
    directory, and that the copy is what gets applied. Both halves of that sentence were false the
    moment the entry point became product-owned, and the command that carried the copy is deleted
    now: nothing is copied any more, and the packaged file is what is applied. So the guard now
    checks the new sentence — that the answer is the packaged root named by one accessor, that
    nothing writes a manifest into a configuration tree, and that a copy left there is not part
    of it.
    """
    doc = inspect.getdoc(config_reads_index.helmfile_paths_for)

    assert "helmfile_asset_dir" in doc, \
        "it has to name the one accessor that resolves the manifest, or the next reader re-derives " \
        "it off a root that is not there: " + doc
    assert "Nothing writes the manifest" in doc, \
        "the reader has to be told no command writes a manifest into the configuration: " + doc
    assert "platform_config/{environment}/kubementat_components" in doc, \
        "the stale copy is the reason this function is the only answer; naming it is what keeps a " \
        "later reader from re-adding the fallback: " + doc
    assert "templates/environment/" not in doc, \
        "the docstring spells a shipped template as a path under a checkout: " + doc


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


def test_the_command_reaches_helmfile_and_no_bash(tmp_path, monkeypatch):
    """One program on the way to the cluster, and it is helmfile.

    Nothing in the path shells out, so nothing can shell out *instead*: the argv is made of
    strings, `cwd` arrives as a keyword argument, no `shell=True` is anywhere near it, and no `.sh`
    token is in it. That is the whole difference between "a Python command runs helmfile" and
    "a Python command runs a shell that runs helmfile".
    """
    (argv, options), components = apply_helmfile(tmp_path, monkeypatch)

    # The cluster sits between the verb and everything else, so the shape this file is about is
    # read with the address flags left out. Nothing here is checking which cluster was named — that
    # is `test_no_helm_call_is_addressed_by_the_process_any_more.py`'s claim — and a guard that made
    # this file's assertions depend on the address would be two tests failing for one change.
    assert argv[0] == "helmfile" and argv[1] == "apply", argv
    asked = without_cluster_address(argv)
    assert asked[2] == "--color", argv
    assert asked[asked.index("-f") + 1] == str(components / "helmfile.yaml.gotmpl"), argv
    assert asked[asked.index("-l") + 1] == "group=standard", argv
    assert all(isinstance(token, str) for token in argv), "argv entries are strings, not paths"

    assert options.get("shell") is not True, "shell=True is not how kubementat runs a command"
    assert options.get("cwd") == str(components), \
        "the working directory is an argument, because os.chdir is not a thing this does"
    assert not any(token.endswith(".sh") for token in argv), \
        "a script is on the command line; that is the second implementation this file deletes"


def test_a_label_filter_with_a_comma_survives_as_one_argument(tmp_path, monkeypatch):
    """The case a string command gets wrong, and an argv list cannot.

    The old script took the filter as `$2` and quoted it once; get the quoting wrong and
    `group=standard,component_name=openbao` becomes two filters, which helmfile reads as "nothing
    matches" and applies. Here it is one entry, by construction.
    """
    (argv, _), _ = apply_helmfile(tmp_path, monkeypatch,
                                  label_filter="group=standard,component_name=openbao")

    assert argv[argv.index("-l") + 1] == "group=standard,component_name=openbao"
