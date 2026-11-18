"""One component goes on the cluster — through the label the environment puts on its release."""


import pytest
from asset_paths import relocate_asset_root, values_path
from click.testing import CliRunner
from cluster_fixture import UNREAD_ROOT, configuration_only_context
from conftest import run_kmt

from cli.commands import component as component_cli
from kubementat.automations.routing import traefik_presence
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.infrastructure.helm import HelmUtils

# The live manifest. It is read here and not written here, because the claim that six components
# are installable is a claim about the tree, and a test that writes its own manifest cannot find out
# whether the tree moved.
LIVE_HELMFILE = values_path("helmfile.yaml.gotmpl")

# The same label vocabulary the live file uses, trimmed to three releases and one commented one. The
# commented block is not padding: a parser that reads `# - name: <something>` as a release would make
# a component installable that the manifest only declares inside a comment, and the only cheap way to
# prove it does not is to have one in the file. It names a namespace-only component for the same
# reason: `polaris` has a release in the live file since 2026-11-01, so it is no longer the example.
FIXTURE_HELMFILE = """\
helmDefaults:
  wait: true
repositories:
- name: grafana
  url: https://grafana.github.io/helm-charts
releases:
- name: grafana
  chart: grafana/grafana
  namespace: grafana
  labels:
    chart: grafana
    component: monitoring
    component_name: grafana
    group: standard
- name: prometheus
  chart: prometheus-community/prometheus
  namespace: prometheus
  labels:
    chart: prometheus
    component: monitoring
    component_name: prometheus
    group: standard
- name: prometheus-blackbox-exporter
  chart: prometheus-community/prometheus-blackbox-exporter
  namespace: prometheus
  labels:
    chart: prometheus-blackbox-exporter
    component: monitoring
    component_name: prometheus-blackbox-exporter
    group: standard
- name: cert-manager
  chart: jetstack/cert-manager
  namespace: cert-manager
  labels:
    component_name: cert-manager
    group: routing
- name: traefik
  chart: traefik/traefik
  namespace: traefik
  labels:
    component_name: traefik
    group: routing
- name: openbao
  chart: openbao/openbao
  namespace: openbao
  labels:
    component_name: openbao
    group: extra
# A component that is only a namespace in this fixture, left as a comment the way a commented release
# looks when someone retires one without deleting it. `goldilocks` is the shape: a `constants` key with
# no release, which is what a stale comment of this shape says about a component.
# - name: goldilocks
#   chart: fairwinds-incubator/goldilocks
#   namespace: goldilocks
#   labels:
#     component_name: goldilocks
#     group: standard
"""


@pytest.fixture(autouse=True)
def _template_half_is_writable(fixture_repo, monkeypatch):
    """Move the asset root into the fixture, so the manifest is a file this run can write."""
    relocate_asset_root(fixture_repo.root, monkeypatch)


def seeded_manifest_path(fixture_repo):
    """Where the manifest this fixture makes the command read lives."""
    return fixture_repo.root / "values" / "helmfile.yaml.gotmpl"


def seeded_helmfile(fixture_repo, body=FIXTURE_HELMFILE):
    """The manifest, written into the relocated asset root so this run reads it."""
    target = seeded_manifest_path(fixture_repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return target


TRAEFIK_RELEASE = """\
- name: traefik
  chart: traefik/traefik
  namespace: traefik
  labels:
    component_name: traefik
    group: routing
"""


def installed(fixture_repo, component, *flags):
    """The install command through the group, in this process."""
    return CliRunner().invoke(component_cli.component,
                              ["install", fixture_repo.environment, component, *flags])


def helmfile_selectors(fake_binaries):
    """The `-l` argument of every helmfile call the command made, in order."""
    selectors = []
    for argv in fake_binaries.commands():
        if argv[:1] == ["helmfile"]:
            selectors.append(argv[argv.index("-l") + 1] if "-l" in argv else None)
    return selectors


# --------------------------------------------------------------------------------------------
# the argv, and what is deliberately not in it
# --------------------------------------------------------------------------------------------


def test_installing_one_component_starts_nothing_helm_could_have_done_itself(fixture_repo,
                                                                            dry_run_env,
                                                                            fake_binaries):
    """There is exactly one way to put a component on a cluster here, and it is helmfile."""
    seeded_helmfile(fixture_repo)

    installed(fixture_repo, "prometheus")

    # Everything helm does except asking what is already on the cluster. The read is not a second
    # install path and must not be counted as one: `helm list` is how `helmfile_apply` learns
    # whether a release it is about to apply already lives in another namespace (the namespace-rename
    # refusal), and it writes nothing. What this test rules out is a *write* outside helmfile.
    helm = [argv for argv in fake_binaries.commands()
            if argv[:1] == ["helm"] and argv[1:2] != ["list"]]
    assert helm == [], f"a second install path appeared: {helm}"


# --------------------------------------------------------------------------------------------
# components the command must refuse, and what it says instead
# --------------------------------------------------------------------------------------------


def test_a_stale_copy_in_the_configuration_changes_nothing(fixture_repo, dry_run_env,
                                                           fake_binaries):
    """No configuration value can point the install at a manifest other than the packaged one."""
    # The manifest that will be applied declares no traefik; the copy beside the configuration does.
    seeded_helmfile(fixture_repo, FIXTURE_HELMFILE.replace(TRAEFIK_RELEASE, ""))

    stale = (fixture_repo.platform_config / fixture_repo.environment / "kubementat_components"
             / "helmfile.yaml.gotmpl")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(FIXTURE_HELMFILE, encoding="utf-8")
    assert "component_name: traefik" in stale.read_text(), "the stale copy has to declare traefik"
    assert "component_name: traefik" not in seeded_manifest_path(fixture_repo).read_text(), \
        "the applied manifest must not, or there is no divergence to arrange"

    result = installed(fixture_repo, "traefik")

    assert result.exit_code != 0, result.output
    assert str(stale) not in result.output, \
        "the refusal still weighs the copy: naming it is what told an operator two files had agreed"
    assert str(seeded_manifest_path(fixture_repo)) in result.output, \
        "the manifest that decides is the one the answer has to name"
    assert "namespace kubementat creates" in result.output, result.output
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


def test_the_refusal_for_a_component_with_no_release_never_points_at_initialize(
        fixture_repo, dry_run_env, fake_binaries):
    """Two states, one sentence, one thing neither of them says."""
    for component, description in (("vpa", "a namespace-only component"),
                                   ("goldilocks", "a release that is only commented out")):
        seeded_helmfile(fixture_repo)
        result = installed(fixture_repo, component)

        assert result.exit_code != 0, f"{description}: {result.output}"
        assert "initialize" not in result.output.lower(), f"{description}: {result.output}"


def test_a_missing_manifest_names_the_file_and_not_a_command_that_cannot_help(
        fixture_repo, dry_run_env, fake_binaries):
    """A run that cannot read the manifest says so, and does not point at `kmt initialize`."""
    result = installed(fixture_repo, "grafana")

    assert result.exit_code != 0, result.output
    assert "Traceback" not in result.output, result.output
    assert "helmfile" in result.output.lower(), result.output
    assert "initialize" not in result.output.lower(), \
        "the missing file is not something kmt initialize writes: " + result.output
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


def test_two_releases_under_one_label_are_refused_as_a_manifest_finding(fixture_repo,
                                                                       dry_run_env,
                                                                       fake_binaries):
    """If the filter selects two releases, the command is wrong to pick one of them."""
    seeded_helmfile(fixture_repo)
    manifest = seeded_helmfile(fixture_repo).read_text()
    manifest += """- name: openbao-second
  chart: openbao/openbao
  namespace: openbao
  labels:
    component_name: openbao
    group: extra
"""
    seeded_helmfile(fixture_repo, manifest)

    result = installed(fixture_repo, "openbao")

    assert result.exit_code != 0, result.output
    assert "openbao-second" in result.output and "openbao" in result.output, result.output
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


def test_a_typo_names_the_component_set_and_deletes_nothing(fixture_repo, dry_run_env,
                                                            fake_binaries):
    """`grafanaa` is not a component. One line says so, lists what is, and exits non-zero."""
    seeded_helmfile(fixture_repo)

    result = run_kmt(["component", "install", fixture_repo.environment, "grafanaa"], dry_run_env)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "not a kubementat component" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "grafana" in result.stdout + result.stderr, "the message must list the valid names"
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


# --------------------------------------------------------------------------------------------
# routing: the question is whose controller this is, not whether the release exists
# --------------------------------------------------------------------------------------------


def a_presence(classification, namespace="traefik"):
    """A `TraefikPresence`-shaped answer. `classification` is the only field the command reads."""
    class Answer:
        def __init__(self, classification, namespace):
            self.classification = classification
            self.namespace = namespace

        def report(self):
            return f"classification: {self.classification}"

    return Answer(classification, namespace)


def test_installing_traefik_where_a_foreign_controller_runs_refuses_and_applies_nothing(
        fixture_repo, dry_run_env, fake_binaries, monkeypatch):
    """The verb must be safe, not merely present."""
    seeded_helmfile(fixture_repo)
    monkeypatch.setattr(traefik_presence, "discover",
                        lambda ctx, **kwargs: a_presence(
                            traefik_presence.CLASSIFICATION_FOREIGN))

    result = installed(fixture_repo, "traefik")

    assert result.exit_code != 0, result.output
    assert "routing install" in result.output, result.output
    assert helmfile_selectors(fake_binaries) == [], \
        f"the foreign controller was installed over: {fake_binaries.commands()}"


def test_installing_traefik_kubementat_already_installed_leaves_the_release_alone(
        fixture_repo, dry_run_env, fake_binaries, monkeypatch):
    """Already ours means already there, and `install` is not how you upgrade it."""
    seeded_helmfile(fixture_repo)
    monkeypatch.setattr(traefik_presence, "discover",
                        lambda ctx, **kwargs: a_presence(
                            traefik_presence.CLASSIFICATION_OURS))

    result = installed(fixture_repo, "traefik")

    assert result.exit_code == 0, result.output
    assert "already installed" in result.output, result.output
    assert "routing install" in result.output, result.output
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


def test_installing_traefik_on_a_cluster_that_has_no_controller_applies_it_once(
        fixture_repo, dry_run_env, fake_binaries, monkeypatch):
    """The branch where installing is correct: nothing answers, so kubementat installs its own."""
    seeded_helmfile(fixture_repo)
    monkeypatch.setattr(traefik_presence, "discover",
                        lambda ctx, **kwargs: a_presence(
                            traefik_presence.CLASSIFICATION_NONE))

    result = installed(fixture_repo, "traefik")

    assert result.exit_code == 0, result.output
    assert helmfile_selectors(fake_binaries) == ["component_name=traefik"], \
        fake_binaries.commands()


# --------------------------------------------------------------------------------------------
# the guards: what the command accepts is derived, so both lists move when the tree does
# --------------------------------------------------------------------------------------------


def test_a_component_added_to_the_constants_is_accepted_by_both_verbs(fixture_repo, dry_run_env,
                                                                      fake_binaries, monkeypatch):
    """The accepted names are `DEFAULT_NAMESPACES`, read at call time."""
    extended = dict(constants.DEFAULT_NAMESPACES)
    extended["newcomer"] = "newcomer"
    monkeypatch.setattr(constants, "DEFAULT_NAMESPACES", extended)
    seeded_helmfile(fixture_repo)

    accepted = installed(fixture_repo, "newcomer")
    assert "not a kubementat component" not in accepted.output, accepted.output
    assert "component_name=newcomer" in accepted.output, accepted.output

    listed = installed(fixture_repo, "nonsense-name")
    assert "newcomer" in listed.output, listed.output


def test_the_live_manifest_makes_the_components_the_readme_names_installable(fake_binaries,
                                                                             monkeypatch):
    """What the tree actually declares, measured and not asserted."""
    # One `Context`. What this reads is the live manifest, not a configuration, so the object
    # is `cluster_fixture`'s configuration-only shape: a real `Config`, lazy, and no claim on a
    # cluster this test is not addressing.
    # The `Config` is here because `HelmUtils` takes a `Context` and a `Context` carries one; this
    # read is of the live helmfile, and "no root named" is a refusal rather than a
    # checkout, so the root is named as the unread thing it is.
    config = Config("dev", "dev1", platform_config_dir=UNREAD_ROOT, preload_configs=False)
    declared = HelmUtils(configuration_only_context(
        "dev", "dev1", config)).component_release_index(LIVE_HELMFILE)

    installable = sorted(name for name in constants.DEFAULT_NAMESPACES if declared.get(name))
    assert installable, \
        "no component has a release the label filter can reach, so `kmt component install` applies nothing"
    assert not [name for name, releases in declared.items() if len(releases) > 1], \
        "one label on more than one release makes a component install resolve twice"
    # A component the index reaches is installable by construction, and one it does not is refused by
    # name. Both directions are what the command does, so neither is written down here.
    for name in installable:
        assert declared[name], f"{name} is installable and its release list is empty"
    # The labels outside `constants` are the releases a group apply brings without a component name,
    # and they are refused by the same accessor everything else asks. Derived, so a fourth exporter is
    # not an edit to this file.
    unlabelled = sorted(label for label, releases in declared.items()
                        if releases and label not in constants.DEFAULT_NAMESPACES)
    assert unlabelled, \
        "every release-carrying label names a component, so nothing here tests the refusal"
    for label in unlabelled:
        refused = None
        try:
            config.component_namespace(label)
        except KeyError as error:  # the refusal is the behaviour, and it is the read's to give
            refused = error
        assert refused is not None, \
            f"{label} ships a release and names no component, yet the read answered a namespace for it: " \
            "a component with no table entry is one `kmt component install` would default silently"
        assert label in str(refused), \
            f"the refusal for {label} does not name the component: {refused}"
    # And the namespace table itself is checked the way a table is worth checking: as invariants, and
    # not as a number. A count is a fact about a Tuesday; these are the properties the namespace
    # export, the group apply and the uninstall sweep all read.
    assert all(namespace.strip() for namespace in constants.DEFAULT_NAMESPACES.values()), \
        sorted(name for name, namespace in constants.DEFAULT_NAMESPACES.items() if not namespace.strip())
    values = sorted(namespace for namespace in constants.DEFAULT_NAMESPACES.values())
    assert len(values) == len(set(values)), \
        f"two components share a namespace, so a rename moves one and leaves the other: " \
        f"{[value for value in set(values) if values.count(value) > 1]}"
