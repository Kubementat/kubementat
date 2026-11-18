"""The pause between the Pipelines release and the releases that are validated through it."""

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

# The wait reads its two numbers as globals of `infrastructure/tekton/install.py`, so the patches land there.
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
    """`tekton_install.install()`, read the way `kmt install` reads it."""
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    return tekton_install.install(KubernetesUtils(context), context.config, context.environment)


# The URLs, abbreviated: which of these went in is the whole question this file asks.
PIPELINE_URL = f"{TEKTON_RELEASE_BASE_URL}/pipeline/previous/v{PINS['TEKTON_VERSION_PIPELINE']}/release.yaml"


def applied_urls(fake_binaries):
    return [call[-1] for call in fake_binaries.commands()
            if is_apply(call) and call[-1].startswith("https://")]


def endpoints(answer) -> V1Endpoints:
    """A cluster's answer to "what is behind this Service", as the client would hand it back."""
    def read(name=None, namespace=None, **kwargs):
        addresses = answer() if callable(answer) else answer
        return V1Endpoints(
            metadata=V1ObjectMeta(name=name, namespace=namespace),
            subsets=[V1EndpointSubset(
                addresses=[V1EndpointAddress(ip=str(a)) for a in addresses])])

    return read


def short_deadlines(monkeypatch):
    """The real bound and the real interval, shrunk to milliseconds."""
    monkeypatch.setattr(tekton_install, "WEBHOOK_READY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(tekton_install, "WEBHOOK_READY_POLL_SECONDS", 0.02)
    return 0.3


def install(fixture_repo, recorded_api, fake_binaries, answer):
    """`tekton_install.install()` against a cluster whose webhook answers however `answer` says."""
    fixture_repo.environment_static(**PINS)
    recorded_api.answer("read_namespaced_endpoints", endpoints(answer))
    started = time.monotonic()
    install_the_pinned_releases(fixture_repo)
    return time.monotonic() - started


def test_a_webhook_that_never_arrives_stops_the_install_and_names_itself(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """The failure mode: nothing behind the Service, forever."""
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
    """It really waited, rather than failing fast on the first empty answer."""
    bound = short_deadlines(monkeypatch)
    started = time.monotonic()

    with pytest.raises(TektonWebhookNotReachableError):
        install(fixture_repo, recorded_api, fake_binaries, lambda: [])

    assert time.monotonic() - started >= bound, "the install gave up before its ceiling"


def test_the_read_that_answers_the_question_does_not_call_a_missing_service_an_error(
        fixture_repo, dry_run_env, fake_binaries, recorded_api, monkeypatch):
    """A Service that has no Endpoints object yet is `not ready`, not a crash."""
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
