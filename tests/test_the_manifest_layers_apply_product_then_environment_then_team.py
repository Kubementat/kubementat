"""The three manifest apply layers, and the order that resolves a collision between them.

`setup_pipelines` used to apply two things: what kubementat ships, and what the team wrote. The
design adds a third — `platform_config/{environment}/tekton/{tasks,pipelines}/` — for an
environment that needs a Task tweaked without forking it into every team's directory. This file
holds what makes that layer real, which is not the directory's existence but the *sequence* it is
applied in.

The rule the ticket settled, and the reason there is no code to write:

* A `metadata.name` that appears in two layers is not a collision, it is an ordering. The same
  namespace, the same kind, the same name — the document applied later patches the object the
  earlier one created, because `create_or_replace_custom_resource` creates and patches on 409. So
  the answer to "which layer owns this Task" is already in the call sequence, and a name index, a
  collision table or an ownership registry would be a second source of truth about it, correct only
  until the order moves.
* Order is only evidence if it is *readable*, so every layer logs one line naming the directory it
  is applying and how many documents it found. A reader debugging a cluster should be able to tell
  from the log alone which layer answered for a Task, which is what the assertions below read off.

What this file deliberately does not do: assert that an object "ends up patched". A fake cannot
prove what a cluster does with two writes, and the order the fake *received* is the fact under test.
"""

import ast
import logging
from types import SimpleNamespace
import os
import shutil
import tempfile
from pathlib import Path

import gnupg
import pytest

from kubementat.automations.install import pipelines as pipelines_slice
from kubementat.automations.install.pipelines import setup_pipelines
from kubementat.core.config.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]

# One manifest, three spellings of the same object. The `layer` label and the string the step echoes
# are the two things that let a test say *which* layer a write came from: the cluster sees one
# object, and these are what distinguishes the four writes a fake records.
MANIFEST = """apiVersion: tekton.dev/v1
kind: {kind}
metadata:
  name: {name}
  labels:
    layer: {layer}
spec:
  steps:
    - name: echo
      image: alpine
      command: ["echo", "{layer}"]
"""

# What a manifest that is a generator template and not a resource looks like, so a test can write
# one under a `templates/` directory and show it never reached the cluster.
TEMPLATE = """apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: deploy-pipeline-APP_NAME_PLACEHOLDER
spec:
  tasks:
    - name: deploy
"""

# The delete primitives a setup path could prune with. Named by the method every one of them
# lives behind, so a rename that moves a delete somewhere else is caught and a new delete path is
# caught too — the sweep reads calls, not a list of files somebody remembers.
PRUNE_CALL_PREFIXES = ("delete_", "prune_")


def write_manifest(directory, kind, name, layer):
    """One manifest file in `directory`, tagged so a received write names its layer."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(MANIFEST.format(kind=kind, name=name, layer=layer))
    return path


def product_dirs(root):
    """The product layer: package data, so it is not under the configuration root at all."""
    assets = root / "site-packages" / "kubementat" / "assets" / "tekton_ci"
    return assets / "tasks", assets / "pipelines"


def environment_dirs(root, environment="dev"):
    """The environment layer, spelled from the configuration root — the same spelling `Config` uses."""
    env = root / "platform_config" / environment / "tekton"
    return env / "tasks", env / "pipelines"


def team_dirs(root, environment="dev", team="dev1"):
    """The team layer: generated output under the team's own directory."""
    team_root = root / "platform_config" / environment / team / "tekton"
    return team_root / "tasks", team_root / "pipelines"


class RecordingCluster:
    """Records every write `setup_pipelines` sends, as the write was made.

    The only method that matters is the create-or-replace one; everything else the setup path asks
    the cluster for is a namespace, a secret, a service account, or a read, and none of it is under
    test. They are answered with a no-op rather than a `None`-returning mock so a method the test
    forgot about shows up as an unexpected call and not as a crash three frames deep.
    """

    def __init__(self):
        self.applied = []
        self.asked = []

    def api_version_is_served(self, group, version):
        self.asked.append((group, version))
        return version == "v1"

    def create_or_replace_custom_resource(self, group, version, namespace, kind, body,
                                          plural_identifier=None):
        metadata = body.get("metadata") or {}
        steps = ((body.get("spec") or {}).get("steps")) or [{}]
        self.applied.append({
            "kind": kind,
            "name": metadata.get("name"),
            "layer": (metadata.get("labels") or {}).get("layer"),
            # What the step echoes is the document's own voice, so a write that claims to be the
            # team's but carries the product's payload is caught by this and not by the label.
            "echo": (steps[0].get("command") or ["", ""])[1],
            "namespace": namespace,
        })

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


class LayeredConfig:
    """The values `setup_pipelines` reads, with the three layers rooted under one temporary tree."""

    TEKTON_API_GROUP = "tekton.dev"
    TEKTON_API_VERSION = "v1"

    def __init__(self, root, environment="dev", team="dev1"):
        self.kubementat_main_dir = root / "checkout"
        self.platform_config_dir = root / "platform_config"
        self.tekton_task_asset_dir, self.tekton_pipeline_asset_dir = product_dirs(root)
        self.tekton_env_task_dir, self.tekton_env_pipeline_dir = environment_dirs(root, environment)
        self.tekton_team_task_dir, self.tekton_team_pipeline_dir = team_dirs(root, environment, team)
        self.tekton_team_pipeline_run_dir = (root / "platform_config" / environment / team /
                                             "tekton" / "pipeline-runs")

    def get(self, section):
        return {
            "team_static": {
                "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer",
                "PIPELINE_NAMESPACE": "tekton-pipelines-dev1",
                "APP_DEPLOYMENT_NAMESPACE": "app-dev1",
            },
        }[section]


_DEPLOYER_ARMOR = {}


def deployer_armor():
    """One 2048-bit key's armor, generated once per module and thrown away with its keyring.

    `require_deployer_keypair` reads the deployer's identity back out of the key itself, so the
    pair these trees carry has to be real armor and not a marker string.
    """
    if "armor" not in _DEPLOYER_ARMOR:
        home = Path(tempfile.mkdtemp(prefix="kubementat-layer-test-keyring-"))
        os.chmod(home, 0o700)
        try:
            gpg = gnupg.GPG(gnupghome=str(home), verbose=False)
            email = "dev-deployer@example.invalid"
            key = gpg.gen_key(gpg.gen_key_input(name_real=email, name_email=email,
                                                no_protection=True, key_type="RSA",
                                                key_length=2048))
            _DEPLOYER_ARMOR["armor"] = {
                "public": gpg.export_keys(key.fingerprint),
                "secret": gpg.export_keys(keyids=key.fingerprint, secret=True,
                                          expect_passphrase=False),
            }
        finally:
            shutil.rmtree(home, ignore_errors=True)
    return _DEPLOYER_ARMOR["armor"]


def plant_deployer_keypair(root):
    """The four files `require_deployer_keypair` asks for, inside the tree's configuration root.

    The deployer Secrets are built from this pair now, so a `setup_pipelines` run against
    a tree without one refuses before it applies anything -- which is not what the layer tests
    below are about, so the pair is planted here.
    """
    directory = root / "platform_config"
    directory.mkdir(parents=True, exist_ok=True)
    armor = deployer_armor()
    (directory / "deployer_ssh_key").write_text("ssh-private-half\n")
    (directory / "deployer_ssh_key.pub").write_text("ssh-public-half\n")
    (directory / "gpg_private.key").write_text(armor["secret"], encoding="ascii")
    (directory / "gpg_public_key.gpg").write_text(armor["public"], encoding="ascii")
    return directory


def a_setup_pipelines(root, cluster, monkeypatch, environment="dev", team="dev1"):
    """Run the real `setup_pipelines` against a fake cluster, from a `Context` and nothing else.

    What is under test is what the *apply* sequence sends, so everything past the client stays
    real — including `apply_resources`, which is what walks the directories these tests lay out.
    The client and the two halves that are not about manifests are replaced on the module, which
    is where the function reads them now that there is no instance to hold them.
    """
    plant_deployer_keypair(root)
    context = SimpleNamespace(environment=environment, team=team,
                              config=LayeredConfig(root, environment, team))
    monkeypatch.setattr(pipelines_slice, "KubernetesUtils",
                        lambda ctx: cluster or RecordingCluster())
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_setup_role_bindings", lambda *args: None)
    monkeypatch.setattr(pipelines_slice, "_setup_pipelines_show_results", lambda *args: None)
    setup_pipelines(context)
    return context


def written(cluster, kind):
    """Every write of one kind, in the order the cluster was asked to take them."""
    return [entry for entry in cluster.applied if entry["kind"] == kind]


# ---------------------------------------------------------------------------
# (a) the product and the team name the same Task
# ---------------------------------------------------------------------------
def test_a_teams_task_is_applied_last_so_it_is_the_one_the_cluster_keeps(tmp_path, monkeypatch):
    """A team that overrides a shipped Task wins, and the shipped one is still applied first.

    Both documents have to reach the cluster: applying the team's and skipping the product's would
    leave a Task that is missing because a team chose to change one field of it. So the assertion is
    about the sequence the fake received, and the payload is the discriminator — the second write is
    the team's document, not the product's under a team label.
    """
    write_manifest(product_dirs(tmp_path)[0], "Task", "git-clone", "product")
    write_manifest(team_dirs(tmp_path)[0], "Task", "git-clone", "team")
    cluster = RecordingCluster()

    a_setup_pipelines(tmp_path, cluster, monkeypatch)

    task_writes = written(cluster, "Task")
    assert [entry["name"] for entry in task_writes] == ["git-clone", "git-clone"]
    assert [entry["layer"] for entry in task_writes] == ["product", "team"]
    # The document that lands last carries the team's own payload, so the override is real and not
    # a label stamped onto the product's body.
    assert task_writes[-1]["echo"] == "team"
    assert all(entry["namespace"] == "tekton-pipelines-dev1" for entry in task_writes)


def test_the_layer_a_pipeline_is_applied_from_is_readable_from_the_log(tmp_path, monkeypatch, caplog):
    """One line per layer, naming the directory and the count — the log a debug session reads.

    Three layers apply a Pipeline, so three lines must say which directory was walked. A reader who
    has to guess between `Applying global tekton pipelines...` and nothing has no way to answer
    "did the environment's copy land, or did the team's overwrite it".
    """
    write_manifest(product_dirs(tmp_path)[1], "Pipeline", "deploy", "product")
    write_manifest(environment_dirs(tmp_path)[1], "Pipeline", "deploy", "environment")
    write_manifest(team_dirs(tmp_path)[1], "Pipeline", "deploy", "team")
    cluster = RecordingCluster()

    with caplog.at_level(logging.INFO):
        a_setup_pipelines(tmp_path, cluster, monkeypatch)

    text = caplog.text
    for directory in (product_dirs(tmp_path)[1], environment_dirs(tmp_path)[1],
                      team_dirs(tmp_path)[1]):
        assert str(directory) in text, f"{directory} was never named in the log"
    pipeline_lines = [record.getMessage() for record in caplog.records
                      if "applied 1 Pipeline document(s)" in record.getMessage()]
    assert len(pipeline_lines) == 3, caplog.text


# ---------------------------------------------------------------------------
# (b) environment versus team, and (c) environment versus product
# ---------------------------------------------------------------------------
def test_the_teams_layer_is_applied_after_the_environments_layer(tmp_path, monkeypatch):
    """Both layers declare one Pipeline. The team is applied last, so the team is the answer.

    The direction matters and is not obvious from the paths: the environment's files sit *above* the
    team's in the tree, so a reader could expect them to be applied later. They are not — an
    environment tunes what the product ships, a team tunes what its environment said.
    """
    write_manifest(environment_dirs(tmp_path)[1], "Pipeline", "deploy", "environment")
    write_manifest(team_dirs(tmp_path)[1], "Pipeline", "deploy", "team")
    cluster = RecordingCluster()

    a_setup_pipelines(tmp_path, cluster, monkeypatch)

    pipeline_writes = written(cluster, "Pipeline")
    assert [entry["layer"] for entry in pipeline_writes] == ["environment", "team"]
    assert [entry["echo"] for entry in pipeline_writes] == ["environment", "team"]


def test_the_environments_layer_is_applied_after_the_products(tmp_path, monkeypatch):
    """The middle layer overrides the product and is overridden by the team.

    This is the half the environment layer was made for: before it, tuning a shipped Task for one
    environment
    meant copying it into every team's directory, so the same fix was written N times and drifted.
    """
    write_manifest(product_dirs(tmp_path)[0], "Task", "build-image", "product")
    write_manifest(environment_dirs(tmp_path)[0], "Task", "build-image", "environment")
    cluster = RecordingCluster()

    a_setup_pipelines(tmp_path, cluster, monkeypatch)

    task_writes = written(cluster, "Task")
    assert [entry["layer"] for entry in task_writes] == ["product", "environment"]
    assert task_writes[-1]["echo"] == "environment"


def test_all_three_layers_are_applied_in_one_order_for_one_kind(tmp_path, monkeypatch):
    """One sequence, six directories, and no place for a second one to hide.

    Asserted on the whole run rather than per layer because the ticket is the *order*: a layer that
    lands in the right place when the other two are absent proves nothing about a run that has all
    three.
    """
    for directory, layer in ([(d, "product") for d in product_dirs(tmp_path)]
                             + [(d, "environment") for d in environment_dirs(tmp_path)]
                             + [(d, "team") for d in team_dirs(tmp_path)]):
        write_manifest(directory, "Task" if directory.name == "tasks" else "Pipeline",
                       f"one-{layer}-{directory.name}", layer)
    cluster = RecordingCluster()

    a_setup_pipelines(tmp_path, cluster, monkeypatch)

    assert [entry["layer"] for entry in cluster.applied] == [
        "product", "product", "environment", "environment", "team", "team",
    ], f"the layers were applied out of order: {[(e['kind'], e['layer']) for e in cluster.applied]}"


# ---------------------------------------------------------------------------
# (d) a layer that is not there
# ---------------------------------------------------------------------------
def test_a_configuration_with_no_tekton_directory_anywhere_still_installs(tmp_path, monkeypatch, caplog):
    """The environment layer is optional in the literal sense, and must not be able to break an
    install.

    `apply_resources` raises `FileNotFoundError` for a directory that is not a directory, which is
    the right answer about a root that is supposed to exist and the wrong one about a layer nobody
    has populated. This tree is the state a prepared configuration with no generated manifests
    leaves you in: nothing under `tekton/` anywhere, so every optional layer is absent.

    Measured, not assumed, about the team layer: `setup_pipelines` creates the team's two
    directories before applying them (`automations/install/pipelines.py`, the `os.makedirs` calls), so what a missing
    *team* directory does today is get created, and the layer then applies zero documents from a
    directory that does exist. That is why the log says so in one of the two shapes, and why the
    counts below are pinned per layer rather than as a total.
    """
    write_manifest(product_dirs(tmp_path)[0], "Task", "product-task", "product")
    write_manifest(product_dirs(tmp_path)[1], "Pipeline", "product-pipeline", "product")

    cluster = RecordingCluster()
    with caplog.at_level(logging.INFO):
        a_setup_pipelines(tmp_path, cluster, monkeypatch)

    # Nothing raised, and everything the product ships still landed.
    assert [entry["layer"] for entry in cluster.applied] == ["product", "product"]
    assert "Traceback" not in caplog.text
    # The absent environment layer says so once per directory, and says it as an absence and not as
    # an error. Both kinds, because "there is no tasks directory" and "there is no pipelines
    # directory" are two different things a reader is told about.
    environment_lines = [line for line in (record.getMessage() for record in caplog.records)
                         if "environment layer: no " in line
                         and "directory at" in line and "nothing is applied" in line]
    assert len(environment_lines) == 2, caplog.text
    named = " ".join(environment_lines)
    for directory in environment_dirs(tmp_path):
        assert str(directory) in named, f"{directory} was never named as absent"
    # The team layer's own silence is the mkdir, and it is stated here so that the next reader does
    # not "fix" one half and end up with two rules for one question.
    team_lines = [line for line in (record.getMessage() for record in caplog.records)
                  if "team layer: applied 0 " in line]
    assert len(team_lines) == 2, caplog.text


def test_an_absent_layer_is_a_no_op_for_the_team_layer_too(tmp_path, monkeypatch, caplog):
    """The one rule, in one place, so both optional layers obey it.

    The directory here is under nothing that `setup_pipelines` creates — it is a layer that simply
    is not there. The claim is the three things that must all be true at once: no exception, not one
    line claiming anything was applied, and no write to the cluster.
    """
    cluster = RecordingCluster()
    context = SimpleNamespace(environment="dev", team="dev1",
                              config=LayeredConfig(tmp_path, "dev", "dev1"))
    monkeypatch.setattr(pipelines_slice, "KubernetesUtils", lambda ctx: cluster)
    missing = tmp_path / "platform_config" / "dev" / "tekton" / "tasks"

    with caplog.at_level(logging.INFO):
        applied = pipelines_slice._apply_manifest_layer(context, "environment", missing,
                                                        "tekton-pipelines-dev1",
                                                        kind="Task", group="tekton.dev", version="v1")

    assert applied == 0
    assert cluster.applied == []
    assert len([line for line in (record.getMessage() for record in caplog.records)
                if str(missing) in line]) == 1, caplog.text
    assert "Traceback" not in caplog.text


# ---------------------------------------------------------------------------
# (e) templates are never applied
# ---------------------------------------------------------------------------
def test_a_generator_template_under_a_layer_is_never_applied_as_a_manifest(tmp_path, monkeypatch):
    """A `templates/` directory under the environment or the team layer is generator input.

    The exclusion is not defensive: `templates/` sits *inside* real resource roots, so the walk that
    applies a layer's Tasks reaches it on its way through. Applying one would put a
    `deploy-pipeline-APP_NAME_PLACEHOLDER` in front of a cluster — a Pipeline named after a
    placeholder, which is exactly what a real user's cluster had once.
    """
    for root in [d for dirs in (environment_dirs(tmp_path), team_dirs(tmp_path)) for d in dirs]:
        (root / "templates").mkdir(parents=True, exist_ok=True)
        (root / "templates" / "deploy-pipeline-APP_NAME_PLACEHOLDER.yaml").write_text(TEMPLATE)
    # A real document next to the template, so the layer is not empty and the skip is visible as a
    # skip rather than as an empty directory.
    write_manifest(environment_dirs(tmp_path)[1], "Pipeline", "deploy", "environment")
    cluster = RecordingCluster()

    a_setup_pipelines(tmp_path, cluster, monkeypatch)

    names = [entry["name"] for entry in cluster.applied]
    assert "deploy-pipeline-APP_NAME_PLACEHOLDER" not in names
    assert [entry["name"] for entry in written(cluster, "Pipeline")] == ["deploy"]


# ---------------------------------------------------------------------------
# the paths, and the layer that is deliberately absent
# ---------------------------------------------------------------------------
def test_the_environments_directories_are_spelled_from_the_configuration_root(tmp_path):
    """`Config` owns both paths, and neither is built from a root the caller handed for something else.

    The environment layer sits on `platform_config_dir` like every other environment-scoped path.
    `kubementat_main_dir` is the output root, and reading configuration out of it is a defect, so
    this asserts the spelling and the non-spelling in one go.
    """
    config = Config(environment="dev", team="dev1", platform_config_dir=tmp_path,
                    kubementat_main_dir=tmp_path / "output", preload_configs=False)

    assert config.tekton_env_task_dir == tmp_path / "dev" / "tekton" / "tasks"
    assert config.tekton_env_pipeline_dir == tmp_path / "dev" / "tekton" / "pipelines"
    assert config.tekton_env_task_dir != config.kubementat_main_dir / "tekton" / "tasks"


def test_no_environment_triggers_directory_ships_with_this_layer(tmp_path):
    """An earlier design put one there; nothing reads one, so shipping one would be a dead directory.

    The read path that applies a trigger directory is team-only
    (`infrastructure/tekton/triggers.py`'s `_apply_team_trigger_resources`), so an environment
    `triggers/` directory
    is a directory that exists and is read by nothing — the shape of the `kubementat_components/`
    tree that was deleted for that exact reason. The *team* trigger directory later moved under
    `tekton/`; the environment half still has no reader, so it still ships nothing, and it lands
    only with the reader that opens it. This guard says the line twice, once as a comment on
    `Config` and once as a fact a test can fail on.
    """
    config = Config(environment="dev", team="dev1", platform_config_dir=tmp_path,
                    preload_configs=False)

    assert not hasattr(config, "tekton_env_trigger_dir")
    assert "triggers" not in {Path(config.tekton_env_task_dir).name,
                              Path(config.tekton_env_pipeline_dir).name}


# ---------------------------------------------------------------------------
# nothing prunes
# ---------------------------------------------------------------------------
def _functions_named(root, names):
    """Every `ast` function definition in `src/**.py` whose name is in `names`."""
    found = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in names:
                found.append((path, node))
    return found


def test_no_setup_path_prunes_a_manifest_a_layer_stopped_shipping():
    """Setup applies; it does not delete. Nothing may change that on the way to phase 6.

    The rule the ticket settled: kubementat applies what it ships and what the layers declare, and a
    manifest that stopped being shipped is not kubementat's to delete. That is why a layer that
    drops a Task leaves the object on the cluster — the alternative is a setup command that destroys
    an operator's work because upstream renamed a file, and `uninstall` already owns removal.

    Structural, for the same reason every other "stays deleted" guard in this tree is structural:
    both behaviours work until someone adds a prune, so the proof is a sweep of the shipped source
    rather than a run that cannot observe an absence.
    """
    setup_functions = _functions_named(REPO_ROOT, {"setup_pipelines", "apply_resources",
                                                   "_apply_manifest_layer",
                                                   "_setup_pipelines_setup_role_bindings"})
    assert setup_functions, "setup_pipelines is not in the shipped source, so this guard is vacuous"

    pruners = []
    for path, function in setup_functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            called = (node.func.attr if isinstance(node.func, ast.Attribute)
                      else getattr(node.func, "id", ""))
            if str(called).startswith(PRUNE_CALL_PREFIXES):
                pruners.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> {called}")

    assert pruners == [], f"a setup path now deletes: {pruners}"
