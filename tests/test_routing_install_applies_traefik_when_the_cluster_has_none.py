"""No Traefik on the cluster: `kmt routing install` installs it, through the whole routing group.

This is the `none` case, and the row the everyday lane cannot show -- a lane
running on k3s finds the node's bundled controller first, so the install branch has to be proved on
its own, against a cluster that answers "nothing". What "nothing" costs is the interesting part: the
command has to select the whole group, not a narrower filter, and it must not run the CRD step, which
belongs to `--update` alone.

No cluster is needed to read this file, and none is used: the three discovery reads are answered the
way a cluster answers them (`tests/shims/` on `PATH`, the same recorder the uninstall sweep is judged
against), and the real `Config`, `HelmUtils` and `kubementat.infrastructure.shell` run on top of them.
"""

import json
import logging
from types import SimpleNamespace

import click
import pytest
import yaml

from conftest import as_k8s_object, cluster_state
from kubementat import helm_utils
from kubementat.infrastructure import shell
from kubementat.core.identity.context import Context
from kubementat.automations.routing import routing

# What the shims print for `helm show crds traefik/traefik`: one definition, in the shape that read
# prints. It is never applied in this file's cases, and its presence here is what lets a test say
# "the definitions were not touched" by checking that nothing was handed to `kubectl apply`.
CRD_DOCUMENT = ("apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\n"
                "metadata:\n  name: middlewares.traefik.io\n")

ACME_CONTACT = "platform-ops@example.test"

# kubementat's own configured answers, so no test here reads this checkout's `platform_config`. The
# namespace is left to `constants.DEFAULT_NAMESPACES` on purpose: it is the fallback the command is
# required to warn about and use, and the case where the configuration renames it is a different
# file's problem (`test_component_namespaces_come_from_configuration.py`).


def cluster(fake_binaries, recorded_api, monkeypatch, helm=(), classes=(), pods=(),
            crds=CRD_DOCUMENT, crds_exit=0):
    """The reads answered on the channel each one travels, plus every binary the install reaches.

    The release read is a command, so the shims answer it; the class and pod reads are client
    calls now that the routing probes go through the client, so the recorder does. Writing all three
    as shims now would
    leave the install reading a cluster that answers in one channel and refuses in the other, and the
    decision this file judges would be a decision about a cluster that does not exist.

    Every shim response is keyed by subcommand, which is what keeps one program's two different
    questions apart: `helm list` is a different file from `helm show crds`, and `kubectl apply` is
    not answered by `helm` at all. A program-wide file would win over both (the shims try the least
    specific answer first), so none is written here for `helm` or `kubectl`.
    """
    fake_binaries.respond("helm", json.dumps(list(helm)), subcommand="list")
    fake_binaries.respond("helm", "", subcommand="repo.update")
    fake_binaries.respond("helm", crds, subcommand="show.crds", exit_code=crds_exit)
    fake_binaries.respond("kubectl", "", subcommand="apply")
    fake_binaries.respond("helmfile", "")

    cluster_state(recorded_api, "ingressclass", items=list(classes))
    recorded_api.answer("list_pod_for_all_namespaces",
                        lambda **kwargs: SimpleNamespace(
                            items=[as_k8s_object(item) for item in pods]))

    # One log for both channels, so a claim about *when* a read happened can still be made.
    #
    # The shims already record every command, but they record it out of process and in their own
    # order, and the client calls land in the recorder's. Two logs can say what was asked; only a
    # shared one can say that a read came after a write, which is the claim
    # `test_the_solver_points_at_a_class_something_serves_only_after_the_release_is_applied` makes.
    started = []
    run = shell.run

    def in_order(argv, **kwargs):
        started.append(["command", *argv])
        return run(argv, **kwargs)

    for method in ("list_ingress_class", "list_pod_for_all_namespaces"):
        recorded_api.answer(method, _recorded(recorded_api.answers[method], started, method))
    monkeypatch.setattr(shell, "run", in_order)
    fake_binaries.started = started
    return fake_binaries


def _recorded(answer, started, method):
    """The answer the recorder would have given, with the moment it was asked written down first.

    The wrapper replaces the answer and calls the original, so the cluster's reply is untouched and
    only the ordering is added to it.
    """
    def asked(**kwargs):
        started.append(["api", method])
        return answer(**kwargs)
    return asked


def helm_list_read():
    """The release read as this run issues it: the statuses helm answers, addressed to the cluster.

    Spelled from the product's own constant plus the address flags rather than hard-coded, because
    this file is about the routing decision and not about flag plumbing -- but the read has to be
    compared to *something*, and comparing it to a bare `helm list` would quietly assert that the
    address flags are not on it.
    """
    from kubementat.helm_utils import helm_list_releases_argv
    return helm_list_releases_argv(Context.build("dev", "fixture").target)


def started_in_order(fake_binaries):
    """Everything the run asked of anything, commands and client calls in the order they were made."""
    return fake_binaries.started


def configured(fixture_repo, **overrides):
    """`platform_config/dev/static.json` with the keys this command reads, and nothing else.

    `CLUSTER_MANAGER_EMAIL` is one of them: both ClusterIssuers register an ACME account with it, so
    a fixture that left it out would be a test of the error branch rather than of the install.
    """
    fixture_repo.environment_static(CLUSTER_MANAGER_EMAIL=ACME_CONTACT, **overrides)
    return fixture_repo


def commands(fake_binaries):
    """Every command the shims were asked to run, in order."""
    return fake_binaries.commands()


def helmfile_filters(fake_binaries):
    """The `-l` every helmfile apply was given -- which is where the decision lives."""
    return [argv[argv.index("-l") + 1] for argv in commands(fake_binaries)
            if argv[:1] == ["helmfile"] and "-l" in argv]


def documents_applied(fake_binaries, kind):
    """The manifests that reached a `kubectl apply -f -`, parsed back out of what was piped in.

    The shims record stdin, so this reads the same YAML the cluster would have received rather than
    the Python dictionary it was built from -- which is the only way to check what the solver field
    was spelled as.
    """
    applied = []
    for call in fake_binaries.calls():
        if call.argv[:2] != ["kubectl", "apply"] or not call.stdin:
            continue
        if f"kind: {kind}\n" not in call.stdin:
            continue
        applied.append(yaml.safe_load(call.stdin))
    return applied


def issued(fake_binaries):
    """The two ClusterIssuers, in the order they were applied."""
    return documents_applied(fake_binaries, "ClusterIssuer")


def solver_classes(issuers):
    """The `ingressClassName` each issuer's solver points at. There is only one place it comes from."""
    return [issuer["spec"]["acme"]["solvers"][0]["http01"]["ingress"]["ingressClassName"]
            for issuer in issuers]


def install(fake_binaries, **flags):
    """`install_routing` run for real: one resolved `Context`, real `Config`, real `HelmUtils`,
    real `shell.run`.

    The `Context` is `Context.build` and not a fabricated one, because the point of this file is
    what the routing group *does* — every read and write it makes is the product's — and the
    cluster sits inside the object that decides them. `fake_binaries` is unused here: the shims are
    installed by the fixture, and this is the call they record.
    """
    return routing.install_routing(Context.build("dev", "fixture"), **flags)


@pytest.fixture
def a_clean_cluster(fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """A cluster that answers all three reads with nothing, and a config that is complete.

    No releases, no IngressClasses, no pods. That is the state a freshly provisioned cluster without
    an ingress controller is in, and the only state in which kubementat puts one there -- stated once
    and on both channels, because two of those reads do not travel as commands any more.
    """
    configured(fixture_repo)
    return cluster(fake_binaries, recorded_api, monkeypatch)


# ---------------------------------------------------------------------------------------
# the decision, and what it costs
# ---------------------------------------------------------------------------------------


def test_a_cluster_with_no_traefik_gets_one_through_the_whole_routing_group(a_clean_cluster,
                                                                            caplog):
    """`none` installs: filter `group=routing`, no exclusion, and the release applied.

    The filter is the whole expression of the decision, so it is asserted as the exact string rather
    than as "something containing group=routing": the skip case below differs from this one by one
    clause, and a looser check could not tell the two apart.
    """
    with caplog.at_level(logging.INFO):
        result = install(a_clean_cluster)

    assert result.action == routing.ACTION_INSTALL
    assert result.installs_traefik is True
    assert result.classification == "none"
    assert helmfile_filters(a_clean_cluster) == ["group=routing"]
    assert result.label_filter == "group=routing"
    assert result.releases_applied is True


def test_the_command_that_installs_a_controller_does_not_apply_the_chart_crds(a_clean_cluster,
                                                                              caplog):
    """The definitions belong to `--update`, and to nothing else.

    On a first install the chart's own `crds/` land with `helm install`, so a CRD step here would be
    a second opinion about the same objects, and one that could disagree with the release about which
    version of them is authoritative. `helm show crds` and `--server-side` are both absent.
    """
    with caplog.at_level(logging.INFO):
        result = install(a_clean_cluster)

    assert result.crds_applied is False
    started = commands(a_clean_cluster)
    assert not [argv for argv in started if argv[:2] == ["helm", "show"]], \
        "the chart's definitions were read even though nothing asked for them"
    assert not [argv for argv in started if "--server-side" in argv], \
        "a CRD apply ran on a command that did not ask for an upgrade"
    assert "--update" not in " ".join(" ".join(argv) for argv in started)


def test_no_discovery_read_was_skipped_just_because_nothing_was_there(a_clean_cluster):
    """Three reads, in order, even when the first one could have decided it alone.

    A controller installed from plain YAML answers in the second or third read and not in the first,
    so an install that trusted `helm list` alone would install a second controller onto a cluster
    that already runs one. This is the cheapest place to notice the difference.
    """
    install(a_clean_cluster)

    asked = started_in_order(a_clean_cluster)
    releases_read = [entry[1:] for entry in asked
                     if entry[0] == "command" and entry[1:3] == ["helm", "list"]]
    # Two, and both are the same question: discovery asks once, and `helmfile_apply` asks once more
    # immediately before it writes, because it refuses to move a release that is already running.
    # This fixture used to have no manifest for the apply to read releases out of, so the second
    # question went unasked -- which is the same behaviour the check that owns it has always had on a
    # tree that did have a manifest, and not a new read.
    assert len(releases_read) == 2, f"the release read was made {len(releases_read)} times: {asked}"
    assert all(entry == helm_list_read() for entry in releases_read), asked

    # The three questions discovery asks, in the order it asks them -- and then the class read the
    # verification step makes after the release has been applied, which is the fourth entry here and
    # whose position is what `test_the_solver_points_at_a_class_something_serves_only_after_the_
    # release_is_applied` is about. Naming it in this test is what keeps "three reads" honest: the
    # count is three *discovery* reads, not three API calls, and a test that counted the way the
    # client logs them would report a second discovery pass that never happened.
    reads = [entry[1] for entry in asked if entry[0] == "api"]
    assert reads[:2] == ["list_ingress_class", "list_pod_for_all_namespaces"], asked
    assert len(reads) == 3, f"discovery was asked {len(reads)} API questions, not two: {asked}"


# ---------------------------------------------------------------------------------------
# the things that must be applied alongside the controller
# ---------------------------------------------------------------------------------------


def test_cert_manager_is_in_the_apply_because_the_skip_is_a_selector_not_a_branch(a_clean_cluster):
    """cert-manager rides in the same selection that Traefik does.

    The claim is about the filter and not about cert-manager's argv: nothing in this command names
    cert-manager, it is one release in `group=routing` and helmfile decides. What is asserted is that
    the routing group is what was selected, and that no narrower filter was tried -- because the
    failure mode the plan warns about is a routing group suppressed as a whole, which would leave the
    platform without an issuer while the log said the install succeeded.
    """
    install(a_clean_cluster)

    assert helmfile_filters(a_clean_cluster) == ["group=routing"]
    applied = [argv for argv in commands(a_clean_cluster) if argv[:1] == ["helmfile"]]
    assert len(applied) == 1, \
        f"the routing group was applied {len(applied)} times, which is how a group gets applied " \
        "twice in one run"
    assert not [argv for argv in commands(a_clean_cluster) if "-l" in argv and "standard" in argv], \
        "a narrower or different group was selected; cert-manager would not have been reached"


def test_both_cluster_issuers_are_applied_pointing_at_the_class_from_the_configuration(
        a_clean_cluster):
    """The solver, in the field Traefik actually serves.

    `ingressClassName` names the IngressClass; the older `class` field writes the
    `kubernetes.io/ingress.class` annotation, which Traefik's default provider does not read, so a
    solver written that way creates an Ingress nothing routes and a certificate that never arrives.
    Both issuer names are kept, because `create_ingress_yml.sh` and both EventListener templates
    reference them by string.
    """
    install(a_clean_cluster)

    issuers = issued(a_clean_cluster)
    assert [issuer["metadata"]["name"] for issuer in issuers] == ["letsencrypt-staging",
                                                                  "letsencrypt"]
    assert solver_classes(issuers) == ["traefik", "traefik"]
    assert all("class" not in issuer["spec"]["acme"]["solvers"][0]["http01"]["ingress"]
               for issuer in issuers), "the old solver field is back; nothing serves through it"
    assert all(issuer["spec"]["acme"]["email"] == ACME_CONTACT for issuer in issuers)


def test_the_solver_points_at_a_class_something_serves_only_after_the_release_is_applied(
        a_clean_cluster, caplog):
    """The verification read comes last, and comes after everything that could change the cluster.

    A warning fired before the release apply is a warning about the cluster that was there when the
    command started, which on a clean cluster is exactly the wrong answer: the class it is checking
    for is the one the release about to run creates. So the class read is the last call made.
    """
    with caplog.at_level(logging.WARNING):
        install(a_clean_cluster)

    asked = started_in_order(a_clean_cluster)
    assert [entry for entry in asked if entry[0] == "api"][-1] == ["api",
                                                                  "list_ingress_class"], asked
    # `list.index` would answer with discovery's probe here, because the same argv is read twice --
    # once before anything is decided, once after the issuers are applied -- and the first of those
    # is the *earliest* thing in the run. The claim this test is written to keep is about the second
    # read: the one whose warning the operator reads. So both sides of the comparison use the *last*
    # index at which they occur, and the read that produced the warning is the one being placed.
    applied = [index for index, entry in enumerate(asked)
               if entry[0] == "command" and entry[1] == "helmfile"]
    read = max((index for index, entry in enumerate(asked) if entry[0] == "api"), default=None)
    assert applied and read is not None and read > max(applied), (
        f"the class read at {read} did not come after the release apply at {applied}: {asked}")


def test_the_routing_group_is_applied_through_helmfile_and_not_a_helm_command(a_clean_cluster):
    """No `helm install`, and no `helm upgrade`, anywhere.

    `HelmUtils.helmfile_apply` is what exports the namespace variables before helmfile runs, which is
    the only reason a release lands in the namespace the configuration named. Bypassing it is how a
    controller ends up installed into the wrong namespace while every reader of the configuration
    looks somewhere else -- and the shims see both spellings.
    """
    install(a_clean_cluster)

    started = commands(a_clean_cluster)
    assert not [argv for argv in started if argv[:2] == ["helm", "install"]], started
    assert not [argv for argv in started if argv[:2] == ["helm", "upgrade"]], started
    assert not [argv for argv in started if argv[:2] == ["helm", "uninstall"]], started


# ------------------------------------------------------------------------ the address, when there is none
#
# Both cases below are the error branch `configured()` above works to avoid, and they belong here rather
# than in a file of their own because the fixture that makes the install possible is the same fixture
# that has to be taken away to make the refusal happen. Two of this repository's lane runs were spent
# discovering that this branch had never been executed anywhere, unit or VM: the key is required by a
# command and answered by nothing, so a lane that installs routing stops where an operator would have
# read a sentence and kept going, and a lane that does not install routing never arrives at all.


def no_acme_contact(fixture_repo, value=...):
    """A tree that does not answer the ACME address.

    `value` chooses the two shapes the absence takes: left out of the file, which is what a directory
    out of `config init` is, or present as the shipped placeholder, which is what a tree copied from
    a template is. Both have to refuse, and a test that covered only one of them would leave the
    placeholder path -- the one that reads as though it were answered -- untested.
    """
    if value is ...:
        fixture_repo.environment_static()
        written = json.loads(
            (fixture_repo.platform_config / "dev" / "static.json").read_text())
        written.pop("CLUSTER_MANAGER_EMAIL", None)
        fixture_repo.write_json(fixture_repo.platform_config / "dev" / "static.json", written)
    else:
        fixture_repo.environment_static(CLUSTER_MANAGER_EMAIL=value)
    return fixture_repo


def test_an_issuer_cannot_be_applied_without_a_contact_to_register_the_account_with(a_clean_cluster,
                                                                                     fixture_repo):
    """No address: the install refuses, names the key and the file, and writes nothing.

    The claim is not that routing is picky. cert-manager registers the ACME account the moment the
    issuer is applied, and a blank or placeholder contact is accepted at apply time and rejected only
    when a certificate is issued, which is an error that stays invisible until something that depends
    on it expires. So the alternative to refusing is not an uglier install; it is an install that
    reports success and fails later, and the refusal has to arrive before the first write for the word
    "nothing" to mean anything.
    """
    no_acme_contact(fixture_repo)

    with pytest.raises(click.ClickException) as refused:
        install(a_clean_cluster)

    sentence = str(refused.value)
    assert "CLUSTER_MANAGER_EMAIL" in sentence, sentence
    assert "static.json" in sentence, "the refusal named no file for the operator to fix"
    assert not issued(a_clean_cluster), \
        "an issuer reached the cluster with no address to register it under"


def test_the_refusal_stops_the_issuer_and_not_the_releases_it_would_have_needed(a_clean_cluster,
                                                                                fixture_repo):
    """The boundary of the refusal is the object that carries the contact, and nothing above it.

    This is a decision and not an accident of ordering, so it is asserted rather than read off the
    source: the Traefik and cert-manager releases go in, and only the two ClusterIssuers stop. A
    controller needs no ACME account to exist, cert-manager has to be present before any issuer can be
    registered with it, and the install is re-run for a reason -- so stopping the whole group because
    the last object in it is refused would leave a cluster that still needs the same two releases
    applied by the next run. The test that matters is that the dangerous write is the one withheld, and
    a stricter claim would be a claim about tidiness rather than about correctness.
    """
    no_acme_contact(fixture_repo)

    with pytest.raises(click.ClickException):
        install(a_clean_cluster)

    started = commands(a_clean_cluster)
    assert [argv for argv in started if argv[:1] == ["helmfile"]], \
        "the releases were withheld too, which is a cluster that has to be finished by hand"
    assert not issued(a_clean_cluster), issued(a_clean_cluster)


def test_a_placeholder_address_is_refused_the_way_an_absent_one_is(a_clean_cluster, fixture_repo):
    """`REPLACEME_...` reads like an answer, which is exactly why it has to fail like one.

    The shipped template carries the address as a sentinel that is a non-empty string, so a check on
    truthiness alone lets it through and hands cert-manager a contact that is not an address. This is
    the case that a lane cannot produce -- a lane writes a real address -- and the reason it is tested
    on the unit side rather than left to the VM.
    """
    no_acme_contact(fixture_repo, "REPLACEME_CLUSTER_MANAGER_EMAIL")

    with pytest.raises(click.ClickException) as refused:
        install(a_clean_cluster)

    assert "CLUSTER_MANAGER_EMAIL" in str(refused.value), str(refused.value)
    assert not issued(a_clean_cluster), "the placeholder was registered as a contact"
