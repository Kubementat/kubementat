"""One component goes on the cluster — through the label the environment puts on its release.

The gap this closes is an asymmetry, not a missing method. kubementat could add the platform (`kmt
install`), add a group (`kmt install --helmfile-installation-group`), and take one component off
(the uninstall command). It could not add one component, so putting grafana back after taking it off
meant applying a group and everything in it.

Two rules decided the shape here, and they are what these tests are for.

**The install path is helmfile with a label filter, and nothing else.** `helm_utils.helmfile_apply`
takes the filter as a mandatory positional argument, `kmt install` passes `group=standard`, and `kmt
routing install` passes `group=routing` (`routing.py:69`). A per-component `helm upgrade` written
beside that would be a second install path, which is the thing this whole migration is against. So
the argv asserted below is helmfile's, and the only difference is the selector.

**What the command can install is read, not written.** A component name is a key in
`constants.DEFAULT_NAMESPACES` — that is what "is this a kubementat component?" means — and the
release it applies is whatever the environment's helmfile labels `component_name=<name>`. The
intersection is the installable set, and it is derived in both directions: a component added to the
constants is accepted, a release added to the manifest becomes installable, and a release commented
out of the manifest stops being so. That is what the two guards at the bottom of this file are for;
a component list typed into the CLI would pass everything above them and fail both.

The other tests are the safety claims, not the plumbing: a component that is only a namespace is
refused rather than applied (with what to run instead), two releases under one label is a manifest
finding rather than a choice, and Traefik is asked routing's question — *mine, foreign, or absent* —
before it is applied, because installing a second controller is not a thing a command named `install`
gets to decide by itself.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.commands import component as component_cli
from kubementat.core import constants
from kubementat.automations.routing import traefik_presence
from cluster_fixture import UNREAD_ROOT, configuration_only_context
from kubementat.core.config.config import Config
from kubementat.infrastructure.helm import HelmUtils
from conftest import run_kmt
from asset_paths import relocate_asset_root, values_path

REPO_ROOT = Path(__file__).resolve().parent.parent

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
    """Move the asset root into the fixture, so the manifest is a file this run can write.

    The shipped manifest is package data and a test cannot edit package data. That used to
    be a problem precisely because this file's subject was two copies saying different things: the
    disagreement could only be arranged in the half a test controlled. There is one manifest now, and
    this file still needs to write it -- a release added, a release commented out, a label duplicated
    -- so the resolver moves for the length of one test and nothing else. The product keeps one rule
    (`test_config_path_resolution.py` states it), and every other guard in this file reads the
    shipped bytes.
    """
    relocate_asset_root(fixture_repo.root, monkeypatch)


def seeded_manifest_path(fixture_repo):
    """Where the manifest this fixture makes the command read lives.

    `relocate_asset_root` above moves the asset root into the fixture, so the packaged manifest --
    the only manifest -- is a file this test can write, and this is where it lands. One
    function rather than a literal, because three tests need to say which path the refusal named.
    """
    return fixture_repo.root / "values" / "helmfile.yaml.gotmpl"


def seeded_helmfile(fixture_repo, body=FIXTURE_HELMFILE):
    """The manifest, written into the relocated asset root so this run reads it.

    Before the manifest became product data this helper wrote two files, because the command answered
    two questions: the copy
    under `platform_config/{environment}/kubementat_components/` was what `helmfile` applied and the
    template was what kubementat shipped, and a test that seeded only one had half an answer. There
    is one manifest now and it is product data, so there is one file to seed and the disagreement
    that needed two is a state the tree cannot construct.
    """
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
    """The install command through the group, in this process.

    In-process rather than `run_kmt` because four of these tests have to change something the
    command reads at call time — a patched `constants`, a patched Traefik discovery — and a
    subprocess would read this interpreter's environment instead of the patched one.
    """
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


def test_one_component_is_applied_by_its_label_and_not_by_a_group(fixture_repo, dry_run_env,
                                                                  fake_binaries):
    """`kmt component install dev grafana` applies one release, addressed by `component_name=`.

    The selector is the whole point of the command. `group=standard` would apply grafana *and* the
    three prometheus releases that happen to be in the same group, which is a different command that
    someone already has.
    """
    seeded_helmfile(fixture_repo)

    result = installed(fixture_repo, "grafana")

    assert result.exit_code == 0, result.output
    assert helmfile_selectors(fake_binaries) == ["component_name=grafana"], \
        fake_binaries.commands()


def test_installing_one_component_starts_nothing_helm_could_have_done_itself(fixture_repo,
                                                                            dry_run_env,
                                                                            fake_binaries):
    """There is exactly one way to put a component on a cluster here, and it is helmfile.

    A `helm upgrade` or `helm install` written into the install half would work against a chart and
    fail against a label: helmfile is what evaluates the gotmpl, exports the namespaces and owns the
    diff the operator reads. So this asserts what the run did *not* call, and it is the assertion
    that keeps a future "simplification" from being called a simplification.
    """
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


def test_a_component_that_is_only_a_namespace_is_refused_and_names_the_ones_that_are_not(
        fixture_repo, dry_run_env, fake_binaries):
    """`vpa` is a namespace kubementat creates. Applying a filter that matches nothing is not "installing vpa".

    helmfile exits non-zero when a selector matches nothing, but the answer a typo-shaped component
    gets has to be the list of what *is* installable, not helmfile's line about selectors — and
    nothing may be applied on the way to finding that out.
    """
    seeded_helmfile(fixture_repo)

    result = installed(fixture_repo, "vpa")

    assert result.exit_code != 0, result.output
    assert "namespace" in result.output and "helm" in result.output, result.output
    assert "grafana" in result.output, "the refusal must say what this command can install"
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


def test_a_release_commented_out_of_the_manifest_is_not_installable(fixture_repo, dry_run_env,
                                                                    fake_binaries):
    """A release that exists only inside a comment is not a release, and the command has to say so.

    The commented block in the fixture is the shape a stale retirement comment leaves behind. It names
    a namespace-only component rather than `polaris`, which has a declared release in the live file
    since 2026-11-01: the parser rule is what this row proves, and the rule does not care which
    component the comment is about. A label parser that read comments would install a component
    nothing ships.
    """
    seeded_helmfile(fixture_repo)

    result = installed(fixture_repo, "goldilocks")

    assert result.exit_code != 0, result.output
    assert "component_name=goldilocks" in result.output, result.output
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


def test_a_stale_copy_in_the_configuration_changes_nothing(fixture_repo, dry_run_env,
                                                           fake_binaries):
    """Plan row 13's bug, made unconstructible rather than refused.

    The state the divergence answer used to be written for is one a test can still arrange: a
    `helmfile.yaml.gotmpl` lying under `platform_config/{environment}/kubementat_components/`,
    declaring releases the product does not. The single-manifest design is what makes that arrangement
    worthless, and this
    is the test that says so -- the manifest `helmfile_apply` applies and the release index reads is
    the packaged one, so the copy is data nobody loads. The proof has to be behavioural rather than a
    path check, and this is the behaviour: a stale copy that names traefik leaves the command
    believing exactly what it believed without it, so it refuses, tells the operator what it read,
    and asks the cluster nothing.
    """
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


def test_the_refusal_names_the_manifest_it_read(fixture_repo, dry_run_env, fake_binaries):
    """One file answered the question, so the answer names it.

    The three-branch refusal used to weigh two files and say which one it could not open. There is
    one file now, it is always readable, and the honest sentence is "this is the manifest, and it
    declares no release for that name" -- which is what an operator needs when they go and look.
    """
    seeded_helmfile(fixture_repo)

    result = installed(fixture_repo, "vpa")

    assert result.exit_code != 0, result.output
    assert "namespace kubementat creates" in result.output, result.output
    assert str(seeded_manifest_path(fixture_repo)) in result.output, \
        "a refusal that does not say what it read is a refusal the operator has to guess at"
    assert "helmfile.yaml.gotmpl" in result.output, result.output
    assert helmfile_selectors(fake_binaries) == [], fake_binaries.commands()


def test_the_refusal_for_a_component_with_no_release_never_points_at_initialize(
        fixture_repo, dry_run_env, fake_binaries):
    """Two states, one sentence, one thing neither of them says.

    `kmt initialize` writes no helmfile, so pointing at it is not a fix for a missing
    release block -- it is advice to run a command that will not touch the file in question. Both
    states get the same check for that, because they now get the same answer.
    """
    for component, description in (("vpa", "a namespace-only component"),
                                   ("goldilocks", "a release that is only commented out")):
        seeded_helmfile(fixture_repo)
        result = installed(fixture_repo, component)

        assert result.exit_code != 0, f"{description}: {result.output}"
        assert "initialize" not in result.output.lower(), f"{description}: {result.output}"


def test_a_missing_manifest_names_the_file_and_not_a_command_that_cannot_help(
        fixture_repo, dry_run_env, fake_binaries):
    """A run that cannot read the manifest says so, and does not point at `kmt initialize`.

    This used to be the "the environment was never initialized" case, and the answer was to tell the
    operator to run `kmt initialize`, because that command is what put a helmfile into
    `platform_config/`. It no longer writes one -- the manifest is product data -- so the same
    sentence now sends a human to a command that will not fix the thing it names. What is missing is
    the installed package's own data, and the refusal says that, names the path, applies nothing, and
    does not hand back a `FileNotFoundError` on the way.
    """
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
    """If the filter selects two releases, the command is wrong to pick one of them.

    "Install the openbao component" cannot mean "apply two releases and call it one component". The
    answer is to name both and put the correction where it belongs — the manifest's labels — and to
    apply nothing on the way there.
    """
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
    """`grafanaa` is not a component. One line says so, lists what is, and exits non-zero.

    Same shape as the uninstall command's answer to a typo, for the same reason: the alternative is a
    command that
    reports success on a namespace it never touched. The `KUBEMENTAT_HOME`-pointed subprocess keeps
    `main()`'s wiring in the path, so the group is really reachable from `kmt component`.
    """
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
    """The verb must be safe, not merely present.

    A cluster already running a Traefik kubementat did not install would gain a second controller, a
    second class and a second writer of `Ingress.status` if this command applied its own release —
    and helmfile would have done it in one apply, without asking. `kmt routing install` refuses this
    case (or asks for consent with `--update`); a command that installs one component beside that
    controller is the hole in it. So: refuse, apply nothing, and name the verb that can ask.
    """
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
    """Already ours means already there, and `install` is not how you upgrade it.

    The same answer `kmt routing install` gives without `--update`: a controller that is serving is
    not touched by a command named `install`, because the upgrade is a values edit and a version pin,
    and applying a release is how you find out mid-traffic.
    """
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
    """The branch where installing is correct: nothing answers, so kubementat installs its own.

    The same filter as every other component, which is the point — routing's decision chooses
    *whether*, and helmfile's label decides *what*. No routing-shaped code path is added to this
    command beyond the question it asks.
    """
    seeded_helmfile(fixture_repo)
    monkeypatch.setattr(traefik_presence, "discover",
                        lambda ctx, **kwargs: a_presence(
                            traefik_presence.CLASSIFICATION_NONE))

    result = installed(fixture_repo, "traefik")

    assert result.exit_code == 0, result.output
    assert helmfile_selectors(fake_binaries) == ["component_name=traefik"], \
        fake_binaries.commands()


def test_the_issuer_is_applied_like_any_other_component_and_is_not_asked_about(
        fixture_repo, dry_run_env, fake_binaries, monkeypatch):
    """`cert-manager` is routing's group and not routing's decision.

    The controller question is about the thing that answers Ingress, and an issuer is not that. It
    has exactly one release and no cluster read to make first, so a presence check here would be a
    warning about a class of mistake this component cannot make. `discover` is patched to blow up if
    called, and the apply is what proves it was not.
    """
    seeded_helmfile(fixture_repo)

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("cert-manager is not a controller; nothing here asks whose it is")

    monkeypatch.setattr(traefik_presence, "discover", must_not_be_called)

    result = installed(fixture_repo, "cert-manager")

    assert result.exit_code == 0, result.output
    assert helmfile_selectors(fake_binaries) == ["component_name=cert-manager"], \
        fake_binaries.commands()


# --------------------------------------------------------------------------------------------
# the guards: what the command accepts is derived, so both lists move when the tree does
# --------------------------------------------------------------------------------------------


def test_a_component_added_to_the_constants_is_accepted_by_both_verbs(fixture_repo, dry_run_env,
                                                                      fake_binaries, monkeypatch):
    """The accepted names are `DEFAULT_NAMESPACES`, read at call time.

    A component that exists only in the constants is a real kubementat component with nothing to
    apply, and the refusal that comes back must be the "no release" one and not the "not a
    component" one. That difference is the whole check: a command carrying its own list of valid
    names would reject a name the constants just added, and every test above, which uses names that
    already exist, would still pass.

    The same patched set is what the typo message has to list, which is the other direction: the
    names a typo tells you about are the ones that exist now.
    """
    extended = dict(constants.DEFAULT_NAMESPACES)
    extended["newcomer"] = "newcomer"
    monkeypatch.setattr(constants, "DEFAULT_NAMESPACES", extended)
    seeded_helmfile(fixture_repo)

    accepted = installed(fixture_repo, "newcomer")
    assert "not a kubementat component" not in accepted.output, accepted.output
    assert "component_name=newcomer" in accepted.output, accepted.output

    listed = installed(fixture_repo, "nonsense-name")
    assert "newcomer" in listed.output, listed.output


def test_a_release_added_to_the_manifest_makes_its_component_installable(fixture_repo,
                                                                        dry_run_env,
                                                                        fake_binaries):
    """The installable set comes from the manifest, not from a list of six typed somewhere.

    `goldilocks` is namespace-only in this build, so anything that knows only the six installable
    names would refuse it. Give it a release in the environment's manifest and the same command
    applies it — which is what makes the documentation true rather than permanent: the sentence that
    lists the installable components is only correct while the manifest is, and this is the test that
    notices when it changes.
    """
    manifest = seeded_helmfile(fixture_repo).read_text()
    manifest += """- name: goldilocks
  chart: fairwinds-incubator/goldilocks
  namespace: goldilocks
  labels:
    component_name: goldilocks
    group: standard
"""
    seeded_helmfile(fixture_repo, manifest)

    result = installed(fixture_repo, "goldilocks")

    assert result.exit_code == 0, result.output
    assert helmfile_selectors(fake_binaries) == ["component_name=goldilocks"], \
        fake_binaries.commands()


def test_the_live_manifest_makes_the_components_the_readme_names_installable(fake_binaries,
                                                                             monkeypatch):
    """What the tree actually declares, measured and not asserted.

    Seven of the thirteen components in `constants.DEFAULT_NAMESPACES` have a release, and the
    seventh is `polaris`, which came out of the commented block on 2026-11-01; that sentence is in the
    README, and this is the other half of it: the list, the count, and the fact that no component is
    labelled onto more than one release (the second of which would make the label filter say something
    other than what it was asked).

    The labels that name no component are the fact this row has to keep honest, because there are
    three of them now and they are not all the same kind of thing. The two prometheus exporters are
    releases under labels `constants` never named, so a `standard` group apply brings them and
    `component install prometheus` does not, which is worth knowing when a reader is told "the group
    is what a component is in".

    `alloy` is there for the third reason, and it is the one that is easy to read as a bug. The
    collector shares the loki namespace by decision, not by accident
    (`helmfile.yaml.gotmpl`, the release block's own comment), and adding an `alloy` entry to
    `constants` would be the wrong fix: it would export an `ALLOY_DEPLOYMENT_NAMESPACE` that
    helmfile never asks for, give `kmt uninstall` a namespace to delete twice, and let a rename of
    `LOKI_DEPLOYMENT_NAMESPACE` leave the collector behind the store it writes to. So alloy is
    installed by the group, swept by the namespace, and refused by `kmt component install alloy` for
    the same reason the exporters are refused. If that ever looks wrong, the thing to change is this
    paragraph, not the map.
    """
    # One `Context`. What this reads is the live manifest, not a configuration, so the object
    # is `cluster_fixture`'s configuration-only shape: a real `Config`, lazy, and no claim on a
    # cluster this test is not addressing.
    # The `Config` is here because `HelmUtils` takes a `Context` and a `Context` carries one; this
    # read is of the live helmfile, and "no root named" is a refusal rather than a
    # checkout, so the root is named as the unread thing it is.
    declared = HelmUtils(configuration_only_context(
        "dev", "dev1", Config("dev", "dev1", platform_config_dir=UNREAD_ROOT,
                              preload_configs=False))).component_release_index(LIVE_HELMFILE)

    installable = sorted(name for name in constants.DEFAULT_NAMESPACES if declared.get(name))
    assert installable == ["cert-manager", "grafana", "loki", "openbao", "polaris", "prometheus",
                           "traefik"], \
        installable
    assert not [name for name, releases in declared.items() if len(releases) > 1], declared
    assert sorted(label for label in declared if label not in constants.DEFAULT_NAMESPACES) == \
        ["alloy", "prometheus-blackbox-exporter", "prometheus-helm-exporter"]
    assert len(constants.DEFAULT_NAMESPACES) == 13
