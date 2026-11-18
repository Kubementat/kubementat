"""The operator's values layer, read in the order the manifest names it.

The rule under test is one sentence: a release's `values:` list is the shipped default and then
exactly one operator file, `<root>/<environment>/values/<component>.encrypted.yaml`. helm merges
`-f` arguments in the order they arrive, so the order is the whole of the precedence, and an
operator layer that is not there must cost nothing.

The single file exists because kubementat's components are cluster-wide: a team has no
cluster-wide answer to give a component, so no values path resolves through a team directory, and
the layer that carries key material is named after the `*.encrypted.*` pattern git-crypt declares,
so it is encrypted by its name rather than by living in a directory the declaration does not match.

Both halves of the precedence sentence belong to helmfile, not to this repository: the order it
hands `helm` comes out of its own templating, and a warning about a file it could not read is its
own. So these tests run the binary rather than restating its behaviour in Python, which is the only
honest way to check a gotmpl. It is also how the two failures this test exists to end would show
up:

* a values file listed but absent is fatal (measured on helmfile 1.7.0), so a rule
  that listed the candidate unconditionally would break every tree that declares nothing; and
* The per-entry `missingFileHandler` a values entry could carry turns out not to exist in
  1.7.0 -- measured: a values entry written as a mapping is read as inline values, so the handler
  would land in the chart's values and the file would still be fatal. Which leaves `isFile` guards,
  and a guard that silently evaluated `false` would look identical to a tree that declares nothing.

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
# `helmfile build` asks the installed helm for its version before it renders anything, so a host with
# helmfile and no helm does not skip this: it fails inside helmfile with `error determining helm
# version`. Both binaries are preconditions, and the guard says so.
helmfile_only = pytest.mark.skipif(HELMFILE is None or shutil.which("helm") is None,
                                   reason="helmfile builds its state through the installed helm, and "
                                           "this host is missing one of the two")

MANIFEST = values_path("helmfile.yaml.gotmpl")

ENVIRONMENT = "t16environment"
TEAM = "t16team"
COMPONENT = "grafana"

# The shipped default of the release the ordering assertions read. Spelled here because the
# assertion is about its *position*, and a position assertion needs the neighbour by name.
GRAFANA_DEFAULT = "monitoring/grafana_values.yaml"

# A probe key per position in the merge, so a layer that lost shows up as a wrong answer and not
# as a file that is not there.
PROBE_KEYS = ("product", "operator")


def child_environment(root=None, environment=ENVIRONMENT):
    """The two coordinates a packaged manifest reads for its values, plus what templating needs.

    `KUBEMENTAT_CONFIG_ROOT` and `KUBEMENTAT_ENVIRONMENT` are what
    `export_configuration_coordinates` in `infrastructure/helm.py` writes before helmfile starts,
    and they are the manifest's only way to address the operator's tree. Passing no root answers a
    hand-run helmfile, which exports nothing and should apply exactly the shipped defaults.
    """
    child = dict(os.environ)
    for name in ("KUBEMENTAT_CONFIG_ROOT", "KUBEMENTAT_ENVIRONMENT", "KUBEMENTAT_TEAM"):
        child.pop(name, None)
    if root is not None:
        child["KUBEMENTAT_CONFIG_ROOT"] = str(root)
    if environment is not None:
        child["KUBEMENTAT_ENVIRONMENT"] = environment
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


def resolved_values_lists(root=None, environment=ENVIRONMENT):
    """`{release: [values entries in the order helmfile will hand them to helm]}`.

    `helmfile build` prints the desired state after templating, so this is what the rule asks for
    as "the order helmfile was handed" -- it is produced by helmfile, not by a parser that read the
    manifest looking for lines that look like the rule.
    """
    result = run_helmfile(values_path(), "-f", str(MANIFEST), "build",
                          root=root, environment=environment)
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

# Everything the layer file may set, dumped through the chart so precedence is a value and not a
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
# operator layer, addressed by the component name.
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
    for expected in ('env "KUBEMENTAT_CONFIG_ROOT"', 'env "KUBEMENTAT_ENVIRONMENT"'):
        assert expected in block, f"the shipped operator block no longer reads {expected}"
    # The team coordinate must reach no values path: a cluster-wide component is not a team's
    # answer, and a block that read the coordinate again would resurrect a layer nothing declares.
    assert 'env "KUBEMENTAT_TEAM"' not in block, \
        "the operator block addresses a team layer again, which no cluster-wide release has"
    assert block.count("%s/%s/values/%s.encrypted.yaml") >= 1, \
        "the shipped operator block no longer addresses .../values/<component>.encrypted.yaml"
    return block


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


def declare(root, component, values, environment=ENVIRONMENT, directory="values",
            suffix=".encrypted.yaml"):
    """Write one operator values file where the tree's layout puts it, and nothing else.

    No `static.json`, no `.gitattributes`, no directory this function was not told to create. The
    keyword arguments exist for the one test that puts files where the rule does *not* read them,
    so the addresses the manifest ignores are provable and not assumed.
    """
    target = root / environment / directory
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{component}{suffix}"
    path.write_text(yaml.dump(values, default_flow_style=False), encoding="utf-8")
    return path


def render_merged_values(configuration, state_directory, environment=ENVIRONMENT):
    """helmfile's answer as `(the merged values, the missing-file warnings it produced)`.

    The warnings are returned rather than asserted here because "one warning is the most a quiet
    tree is allowed to produce" is a count, and a count belongs to the test that states it.
    """
    result = run_helmfile(state_directory, "-f", "helmfile.yaml.gotmpl", "template",
                          root=configuration, environment=environment)
    warnings = [line for line in result.stderr.splitlines()
                if "does not exist" in line or "missing values file" in line]
    assert result.returncode == 0, f"`helmfile template` said: {result.stderr.strip()}"
    documents = [document for document in yaml.safe_load_all(result.stdout) if document]
    assert len(documents) == 1, documents
    return yaml.safe_load(documents[0]["data"]["merged"]), warnings


# --- (a) zero operator files -------------------------------------------------------------------


# --- (b) the order, which is the precedence ----------------------------------------------------


@helmfile_only
def test_the_operator_layer_is_handed_after_the_default_and_nothing_is_handed_after_it(tmp_path):
    """The resolved list of a release whose operator file exists, as helmfile printed it.

    This is the ordering assertion the rule asks for, and it is the binary's own answer: the shipped
    default, then the one layer. No team entry follows -- a team address is not a values address.
    """
    configuration, _ = scratch_tree(tmp_path)
    written = declare(configuration, COMPONENT, {"layer": "environment"})
    environment_directory = configuration / ENVIRONMENT

    listed = listed_files(resolved_values_lists(configuration)[COMPONENT])

    assert listed == [GRAFANA_DEFAULT, str(written)], \
        f"the order helmfile was handed is not the layering the rule states: {listed}"
    assert str(environment_directory / TEAM) not in " ".join(listed), listed


@helmfile_only
def test_an_operator_override_wins_over_the_default_and_leaves_the_rest_to_it(tmp_path):
    """The order above is not decoration: one rendered chart says which layer answered each key.

    The layer is read after the default, so it overrides the product's own key and touches nothing
    else; a key it never declares stays answered by the shipped file.
    """
    configuration, state_directory = scratch_tree(tmp_path)
    declare(configuration, "layerprobe", {"operator": "operator-won"})

    merged, warnings = render_merged_values(configuration, state_directory)

    assert warnings == [], f"a layer that exists warned: {warnings}"
    assert merged["operator"] == "operator-won", \
        "the operator layer did not override the shipped default for its own key: " + str(merged)
    assert merged["product"] == "product-default", \
        "the shipped default moved without an override of that key"


# --- (c) a quiet tree is quiet -----------------------------------------------------------------


@helmfile_only
def test_a_tree_that_declares_no_layer_warns_about_nothing_and_writes_the_default(tmp_path):
    """One candidate file, none of them there, and a tree that says nothing.

    The count is the assertion. helmfile's default handler is `Error`, so a rule that listed the
    file unconditionally would end this run before it rendered anything, and the release-level
    `Warn` that is the only handler 1.7.0 has would leave a warning about a file its operator never
    intended to write. One is the most this tree is allowed to produce.
    """
    configuration, state_directory = scratch_tree(tmp_path)
    (configuration / ENVIRONMENT).mkdir(parents=True)

    merged, warnings = render_merged_values(configuration, state_directory)

    assert len(warnings) <= 1, f"a quiet tree produced {len(warnings)} warnings: {warnings}"
    assert merged["product"] == "product-default", merged
    assert merged["operator"] == "product-default", f"operator was answered by nothing: {merged}"


# --- (d) the layer is named for its material ----------------------------------------------------


@helmfile_only
def test_a_key_that_lives_only_in_the_layer_is_still_applied(tmp_path):
    """The done-when for this rule. No plain file exists anywhere, and the key is only ever written
    in `<component>.encrypted.yaml`. A rule that looked for the plain name answers `product-default`."""
    configuration, state_directory = scratch_tree(tmp_path)
    declare(configuration, "layerprobe", {"operator": "encrypted-only"})

    merged, warnings = render_merged_values(configuration, state_directory)

    assert warnings == [], f"a file that exists warned: {warnings}"
    assert merged["operator"] == "encrypted-only", merged


def test_the_layer_is_named_as_the_encryption_declaration_reads_it():
    """The `.encrypted.` half is what puts the file under `*.encrypted.*`, so it must be the name.

    A layer that moved into a `values.encrypted/` directory would put its key material where the
    git-crypt declaration does not look -- the whole protection story rests on the file's name, so
    this is asserted on the shipped manifest and not on a scratch tree.
    """
    body = MANIFEST.read_text(encoding="utf-8")

    assert "values/%s.encrypted.yaml" in body, "the layer's name no longer carries .encrypted."
    assert "values.encrypted/%s.yaml" not in body, \
        "the manifest reads a values.encrypted/ directory again, which git-crypt does not declare"


# --- the halves that hold without the binary ---------------------------------------------------


def test_the_manifest_hands_its_component_name_to_the_operator_layer():
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

    assert len(labelled) == len(passed), (
        f"{len(labelled)} blocks declare a component_name and {len(passed)} hand one to the "
        f"operator layer -- the difference is a block that declares without handing over, which "
        f"is the miss this pair of counts is for, whatever the census of the file happens to be")
    assert len(labelled) >= 10, f"a file that lost a whole release block reads like this: {labelled}"
    assert sorted(labelled) == sorted(passed), (
        "the name handed to the operator layer stopped being the name `kmt component install` "
        f"addresses: {sorted(set(labelled) - set(passed))} / {sorted(set(passed) - set(labelled))}")


def test_no_release_block_carries_a_per_release_missing_file_handler():
    """The handler is absent on purpose, and a `Warn` put back on a release would be the wrong fix.

    helmfile 1.7.0 has no per-values-file handler -- measured: a values entry written as a mapping
    is read as inline values, so `- missingFileHandler: Warn` puts that word and a `values:` key
    into the chart's values and still fails the apply on the missing file. The only handler left is
    the release's own, and it silences a missing *product* default as well: a shipped file that
    moved would install chart defaults while the manifest looked satisfied. So the manifest keeps
    helmfile's default `Error` handler and guards the operator entry with `isFile` instead.
    """
    body = MANIFEST.read_text(encoding="utf-8")
    live = [line for line in body.splitlines() if not line.lstrip().startswith("#")]

    assert not [line for line in live if "missingFileHandler" in line], (
        "a per-release handler silences a missing product default, which is not this file's answer")
    assert "isFile" in body, "the guard that makes an absent operator file invisible is gone"


def test_the_operator_layer_is_addressed_by_the_two_coordinates_and_by_nothing_else():
    """The coordinate rule, asserted as text. No path in the manifest names the checkout, and a
    team coordinate reaches no values path, because a cluster-wide component has no team address."""
    body = MANIFEST.read_text(encoding="utf-8")
    live = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))

    assert "KUBEMENTAT_CONFIG_ROOT" in live, "the operator's root is not read by the manifest"
    assert "KUBEMENTAT_ENVIRONMENT" in live, "the environment is not read by the manifest"
    assert 'env "KUBEMENTAT_TEAM"' not in live, \
        "the manifest addresses a team layer again, and cluster-wide components have none"
    for not_a_path in ("kubementat_main_dir", "KUBEMENTAT_PLATFORM_CONFIG_DIR", "platform_config/"):
        assert not_a_path not in live, f"{not_a_path} reached the manifest, which is the old address"
    assert "../" not in live, "a relative upward path resolves against wherever helmfile was run"
