"""The pause between the Pipelines release and the releases that are validated through it.

`kubectl apply` returns when the objects are accepted. For three of the four manifests kubementat
applies that is the whole story. For the Pipelines release it is not: the objects it creates include
the webhook configuration that tells the API server to call
`tekton-pipelines-webhook.<namespace>.svc` before it will accept a Triggers object, so the apply has
finished and the pod behind it has not. The E2E suite caught exactly this on a reinstall and the
failure landed on the *next* command:

    error when creating ".../triggers/previous/v0.37.2/release.yaml": Internal error occurred:
    failed calling webhook "config.webhook.pipeline.tekton.dev": Post
    "https://tekton-pipelines-webhook.tekton-pipelines.svc:443/config-validation?timeout=10s":
    no endpoints available for service "tekton-pipelines-webhook"

Three things are asserted here, and none of them is "it sleeps":

1. A cluster that already answers gets no waiting at all — one read, no delay.
2. A cluster that is not ready yet is waited on, and the releases that depend on the webhook are
   applied the moment it is, in the order they were meant to go in.
3. A webhook that never comes up stops the install and says which Service, which namespace, and how
   long it was given — rather than letting the API server report a webhook failure two commands
   later, pointing at a release that is not the thing that is broken.

The timeout constants are shortened rather than worked around: the bound is the behaviour under
test, and a test that slept the real two minutes would be a test that only runs when nobody is
watching.
"""

import time

import pytest
from kubernetes.client import V1EndpointAddress, V1EndpointSubset, V1Endpoints, V1ObjectMeta

from kubementat import tekton_utils
from kubementat.tekton_utils import (HELM_DEPLOYER_CLUSTER_ROLE_NAME, PIPELINE_WEBHOOK_SERVICE_NAME,
                                     TEKTON_RELEASE_BASE_URL, TektonUtils,
                                     TektonWebhookNotReachableError)
from conftest import is_apply
from cluster_fixture import configuration_only_context

VERSION_KEYS = ("TEKTON_VERSION_PIPELINE", "TEKTON_VERSION_TRIGGERS", "TEKTON_VERSION_DASHBOARD")
PINS = dict(zip(VERSION_KEYS, ("1.17.1", "0.37.2", "0.73.0")))

NAMESPACE = "tekton-pipelines"

# The URLs, abbreviated: which of these went in is the whole question this file asks.
PIPELINE_URL = f"{TEKTON_RELEASE_BASE_URL}/pipeline/previous/v{PINS['TEKTON_VERSION_PIPELINE']}/release.yaml"
DASHBOARD_URL = (f"{TEKTON_RELEASE_BASE_URL}/dashboard/previous/"
                 f"v{PINS['TEKTON_VERSION_DASHBOARD']}/release-full.yaml")


def applied_urls(fake_binaries):
    return [call[-1] for call in fake_binaries.commands()
            if is_apply(call) and call[-1].startswith("https://")]


def endpoints(answer) -> V1Endpoints:
    """A cluster's answer to "what is behind this Service", as the client would hand it back.

    A test spells an address as a plain string; the client hands back `V1EndpointAddress` objects,
    so the conversion happens here rather than in every test. What a test is really deciding is
    whether the list is empty, because that is the state the wait acts on.
    """
    def read(name=None, namespace=None, **kwargs):
        addresses = answer() if callable(answer) else answer
        return V1Endpoints(
            metadata=V1ObjectMeta(name=name, namespace=namespace),
            subsets=[V1EndpointSubset(
                addresses=[V1EndpointAddress(ip=str(a)) for a in addresses])])

    return read


def short_deadlines(monkeypatch):
    """The real bound and the real interval, shrunk to milliseconds.

    Only the *numbers* change. The loop, the read it makes and the error it raises on expiry are
    the ones that run in production.
    """
    monkeypatch.setattr(tekton_utils, "WEBHOOK_READY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(tekton_utils, "WEBHOOK_READY_POLL_SECONDS", 0.02)
    return 0.3


def install(fixture_repo, recorded_api, fake_binaries, answer):
    """`TektonUtils.install()` against a cluster whose webhook answers however `answer` says.

    The clock is what is under test as much as the code is, so the install is timed: a test that
    claimed to wait while sleeping zero would pass for the wrong reason.
    """
    fixture_repo.environment_static(**PINS)
    recorded_api.answer("read_namespaced_endpoints", endpoints(answer))
    started = time.monotonic()
    TektonUtils(configuration_only_context(fixture_repo.environment, fixture_repo.team)).install()
    return time.monotonic() - started


def test_a_cluster_whose_webhook_answers_needs_no_waiting(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The fast path, asserted as a cost: one read, and no sleep on top of it.

    The wait is a gate, not a tax. A rerun against a healthy cluster pays one API call and gets the
    same four `kubectl apply` calls it always got, so the fix does not slow down the install that
    already worked.
    """
    elapsed = install(fixture_repo, recorded_api, fake_binaries,
                      lambda: ["10.42.0.1"])

    assert [method for method, _ in recorded_api.calls].count(
        "read_namespaced_endpoints") == 1, "a ready webhook is read once, not polled"
    assert elapsed < 0.5, "an already-reachable webhook must not cost the install a single sleep"
    assert applied_urls(fake_binaries)[0] == PIPELINE_URL
    assert DASHBOARD_URL in applied_urls(fake_binaries)


def test_the_releases_that_call_the_webhook_are_applied_the_moment_it_is_there(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """A webhook that arrives between two reads is waited for, and the order is not disturbed.

    The first read comes back empty — the pod is still being scheduled — and the second one is
    ready. The install must continue into the remaining releases rather than stopping at the first
    empty answer, and it must still put the control plane in before the things that read it.
    """
    short_deadlines(monkeypatch)
    reads = []

    def arriving():
        reads.append(1)
        return [] if len(reads) == 1 else ["10.42.0.1"]

    fixture_repo.environment_static(**PINS)
    recorded_api.answer("read_namespaced_endpoints", endpoints(arriving))
    TektonUtils(configuration_only_context(fixture_repo.environment, fixture_repo.team)).install()

    urls = applied_urls(fake_binaries)
    assert urls.index(PIPELINE_URL) == 0, "the control plane still goes in first"
    assert DASHBOARD_URL in urls and len(urls) == 4, \
        "the install stopped at the first empty answer instead of waiting for the webhook"


def test_a_webhook_that_never_arrives_stops_the_install_and_names_itself(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """The failure mode: nothing behind the Service, forever.

    The old behaviour was a `kubectl apply` of the Triggers release failing with a webhook call.
    This asserts the replacement: no further release is applied, and the error says what is not
    answering, where, and for how long it was given.
    """
    bound = short_deadlines(monkeypatch)

    with pytest.raises(TektonWebhookNotReachableError) as error:
        install(fixture_repo, recorded_api, fake_binaries, lambda: [])

    message = str(error.value)
    assert PIPELINE_WEBHOOK_SERVICE_NAME in message, "the Service nobody can name is the bug"
    assert NAMESPACE in message, "the operator has to know where to look"
    assert str(bound) in message, "the bound is the thing that expired; it belongs in the sentence"
    assert applied_urls(fake_binaries) == [PIPELINE_URL], \
        "releases that are validated through the webhook were applied anyway"


def test_the_wait_holds_the_line_it_was_given(fixture_repo, dry_run_env, fake_binaries,
                                             recorded_api, monkeypatch):
    """It really waited, rather than failing fast on the first empty answer.

    With a bound of 300 ms and a poll of 20 ms, giving up immediately would be the cheapest way to
    pass the test above, so the elapsed time is the assertion: the loop ran the bound out.
    """
    bound = short_deadlines(monkeypatch)
    started = time.monotonic()

    with pytest.raises(TektonWebhookNotReachableError):
        install(fixture_repo, recorded_api, fake_binaries, lambda: [])

    assert time.monotonic() - started >= bound, "the install gave up before its ceiling"


def test_the_read_that_answers_the_question_does_not_call_a_missing_service_an_error(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """A Service that has no Endpoints object yet is `not ready`, not a crash.

    The very first read after an apply can 404 — the service controller has not written anything.
    Raising a stack trace there would turn the normal fast path of a cold cluster into a failure,
    so the read swallows only 404 and the wait decides.
    """
    from kubernetes import client

    bound = short_deadlines(monkeypatch)
    fixture_repo.environment_static(**PINS)
    recorded_api.answer("read_namespaced_endpoints",
                        client.exceptions.ApiException(status=404, reason="Not Found"))
    started = time.monotonic()

    with pytest.raises(TektonWebhookNotReachableError):
        TektonUtils(configuration_only_context(fixture_repo.environment, fixture_repo.team)).install()

    assert time.monotonic() - started >= bound, \
        "a 404 was treated as an error rather than as 'nothing here yet'"


def test_the_wait_is_aimed_at_the_namespace_the_control_plane_landed_in(
        fixture_repo, dry_run_env, recorded_api, monkeypatch):
    """The question is asked of the namespace the configuration chose, not a literal.

    `kmt install` puts the control plane where `TEKTON_DEPLOYMENT_NAMESPACE` says, and the webhook
    is in the same place; a wait that read `tekton-pipelines` by name would report "not ready" on a
    cluster that is perfectly ready somewhere else.
    """
    asked = []
    fixture_repo.environment_static(TEKTON_DEPLOYMENT_NAMESPACE="team-mesh", **PINS)

    def record(name=None, namespace=None, **kwargs):
        asked.append((name, namespace))
        return V1Endpoints(metadata=V1ObjectMeta(name=name, namespace=namespace),
                           subsets=[V1EndpointSubset(
                               addresses=[V1EndpointAddress(ip="10.42.0.2")])])

    monkeypatch.setattr(tekton_utils, "WEBHOOK_READY_POLL_SECONDS", 0.01)
    recorded_api.answer("read_namespaced_endpoints", record)
    TektonUtils(configuration_only_context(fixture_repo.environment, fixture_repo.team)).install()

    assert (PIPELINE_WEBHOOK_SERVICE_NAME, "team-mesh") in asked, \
        f"the webhook was looked for somewhere else: {asked}"


def test_the_install_still_writes_the_role_it_always_wrote(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The wait is a pause, not a new step: one ClusterRole write, exactly as before.

    Guarding the shape a reader of the log expects. `helm-deployer-cluster-role` is written once per
    install and nothing about the webhook wait changes that, so a second write here would mean the
    pause was mistaken for a retry.
    """
    install(fixture_repo, recorded_api, fake_binaries, lambda: ["10.42.0.1"])

    writes = [method for method, _ in recorded_api.calls
              if method.endswith("cluster_role")]
    assert writes == ["create_cluster_role"], \
        f"the ClusterRole was written more than once: {writes}"
    assert recorded_api.calls[0][1]["body"].metadata.name == HELM_DEPLOYER_CLUSTER_ROLE_NAME
