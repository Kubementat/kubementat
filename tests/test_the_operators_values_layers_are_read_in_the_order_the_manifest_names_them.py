"""The operator's values layers, read in the order the manifest names them.

The rule under test is one sentence: a release's `values:` list is the shipped default, then the
environment's plain file, then the environment's encrypted file, then the team's plain file and the
team's encrypted file. helm merges `-f` arguments in the order they arrive, so the order is the
whole of the precedence, and an operator layer that is not there must cost nothing.

Both halves of that sentence belong to helmfile, not to this repository: the order it hands `helm`
comes out of its own templating, and a warning about a file it could not read is its own. So these
tests run the binary rather than restating its behaviour in Python, which is the only honest way to
check a gotmpl. It is also how the two failures this ticket exists to end would show up:

* a values file listed but absent is fatal (measured on helmfile 1.7.0), so a rule
  that listed all four candidates unconditionally would break every tree that declares nothing; and
* The per-entry `missingFileHandler` a values entry could carry turns out not to exist in
  1.7.0 -- measured: a values entry
  written as a mapping is read as inline values, so the handler would land in the chart's values and
  the file would still be fatal. Which leaves `isFile` guards, and a guard that silently evaluated
  `false` would look identical to a tree that declares nothing.

Two harnesses, because the shipped releases read charts over a network:

* `resolved_values_lists` reads the **shipped manifest** with `helmfile build`, whose output is the
  desired state after templating -- literally "the order helmfile was handed".
* `render_merged_values` renders a scratch tree: the shipped `kubementat-operator-values` block,
  sliced out of the shipped manifest rather than transcribed, plus one local chart. That is where
  "which layer won" and "did it warn" can actually be read.

Neither writes into the repository, and neither needs a cluster: `build` resolves and `template`
renders, and both work against a local chart.
"""

import os
import shutil
import subprocess

import pytest
import yaml

from asset_paths import values_path

# What is measured here. helmfile is a declared dependency; where it is not installed these tests
# skip, because a Python re-reading of a gotmpl is not a measurement of anything.
HELMFILE = shutil.which("helmfile")
helmfile_only = pytest.mark.skipif(HELMFILE is None,
                                   reason="helmfile is what is under test, and it is not installed")

MANIFEST = values_path("helmfile.yaml.gotmpl")

ENVIRONMENT = "t16environment"
TEAM = "t16team"
COMPONENT = "grafana"

# The shipped default of the release the ordering assertions read. Spelled here because the
# assertion is about its *position*, and a position assertion needs the neighbour by name.
GRAFANA_DEFAULT = "monitoring/grafana_values.yaml"

# A component key per layer, so a layer that lost shows up as a wrong answer and not as a file that
# is not there.
PROBE_KEYS = ("product", "environment_plain", "environment_encrypted",
              "team_plain", "team_encrypted")


def child_environment(root=None, environment=ENVIRONMENT, team=None):
    """The three coordinates a packaged manifest reads, plus what its own templating requires.

    `KUBEMENTAT_CONFIG_ROOT` / `KUBEMENTAT_ENVIRONMENT` / `KUBEMENTAT_TEAM` are what
    `helm_utils.export_configuration_coordinates` writes before helmfile starts, and they are
    the manifest's only way to address the operator's tree. Passing no root answers a hand-run
    helmfile, which exports nothing and should apply exactly the shipped defaults.
    """
    child = dict(os.environ)
    for name in ("KUBEMENTAT_CONFIG_ROOT", "KUBEMENTAT_ENVIRONMENT", "KUBEMENTAT_TEAM"):
        child.pop(name, None)
    if root is not None:
        child["KUBEMENTAT_CONFIG_ROOT"] = str(root)
    if environment is not None:
        child["KUBEMENTAT_ENVIRONMENT"] = environment
    if team is not None:
        child["KUBEMENTAT_TEAM"] = team
    # `grafana`'s block names its admin pair through `requiredEnv`, exactly as a real apply does,
    # where HelmUtils exports it from `static.encrypted.json` first. Without these two the state
    # file fails to template and every assertion below is about the wrong failure.
    child.setdefault("GRAFANA_ADMIN_USER", "admin")
    child.setdefault("GRAFANA_ADMIN_PASSWORD", "not-a-password")
    return child


def run_helmfile(cwd, *arguments, **env_overrides):
    """One helmfile run: exit status, stdout, stderr. Nothing is swallowed, because stderr is an
    assertion target (it is where helmfile's warning about a missing file lives)."""
    child = child_environment(**env_overrides)
    return subprocess.run([HELMFILE, *arguments], cwd=str(cwd), env=child,
                          capture_output=True, text=True)


def resolved_values_lists(root=None, environment=ENVIRONMENT, team=None):
    """`{release: [values entries in the order helmfile will hand them to helm]}`.

    `helmfile build` prints the desired state after templating, so this is what the ticket asks for
    as "the order helmfile was handed" -- it is produced by helmfile, not by a parser that read the
    manifest looking for lines that look like the rule.
    """
    result = run_helmfile(values_path(), "-f", str(MANIFEST), "build",
                          root=root, environment=environment, team=team)
    assert result.returncode == 0, f"`helmfile build` said: {result.stderr.strip()}"
    state = yaml.safe_load(result.stdout)
    assert state and state.get("releases"), f"helmfile returned no releases:\n{result.stdout[:400]}"
    return {release["name"]: list(release.get("values") or []) for release in state["releases"]}


def listed_files(release_values):
    """The paths a release's `values:` list names, in order, with inline entries dropped.

    An inline entry is what the product contributes (`adminUser`, `ingressClass`), never a file, so
    the layering question is only ever about the strings.
    """
    return [str(entry).replace("\\", "/") for entry in release_values if isinstance(entry, str)]


# --- the scratch tree that renders -------------------------------------------------------------

PROBE_CHART = """apiVersion: v2
name: layerprobe
version: 0.1.0
"""

# Everything the layer files may set, dumped through the chart so precedence is a value and not a
# missing file.
PROBE_TEMPLATE = """apiVersion: v1
kind: ConfigMap
metadata:
  name: layerprobe
data:
  merged: |
{{ toYaml .Values | indent 4 }}
"""

# One release, shaped exactly like a block of the shipped manifest: its default first, then the
# operator layers, addressed by the component name.
PROBE_RELEASE = """releases:
- name: layerprobe
  chart: ./layerprobe
  labels:
    component: probe
    component_name: layerprobe
    group: standard
  values:
    - product_values.yaml
    {{- template "kubementat-operator-values" "layerprobe" }}
"""


def shipped_operator_block():
    """The shipped manifest's `kubementat-operator-values` definition, verbatim.

    That this is a slice of the shipped file rather than a transcription is the point: a test that
    re-typed the rule would keep passing after the rule changed, which is exactly how a guard like
    this one becomes decoration.
    """
    text = MANIFEST.read_text(encoding="utf-8")
    start = text.index('{{- define "kubementat-operator-values"')
    block = text[start:text.index("# Releases configuration for deployments", start)]
    for expected in ('env "KUBEMENTAT_CONFIG_ROOT"', 'env "KUBEMENTAT_ENVIRONMENT"',
                     'env "KUBEMENTAT_TEAM"'):
        assert expected in block, f"the shipped operator block no longer reads {expected}"
    for layer in ("values/%s.yaml", "values.encrypted/%s.yaml"):
        assert block.count(block_path_pattern(layer)) >= 1, \
            f"the shipped operator block no longer addresses .../{layer}"
    return block


def block_path_pattern(layer):
    """The printf format string the shipped block uses for one layer directory."""
    return "%s/" + layer


def scratch_tree(tmp_path):
    """A tree that renders. Returns `(configuration_root, state_directory)`.

    The two are siblings under `tmp_path` and neither is the repository: the configuration root is
    what `KUBEMENTAT_CONFIG_ROOT` is pointed at, and the state directory holds the manifest and the
    chart and is helmfile's working directory.
    """
    state_directory = tmp_path / "state"
    configuration = tmp_path / "platform_config"
    (state_directory / "layerprobe" / "templates").mkdir(parents=True)
    (state_directory / "layerprobe" / "Chart.yaml").write_text(PROBE_CHART, encoding="utf-8")
    (state_directory / "layerprobe" / "templates" / "cm.yaml").write_text(PROBE_TEMPLATE,
                                                                          encoding="utf-8")
    (state_directory / "product_values.yaml").write_text(
        "".join(f"{key}: product-default\n" for key in PROBE_KEYS), encoding="utf-8")
    (state_directory / "helmfile.yaml.gotmpl").write_text(
        shipped_operator_block() + "\n" + PROBE_RELEASE, encoding="utf-8")
    return configuration, state_directory


def declare(root, layer, component, values, environment=ENVIRONMENT, team=None):
    """Write one operator values file where the tree's layout puts it, and nothing else.

    No `static.json`, no `.gitattributes`, no directory this function was not told to create.
    """
    directory = root / environment
    if team is not None:
        directory = directory / team
    target = directory / layer
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{component}.yaml"
    path.write_text(yaml.dump(values, default_flow_style=False), encoding="utf-8")
    return path


def render_merged_values(configuration, state_directory, environment=ENVIRONMENT, team=None):
    """helmfile's answer as `(the merged values, the missing-file warnings it produced)`.

    The warnings are returned rather than asserted here because "one warning is the most a quiet
    tree is allowed to produce" is a count, and a count belongs to the test that states it.
    """
    result = run_helmfile(state_directory, "-f", "helmfile.yaml.gotmpl", "template",
                          root=configuration, environment=environment, team=team)
    warnings = [line for line in result.stderr.splitlines()
                if "does not exist" in line or "missing values file" in line]
    assert result.returncode == 0, f"`helmfile template` said: {result.stderr.strip()}"
    documents = [document for document in yaml.safe_load_all(result.stdout) if document]
    assert len(documents) == 1, documents
    return yaml.safe_load(documents[0]["data"]["merged"]), warnings


# --- (a) zero operator files -------------------------------------------------------------------


@helmfile_only
def test_a_tree_that_declares_no_operator_values_applies_the_shipped_defaults(tmp_path):
    """Every release resolves to exactly its shipped file, and helmfile is given no reason to warn.

    The configuration root exists -- the environment directory is there and empty -- because "the
    operator declared nothing" is a state a real run is in, not a convenience of the test.
    """
    configuration, _ = scratch_tree(tmp_path)
    (configuration / ENVIRONMENT).mkdir(parents=True)

    for release, values in resolved_values_lists(configuration).items():
        assert not any(str(configuration) in entry for entry in listed_files(values)), \
            f"{release} read an operator file from a root that declared nothing: {listed_files(values)}"

    result = run_helmfile(values_path(), "-f", str(MANIFEST), "build",
                          root=configuration, environment=ENVIRONMENT)
    assert "does not exist" not in result.stderr, \
        f"a tree that declares nothing warned: {result.stderr}"


@helmfile_only
def test_a_run_that_named_no_configuration_root_reads_the_shipped_defaults_only():
    """§rule 3 from the other side: the exports are the only path, so their absence is survivable.

    A hand-run helmfile in the packaged directory exports none of them. It must apply exactly the
    shipped defaults rather than fail, guess, or reach for a checkout it was never told about.
    """
    for release, values in resolved_values_lists(root=None).items():
        assert all("/values/" not in entry and "/values.encrypted/" not in entry
                   for entry in listed_files(values)), \
            f"{release} addressed an operator layer with no root exported: {listed_files(values)}"


# --- (b) the order, which is the precedence ----------------------------------------------------


@helmfile_only
def test_the_environment_layer_is_handed_after_the_default_and_the_team_layer_after_it(tmp_path):
    """The resolved list of a release whose four operator files all exist, as helmfile printed it.

    This is the ordering assertion the ticket asks for, and it is the binary's own answer: the
    shipped default, then environment plain, environment encrypted, team plain, team encrypted.
    """
    configuration, _ = scratch_tree(tmp_path)
    declare(configuration, "values", COMPONENT, {"layer": "environment-plain"})
    declare(configuration, "values.encrypted", COMPONENT, {"layer": "environment-encrypted"})
    declare(configuration, "values", COMPONENT, {"layer": "team-plain"}, team=TEAM)
    declare(configuration, "values.encrypted", COMPONENT, {"layer": "team-encrypted"}, team=TEAM)

    listed = listed_files(resolved_values_lists(configuration, team=TEAM)[COMPONENT])
    environment_directory = configuration / ENVIRONMENT

    assert listed == [GRAFANA_DEFAULT,
                      str(environment_directory / "values" / f"{COMPONENT}.yaml"),
                      str(environment_directory / "values.encrypted" / f"{COMPONENT}.yaml"),
                      str(environment_directory / TEAM / "values" / f"{COMPONENT}.yaml"),
                      str(environment_directory / TEAM / "values.encrypted" / f"{COMPONENT}.yaml")], \
        f"the order helmfile was handed is not the layering the rule states: {listed}"


@helmfile_only
def test_an_environment_override_wins_over_the_default_and_a_team_override_wins_over_it(tmp_path):
    """The order above is not decoration: one rendered chart says which layer answered each key.

    The environment layer is read first, so it overrides the product's own key and touches nothing
    else; the team layer is read after it, so the same key is answered by the team while a key the
    team never declares stays with the environment.
    """
    configuration, state_directory = scratch_tree(tmp_path)
    declare(configuration, "values", "layerprobe", {"environment_plain": "environment"})
    declare(configuration, "values.encrypted", "layerprobe",
            {"environment_encrypted": "environment"})
    declare(configuration, "values", "layerprobe", {"environment_plain": "team"}, team=TEAM)
    declare(configuration, "values.encrypted", "layerprobe", {"team_encrypted": "team"}, team=TEAM)

    environment_only, warnings = render_merged_values(configuration, state_directory)
    assert warnings == [], f"a complete environment layer warned: {warnings}"
    assert environment_only["environment_plain"] == "environment", \
        "the environment layer did not override the shipped default for its own key"
    assert environment_only["product"] == "product-default", \
        "the shipped default moved without an override of that key"

    merged, warnings = render_merged_values(configuration, state_directory, team=TEAM)
    assert warnings == [], f"a complete tree warned: {warnings}"
    assert merged["environment_plain"] == "team", \
        "the team layer did not override the environment: " + str(merged)
    assert merged["environment_encrypted"] == "environment", \
        "the team layer answered a key it never declared: " + str(merged)
    assert merged["team_encrypted"] == "team", merged
    assert merged["product"] == "product-default", merged


# --- (c) a quiet tree is quiet ----------------------------------------------------------------


@helmfile_only
def test_a_team_named_and_declaring_nothing_does_not_warn_about_four_missing_files(tmp_path):
    """Four candidate files, none of them there, and a tree that says nothing.

    The count is the assertion. helmfile's default handler is `Error`, so a rule that listed the
    files unconditionally would end this run before it rendered anything, and the release-level
    `Warn` that is the only handler 1.7.0 has would leave four warnings about files its operator
    never intended to write. One is the most this tree is allowed to produce.
    """
    configuration, state_directory = scratch_tree(tmp_path)
    (configuration / ENVIRONMENT / TEAM).mkdir(parents=True)

    merged, warnings = render_merged_values(configuration, state_directory, team=TEAM)

    assert len(warnings) <= 1, f"a quiet tree produced {len(warnings)} warnings: {warnings}"
    assert merged["product"] == "product-default", merged
    for key in PROBE_KEYS[1:]:
        assert merged[key] == "product-default", f"{key} was answered by nothing: {merged}"


@helmfile_only
def test_an_environment_that_declared_only_an_encrypted_file_lists_exactly_that_file(tmp_path):
    """One file present is one entry, not four with three absent.

    The same shape as the quiet tree, from the other side: presence is what makes an entry.
    """
    configuration, _ = scratch_tree(tmp_path)
    written = declare(configuration, "values.encrypted", COMPONENT, {"layer": "env-encrypted"})

    listed = listed_files(resolved_values_lists(configuration)[COMPONENT])

    assert listed == [GRAFANA_DEFAULT, str(written)], listed


# --- (d) the encrypted layer, by the same rule -------------------------------------------------


@helmfile_only
def test_a_key_that_lives_only_in_values_encrypted_is_still_applied(tmp_path):
    """The done-when for this rule. No `values/layerprobe.yaml` exists anywhere, and the key is only ever
    written under `values.encrypted/`. A rule that looked only at the plain directory answers
    `product-default`."""
    configuration, state_directory = scratch_tree(tmp_path)
    declare(configuration, "values.encrypted", "layerprobe", {"team_plain": "encrypted-only"})

    merged, warnings = render_merged_values(configuration, state_directory)

    assert warnings == [], f"a file that exists warned: {warnings}"
    assert merged["team_plain"] == "encrypted-only", merged


@helmfile_only
def test_the_encrypted_layer_is_read_after_the_plain_one_at_each_level(tmp_path):
    """Within one layer the encrypted file wins, because it is the one the operator means secretly.

    Same key in both, one layer, no team: the encrypted answer is what renders. This is the same
    order `Config._load_config` applies to `static.json` and its encrypted neighbour.
    """
    configuration, state_directory = scratch_tree(tmp_path)
    declare(configuration, "values", "layerprobe", {"environment_plain": "plain-won?",
                                                    "team_plain": "plain-layer-only"})
    declare(configuration, "values.encrypted", "layerprobe", {"environment_plain": "encrypted"})

    merged, warnings = render_merged_values(configuration, state_directory)

    assert warnings == [], warnings
    assert merged["environment_plain"] == "encrypted", merged
    assert merged["team_plain"] == "plain-layer-only", \
        "the encrypted layer answered a key only the plain one declared: " + str(merged)
    assert merged["product"] == "product-default", merged


# --- the halves that hold without the binary ---------------------------------------------------


def test_the_manifest_hands_its_component_name_to_the_operator_layers():
    """Every release block passes its own `component_name` label, not its release name.

    The two prometheus exporters and `polaris` are what make this a check: for them the label, the
    release name and the chart name are three different strings, and a block that passed its release
    name would still render -- and read a file nobody writes.
    """
    body = MANIFEST.read_text(encoding="utf-8")
    labelled = body.splitlines()
    labelled = [line.split("component_name:")[1].strip() for line in labelled
                if "component_name:" in line and not line.lstrip().startswith("#")]
    passed = [line.split('template "kubementat-operator-values"')[1].split('"')[1]
              for line in body.splitlines()
              if 'template "kubementat-operator-values"' in line
              and not line.lstrip().startswith("#")]

    assert len(labelled) == 10, f"expected ten release blocks with a component_name label, got {labelled}"
    assert sorted(labelled) == sorted(passed), (
        "the name handed to the operator layers stopped being the name `kmt component install` "
        f"addresses: {sorted(set(labelled) - set(passed))} / {sorted(set(passed) - set(labelled))}")


def test_no_release_block_carries_a_per_release_missing_file_handler():
    """The handler is absent on purpose, and a `Warn` put back on a release would be the wrong fix.

    helmfile 1.7.0 has no per-values-file handler -- measured: a values entry written as a mapping
    is read as inline values, so `- missingFileHandler: Warn` puts that word and a `values:` key
    into the chart's values and still fails the apply on the missing file. The only handler left is
    the release's own, and it silences a missing *product* default as well: a shipped file that
    moved would install chart defaults while the manifest looked satisfied. So the manifest keeps
    helmfile's default `Error` handler and guards every operator entry with `isFile` instead.
    """
    body = MANIFEST.read_text(encoding="utf-8")
    live = [line for line in body.splitlines() if not line.lstrip().startswith("#")]

    assert not [line for line in live if "missingFileHandler" in line], (
        "a per-release handler silences a missing product default, which is not this file's answer")
    assert "isFile" in body, "the guard that makes an absent operator file invisible is gone"


def test_the_operator_layers_are_addressed_by_the_three_exports_and_by_nothing_else():
    """§rule 3, asserted as text. No path in the manifest names the checkout, and nothing is derived
    from `kubementat_main_dir`."""
    body = MANIFEST.read_text(encoding="utf-8")
    live = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))

    assert "KUBEMENTAT_CONFIG_ROOT" in live, "the operator's root is not read by the manifest"
    assert "KUBEMENTAT_TEAM" in live, "the team layer is not addressed"
    for not_a_path in ("kubementat_main_dir", "KUBEMENTAT_PLATFORM_CONFIG_DIR", "platform_config/"):
        assert not_a_path not in live, f"{not_a_path} reached the manifest, which is the old address"
    assert "../" not in live, "a relative upward path resolves against wherever helmfile was run"
