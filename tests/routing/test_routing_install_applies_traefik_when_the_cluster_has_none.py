"""No Traefik on the cluster: `kmt routing install` installs it, through the whole routing group."""

import json
from types import SimpleNamespace

import pytest
import yaml
from conftest import as_k8s_object, cluster_state

from kubementat.automations.routing import routing
from kubementat.core.identity.context import Context
from kubementat.infrastructure import shell

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
    """The reads answered on the channel each one travels, plus every binary the install reaches."""
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
    """The answer the recorder would have given, with the moment it was asked written down first."""
    def asked(**kwargs):
        started.append(["api", method])
        return answer(**kwargs)
    return asked


def helm_list_read():
    """The release read as this run issues it: the statuses helm answers, addressed to the cluster.
    """
    from kubementat.infrastructure.helm import helm_list_releases_argv
    return helm_list_releases_argv(Context.build("dev", "fixture").target)


def started_in_order(fake_binaries):
    """Everything the run asked of anything, commands and client calls in the order they were made.
    """
    return fake_binaries.started


def configured(fixture_repo, **overrides):
    """`platform_config/dev/static.json` with the keys this command reads, and nothing else."""
    fixture_repo.environment_static(CLUSTER_MANAGER_EMAIL=ACME_CONTACT, **overrides)
    return fixture_repo


def commands(fake_binaries):
    """Every command the shims were asked to run, in order."""
    return fake_binaries.commands()


def documents_applied(fake_binaries, kind):
    """The manifests that reached a `kubectl apply -f -`, parsed back out of what was piped in."""
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
    """The `ingressClassName` each issuer's solver points at. There is only one place it comes from.
    """
    return [issuer["spec"]["acme"]["solvers"][0]["http01"]["ingress"]["ingressClassName"]
            for issuer in issuers]


def install(fake_binaries, **flags):
    """`install_routing` run for real: one resolved `Context`, real `Config`, real `HelmUtils`, real
    `shell.run`.
    """
    return routing.install_routing(Context.build("dev", "fixture"), **flags)


@pytest.fixture
def a_clean_cluster(fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """A cluster that answers all three reads with nothing, and a config that is complete."""
    configured(fixture_repo)
    return cluster(fake_binaries, recorded_api, monkeypatch)


# ---------------------------------------------------------------------------------------
# the decision, and what it costs
# ---------------------------------------------------------------------------------------


def test_no_discovery_read_was_skipped_just_because_nothing_was_there(a_clean_cluster):
    """Three reads, in order, even when the first one could have decided it alone."""
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


def test_both_cluster_issuers_are_applied_pointing_at_the_class_from_the_configuration(
        a_clean_cluster):
    """The solver, in the field Traefik actually serves."""
    install(a_clean_cluster)

    issuers = issued(a_clean_cluster)
    assert [issuer["metadata"]["name"] for issuer in issuers] == ["letsencrypt-staging",
                                                                  "letsencrypt"]
    assert solver_classes(issuers) == ["traefik", "traefik"]
    assert all("class" not in issuer["spec"]["acme"]["solvers"][0]["http01"]["ingress"]
               for issuer in issuers), "the old solver field is back; nothing serves through it"
    assert all(issuer["spec"]["acme"]["email"] == ACME_CONTACT for issuer in issuers)


# ------------------------------------------------------------------------ the address, when there is none
#
# Both cases below are the error branch `configured()` above works to avoid, and they belong here rather
# than in a file of their own because the fixture that makes the install possible is the same fixture
# that has to be taken away to make the refusal happen. Two of this repository's lane runs were spent
# discovering that this branch had never been executed anywhere, unit or VM: the key is required by a
# command and answered by nothing, so a lane that installs routing stops where an operator would have
# read a sentence and kept going, and a lane that does not install routing never arrives at all.
