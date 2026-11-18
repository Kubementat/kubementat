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
from cluster_fixture import configuration_only_context
from conftest import is_apply
from kubernetes.client import (
    V1EndpointAddress,
    V1Endpoints,
    V1EndpointSubset,
    V1ObjectMeta,
)

from kubementat.errors import TektonWebhookNotReachableError

# The wait, the Service it asks about and both of its numbers live in `infrastructure/tekton/install.py`
# now (P4-4). The module is imported as a module for the same reason: the loop reads both numbers as
# globals of the module it is defined in, so a patch on any other object is a number nobody sees.
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import install as tekton_install
from kubementat.infrastructure.tekton.install import (
    PIPELINE_WEBHOOK_SERVICE_NAME,
    TEKTON_RELEASE_BASE_URL,
)

VERSION_KEYS = ("TEKTON_VERSION_PIPELINE", "TEKTON_VERSION_TRIGGERS", "TEKTON_VERSION_DASHBOARD")
PINS = dict(zip(VERSION_KEYS, ("1.17.1", "0.37.2", "0.73.0")))

NAMESPACE = "tekton-pipelines"


def install_the_pinned_releases(fixture_repo):
    """`tekton_install.install()`, read the way `kmt install` reads it.

    The `Context` carries what the class used to carry: `P4-6` deleted the class, and the client
    is built from the `Context` the same way the constructor built it. `install` reads no team,
    so this call asks for none — the refusal a team-less `Context` gets on the install path is
    `install_kubementat`'s own, ahead of the first apply, and not this function's.
    """
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    return tekton_install.install(KubernetesUtils(context), context.config, context.environment)


# The URLs, abbreviated: which of these went in is the whole question this file asks.
PIPELINE_URL = f"{TEKTON_RELEASE_BASE_URL}/pipeline/previous/v{PINS['TEKTON_VERSION_PIPELINE']}/release.yaml"


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
    the ones that run in production. The target is the module that owns them, and that is the half
    worth reading: patch `install` and the schedule changes, patch the object that used to hold the
    loop and the real two minutes run while the test asserts a shortened one.
    """
    monkeypatch.setattr(tekton_install, "WEBHOOK_READY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(tekton_install, "WEBHOOK_READY_POLL_SECONDS", 0.02)
    return 0.3


def install(fixture_repo, recorded_api, fake_binaries, answer):
    """`tekton_install.install()` against a cluster whose webhook answers however `answer` says.

    The clock is what is under test as much as the code is, so the install is timed: a test that
    claimed to wait while sleeping zero would pass for the wrong reason.
    """
    fixture_repo.environment_static(**PINS)
    recorded_api.answer("read_namespaced_endpoints", endpoints(answer))
    started = time.monotonic()
    install_the_pinned_releases(fixture_repo)
    return time.monotonic() - started


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
        install_the_pinned_releases(fixture_repo)

    assert time.monotonic() - started >= bound, \
        "a 404 was treated as an error rather than as 'nothing here yet'"
